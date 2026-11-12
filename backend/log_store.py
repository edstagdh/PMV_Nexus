"""
Searchable app log for Admin -> Logs.

Every loguru record at the configured level (backend processes: downloads,
re-encodes, syncs, scheduler, errors...) plus one "action" record per change
made through the API (see action_log.py) is written to its own small SQLite
file, data/logs/app_logs.db -- separate from library.db so heavy logging
never competes with library writes. The rotated text file (backend.log) is
unchanged; this is the queryable copy the interface reads.

The loguru sink only builds a row and puts it on a queue; one background
thread writes rows in batches and prunes old ones, so logging never blocks
the event loop on disk I/O.
"""
from __future__ import annotations

import atexit
import json
import queue
import sqlite3
import threading
import time
import traceback
from pathlib import Path
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS logs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL    NOT NULL,
    level     TEXT    NOT NULL,
    level_no  INTEGER NOT NULL,
    kind      TEXT    NOT NULL,          -- "action" (done by a user) | "system"
    category  TEXT,                      -- "library", "auth", "users", ... | "system"
    event     TEXT,                      -- "<category>.<what>", e.g. "library.rename_item"
    user      TEXT,
    ip        TEXT,                      -- as logging.ip_mode allows (ip_privacy.py)
    source    TEXT,
    message   TEXT    NOT NULL,
    detail    TEXT,                      -- traceback, if any
    extra     TEXT                       -- JSON: method, path, status, ms, item...
);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);
CREATE INDEX IF NOT EXISTS idx_logs_kind_id ON logs(kind, id);
CREATE INDEX IF NOT EXISTS idx_logs_level_id ON logs(level_no, id);
"""
# Added in 1.11.0; ALTERed into older log databases on startup.
_NEW_COLUMNS = (("category", "TEXT"), ("event", "TEXT"), ("ip", "TEXT"))
_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_logs_category_id ON logs(category, id);
CREATE INDEX IF NOT EXISTS idx_logs_event_id ON logs(event, id);
CREATE INDEX IF NOT EXISTS idx_logs_user_id ON logs(user, id);
"""

