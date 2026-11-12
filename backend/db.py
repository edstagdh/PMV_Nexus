"""
SQLite-backed persistence for the download library, replacing the old
single-JSON-file store (`library.json`).

Why: the old store rewrote the *entire* library -- every item, serialized to
one JSON blob -- on every single save, including per-second progress ticks
during a download/re-encode/share. That gets slow and disk-heavy once the
library holds a lot of history. Here, each LibraryItem is one SQLite row, and
callers can upsert just the row(s) that actually changed instead of the
whole table.

The full item is still stored as a JSON blob (`data` column) rather than
being fully normalized into columns -- LibraryItem's schema keeps growing
(tags, tech_* fields, shares, etc.) and this way adding a new field to the
model doesn't require a matching SQL migration. A few "hot" fields are
promoted to real columns purely so they're indexable/filterable without
deserializing every row: id, status, created_at, download_datetime.

LibraryManager still keeps the full library in memory (self._items) as the
source of truth during a run, same as before -- this module is purely the
persistence layer underneath it, not a query engine the rest of the app
talks to directly.

USERS / ROLES / SETTINGS
-------------------------
Added alongside the library-item tables above, in the same SQLite file
(this app is explicitly single-process/single-database -- see the README's
"Reliability & design notes" -- so there's no benefit to a second file/
connection just for user accounts).

Unlike LibraryItem, User/Role rows use real columns rather than a JSON
blob: their schema is small and stable (not something that grows a new
field every few weeks the way LibraryItem has), so normal indexable
columns are simpler and cheaper to query than a blob would be here.

- `roles`: one row per user class (e.g. "admin", "user"). `permissions` is
  a JSON-encoded array of permission strings (or `["*"]` for "grants
  everything", used by admin) -- kept as a role-level list rather than
  hardcoded in Python so adding a new class later (per the project's
  stated plan to add more later) is a new DB row, not a code change.
- `users`: one row per account. `password_hash` is a bcrypt hash (hashing
  itself happens in backend/security.py, a business-logic concern -- this
  layer just stores/retrieves whatever hash string it's given, same
  separation LibraryManager keeps from LibraryDB for library items).
  `token_version` is an integer bumped by bump_token_version() any time a
  user's role/active-status/password changes -- the session cookie carries
  the token_version that was current when it was issued, and the server
  compares the two on each request (see security.py) to log a user out
  immediately when they're demoted/deactivated/have their password reset,
  without a DB hit on every normal request.
- `user_settings`: one row per (user, setting key) -- e.g. ("u1",
  "page_size", "50"). Key-value rather than fixed columns, mirroring the
  same pattern config.py's settings_overrides.json already uses for
  app-wide settings, so a new per-user setting later (a new preference)
  never needs a schema migration.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from collections import deque
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional

from loguru import logger

from .models import LibraryItem, Role, User

_SCHEMA = """
CREATE TABLE IF NOT EXISTS library_items (
    id                 TEXT PRIMARY KEY,
    status             TEXT NOT NULL,
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL,
    download_datetime  TEXT,
    data               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_library_items_status ON library_items(status);
CREATE INDEX IF NOT EXISTS idx_library_items_created_at ON library_items(created_at);
CREATE INDEX IF NOT EXISTS idx_library_items_download_datetime ON library_items(download_datetime);

-- Issues the person has explicitly told the Settings -> Issues tab to
-- stop flagging (e.g. "these two videos aren't actually duplicates").
-- `id` is a deterministic key built from issue_type + the sorted item
-- ids involved (see _ignored_issue_key below), so ignoring the same
-- exact issue twice is just a harmless upsert, not a duplicate row.
CREATE TABLE IF NOT EXISTS ignored_issues (
    id           TEXT PRIMARY KEY,
    issue_type   TEXT NOT NULL,
    item_ids     TEXT NOT NULL,
    created_at   REAL NOT NULL
);
-- Persisted pairwise duplicate-video matches, computed by the background
-- duplicate-scan worker (see backend/library_mixins/duplicate_worker.py)
-- rather than recomputed live on every request. `id` is a deterministic
-- key built from the two item ids, sorted (see db.upsert_duplicate_match())
-- so re-scoring the same pair is a harmless upsert, not a duplicate row.
-- Only pairs scoring >= the worker's "likely" threshold are ever written
-- here -- everything below that is noise and is never persisted at all.
CREATE TABLE IF NOT EXISTS duplicate_matches (
    id                 TEXT PRIMARY KEY,
    item_id_a          TEXT NOT NULL,
    item_id_b          TEXT NOT NULL,
    match_percentage   REAL NOT NULL,
    verdict            TEXT NOT NULL,
    best_offset        INTEGER,
    computed_at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_duplicate_matches_item_a ON duplicate_matches(item_id_a);
CREATE INDEX IF NOT EXISTS idx_duplicate_matches_item_b ON duplicate_matches(item_id_b);

-- Per-file samples for the duplicate finder (see backend/dupfinder.py):
-- N 32x32 grayscale frames (N*1024 bytes) taken at fixed relative positions,
-- a validity byte per frame, a cheap whole-file identity hash, and the audio
-- fingerprint (uint32 little-endian). Kept out of the item JSON blob on
-- purpose -- ~10-25 KB per file would bloat every item load/save.
CREATE TABLE IF NOT EXISTS dup_samples (
    item_id        TEXT PRIMARY KEY,
    algo_version   INTEGER NOT NULL,
    source_size    INTEGER NOT NULL,
    source_mtime   REAL NOT NULL,
    duration       REAL NOT NULL,
    num_samples    INTEGER NOT NULL,
    frames         BLOB NOT NULL,
    valid          BLOB NOT NULL,
    quick_hash     TEXT NOT NULL,
    audio_status   TEXT NOT NULL,
    audio_fp       BLOB NOT NULL,
    created_at     REAL NOT NULL
);

-- Small generic key-value store for app-level background-process state
-- that isn't tied to any one library item or user -- e.g. the duplicate
-- scan's "last sweep" timestamp, and cumulative Stats counters (see
-- LibraryDB.increment_counter()/get_counter() -- backed by this same
-- table; a counter is just a JSON-encoded int under its own key). Value
-- is JSON-encoded, so any JSON-serializable value works without a
-- schema change.
CREATE TABLE IF NOT EXISTS app_state (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

-- Historical Stats snapshots (see backend/library_mixins/stats_worker.py)
-- -- one row per captured point in time, `data` a JSON blob of the full
-- System+Library stats at that moment. This is what powers trend charts
-- (e.g. "library size over the last 30 days") -- a snapshot is only ever
-- taken when explicitly triggered (the Stats page's "Capture now"
-- button, or a Scheduler job of type "stats_snapshot"); nothing captures
-- one automatically on its own.
-- Notifications (2.10.0, backend/notifications.py): finished / failed
-- downloads, re-encodes, repairs, uploads, shares and jobs, low disk space...
-- user_id NULL = for the admins (settings.manage). Shown as toasts while the
-- app is open and kept in the notification center.
CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  REAL NOT NULL,
    user_id     TEXT,
    category    TEXT NOT NULL,
    level       TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT,
    link        TEXT,
    read        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, id);
-- Stats (2.11.0): per-day counters (downloads, re-encodes, uploads, shares,
-- repairs and their failures, bytes) and what was watched -- per user, video
-- and day. Days are local dates "YYYY-MM-DD".
CREATE TABLE IF NOT EXISTS stats_daily (
    day     TEXT NOT NULL,
    metric  TEXT NOT NULL,
    value   REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (day, metric)
);
CREATE TABLE IF NOT EXISTS watch_log (
    day      TEXT NOT NULL,
    user_id  TEXT NOT NULL,
    item_id  TEXT NOT NULL,
    seconds  REAL NOT NULL DEFAULT 0,
    plays    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, user_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_watch_log_item ON watch_log(item_id);
-- 2.17.0 performers not on ThePornDB: their placeholder gender (their photos use local_id
-- as performer_id in performer_custom_photos). library_mixins/performers.py
CREATE TABLE IF NOT EXISTS performer_local (
    local_id  TEXT PRIMARY KEY,
    name_key  TEXT NOT NULL UNIQUE,
    name      TEXT NOT NULL,
    gender    TEXT NOT NULL DEFAULT 'female'
);
-- 2.16.0 photos of performers added by hand (library_mixins/performers.py)
CREATE TABLE IF NOT EXISTS performer_custom_photos (
    id           TEXT PRIMARY KEY,
    performer_id TEXT NOT NULL,
    path         TEXT NOT NULL,       -- data/performer_photos/<id>.webp
    main         INTEGER NOT NULL DEFAULT 0,  -- shown first (her main photo)
    created_at   REAL NOT NULL,
    created_by   TEXT
);
CREATE INDEX IF NOT EXISTS idx_perf_custom_photos ON performer_custom_photos(performer_id);
-- 2.14.0 song recognition (library_mixins/songs.py)
CREATE TABLE IF NOT EXISTS song_scans (
    item_id    TEXT PRIMARY KEY,
    scanned_at REAL NOT NULL,
    samples    INTEGER NOT NULL DEFAULT 0,
    matched    INTEGER NOT NULL DEFAULT 0,
    status     TEXT NOT NULL,            -- done | failed
    detail     TEXT
);
CREATE TABLE IF NOT EXISTS song_suggestions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id    TEXT NOT NULL,
    label      TEXT NOT NULL,            -- "Song — Artist"
    title      TEXT, artist TEXT, url TEXT, source TEXT,
    hits       INTEGER NOT NULL DEFAULT 1,   -- samples that matched it
    samples    INTEGER NOT NULL DEFAULT 1,   -- samples taken in that scan
    times      TEXT NOT NULL DEFAULT '[]',   -- where in the video (seconds)
    status     TEXT NOT NULL DEFAULT 'pending', -- pending | added | dismissed
    created_at REAL NOT NULL,
    decided_at REAL, decided_by TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_song_sugg_item_label ON song_suggestions(item_id, label COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_song_sugg_status ON song_suggestions(status);
CREATE TABLE IF NOT EXISTS stats_snapshots (
    id            TEXT PRIMARY KEY,
    captured_at   REAL NOT NULL,
    data          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stats_snapshots_captured_at ON stats_snapshots(captured_at);
-- Generic background-job Scheduler (see backend/library_mixins/
-- scheduler_worker.py) -- one row per configured job (duplicate scan,
-- library repair, and future job types). schedule_config is JSON, shape
-- depends on schedule_mode ("simple": {"kind": "daily"|"interval_hours"
-- |"weekly", ...} / "advanced": {"expression": "<cron string>"}).
-- job_config is JSON too, but holds JOB-TYPE-SPECIFIC options rather
-- than scheduling info -- e.g. duplicate_scan's {"verbose_logging": bool}.
-- Defaults to '{}' so an old row from before this column existed reads
-- back as "no options set" rather than needing a value at all.
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id               TEXT PRIMARY KEY,
    job_type         TEXT NOT NULL,
    name             TEXT NOT NULL,
    enabled          INTEGER NOT NULL DEFAULT 1,
    schedule_mode    TEXT NOT NULL,
    schedule_config  TEXT NOT NULL,
    job_config       TEXT NOT NULL DEFAULT '{}',
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL,
    last_run_at      REAL,
    last_run_status  TEXT,
    next_run_at      REAL
);

-- One row per actual execution of a scheduled_jobs row -- the full run
-- history the Admin Portal's Scheduler page shows per job. `trigger` is
-- "schedule" (fired by the scheduler tick) or "manual" ("Run now").
CREATE TABLE IF NOT EXISTS scheduled_job_runs (
    id              TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL,
    started_at      REAL NOT NULL,
    finished_at     REAL,
    status          TEXT NOT NULL,
    trigger         TEXT NOT NULL,
    result_summary  TEXT
);
CREATE INDEX IF NOT EXISTS idx_scheduled_job_runs_job_id ON scheduled_job_runs(job_id);

-- One row per user class ("admin", "user", and future classes).
-- `permissions` is a JSON array of permission strings, or the single
-- string "*" (still JSON-encoded, e.g. '["*"]') meaning "every
-- permission" -- used by the admin role so a new permission string added
-- later doesn't require also updating every existing admin row.
CREATE TABLE IF NOT EXISTS roles (
    name         TEXT PRIMARY KEY,
    permissions  TEXT NOT NULL,
    created_at   REAL NOT NULL
);

-- Groups (2.12.0, backend/permissions.py): a set of permissions given to
-- every member, e.g. "Info helpers" -> names.manage.
CREATE TABLE IF NOT EXISTS user_groups (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    description  TEXT NOT NULL DEFAULT '',
    permissions  TEXT NOT NULL DEFAULT '[]',
    created_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS user_group_members (
    group_id  TEXT NOT NULL REFERENCES user_groups(id) ON DELETE CASCADE,
    user_id   TEXT NOT NULL,
    PRIMARY KEY (group_id, user_id)
);

-- One row per user account.
CREATE TABLE IF NOT EXISTS users (
    id             TEXT PRIMARY KEY,
    username       TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL REFERENCES roles(name),
    display_name   TEXT NOT NULL DEFAULT '',
    is_active      INTEGER NOT NULL DEFAULT 1,
    -- Bumped whenever this user's role/active-status/password changes --
    -- see the module docstring above for how this powers session
    -- invalidation without a DB hit on every request.
    token_version  INTEGER NOT NULL DEFAULT 0,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    last_login_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

-- Per-user key-value settings (page size, theme, default view, and
-- whatever future preferences get added) -- one row per (user, key), so
-- a new setting never needs a schema migration. `value` is JSON-encoded
-- so any JSON-serializable type (string/number/bool/object) can be
-- stored without a matching column type per setting.
CREATE TABLE IF NOT EXISTS user_settings (
    user_id      TEXT NOT NULL REFERENCES users(id),
    key          TEXT NOT NULL,
    value        TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);

-- Discover/Random: which videos each user has already been shown, so picks
-- don't repeat until the whole library has been seen (see
-- library_mixins/discover.py).
CREATE TABLE IF NOT EXISTS discover_history (
    user_id      TEXT NOT NULL,
    item_id      TEXT NOT NULL,
    seen_at      REAL NOT NULL,
    PRIMARY KEY (user_id, item_id)
);
"""

_UPSERT = """
INSERT INTO library_items (id, status, created_at, updated_at, download_datetime, data)
VALUES (:id, :status, :created_at, :updated_at, :download_datetime, :data)
ON CONFLICT(id) DO UPDATE SET
    status=excluded.status,
    created_at=excluded.created_at,
    updated_at=excluded.updated_at,
    download_datetime=excluded.download_datetime,
    data=excluded.data
"""

_DEFAULT_ROLES: dict[str, list[str]] = {
    "admin": ["*"],
    "user": [
        "library.view",
        "library.stream",
        "library.search",
        "live.view",
        "self.settings",
        "self.password_reset",
    ],
}

_USER_UPSERT = """
INSERT INTO users (id, username, password_hash, role, display_name, is_active, token_version, created_at, updated_at, last_login_at)
VALUES (:id, :username, :password_hash, :role, :display_name, :is_active, :token_version, :created_at, :updated_at, :last_login_at)
ON CONFLICT(id) DO UPDATE SET
    username=excluded.username,
    password_hash=excluded.password_hash,
    role=excluded.role,
    display_name=excluded.display_name,
    is_active=excluded.is_active,
    token_version=excluded.token_version,
    updated_at=excluded.updated_at,
    last_login_at=excluded.last_login_at
"""


# Playlists (2.2.0). `kind` "manual" keeps its own ordered rows in
# playlist_items; "smart" stores a library query (smart_query, JSON of the
# /api/library parameters) and is filled from it whenever it's read.
# visibility: "private" (owner only) | "public" (every user, read-only).
_PLAYLIST_SCHEMA = """
CREATE TABLE IF NOT EXISTS playlists (
    id           TEXT PRIMARY KEY,
    owner_id     TEXT NOT NULL,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    visibility   TEXT NOT NULL DEFAULT 'private',
    kind         TEXT NOT NULL DEFAULT 'manual',
    smart_query  TEXT,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_playlists_owner ON playlists(owner_id);
CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_id  TEXT NOT NULL,
    item_id      TEXT NOT NULL,
    position     REAL NOT NULL,
    added_at     REAL NOT NULL,
    PRIMARY KEY (playlist_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_playlist_items_item ON playlist_items(item_id);
"""


# Performers (2.7.0, ThePornDB). `performers`: one row per ThePornDB profile
# the library uses (our own id, TPDB's uuid, its data as JSON, local photo).
# `performer_names`: every performer name found in the library (folded:
# case/space-insensitive) and what it points to -- status linked (performer_id
# set; source auto|alias|manual), review (candidates JSON waiting for a
# pick), none (nothing on ThePornDB), skipped (left alone on purpose).
# `blocked` lists performer ids this name must never be merged into again
# (set when a wrong merge is split).
# Where each person stopped in each video (2.24.0): continue from there next time,
# and the thin "how far" line on thumbnails. Only ever shown to that person.
_WATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS watch_positions (
    user_id    TEXT NOT NULL,
    video_id   TEXT NOT NULL,
    position   REAL NOT NULL,
    duration   REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, video_id)
);
CREATE INDEX IF NOT EXISTS idx_watch_positions_video ON watch_positions(video_id);
"""

_PERFORMER_SCHEMA = """
CREATE TABLE IF NOT EXISTS performers (
    id           TEXT PRIMARY KEY,
    tpdb_id      TEXT UNIQUE,
    name         TEXT NOT NULL,
    data         TEXT NOT NULL DEFAULT '{}',
    similar      TEXT NOT NULL DEFAULT '[]',
    photo_path   TEXT,
    fetched_at   REAL,
    created_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS performer_names (
    name_key     TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    performer_id TEXT,
    status       TEXT NOT NULL DEFAULT 'review',
    source       TEXT,
    candidates   TEXT NOT NULL DEFAULT '[]',
    blocked      TEXT NOT NULL DEFAULT '[]',
    checked_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_performer_names_performer ON performer_names(performer_id);
"""


class _Result:
    """A statement's result, read in full while the connection was locked."""
    __slots__ = ("_rows", "_pos", "rowcount", "lastrowid", "description")

    def __init__(self, cursor: sqlite3.Cursor):
        self.description = cursor.description
        self._rows = cursor.fetchall() if cursor.description else []
        self._pos = 0
        self.rowcount = cursor.rowcount
        self.lastrowid = cursor.lastrowid

    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        self._pos += 1
        return self._rows[self._pos - 1]

    def fetchall(self) -> list:
        rest, self._pos = self._rows[self._pos:], len(self._rows)
        return rest

    def __iter__(self):
        return iter(self.fetchall())


class _LockedConnection:
    """The one sqlite3 connection, used by one thread at a time (2.22.0).

    Plain `def` routes run on FastAPI's worker threads, so the connection IS
    used from several threads at once (the old belief was "only the event-loop
    thread"). Interleaved statements on one connection mix up their rows --
    e.g. "IndexError: tuple index out of range" in list_scheduled_jobs while
    Tasks refreshed. Each statement now runs, and is read in full, under one
    lock."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self.lock = threading.RLock()

    def execute(self, sql: str, params=()) -> _Result:
        with self.lock:
            return _Result(self._conn.execute(sql, params))

    def executemany(self, sql: str, seq) -> _Result:
        with self.lock:
            return _Result(self._conn.executemany(sql, seq))

    def executescript(self, script: str) -> None:
        with self.lock:
            self._conn.executescript(script)

    def commit(self) -> None:
        with self.lock:
            self._conn.commit()

    def rollback(self) -> None:
        with self.lock:
            self._conn.rollback()

    def close(self) -> None:
        with self.lock:
            self._conn.close()


class LibraryDB:
    """
    Thin synchronous wrapper around sqlite3.

    Deliberately not async: every call here is a single fast local-disk
    operation. Called from the event-loop thread AND from FastAPI's worker
    threads (plain `def` routes), so every statement goes through
    _LockedConnection.
    """

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self.revision = 0
        self._change_log: deque[tuple[int, str]] = deque(maxlen=self._CHANGE_LOG_MAX)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        self._conn = _LockedConnection(conn)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        # If a write ever finds the connection momentarily busy, retry for up
        # to 5s instead of raising "database is locked" immediately. Belt and
        # braces alongside WAL mode; all writes in this app already funnel
        # through the single event-loop thread, so contention should be rare,
        # but this makes a burst of rapid saves (e.g. many reencode requests
        # queued back-to-back) resilient rather than erroring.
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._migrate_table_name_if_needed()
        self._conn.executescript(_SCHEMA)
        self._conn.executescript(_PLAYLIST_SCHEMA)
        self._conn.executescript(_PERFORMER_SCHEMA)
        self._conn.executescript(_WATCH_SCHEMA)
        self._conn.commit()
        # One-off additive migration: job_config was added to
        # scheduled_jobs after that table may already exist on an
        # install (e.g. a job already created this session) --
        # CREATE TABLE IF NOT EXISTS above only helps a genuinely NEW
        # table, so an ALTER TABLE is needed to backfill the column onto
        # an existing one. Idempotent -- a no-op once the column exists.
        self._ensure_column("scheduled_jobs", "job_config", "TEXT NOT NULL DEFAULT '{}'")
        # 2.1.0: built-in "system" jobs (backup, update check, duplicate
        # scan) carry a key; they can be edited and disabled, not deleted.
        self._ensure_column("scheduled_jobs", "system_key", "TEXT")
        # Duplicate finder rebuild: audio similarity + per-position details
        # on persisted matches (NULL for rows written by older versions).
        self._ensure_column("duplicate_matches", "audio_score", "REAL")
        self._ensure_column("duplicate_matches", "details", "TEXT")
        # 2.5.0: built-in playlists ("favorites", one per user) can't be deleted or renamed.
        self._ensure_column("playlists", "system_key", "TEXT")
        # 2.10.1: Favorites are always private (one made public before is made private again).
        self._conn.execute("UPDATE playlists SET visibility = 'private' WHERE system_key = 'favorites' AND visibility != 'private'")
        self._conn.commit()
        # 2.7.1: every photo of a performer (main image + ThePornDB posters), local paths as JSON.
        self._ensure_column("performers", "photos", "TEXT NOT NULL DEFAULT '[]'")
        self._seed_default_roles_if_needed()
        # 2.12.0: permissions given to one person directly (on top of role and groups).
        self._ensure_column("users", "extra_permissions", "TEXT NOT NULL DEFAULT '[]'")
        # 2.18.0: playlist covers -- "collage" (first 4 videos), "video" (cover_item's), "image" (data/playlist_covers/<id>.webp;
        # cover_path only records that one was set -- the file is found by the playlist id).
        self._ensure_column("playlists", "cover_mode", "TEXT NOT NULL DEFAULT 'collage'")
        self._ensure_column("playlists", "cover_item", "TEXT")
        self._ensure_column("playlists", "cover_path", "TEXT")
        # 2.14.0: the name a suggested song went into Music as (it may have been corrected).
        self._ensure_column("song_suggestions", "added_as", "TEXT")

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        """Adds `column` to `table` if it doesn't already exist -- a
        lightweight additive migration for a column introduced after the
        table itself may already have real data in it (see the call site
        in __init__ for scheduled_jobs.job_config). Safe to call every
        startup; a no-op once the column is present."""
        existing = {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            self._conn.commit()

    def _migrate_table_name_if_needed(self) -> None:
        """One-time migration for existing installs: the queue->library
        rename moved the *file* (data/queue.db -> data/library.db, handled
        in config.py) but that alone doesn't rename the *table* inside it --
        without this, CREATE TABLE IF NOT EXISTS library_items below would
        just create a second, empty table next to the old queue_items one
        still holding all the real data. Safe to leave in place permanently,
        like the JSON->SQLite migration this mirrors -- a no-op once the
        rename has happened."""
        has_old = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='queue_items'"
        ).fetchone()
        has_new = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='library_items'"
        ).fetchone()
        if has_old and not has_new:
            logger.info("Migrating SQLite table queue_items -> library_items")
            self._conn.execute("ALTER TABLE queue_items RENAME TO library_items")
            self._conn.commit()

    def _seed_default_roles_if_needed(self) -> None:
        """Inserts the default roles (admin/user) the first time this
        database is opened with an empty `roles` table. Safe to call every
        startup -- a no-op once roles already exist, same pattern as the
        legacy-JSON migration above. Does NOT touch the `roles` table again
        after that, so an admin who edits a role's permissions later (once
        role management is exposed) won't have those edits silently reset
        on the next restart."""
        has_any = self._conn.execute("SELECT 1 FROM roles LIMIT 1").fetchone()
        if has_any:
            return
        now = time.time()
        rows = [
            {"name": name, "permissions": json.dumps(perms), "created_at": now}
            for name, perms in _DEFAULT_ROLES.items()
        ]
        self._conn.executemany(
            "INSERT INTO roles (name, permissions, created_at) VALUES (:name, :permissions, :created_at)",
            rows,
        )
        self._conn.commit()
        logger.info("Seeded default roles: {}", ", ".join(_DEFAULT_ROLES.keys()))

    def ping(self) -> None:
        """Cheap liveness check for the health endpoint -- confirms the
        connection is open and the file is readable/not locked, without
        paying for a full table deserialize like load_all() would."""
        self._conn.execute("SELECT 1")

    # ---------------------------------------------------------------- read
    def load_all(self) -> dict[str, LibraryItem]:
        items: dict[str, LibraryItem] = {}
        for row in self._conn.execute("SELECT data FROM library_items"):
            try:
                data = json.loads(row["data"])
                # Defensive normalization for fields whose stored value can
                # be a legacy/malformed `null` even though the model
                # requires a real list -- LibraryItem.dup_phash_sequence
                # also has its own model-level validator for the same
                # thing (see models.py), but this catches it here too so a
                # future field added the same way can't silently drop a
                # whole row from loading again the way this one did.
                if data.get("dup_phash_sequence") is None:
                    data["dup_phash_sequence"] = []
                item = LibraryItem(**data)
                items[item.id] = item
            except Exception:
                logger.exception("Skipping unreadable library row while loading from SQLite")
        return items

    def is_empty(self) -> bool:
        return self._conn.execute("SELECT 1 FROM library_items LIMIT 1").fetchone() is None

    # --------------------------------------------------------------- write
    # ------------------------------------------------------ change tracking
    # Every item write bumps `revision` and records the item id, so the live
    # event stream (routers/events.py) can tell clients which items changed
    # instead of every client re-polling the whole library.
    _CHANGE_LOG_MAX = 5000

    # ------------------------------------------------------------ playlists
    def list_playlists(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM playlist_items i WHERE i.playlist_id = p.id) AS item_count "
            "FROM playlists p ORDER BY p.name COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_playlist(self, playlist_id: str) -> Optional[dict]:
        row = self._conn.execute("SELECT * FROM playlists WHERE id = ?", (playlist_id,)).fetchone()
        return dict(row) if row else None

    def create_playlist(self, row: dict) -> None:
        self._conn.execute(
            "INSERT INTO playlists (id, owner_id, name, description, visibility, kind, smart_query, created_at, updated_at, system_key) "
            "VALUES (:id, :owner_id, :name, :description, :visibility, :kind, :smart_query, :created_at, :updated_at, :system_key)",
            {"system_key": None, **row})
        self._conn.commit()
        self._note_change([f"playlist:{row['id']}"])

    def update_playlist(self, playlist_id: str, fields: dict) -> None:
        if fields:
            set_clause = ", ".join(f"{k} = :{k}" for k in fields)
            self._conn.execute(f"UPDATE playlists SET {set_clause} WHERE id = :id", {**fields, "id": playlist_id})
            self._conn.commit()
        self._note_change([f"playlist:{playlist_id}"])

    # ---- watch positions (2.24.0) ----
    def watch_positions(self, user_id: str) -> dict[str, tuple[float, float]]:
        rows = self._conn.execute("SELECT video_id, position, duration FROM watch_positions WHERE user_id = ?",
                                  (user_id,)).fetchall()
        return {r["video_id"]: (r["position"], r["duration"]) for r in rows}

    def set_watch_position(self, user_id: str, video_id: str, position: float, duration: float) -> None:
        self._conn.execute(
            "INSERT INTO watch_positions (user_id, video_id, position, duration, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, video_id) DO UPDATE SET position = excluded.position, "
            "duration = excluded.duration, updated_at = excluded.updated_at",
            (user_id, video_id, position, duration, time.time()))
        self._conn.commit()

    def delete_watch_position(self, user_id: Optional[str], video_id: str) -> None:
        """One person's (user_id) or everyone's (None) place in a video."""
        if user_id is None:
            self._conn.execute("DELETE FROM watch_positions WHERE video_id = ?", (video_id,))
        else:
            self._conn.execute("DELETE FROM watch_positions WHERE user_id = ? AND video_id = ?", (user_id, video_id))
        self._conn.commit()

    def delete_playlist(self, playlist_id: str) -> None:
        self._conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
        self._conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        self._conn.commit()
        self._note_change([f"playlist:{playlist_id}"])

    def playlist_item_ids(self, playlist_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT item_id FROM playlist_items WHERE playlist_id = ? ORDER BY position, added_at", (playlist_id,)).fetchall()
        return [r["item_id"] for r in rows]

    def playlist_first_items(self) -> dict[str, list[str]]:
        """playlist id -> its first few item ids (cover thumbnails)."""
        out: dict[str, list[str]] = {}
        for r in self._conn.execute("SELECT playlist_id, item_id FROM playlist_items ORDER BY playlist_id, position, added_at"):
            ids = out.setdefault(r["playlist_id"], [])
            if len(ids) < 4:
                ids.append(r["item_id"])
        return out

    def playlists_containing(self, item_id: str) -> set[str]:
        rows = self._conn.execute("SELECT playlist_id FROM playlist_items WHERE item_id = ?", (item_id,)).fetchall()
        return {r["playlist_id"] for r in rows}

    def add_playlist_items(self, playlist_id: str, item_ids: list[str]) -> int:
        """Appends the ids not already in the playlist, in order. Returns how many were added."""
        existing = set(self.playlist_item_ids(playlist_id))
        row = self._conn.execute("SELECT MAX(position) AS m FROM playlist_items WHERE playlist_id = ?", (playlist_id,)).fetchone()
        pos = (row["m"] or 0) if row else 0
        now = time.time()
        new = []
        for item_id in item_ids:
            if item_id in existing:
                continue
            existing.add(item_id)
            pos += 1
            new.append((playlist_id, item_id, pos, now))
        if new:
            self._conn.executemany("INSERT INTO playlist_items (playlist_id, item_id, position, added_at) VALUES (?, ?, ?, ?)", new)
            self._conn.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (now, playlist_id))
            self._conn.commit()
            self._note_change([f"playlist:{playlist_id}"])
        return len(new)

    def remove_playlist_items(self, playlist_id: str, item_ids: list[str]) -> int:
        cur = self._conn.executemany("DELETE FROM playlist_items WHERE playlist_id = ? AND item_id = ?",
                                     [(playlist_id, i) for i in item_ids])
        self._conn.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (time.time(), playlist_id))
        self._conn.commit()
        self._note_change([f"playlist:{playlist_id}"])
        return cur.rowcount

    def repoint_playlist_items(self, old_id: str, new_id: str) -> None:
        """old_id was merged into new_id: playlists with old_id get new_id in
        its place (or just lose old_id if they already have new_id)."""
        rows = self._conn.execute("SELECT playlist_id FROM playlist_items WHERE item_id = ?", (old_id,)).fetchall()
        for r in rows:
            pid = r["playlist_id"]
            has_new = self._conn.execute("SELECT 1 FROM playlist_items WHERE playlist_id = ? AND item_id = ?", (pid, new_id)).fetchone()
            if has_new:
                self._conn.execute("DELETE FROM playlist_items WHERE playlist_id = ? AND item_id = ?", (pid, old_id))
            else:
                self._conn.execute("UPDATE playlist_items SET item_id = ? WHERE playlist_id = ? AND item_id = ?", (new_id, pid, old_id))
        if rows:
            self._conn.commit()
            self._note_change([f"playlist:{r['playlist_id']}" for r in rows])

    # ------------------------------------------------------------ performers
    def performer_rows(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM performers").fetchall()]

    def performer_name_rows(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM performer_names").fetchall()]

    def upsert_performer(self, row: dict) -> None:
        cols = ", ".join(row)
        self._conn.execute(
            f"INSERT INTO performers ({cols}) VALUES ({', '.join(':' + k for k in row)}) "
            f"ON CONFLICT(id) DO UPDATE SET {', '.join(f'{k} = excluded.{k}' for k in row if k != 'id')}", row)
        self._conn.commit()
        self._note_change([f"performer:{row['id']}"])

    # 2.17.0 performers not on ThePornDB
    def performer_local_rows(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM performer_local")]

    def upsert_performer_local(self, local_id: str, name_key: str, name: str, gender: str) -> None:
        self._conn.execute("INSERT INTO performer_local (local_id, name_key, name, gender) VALUES (?, ?, ?, ?) "
                           "ON CONFLICT(local_id) DO UPDATE SET name = excluded.name, gender = excluded.gender",
                           (local_id, name_key, name, gender))
        self._conn.commit()
        self._note_change([f"performer:{local_id}", "performers"])

    def move_custom_photos(self, from_owner: str, to_owner: str) -> int:
        """Photos of a name that just got its ThePornDB profile go with her; one stays
        "shown first" only if she had none."""
        has_main = self._conn.execute("SELECT 1 FROM performer_custom_photos WHERE performer_id = ? AND main = 1",
                                      (to_owner,)).fetchone()
        if has_main:
            self._conn.execute("UPDATE performer_custom_photos SET main = 0 WHERE performer_id = ?", (from_owner,))
        n = self._conn.execute("UPDATE performer_custom_photos SET performer_id = ? WHERE performer_id = ?",
                               (to_owner, from_owner)).rowcount
        self._conn.commit()
        if n:
            self._note_change([f"performer:{to_owner}", "performers"])
        return n

    # 2.16.0 photos added by hand
    def custom_photo_rows(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM performer_custom_photos ORDER BY created_at")]

    def add_custom_photo(self, photo_id: str, performer_id: str, path: str, by: str | None) -> None:
        self._conn.execute("INSERT INTO performer_custom_photos (id, performer_id, path, main, created_at, created_by) "
                           "VALUES (?, ?, ?, 0, ?, ?)", (photo_id, performer_id, path, time.time(), by))
        self._conn.commit()
        self._note_change([f"performer:{performer_id}"])

    def delete_custom_photo(self, photo_id: str) -> None:
        row = self._conn.execute("SELECT performer_id FROM performer_custom_photos WHERE id = ?", (photo_id,)).fetchone()
        self._conn.execute("DELETE FROM performer_custom_photos WHERE id = ?", (photo_id,))
        self._conn.commit()
        if row:
            self._note_change([f"performer:{row['performer_id']}"])

    def set_custom_photo_main(self, performer_id: str, photo_id: str | None) -> None:
        """photo_id first for her (None: ThePornDB's main photo first again)."""
        self._conn.execute("UPDATE performer_custom_photos SET main = (id = ?) WHERE performer_id = ?", (photo_id, performer_id))
        self._conn.commit()
        self._note_change([f"performer:{performer_id}"])

    def set_performer_photos(self, performer_id: str, photos: list[str]) -> None:
        self._conn.execute("UPDATE performers SET photos = ?, photo_path = ? WHERE id = ?",
                           (json.dumps(photos), photos[0] if photos else None, performer_id))
        self._conn.commit()
        self._note_change([f"performer:{performer_id}"])

    def upsert_performer_name(self, row: dict) -> None:
        cols = ", ".join(row)
        self._conn.execute(
            f"INSERT INTO performer_names ({cols}) VALUES ({', '.join(':' + k for k in row)}) "
            f"ON CONFLICT(name_key) DO UPDATE SET {', '.join(f'{k} = excluded.{k}' for k in row if k != 'name_key')}", row)
        self._conn.commit()
        self._note_change(["performers"])

    def delete_performer(self, performer_id: str) -> None:
        self._conn.execute("UPDATE performer_names SET performer_id = NULL, status = 'review', source = NULL "
                           "WHERE performer_id = ?", (performer_id,))
        self._conn.execute("DELETE FROM performers WHERE id = ?", (performer_id,))
        self._conn.commit()
        self._note_change(["performers"])

    def set_playlist_order(self, playlist_id: str, item_ids: list[str]) -> None:
        """Renumbers the playlist in the given order; ids not listed keep their place after them."""
        current = self.playlist_item_ids(playlist_id)
        listed = [i for i in item_ids if i in set(current)]
        order = listed + [i for i in current if i not in set(listed)]
        self._conn.executemany("UPDATE playlist_items SET position = ? WHERE playlist_id = ? AND item_id = ?",
                               [(n + 1, playlist_id, i) for n, i in enumerate(order)])
        self._conn.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (time.time(), playlist_id))
        self._conn.commit()
        self._note_change([f"playlist:{playlist_id}"])

    def _note_change(self, item_ids: Iterable[str]) -> None:
        for item_id in item_ids:
            self.revision += 1
            self._change_log.append((self.revision, item_id))

    def changes_since(self, revision: int) -> tuple[int, Optional[set[str]]]:
        """(current revision, ids changed after `revision`). The id set is
        None when the log no longer reaches back that far (client must do a
        full refresh)."""
        log = self._change_log
        if revision >= self.revision:
            return self.revision, set()
        if log and log[0][0] > revision + 1 and len(log) == log.maxlen:
            return self.revision, None
        return self.revision, {item_id for rev, item_id in log if rev > revision}

    def save_item(self, item: LibraryItem) -> None:
        item.apply_user_edits()
        self._conn.execute(_UPSERT, _row_params(item))
        self._conn.commit()
        self._note_change([item.id])

    def save_items(self, items: Iterable[LibraryItem]) -> None:
        """Upserts several items in a single transaction. Used both for the
        'save everything' fallback (mirrors the old JSON writer's
        behavior, for call sites that touch more than one item at once)
        and for one-time bulk operations like migration/backfill."""
        items = list(items)
        for i in items:
            i.apply_user_edits()
        rows = [_row_params(i) for i in items]
        self._note_change(i.id for i in items)
        if not rows:
            return
        self._conn.executemany(_UPSERT, rows)
        self._conn.commit()

    def delete_item(self, item_id: str) -> None:
        self._conn.execute("DELETE FROM library_items WHERE id = ?", (item_id,))
        self._conn.execute("DELETE FROM playlist_items WHERE item_id = ?", (item_id,))
        self._conn.execute("DELETE FROM song_scans WHERE item_id = ?", (item_id,))
        self._conn.execute("DELETE FROM song_suggestions WHERE item_id = ?", (item_id,))
        self._conn.commit()
        self._note_change([item_id])

    # ------------------------------------------------------- song recognition (2.14.0)
    def save_song_scan(self, item_id: str, samples: int, matched: int, status: str, detail: str | None) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO song_scans (item_id, scanned_at, samples, matched, status, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (item_id, time.time(), samples, matched, status, detail))
        self._conn.commit()

    def get_song_scan(self, item_id: str) -> Optional[dict]:
        row = self._conn.execute("SELECT * FROM song_scans WHERE item_id = ?", (item_id,)).fetchone()
        return dict(row) if row else None

    def song_scan_times(self) -> dict[str, float]:
        return {r["item_id"]: r["scanned_at"] for r in self._conn.execute("SELECT item_id, scanned_at FROM song_scans")}

    def store_song_suggestions(self, item_id: str, found: list[dict]) -> int:
        """A scan's songs for one video. Songs already added or dismissed for it stay
        as they were; pending ones not found again are dropped. Returns how many are pending."""
        decided = {r["label"].lower() for r in self._conn.execute(
            "SELECT label FROM song_suggestions WHERE item_id = ? AND status != 'pending'", (item_id,))}
        self._conn.execute("DELETE FROM song_suggestions WHERE item_id = ? AND status = 'pending'", (item_id,))
        n = 0
        for s in found:
            if s["label"].lower() in decided:
                continue
            self._conn.execute(
                "INSERT INTO song_suggestions (item_id, label, title, artist, url, source, hits, samples, times, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (item_id, s["label"], s.get("title"), s.get("artist"), s.get("url"), s.get("source"),
                 s["hits"], s["samples"], json.dumps(s.get("times") or []), time.time()))
            n += 1
        self._conn.commit()
        self._note_change(["songs"])
        return n

    def list_song_suggestions(self, status: str = "pending", item_id: Optional[str] = None) -> list[dict]:
        sql, args = "SELECT * FROM song_suggestions WHERE status = ?", [status]
        if item_id:
            sql += " AND item_id = ?"
            args.append(item_id)
        rows = self._conn.execute(sql + " ORDER BY created_at DESC, hits DESC", args).fetchall()
        return [{**dict(r), "times": json.loads(r["times"] or "[]")} for r in rows]

    def get_song_suggestion(self, suggestion_id: int) -> Optional[dict]:
        row = self._conn.execute("SELECT * FROM song_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        return dict(row) if row else None

    def decide_song_suggestion(self, suggestion_id: int, status: str, by: str, label: Optional[str] = None) -> None:
        self._conn.execute(
            "UPDATE song_suggestions SET status = ?, decided_at = ?, decided_by = ?, added_as = ? WHERE id = ?",
            (status, time.time(), by, label, suggestion_id))
        self._conn.commit()
        self._note_change(["songs"])

    def song_counts(self) -> dict:
        row = self._conn.execute(
            "SELECT SUM(status = 'pending') AS pending, COUNT(DISTINCT CASE WHEN status = 'pending' THEN item_id END) AS videos, "
            "SUM(status = 'added') AS added, SUM(status = 'dismissed') AS dismissed FROM song_suggestions").fetchone()
        scanned = self._conn.execute("SELECT COUNT(*) FROM song_scans").fetchone()[0]
        return {"pending": row["pending"] or 0, "pending_videos": row["videos"] or 0, "added": row["added"] or 0,
                "dismissed": row["dismissed"] or 0, "scanned": scanned}

    # ------------------------------------------------------- ignored issues
    def is_issue_ignored(self, issue_type: str, item_ids: list[str]) -> bool:
        key = _ignored_issue_key(issue_type, item_ids)
        row = self._conn.execute("SELECT 1 FROM ignored_issues WHERE id = ?", (key,)).fetchone()
        return row is not None

    def ignore_issue(self, issue_type: str, item_ids: list[str]) -> None:
        key = _ignored_issue_key(issue_type, item_ids)
        self._conn.execute(
            "INSERT OR REPLACE INTO ignored_issues (id, issue_type, item_ids, created_at) VALUES (?, ?, ?, ?)",
            (key, issue_type, json.dumps(sorted(item_ids)), time.time()),
        )
        self._conn.commit()

    def unignore_issue(self, issue_type: str, item_ids: list[str]) -> None:
        key = _ignored_issue_key(issue_type, item_ids)
        self._conn.execute("DELETE FROM ignored_issues WHERE id = ?", (key,))
        self._conn.commit()

    def list_ignored_issues(self, issue_type: Optional[str] = None) -> list[dict]:
        if issue_type:
            rows = self._conn.execute(
                "SELECT issue_type, item_ids FROM ignored_issues WHERE issue_type = ?", (issue_type,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT issue_type, item_ids FROM ignored_issues").fetchall()
        return [{"issue_type": r["issue_type"], "item_ids": json.loads(r["item_ids"])} for r in rows]

    # ------------------------------------------------------ duplicate matches
    def upsert_duplicate_match(
            self, item_id_a: str, item_id_b: str, match_percentage: float, verdict: str, best_offset: Optional[int],
            audio_score: Optional[float] = None, details: Optional[dict] = None,
    ) -> None:
        """Stores (or re-scores) one pairwise duplicate-match result --
        `id` is built from the two item ids sorted, so it doesn't matter
        which order the caller passes them in; scoring the same pair
        again later (e.g. after a file change) just overwrites this same
        row instead of creating a duplicate."""
        id_a, id_b = sorted([item_id_a, item_id_b])
        key = f"{id_a}:{id_b}"
        self._conn.execute(
            """
            INSERT INTO duplicate_matches (id, item_id_a, item_id_b, match_percentage, verdict, best_offset, computed_at, audio_score, details)
            VALUES (:id, :item_id_a, :item_id_b, :match_percentage, :verdict, :best_offset, :computed_at, :audio_score, :details)
            ON CONFLICT(id) DO UPDATE SET
                match_percentage=excluded.match_percentage,
                verdict=excluded.verdict,
                best_offset=excluded.best_offset,
                computed_at=excluded.computed_at,
                audio_score=excluded.audio_score,
                details=excluded.details
            """,
            {
                "id": key, "item_id_a": id_a, "item_id_b": id_b,
                "match_percentage": match_percentage, "verdict": verdict,
                "best_offset": best_offset, "computed_at": time.time(),
                "audio_score": audio_score,
                "details": json.dumps(details) if details is not None else None,
            },
        )
        self._conn.commit()

    def list_duplicate_matches(self, min_percentage: float = 0.0) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM duplicate_matches WHERE match_percentage >= ? ORDER BY match_percentage DESC",
            (min_percentage,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_duplicate_matches_for_item(self, item_id: str) -> None:
        self._conn.execute(
            "DELETE FROM duplicate_matches WHERE item_id_a = ? OR item_id_b = ?", (item_id, item_id)
        )
        self._conn.commit()

    def clear_duplicate_matches(self) -> None:
        self._conn.execute("DELETE FROM duplicate_matches")
        self._conn.commit()

    # ------------------------------------------------------------ dup samples
    def save_dup_sample(self, item_id: str, s: dict) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO dup_samples
              (item_id, algo_version, source_size, source_mtime, duration, num_samples,
               frames, valid, quick_hash, audio_status, audio_fp, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (item_id, s["algo_version"], s["source_size"], s["source_mtime"], s["duration"],
             s["num_samples"], s["frames"], s["valid"], s["quick_hash"], s["audio_status"],
             s["audio_fp"], time.time()),
        )
        self._conn.commit()

    def list_dup_sample_stamps(self) -> dict[str, dict]:
        """id -> {algo_version, source_size, source_mtime, num_samples, audio_status}
        without loading the (large) blobs."""
        rows = self._conn.execute(
            "SELECT item_id, algo_version, source_size, source_mtime, num_samples, audio_status FROM dup_samples"
        ).fetchall()
        return {r["item_id"]: dict(r) for r in rows}

    def load_dup_samples(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM dup_samples").fetchall()]

    def delete_dup_samples_for_item(self, item_id: str) -> None:
        self._conn.execute("DELETE FROM dup_samples WHERE item_id = ?", (item_id,))
        self._conn.commit()

    # ------------------------------------------------------------- app state
    def get_app_state(self, key: str, default: Any = None) -> Any:
        row = self._conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return default

    def set_app_state(self, key: str, value: Any) -> None:
        self._conn.execute(
            """
            INSERT INTO app_state (key, value) VALUES (:key, :value)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            {"key": key, "value": json.dumps(value)},
        )
        self._conn.commit()

    def increment_counter(self, key: str, by: int = 1) -> int:
        """Cumulative Stats counters (e.g. "how many repairs have ever
        fixed something", "net bytes saved by every re-encode so far") --
        backed by the same app_state table as get_app_state()/
        set_app_state() above, just always an int. `by` can be negative
        (e.g. a re-encode that grew the file rather than shrinking it),
        so the running total is a genuine net sum, not just a count of
        events. Returns the new total."""
        current = self.get_app_state(key, 0) or 0
        new_value = current + by
        self.set_app_state(key, new_value)
        return new_value

    def get_counter(self, key: str) -> int:
        return self.get_app_state(key, 0) or 0

    # ------------------------------------------------------ daily stats (2.11.0)
    def bump_daily(self, day: str, metric: str, value: float = 1) -> None:
        self._conn.execute(
            "INSERT INTO stats_daily (day, metric, value) VALUES (?, ?, ?) "
            "ON CONFLICT(day, metric) DO UPDATE SET value = value + excluded.value", (day, metric, value))
        self._conn.commit()

    def daily_stats(self, since_day: str) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT day, metric, value FROM stats_daily WHERE day >= ? ORDER BY day", (since_day,))]

    def add_watch(self, day: str, user_id: str, item_id: str, seconds: float, plays: int) -> None:
        self._conn.execute(
            "INSERT INTO watch_log (day, user_id, item_id, seconds, plays) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(day, user_id, item_id) DO UPDATE SET seconds = seconds + excluded.seconds, "
            "plays = plays + excluded.plays", (day, user_id, item_id, seconds, plays))
        self._conn.commit()

    def watch_rows(self, since_day: str) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT day, user_id, item_id, seconds, plays FROM watch_log WHERE day >= ?", (since_day,))]

    def delete_user_watch_log(self, user_id: str) -> None:
        self._conn.execute("DELETE FROM watch_log WHERE user_id = ?", (user_id,))
        self._conn.commit()

    # ------------------------------------------------------ stats snapshots
    # ------------------------------------------------------------ notifications
    def add_notification(self, user_id: Optional[str], category: str, level: str, title: str,
                         body: Optional[str], link: Optional[str]) -> int:
        cur = self._conn.execute(
            "INSERT INTO notifications (created_at, user_id, category, level, title, body, link) VALUES (?,?,?,?,?,?,?)",
            (time.time(), user_id, category, level, title, body, link))
        self._conn.commit()
        return cur.lastrowid

    @staticmethod
    def _notif_where(user_id: str, admin: bool) -> tuple[str, list]:
        return ("(user_id = ? OR user_id IS NULL)", [user_id]) if admin else ("user_id = ?", [user_id])

    def list_notifications(self, user_id: str, admin: bool, after: int = 0, limit: int = 100,
                           problems_only: bool = False) -> list[dict]:
        where, args = self._notif_where(user_id, admin)
        sql = f"SELECT * FROM notifications WHERE {where} AND id > ?"
        args.append(after)
        if problems_only:
            sql += " AND level IN ('warning', 'error')"
        rows = self._conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [dict(r) | {"read": bool(r["read"])} for r in rows]

    def notification_counts(self, user_id: str, admin: bool) -> dict:
        where, args = self._notif_where(user_id, admin)
        row = self._conn.execute(
            f"SELECT MAX(id) AS last_id, SUM(CASE WHEN read = 0 AND level IN ('warning','error') THEN 1 ELSE 0 END) AS unread "
            f"FROM notifications WHERE {where}", args).fetchone()
        return {"last_id": row["last_id"] or 0, "unread": row["unread"] or 0}

    def mark_notifications_read(self, user_id: str, admin: bool, ids: Optional[list[int]] = None) -> None:
        where, args = self._notif_where(user_id, admin)
        sql = f"UPDATE notifications SET read = 1 WHERE {where}"
        if ids is not None:
            sql += f" AND id IN ({','.join('?' * len(ids))})"
            args += list(ids)
        self._conn.execute(sql, args)
        self._conn.commit()

    def clear_notifications(self, user_id: str, admin: bool) -> None:
        where, args = self._notif_where(user_id, admin)
        self._conn.execute(f"DELETE FROM notifications WHERE {where}", args)
        self._conn.commit()

    def prune_notifications(self, older_than: float, keep: int) -> None:
        self._conn.execute("DELETE FROM notifications WHERE created_at < ? OR id <= "
                           "(SELECT id FROM notifications ORDER BY id DESC LIMIT 1 OFFSET ?)", (older_than, keep))
        self._conn.commit()

    def create_stats_snapshot(self, data: dict) -> dict:
        row = {"id": str(uuid.uuid4()), "captured_at": time.time(), "data": json.dumps(data)}
        self._conn.execute(
            "INSERT INTO stats_snapshots (id, captured_at, data) VALUES (:id, :captured_at, :data)", row
        )
        self._conn.commit()
        return {"id": row["id"], "captured_at": row["captured_at"], **data}

    def list_stats_snapshots(self, since: Optional[float] = None, limit: int = 200) -> list[dict]:
        if since is not None:
            rows = self._conn.execute(
                "SELECT id, captured_at, data FROM stats_snapshots WHERE captured_at >= ? "
                "ORDER BY captured_at ASC LIMIT ?",
                (since, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, captured_at, data FROM stats_snapshots ORDER BY captured_at ASC LIMIT ?", (limit,)
            ).fetchall()
        return [{"id": r["id"], "captured_at": r["captured_at"], **json.loads(r["data"])} for r in rows]

    # ------------------------------------------------------- scheduled jobs
    def create_scheduled_job(self, job: dict) -> None:
        self._conn.execute(
            """
            INSERT INTO scheduled_jobs
                (id, job_type, name, enabled, schedule_mode, schedule_config, job_config,
                 created_at, updated_at, last_run_at, last_run_status, next_run_at)
            VALUES
                (:id, :job_type, :name, :enabled, :schedule_mode, :schedule_config, :job_config,
                 :created_at, :updated_at, :last_run_at, :last_run_status, :next_run_at)
            """,
            {**job, "enabled": 1 if job["enabled"] else 0},
        )
        self._conn.commit()

    def get_scheduled_job(self, job_id: str) -> Optional[dict]:
        row = self._conn.execute("SELECT * FROM scheduled_jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_scheduled_jobs(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM scheduled_jobs ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def update_scheduled_job(self, job_id: str, fields: dict) -> None:
        if not fields:
            return
        if "enabled" in fields:
            fields = {**fields, "enabled": 1 if fields["enabled"] else 0}
        set_clause = ", ".join(f"{k} = :{k}" for k in fields)
        self._conn.execute(f"UPDATE scheduled_jobs SET {set_clause} WHERE id = :id", {**fields, "id": job_id})
        self._conn.commit()

    def delete_scheduled_job(self, job_id: str) -> None:
        self._conn.execute("DELETE FROM scheduled_job_runs WHERE job_id = ?", (job_id,))
        self._conn.execute("DELETE FROM scheduled_jobs WHERE id = ?", (job_id,))
        self._conn.commit()

    def create_job_run(self, job_id: str, trigger: str) -> str:
        run_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO scheduled_job_runs (id, job_id, started_at, finished_at, status, trigger, result_summary) "
            "VALUES (?, ?, ?, NULL, 'running', ?, NULL)",
            (run_id, job_id, time.time(), trigger),
        )
        self._conn.commit()
        return run_id

    def finish_job_run(self, run_id: str, status: str, result_summary: Optional[str]) -> None:
        self._conn.execute(
            "UPDATE scheduled_job_runs SET finished_at = ?, status = ?, result_summary = ? WHERE id = ?",
            (time.time(), status, result_summary, run_id),
        )
        self._conn.commit()

    def list_job_runs(self, job_id: str, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM scheduled_job_runs WHERE job_id = ? ORDER BY started_at DESC LIMIT ?",
            (job_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------- roles
    def get_role(self, name: str) -> Optional[Role]:
        row = self._conn.execute("SELECT * FROM roles WHERE name = ?", (name,)).fetchone()
        return _role_from_row(row) if row else None

    def list_roles(self) -> list[Role]:
        rows = self._conn.execute("SELECT * FROM roles ORDER BY name").fetchall()
        return [_role_from_row(r) for r in rows]

    def delete_role(self, name: str) -> None:
        self._conn.execute("DELETE FROM roles WHERE name = ?", (name,))
        self._conn.commit()

    def role_user_count(self, name: str) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM users WHERE role = ?", (name,)).fetchone()[0]

    # ------------------------------------------------------------- groups (2.12.0)
    def list_groups(self) -> list[dict]:
        groups = [dict(r) for r in self._conn.execute("SELECT * FROM user_groups ORDER BY name COLLATE NOCASE")]
        members: dict[str, list[str]] = {}
        for r in self._conn.execute("SELECT group_id, user_id FROM user_group_members"):
            members.setdefault(r["group_id"], []).append(r["user_id"])
        for g in groups:
            g["permissions"] = json.loads(g["permissions"] or "[]")
            g["members"] = members.get(g["id"], [])
        return groups

    def save_group(self, group_id: str, name: str, description: str, permissions: list[str]) -> None:
        self._conn.execute(
            "INSERT INTO user_groups (id, name, description, permissions, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name = excluded.name, description = excluded.description, "
            "permissions = excluded.permissions", (group_id, name, description, json.dumps(permissions), time.time()))
        self._conn.commit()

    def delete_group(self, group_id: str) -> None:
        self._conn.execute("DELETE FROM user_group_members WHERE group_id = ?", (group_id,))
        self._conn.execute("DELETE FROM user_groups WHERE id = ?", (group_id,))
        self._conn.commit()

    def set_group_members(self, group_id: str, user_ids: list[str]) -> None:
        self._conn.execute("DELETE FROM user_group_members WHERE group_id = ?", (group_id,))
        self._conn.executemany("INSERT OR IGNORE INTO user_group_members (group_id, user_id) VALUES (?, ?)",
                               [(group_id, u) for u in user_ids])
        self._conn.commit()

    def set_user_groups(self, user_id: str, group_ids: list[str]) -> None:
        self._conn.execute("DELETE FROM user_group_members WHERE user_id = ?", (user_id,))
        self._conn.executemany("INSERT OR IGNORE INTO user_group_members (group_id, user_id) VALUES (?, ?)",
                               [(g, user_id) for g in group_ids])
        self._conn.commit()

    def get_user_extra_permissions(self, user_id: str) -> list[str]:
        row = self._conn.execute("SELECT extra_permissions FROM users WHERE id = ?", (user_id,)).fetchone()
        return json.loads(row["extra_permissions"] or "[]") if row else []

    def set_user_extra_permissions(self, user_id: str, permissions: list[str]) -> None:
        self._conn.execute("UPDATE users SET extra_permissions = ? WHERE id = ?", (json.dumps(permissions), user_id))
        self._conn.commit()

    def effective_permissions(self, user) -> list[str]:
        """Role + groups + the person's own grants (backend/permissions.py)."""
        from .permissions import expand
        role = self.get_role(user.role)
        perms = set(role.permissions if role else [])
        for r in self._conn.execute(
                "SELECT g.permissions FROM user_groups g JOIN user_group_members m ON m.group_id = g.id WHERE m.user_id = ?",
                (user.id,)):
            perms.update(json.loads(r["permissions"] or "[]"))
        perms.update(self.get_user_extra_permissions(user.id))
        return ["*"] if "*" in perms else expand(perms)

    def upsert_role(self, role: Role) -> None:
        """Creates a role, or replaces an existing one's permissions --
        not currently exposed via any API route (only the two seeded
        defaults exist today), but kept here ready for when role
        management is added, per the project's plan to add more user
        classes later."""
        self._conn.execute(
            """
            INSERT INTO roles (name, permissions, created_at)
            VALUES (:name, :permissions, :created_at)
            ON CONFLICT(name) DO UPDATE SET permissions=excluded.permissions
            """,
            {"name": role.name, "permissions": json.dumps(role.permissions), "created_at": role.created_at},
        )
        self._conn.commit()

    def ensure_role_permissions(self, name: str, required: list[str]) -> None:
        """Additive-only: makes sure role `name` includes every permission
        in `required`, without removing anything already there, and without
        touching a role that already has the wildcard "*" (nothing to add).
        Used for narrow, versioned migrations when a new permission string
        is introduced after roles have already been seeded on an existing
        install -- see the call site in __init__."""
        role = self.get_role(name)
        if role is None or "*" in role.permissions:
            return
        merged = list(dict.fromkeys([*role.permissions, *required]))  # de-dup, preserve order
        if merged != role.permissions:
            self._conn.execute(
                "UPDATE roles SET permissions = ? WHERE name = ?",
                (json.dumps(merged), name),
            )
            self._conn.commit()
            logger.info("Added permission(s) {} to role '{}'", required, name)

    # ------------------------------------------------------------- users
    def is_users_empty(self) -> bool:
        """Used at startup to decide whether to bootstrap the first admin
        account from APP_USERNAME/APP_PASSWORD in .env (see security.py) --
        True only on a genuinely fresh install/database."""
        return self._conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None

    def create_user(self, user: User) -> None:
        """Inserts a brand-new user. Raises sqlite3.IntegrityError if the
        username is already taken (the `users.username` UNIQUE constraint) --
        callers (the /api/users route, and the .env bootstrap path) should
        catch that and surface a clear "username already exists" error
        rather than letting a raw IntegrityError propagate."""
        self._conn.execute(_USER_UPSERT, _user_row_params(user))
        self._conn.commit()

    def get_user_by_id(self, user_id: str) -> Optional[User]:
        row = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user_from_row(row) if row else None

    def get_user_by_username(self, username: str) -> Optional[User]:
        row = self._conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return _user_from_row(row) if row else None

    def list_users(self) -> list[User]:
        rows = self._conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [_user_from_row(r) for r in rows]

    def count_users_by_role(self, role: str) -> int:
        """Used to enforce the single-master-admin invariant -- see
        main.py's create_user route, which rejects creating a second
        "admin"-role account if one already exists."""
        row = self._conn.execute("SELECT COUNT(*) as c FROM users WHERE role = ?", (role,)).fetchone()
        return row["c"] if row else 0

    def update_user(self, user: User) -> None:
        """Full upsert of an existing user row -- callers mutate the User
        object's fields (role/display_name/is_active/password_hash/etc),
        call user.touch() to bump updated_at, then pass it here. Does NOT
        bump token_version itself -- call bump_token_version() explicitly
        for changes that should invalidate existing sessions (role,
        active-status, password), so a purely cosmetic change (e.g.
        display_name) doesn't force a re-login."""
        self._conn.execute(_USER_UPSERT, _user_row_params(user))
        self._conn.commit()

    def delete_user(self, user_id: str) -> None:
        self._conn.execute("DELETE FROM playlist_items WHERE playlist_id IN (SELECT id FROM playlists WHERE owner_id = ?)", (user_id,))
        self._conn.execute("DELETE FROM watch_log WHERE user_id = ?", (user_id,))
        self._conn.execute("DELETE FROM user_group_members WHERE user_id = ?", (user_id,))
        self._conn.execute("DELETE FROM playlists WHERE owner_id = ?", (user_id,))
        self._conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self._conn.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))
        self._conn.execute("DELETE FROM discover_history WHERE user_id = ?", (user_id,))
        self._conn.commit()

    def bump_token_version(self, user_id: str) -> int:
        """Increments and returns this user's token_version. Call this any
        time a change should invalidate that user's existing session
        cookie(s) -- role change, deactivation/reactivation, or a password
        change/reset (self-service or admin-initiated). See the module
        docstring for how this powers session invalidation without a DB
        hit on every ordinary request: the session layer (security.py)
        keeps an in-memory cache of the current token_version per user and
        compares it against the value embedded in the cookie on each
        request, refreshing its cache from here whenever a mutation like
        this happens."""
        self._conn.execute(
            "UPDATE users SET token_version = token_version + 1, updated_at = ? WHERE id = ?",
            (time.time(), user_id),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT token_version FROM users WHERE id = ?", (user_id,)).fetchone()
        return row["token_version"] if row else 0

    def update_last_login(self, user_id: str, when: Optional[float] = None) -> None:
        self._conn.execute(
            "UPDATE users SET last_login_at = ? WHERE id = ?", (when if when is not None else time.time(), user_id)
        )
        self._conn.commit()

    # --------------------------------------------------------- user settings
    def get_user_settings(self, user_id: str) -> dict[str, Any]:
        """Every setting currently saved for this user, decoded from JSON,
        as a plain {key: value} dict -- e.g. {"page_size": 50, "theme":
        "midnight"}. Returns an empty dict (not an error) for a user with
        no settings saved yet, so callers can safely do
        `settings.get("page_size", DEFAULT)` without a None-check."""
        rows = self._conn.execute("SELECT key, value FROM user_settings WHERE user_id = ?", (user_id,)).fetchall()
        out: dict[str, Any] = {}
        for row in rows:
            try:
                out[row["key"]] = json.loads(row["value"])
            except Exception:
                logger.warning("Skipping unreadable user_setting {}={} for user {}", row["key"], row["value"], user_id)
        return out

    def set_user_setting(self, user_id: str, key: str, value: Any) -> None:
        """Stores one setting (JSON-encoded, so any JSON-serializable value
        -- string/number/bool/object -- works without a new column)."""
        self._conn.execute(
            """
            INSERT INTO user_settings (user_id, key, value)
            VALUES (:user_id, :key, :value)
            ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value
            """,
            {"user_id": user_id, "key": key, "value": json.dumps(value)},
        )
        self._conn.commit()

    def set_user_settings(self, user_id: str, settings: dict[str, Any]) -> None:
        """Bulk version of set_user_setting() -- upserts several keys in one
        transaction, used by the PATCH /api/me/settings route so saving
        several preferences at once (e.g. page size + theme together)
        isn't N separate commits."""
        if not settings:
            return
        rows = [{"user_id": user_id, "key": k, "value": json.dumps(v)} for k, v in settings.items()]
        self._conn.executemany(
            """
            INSERT INTO user_settings (user_id, key, value)
            VALUES (:user_id, :key, :value)
            ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value
            """,
            rows,
        )
        self._conn.commit()

    def delete_user_setting(self, user_id: str, key: str) -> None:
        self._conn.execute("DELETE FROM user_settings WHERE user_id = ? AND key = ?", (user_id, key))
        self._conn.commit()

    # ------------------------------------------------------ discover history
    def list_discover_history(self, user_id: str) -> list[str]:
        """Item ids this user was shown by Discover, newest first."""
        rows = self._conn.execute(
            "SELECT item_id FROM discover_history WHERE user_id = ? ORDER BY seen_at DESC", (user_id,)
        ).fetchall()
        return [r["item_id"] for r in rows]

    def record_discover(self, user_id: str, item_id: str) -> None:
        self._conn.execute(
            "INSERT INTO discover_history (user_id, item_id, seen_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, item_id) DO UPDATE SET seen_at = excluded.seen_at",
            (user_id, item_id, time.time()),
        )
        self._conn.commit()

    def reset_discover_history(self, user_id: str, keep_item_ids: list[str]) -> None:
        """Forgets this user's Discover history except `keep_item_ids`."""
        keep = set(keep_item_ids)
        drop = [i for i in self.list_discover_history(user_id) if i not in keep]
        self._conn.executemany(
            "DELETE FROM discover_history WHERE user_id = ? AND item_id = ?", [(user_id, i) for i in drop]
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _ignored_issue_key(issue_type: str, item_ids: list[str]) -> str:
    """Deterministic id for one ignored-issue row -- the same issue_type +
    same set of item ids (regardless of order) always maps to the same
    key, so ignoring an already-ignored issue again is a harmless upsert
    rather than creating a duplicate row."""
    return f"{issue_type}:" + ":".join(sorted(item_ids))


def _row_params(item: LibraryItem) -> dict:
    return {
        "id": item.id,
        "status": item.status,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "download_datetime": item.download_datetime,
        "data": json.dumps(item.model_dump(context={"raw": True}), ensure_ascii=False),  # as the source wrote them (spellings.py)
    }


def _role_from_row(row: sqlite3.Row) -> Role:
    return Role(
        name=row["name"],
        permissions=json.loads(row["permissions"]),
        created_at=row["created_at"],
    )


def _user_from_row(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        password_hash=row["password_hash"],
        role=row["role"],
        display_name=row["display_name"] or "",
        is_active=bool(row["is_active"]),
        token_version=row["token_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_login_at=row["last_login_at"],
    )


def _user_row_params(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "password_hash": user.password_hash,
        "role": user.role,
        "display_name": user.display_name,
        "is_active": 1 if user.is_active else 0,
        "token_version": user.token_version,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "last_login_at": user.last_login_at,
    }
