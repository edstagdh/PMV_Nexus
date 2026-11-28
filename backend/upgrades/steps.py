"""The upgrade steps, in order (3.12.0). Standard library only: the installers run this file with
the installed app's Python, before anything is replaced, to say what an update will do
(Tools/upgrade_plan.py). Their `run` may import the app's own modules: only the app runs them.

Rules for a step (never break them -- libraries are upgraded from any older version):
- its id never changes, and a step is never removed or rewritten once released;
- `needed(ctx)` is quick, and true only while there's something to do (a step that isn't
  needed is recorded as done without running);
- `run(ctx, progress)` can run twice, or after being cut off half-way, without harm, and
  deletes nothing before what replaces it is written and checked;
- `estimate(ctx)` counts the real library: {"what": text, "seconds": float, "bytes": int}.
- `blocking`: a long step -- the app shows "Upgrading" and can't be used until it's done.
  A quick one runs while the app starts, before anything reads the data.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


@dataclass
class Ctx:
    """Where the data is. `library_db` may not exist yet (a new install)."""
    data_dir: Path
    library_db: Path
    derived_db: Path

    @classmethod
    def for_data_dir(cls, data_dir: Path, library_db: Optional[Path] = None) -> "Ctx":
        library_db = library_db or data_dir / "library.db"
        return cls(data_dir=data_dir, library_db=library_db, derived_db=library_db.parent / "derived.db")


@dataclass
class Step:
    id: str
    since: str                 # the version that brought it
    title: str
    needed: Callable[[Ctx], bool]
    estimate: Callable[[Ctx], dict]
    run: Callable[[Ctx, Callable[[int, int], None]], str]
    blocking: bool = False
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ helpers (read-only)
def _ro(path: Path) -> Optional[sqlite3.Connection]:
    """Read-only connection, or None when the file isn't there. Never creates the file."""
    if not path.is_file():
        return None
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10)