LEVELS = {"DEBUG": 10, "INFO": 20, "SUCCESS": 25, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
_MAX_MESSAGE = 4000
_MAX_DETAIL = 20000
_PRUNE_EVERY_S = 600


def _short_source(name: Optional[str]) -> str:
    """'backend.library_mixins.download_worker' -> 'download_worker'."""
    if not name:
        return ""
    return name.rsplit(".", 1)[-1] if name.startswith("backend.") else name


_RETARGET = "__retarget__"
_RETARGET_WINDOW_S = 7 * 86400
_PLACEHOLDER_TITLES = {"Fetching details..."}  # library_mixins/core.py add()


class LogStore:
    def __init__(self, path: Path, retention_days: int, max_rows: int) -> None:
        self.path = path
        self.retention_days = max(1, retention_days)
        self.max_rows = max(1000, max_rows)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        conn.executescript(_SCHEMA)
        have = {r[1] for r in conn.execute("PRAGMA table_info(logs)")}
        for name, typ in _NEW_COLUMNS:
            if name not in have:
                conn.execute(f"ALTER TABLE logs ADD COLUMN {name} {typ}")
        # Older rows: derive category/event from what they already have.
        conn.execute("UPDATE logs SET category = CASE kind WHEN 'action' THEN COALESCE(source, 'api') ELSE 'system' END "
                     "WHERE category IS NULL")
        conn.execute("UPDATE logs SET event = category || '.' || COALESCE(source, 'app') WHERE event IS NULL")
        conn.executescript(_INDEXES)
        # 1.9.0 wrote sign-in addresses in full ("client" field and message);
        # logging.ip_mode now governs them, so the old raw values are dropped.
        conn.execute("UPDATE logs SET extra = json_remove(extra, '$.client') WHERE extra LIKE '%\"client\"%'")
        conn.execute("UPDATE logs SET message = substr(message, 1, instr(message, ' from ') - 1) "
                     "WHERE source = 'auth' AND message LIKE '%Sign% from %'")
        conn.commit()
        conn.close()
        self._queue: "queue.Queue[Optional[tuple]]" = queue.Queue(maxsize=50_000)
        self._thread = threading.Thread(target=self._writer, name="log-store-writer", daemon=True)
        self._thread.start()
        atexit.register(self.close)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    # ------------------------------------------------------------ writing
    def sink(self, message) -> None:
        """loguru sink: turns a record into a row and queues it (never blocks)."""
        r = message.record
        extra = dict(r["extra"])
        kind = extra.pop("kind", "system")
        user = extra.pop("user", None)
        source = extra.pop("source", None) or _short_source(r["name"])
        ip = extra.pop("ip", None)
        category = extra.pop("category", None) or ("api" if kind == "action" else "system")
        event = extra.pop("event", None) or f"{category}.{source or 'app'}"
        detail = None
        if r["exception"] is not None:
            exc = r["exception"]
            detail = "".join(traceback.format_exception(exc.type, exc.value, exc.traceback))[-_MAX_DETAIL:]
        safe_extra = {k: v for k, v in extra.items() if isinstance(v, (str, int, float, bool)) or v is None}
        row = (
            r["time"].timestamp(), r["level"].name, r["level"].no, kind, category, event, user, ip, source,
            str(r["message"])[:_MAX_MESSAGE], detail, json.dumps(safe_extra) if safe_extra else None,
        )
        try:
            self._queue.put_nowait(row)
        except queue.Full:
            pass  # a runaway log storm must not take the app down

    def _writer(self) -> None:
        conn = self._connect()
        last_prune = 0.0
        running = True
        while running:
            batch = []
            try:
                item = self._queue.get(timeout=1.0)
                if item is None:
                    running = False
                else:
                    batch.append(item)
                while len(batch) < 500:
                    item = self._queue.get_nowait()
                    if item is None:
                        running = False
                        break
                    batch.append(item)
            except queue.Empty:
                pass
            if batch:
                # Rows, with the occasional retarget() in between: kept in order.
                rows: list = []
                for entry in batch + [None]:
                    if entry is None or entry[0] == _RETARGET:
                        if rows:
                            try:
                                conn.executemany(
                                    "INSERT INTO logs (ts, level, level_no, kind, category, event, user, ip, source, message, detail, extra) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
                                conn.commit()
                            except sqlite3.Error:
                                pass
                            rows = []
                        if entry is not None:
                            self._apply_retarget(conn, entry[1], entry[2])
                    else:
                        rows.append(entry)
            if time.time() - last_prune > _PRUNE_EVERY_S:
                last_prune = time.time()
                self._prune(conn)
        conn.close()

    def retarget(self, item_id: str, title: str) -> None:
        """A video's real title is known now (its details were fetched after it
        was added): recent action entries that named it by its link or the
        "Fetching details..." placeholder are updated to the title."""
        if item_id and title:
            try:
                self._queue.put_nowait((_RETARGET, item_id, title))
            except queue.Full:
                pass

    @staticmethod
    def _apply_retarget(conn: sqlite3.Connection, item_id: str, title: str) -> None:
        try:
            rows = conn.execute(
                "SELECT id, message, extra FROM logs WHERE kind = 'action' AND ts > ? AND json_extract(extra, '$.item_id') = ?",
                (time.time() - _RETARGET_WINDOW_S, item_id)).fetchall()
            for row in rows:
                extra = json.loads(row["extra"] or "{}")
                old = extra.get("target")
                if not old or old == title or not (old.startswith("http") or old in _PLACEHOLDER_TITLES):
                    continue
                extra["target"] = title
                extra["target_was"] = old
                conn.execute("UPDATE logs SET message = ?, extra = ? WHERE id = ?",
                             (row["message"].replace(f"“{old}”", f"“{title}”"), json.dumps(extra), row["id"]))
            conn.commit()
        except (sqlite3.Error, ValueError):
            pass

    def _prune(self, conn: sqlite3.Connection) -> None:
        try:
            conn.execute("DELETE FROM logs WHERE ts < ?", (time.time() - self.retention_days * 86400,))
            row = conn.execute("SELECT MAX(id) FROM logs").fetchone()
            if row and row[0]:
                conn.execute("DELETE FROM logs WHERE id <= ?", (row[0] - self.max_rows,))
            conn.commit()
        except sqlite3.Error:
            pass

    def close(self) -> None:
        if self._thread.is_alive():
            try:
                self._queue.put(None, timeout=1)
            except queue.Full:
                return
            self._thread.join(timeout=3)

    # ------------------------------------------------------------ reading
    @staticmethod
    def _where(min_level: str, kind, category, event, user, ip, source, q) -> tuple[list[str], list]:
        where, args = ["level_no >= ?"], [LEVELS.get((min_level or "INFO").upper(), 20)]
        if kind in ("action", "system"):
            where.append("kind = ?"); args.append(kind)
        if category:
            if category.startswith("!"):  # "!system" = everything but these
                skip = [c for c in category[1:].split(",") if c]
                where.append(f"COALESCE(category, '') NOT IN ({','.join('?' * len(skip))})"); args += skip
            else:
                where.append("category = ?"); args.append(category)
        for col, value in (("event", event), ("user", user), ("ip", ip), ("source", source)):
            if value:
                where.append(f"{col} = ?"); args.append(value)
        if q:
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("(message LIKE ? ESCAPE '\\' OR detail LIKE ? ESCAPE '\\' OR extra LIKE ? ESCAPE '\\' "
                         "OR event LIKE ? ESCAPE '\\')")
            args += [like] * 4
        return where, args

    @staticmethod
    def _record(row) -> dict:
        """One entry as a JSON-ready dict: the fixed fields plus its own extra fields."""
        rec = dict(row)
        raw = rec.pop("extra", None)
        extra = json.loads(raw) if raw else {}
        rec.pop("level_no", None)
        rec["time"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(rec["ts"])) + f".{int(rec['ts'] % 1 * 1000):03d}Z"
        return {**rec, **{k: v for k, v in extra.items() if k not in rec}}

    def query(self, *, before_id: Optional[int] = None, after_id: Optional[int] = None, limit: int = 200,
              min_level: str = "INFO", kind: Optional[str] = None, category: Optional[str] = None,
              event: Optional[str] = None, user: Optional[str] = None, ip: Optional[str] = None,
              source: Optional[str] = None, q: Optional[str] = None, with_total: bool = True) -> dict:
        where, args = self._where(min_level, kind, category, event, user, ip, source, q)
        base_where, base_args = list(where), list(args)
        if after_id is not None:
            where.append("id > ?"); args.append(after_id)  # live tail: only newer
            order = "ASC"
        else:
            if before_id is not None:
                where.append("id < ?"); args.append(before_id)
            order = "DESC"
        limit = max(1, min(limit, 1000))
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM logs WHERE {' AND '.join(where)} ORDER BY id {order} LIMIT ?", (*args, limit + 1),
            ).fetchall()
            total = (conn.execute(f"SELECT COUNT(*) FROM logs WHERE {' AND '.join(base_where)}", base_args).fetchone()[0]
                     if with_total and after_id is None else None)
            latest = conn.execute("SELECT MAX(id) FROM logs").fetchone()[0] or 0
        finally:
            conn.close()
        has_more = len(rows) > limit
        rows = rows[:limit]
        if order == "ASC":
            rows.reverse()
        return {"items": [self._record(r) for r in rows], "has_more": has_more, "total": total, "latest_id": latest}

    def export(self, *, limit: int = 100_000, **filters) -> list[dict]:
        """Matching entries, newest first, as JSON-ready dicts (for a .jsonl download)."""
        where, args = self._where(filters.get("min_level", "INFO"), filters.get("kind"), filters.get("category"),
                                  filters.get("event"), filters.get("user"), filters.get("ip"),
                                  filters.get("source"), filters.get("q"))
        conn = self._connect()
        try:
            rows = conn.execute(f"SELECT * FROM logs WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",
                                (*args, limit)).fetchall()
        finally:
            conn.close()
        return [self._record(r) for r in rows]

    def facets(self, min_level: str = "INFO") -> dict:
        """What occurs in the log, with counts, for the filter menus."""
        level = LEVELS.get((min_level or "INFO").upper(), 20)
        conn = self._connect()
        try:
            def counts(col: str) -> dict:
                return {r[0]: r[1] for r in conn.execute(
                    f"SELECT {col}, COUNT(*) FROM logs WHERE level_no >= ? AND {col} IS NOT NULL "
                    f"GROUP BY {col} ORDER BY {col}", (level,))}
            return {"category": counts("category"), "event": counts("event"), "user": counts("user"),
                    "levels": list(LEVELS)}
        finally:
            conn.close()


_store: Optional[LogStore] = None


def init_log_store(path: Path, retention_days: int, max_rows: int) -> LogStore:
    global _store
    if _store is None:
        _store = LogStore(path, retention_days, max_rows)
    return _store


def get_log_store() -> Optional[LogStore]:
    return _store
