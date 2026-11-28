"""Runs the upgrade steps (steps.py) on this app's data (3.12.0).

At start (CoreMixin.__init__, before anything reads the library):
1. check_not_newer(): data a newer app upgraded -> this app refuses to start (it could damage it),
   unless PMV_ALLOW_OLDER_APP=1 is set (then it only warns);
2. run_quick(): a copy of library.db (data/backups/before-upgrade/), then every quick step;
3. later, CoreMixin.start() runs the long ("blocking") steps while STATE["active"] makes the
   API answer 503 "upgrading" and the pages show the Upgrading screen (main.py, App.vue).

Every step done is recorded in library.db's app_upgrades table, with the version that ran it.
A step that fails is logged and stays pending: the app starts without it (each step leaves the
data usable as it was) and tries again at the next start.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from .steps import STEPS, Ctx, plan

KEEP_COPIES = 3
STATE: dict = {"active": False, "steps": [], "current": None, "done": 0, "total": 0, "error": None,
               "started_at": None, "finished_at": None}
_lock = threading.Lock()


class DataTooNewError(RuntimeError):
    pass


def _ensure_table(con: sqlite3.Connection) -> None:
    con.execute("CREATE TABLE IF NOT EXISTS app_upgrades (id TEXT PRIMARY KEY, app_version TEXT NOT NULL, "
                "done_at REAL NOT NULL, seconds REAL, summary TEXT)")
    con.commit()


def _record(ctx: Ctx, step_id: str, app_version: str, seconds: float, summary: str) -> None:
    con = sqlite3.connect(str(ctx.library_db), timeout=30)
    try:
        _ensure_table(con)
        con.execute("INSERT OR REPLACE INTO app_upgrades (id, app_version, done_at, seconds, summary) VALUES (?, ?, ?, ?, ?)",
                    (step_id, app_version, time.time(), round(seconds, 2), summary[:500]))
        con.commit()
    finally:
        con.close()


def check_not_newer(ctx: Ctx) -> None:
    unknown = plan(ctx)["unknown"]
    if not unknown:
        return
    msg = ("This library was upgraded by a newer version of PMV Nexus (steps: " + ", ".join(unknown) + "). "
           "This version could damage it, so it won't start. Install the newer version again, or restore a backup "
           "made before it (data/backups/before-upgrade has the database as it was).")
    if os.environ.get("PMV_ALLOW_OLDER_APP") == "1":
        logger.warning("{} -- starting anyway (PMV_ALLOW_OLDER_APP=1)", msg)
        return
    logger.error(msg)
    raise DataTooNewError(msg)


def backup_before(ctx: Ctx, app_version: str) -> Path:
    """A consistent copy of library.db (SQLite's backup, safe while it's open). Raises when it can't
    be made -- then no step runs."""
    folder = ctx.data_dir / "backups" / "before-upgrade"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"library-before-{app_version}-{time.strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(str(ctx.library_db), timeout=30)
    try:
        out = sqlite3.connect(str(dest))
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    check = sqlite3.connect(f"file:{dest.as_posix()}?mode=ro", uri=True)
    try:
        if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError(f"the copy {dest} didn't pass SQLite's check")
    finally:
        check.close()
    copies = sorted(folder.glob("library-before-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in copies[KEEP_COPIES:]:
        try:
            old.unlink()
        except OSError:
            pass
    logger.info("Upgrade: copied the database to {} first", dest)
    return dest


def _run(ctx: Ctx, step, app_version: str) -> bool:
    started = time.monotonic()
    with _lock:
        STATE["current"] = step.title

    def progress(done: int, total: int) -> None:
        with _lock:
            STATE["step_done"], STATE["step_total"] = done, total

    try:
        summary = step.run(ctx, progress)
    except Exception as exc:  # noqa: BLE001 -- a failed step stays pending, the data as it was
        logger.opt(exception=exc).error("Upgrade step '{}' failed: {} -- it's tried again at the next start", step.id, exc)
        with _lock:
            STATE["error"] = f"{step.title}: {exc}"
        return False
    took = time.monotonic() - started
    _record(ctx, step.id, app_version, took, summary or "")
    logger.info("Upgrade step '{}' done in {:.1f}s: {}", step.id, took, summary)
    return True


def _skip_unneeded(ctx: Ctx, app_version: str, pending_ids: set[str], done: set[str]) -> None:
    """Steps this data never needed (a library made after them) are recorded as done."""
    for step in STEPS:
        if step.id not in done and step.id not in pending_ids:
            _record(ctx, step.id, app_version, 0.0, "not needed")


def run_quick(ctx: Ctx, app_version: str) -> dict:
    """The quick steps, now. Returns the plan of what's left (the long steps)."""
    if not ctx.library_db.is_file():
        return {"steps": []}
    con = sqlite3.connect(str(ctx.library_db), timeout=30)
    try:
        _ensure_table(con)
        done = {r[0] for r in con.execute("SELECT id FROM app_upgrades")}
    finally:
        con.close()
    p = plan(ctx)
    pending = {s["id"] for s in p["steps"]}
    _skip_unneeded(ctx, app_version, pending, done)
    if not pending:
        return p
    try:
        backup_before(ctx, app_version)
    except Exception as exc:  # noqa: BLE001
        logger.opt(exception=exc).error("Upgrade: couldn't copy the database first ({}) -- no upgrade step runs "
                                        "until it can (free some space?)", exc)
        with _lock:
            STATE["error"] = f"Couldn't copy the database before upgrading: {exc}"
        return {"steps": []}
    by_id = {s.id: s for s in STEPS}
    for s in p["steps"]:
        step = by_id[s["id"]]
        if step.blocking:
            break          # a long step: the ones after it wait for it (their order matters)
        if not _run(ctx, step, app_version):
            break
    rest = plan(ctx)
    with _lock:
        STATE["current"] = None
    return rest


