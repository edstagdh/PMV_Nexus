"""Upgrade check (3.12.0, .github/workflows/upgrades.yml): a library made by an OLDER version of
the app is opened by this one, and nothing may be lost.

    python Tools/upgrade_ci.py old <old app folder>   # run with the OLD code: makes data/ there
    python Tools/upgrade_ci.py new <new app folder>   # run with THIS code, on a copy of that data/

"old" writes videos, a playlist, settings values and (3.9.0+) shared-scene rows with the old
version's own code, and a summary (data/upgrade_ci.json). "new" starts this version on that data
(the upgrade steps run as they do at a real start) and compares. Exit code 0 = all kept.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

VIDEOS = 40


def _setup(root: Path):
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ.setdefault("PMV_NO_TRAY", "1")


def old(root: Path) -> int:
    _setup(root)
    from backend.config import get_settings
    from backend.db import LibraryDB
    from backend.models import LibraryItem
    s = get_settings()
    s.library_db_file.parent.mkdir(parents=True, exist_ok=True)
    db = LibraryDB(s.library_db_file)
    for i in range(VIDEOS):
        vid = f"{i:024x}"
        db.save_item(LibraryItem(id=vid, url=f"https://pmvhaven.com/video/{vid}", status="done", title=f"Video {i}",
                                 creator="Someone", performers=[f"Performer {i % 5}"], tags=["a", "b"]))
    db.set_app_state("upgrade_ci_marker", {"made_by": (root / "VERSION").read_text().strip()})
    scenes = 0
    if hasattr(db, "set_scene_hash"):   # 3.9.0 .. 3.11.x keep them in library.db
        for i in range(VIDEOS):
            db.set_scene_hash(f"{i:024x}", 1, 100.0 + i, bytes([i % 256]) * 2000)
        db.replace_scene_links([(f"{i:024x}", f"{i + 1:024x}", 3, 4, [[1, 2, 3, 4]]) for i in range(VIDEOS - 1)])
        scenes = VIDEOS
    summary = {"version": (root / "VERSION").read_text().strip(), "videos": VIDEOS, "scenes": scenes}
    (s.library_db_file.parent / "upgrade_ci.json").write_text(json.dumps(summary), encoding="utf-8")
    print("old library made:", summary)
    return 0


def new(root: Path) -> int:
    _setup(root)
    from backend.config import get_settings
    s = get_settings()
    want = json.loads((s.library_db_file.parent / "upgrade_ci.json").read_text(encoding="utf-8"))
    from backend.library_manager import library_manager as lm   # opens the library: quick upgrade steps run
    problems = []

    def check(ok: bool, what: str) -> None:
        print(("ok      " if ok else "FAILED  ") + what)
        if not ok:
            problems.append(what)

    done = [i for i in lm._items.values() if i.status == "done"]
    check(len(done) == want["videos"], f"{want['videos']} videos kept ({len(done)})")
    check(all(i.title and i.performers and i.tags for i in done), "their details kept")
    check((lm.db.get_app_state("upgrade_ci_marker") or {}).get("made_by") == want["version"], "saved state kept")
    if want["scenes"]:
        check(len(lm.derived.scene_hash_marks()) == want["scenes"], "shared-scene fingerprints moved to derived.db")
        check(len(lm.derived.scene_link_rows()) == want["scenes"] - 1, "shared-scene pairs moved to derived.db")
        conn = sqlite3.connect(str(s.library_db_file))
        left = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        check(not ({"scene_hashes", "scene_links"} & left), "and gone from library.db")
        check(any((s.library_db_file.parent / "backups" / "before-upgrade").glob("library-before-*.db")),
              "a copy of the old library was made first")
    from backend.upgrades import runner, steps
    ctx = runner.ctx_for(s.library_db_file)
    pending = steps.plan(ctx)["steps"]
    check(not pending, "no upgrade step left: " + ", ".join(p["id"] for p in pending))
    check(runner.state().get("error") in (None, ""), f"no step failed ({runner.state().get('error')})")
    print("upgrade from", want["version"], "->", (root / "VERSION").read_text().strip(), ":",
          "everything kept" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("old", "new"):
        print(__doc__)
        sys.exit(2)
    sys.exit((old if sys.argv[1] == "old" else new)(Path(sys.argv[2]).resolve()))