def _tables(con: sqlite3.Connection, schema: str = "main") -> set[str]:
    return {r[0] for r in con.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table'")}


def _count(con: sqlite3.Connection, table: str, schema: str = "main") -> int:
    return int(con.execute(f"SELECT COUNT(*) FROM {schema}.{table}").fetchone()[0])


# ------------------------------------------------------------------ 3.12.0: derived.db
_SCENE_TABLES = ("scene_hashes", "scene_links")


def _derived_needed(ctx: Ctx) -> bool:
    con = _ro(ctx.library_db)
    if con is None:
        return False
    try:
        return bool(_tables(con) & set(_SCENE_TABLES))
    finally:
        con.close()


def _derived_estimate(ctx: Ctx) -> dict:
    con = _ro(ctx.library_db)
    if con is None:
        return {"what": "nothing to move", "seconds": 0.0, "bytes": 0}
    try:
        have = _tables(con)
        videos = _count(con, "scene_hashes") if "scene_hashes" in have else 0
        pairs = _count(con, "scene_links") if "scene_links" in have else 0
        size = int(con.execute("SELECT COALESCE(SUM(LENGTH(data)), 0) FROM scene_hashes").fetchone()[0]) if videos else 0
    finally:
        con.close()
    return {"what": f"shared-scene fingerprints of {videos} videos and {pairs} pairs of videos",
            "seconds": round(1 + size / 40e6 + (videos + pairs) / 20000, 1), "bytes": size}


def _derived_run(ctx: Ctx, progress: Callable[[int, int], None]) -> str:
    """Copies the shared-scene tables into derived.db, checks every row is there, then drops them
    from library.db. Cut off before the drop: the next start copies again (INSERT OR REPLACE)."""
    from ..derived_db import SCHEMA   # the app's own schema for derived.db

    dst = sqlite3.connect(str(ctx.derived_db), timeout=30)
    try:
        dst.execute("PRAGMA journal_mode=WAL")
        dst.executescript(SCHEMA)
        dst.commit()
    finally:
        dst.close()
    con = sqlite3.connect(str(ctx.library_db), timeout=30)
    try:
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("ATTACH DATABASE ? AS d", (str(ctx.derived_db),))
        have = _tables(con)
        moved = {}
        steps = [t for t in _SCENE_TABLES if t in have]
        for i, table in enumerate(steps):
            progress(i, len(steps))
            cols = [r[1] for r in con.execute(f"PRAGMA main.table_info({table})")]
            keep = [c for c in cols if c in [r[1] for r in con.execute(f"PRAGMA d.table_info({table})")]]
            col_list = ", ".join(keep)
            con.execute(f"INSERT OR REPLACE INTO d.{table} ({col_list}) SELECT {col_list} FROM main.{table}")
            con.commit()
            key = "video_id" if table == "scene_hashes" else "a, b"
            missing = con.execute(f"SELECT COUNT(*) FROM main.{table} m WHERE NOT EXISTS "
                                  f"(SELECT 1 FROM d.{table} x WHERE " +
                                  " AND ".join(f"x.{k.strip()} = m.{k.strip()}" for k in key.split(",")) + ")").fetchone()[0]
            if missing:
                raise RuntimeError(f"{missing} rows of {table} weren't copied to derived.db -- nothing was removed")
            moved[table] = _count(con, table)
        for table in steps:      # every table checked: only now remove them from library.db
            con.execute(f"DROP TABLE IF EXISTS main.{table}")
        con.execute("DROP INDEX IF EXISTS main.idx_scene_links_b")
        con.commit()
        con.execute("DETACH DATABASE d")
        progress(len(steps), len(steps))
    finally:
        con.close()
    return ", ".join(f"{n} rows of {t}" for t, n in moved.items()) or "nothing to move"


STEPS: list[Step] = [
    Step(
        id="derived_db", since="3.12.0",
        title="Move data the app can make again (shared-scene fingerprints) into a second database, data/derived.db",
        needed=_derived_needed, estimate=_derived_estimate, run=_derived_run,
        notes=["library.db gets smaller; nothing of yours moves (ratings, history, settings stay in library.db)"],
    ),
]
STEP_IDS = {s.id for s in STEPS}


# ------------------------------------------------------------------ what's pending
def done_ids(ctx: Ctx) -> set[str]:
    con = _ro(ctx.library_db)
    if con is None:
        return set()
    try:
        if "app_upgrades" not in _tables(con):
            return set()
        return {r[0] for r in con.execute("SELECT id FROM app_upgrades")}
    finally:
        con.close()


def plan(ctx: Ctx) -> dict:
    """{"new_install", "steps": [{id, since, title, blocking, estimate, notes}], "seconds", "unknown"}:
    the steps still to run on this data, and "unknown": steps a newer app ran (don't start this one)."""
    if not ctx.library_db.is_file():
        return {"new_install": True, "steps": [], "seconds": 0.0, "unknown": []}
    done = done_ids(ctx)
    out = []
    for step in STEPS:
        if step.id in done:
            continue
        try:
            if not step.needed(ctx):
                continue
            est = step.estimate(ctx)
        except sqlite3.Error as exc:   # a locked or damaged file: say so, don't guess
            est = {"what": f"couldn't be measured ({exc})", "seconds": 0.0, "bytes": 0}
        out.append({"id": step.id, "since": step.since, "title": step.title, "blocking": step.blocking,
                    "estimate": est, "notes": step.notes})
    return {"new_install": False, "steps": out, "seconds": round(sum(s["estimate"]["seconds"] for s in out), 1),
            "unknown": sorted(done - STEP_IDS)}


def describe(p: dict, from_version: Optional[str], to_version: str) -> str:
    """The plan as plain text (installers, the console)."""
    if p["new_install"]:
        return "A new install: nothing to upgrade."
    if p["unknown"]:
        return ("This library was upgraded by a newer version of PMV Nexus (" + ", ".join(p["unknown"]) +
                f"). Version {to_version} can't use it: install that newer version again, or restore a backup made "
                "before it.")
    if not p["steps"]:
        return f"Upgrading to {to_version}: nothing in your library needs to change."
    lines = [f"Upgrading {('from ' + from_version + ' ') if from_version else ''}to {to_version} will:"]
    for s in p["steps"]:
        e = s["estimate"]
        lines.append(f"  - {s['title']} ({e['what']}; about {_duration(e['seconds'])})")
        lines += [f"      {n}" for n in s["notes"]]
    long = [s for s in p["steps"] if s["blocking"]]
    lines.append(f"In all about {_duration(p['seconds'])}. A copy of the database is made first.")
    if long:
        lines.append("The app shows \"Upgrading\" and can't be used until that's done; leave it running.")
    else:
        lines.append("It happens while the app starts.")
    return "\n".join(lines)


def _duration(seconds: float) -> str:
    if seconds < 60:
        return "less than a minute"
    if seconds < 3600:
        return f"{round(seconds / 60)} minutes"
    return f"{seconds / 3600:.1f} hours"