def run_blocking(ctx: Ctx, app_version: str) -> None:
    """BLOCKING: the long steps and everything after them, with STATE showing progress."""
    p = plan(ctx)
    if not p["steps"]:
        return
    by_id = {s.id: s for s in STEPS}
    with _lock:
        STATE.update(active=True, steps=[s["title"] for s in p["steps"]], done=0, total=len(p["steps"]),
                     error=None, started_at=time.time(), finished_at=None, seconds=p["seconds"])
    try:
        for s in p["steps"]:
            if not _run(ctx, by_id[s["id"]], app_version):
                break
            with _lock:
                STATE["done"] += 1
    finally:
        with _lock:
            STATE.update(active=False, current=None, finished_at=time.time())


def has_blocking(p: dict) -> bool:
    return any(s.get("blocking") for s in p.get("steps", []))


def state() -> dict:
    with _lock:
        return dict(STATE)


def ctx_for(library_db: Path) -> Ctx:
    return Ctx.for_data_dir(library_db.parent, library_db)


def history(ctx: Ctx) -> list[dict]:
    con = sqlite3.connect(f"file:{ctx.library_db.as_posix()}?mode=ro", uri=True) if ctx.library_db.is_file() else None
    if con is None:
        return []
    try:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute("SELECT * FROM app_upgrades ORDER BY done_at")]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def from_version(ctx: Ctx) -> Optional[str]:
    """The version that last ran on this data (app_state "app_version_seen", 3.12.0), when known."""
    con = sqlite3.connect(f"file:{ctx.library_db.as_posix()}?mode=ro", uri=True) if ctx.library_db.is_file() else None
    if con is None:
        return None
    try:
        row = con.execute("SELECT value FROM app_state WHERE key = 'app_version_seen'").fetchone()
        return row[0].strip('"') if row else None
    except sqlite3.Error:
        return None
    finally:
        con.close()
