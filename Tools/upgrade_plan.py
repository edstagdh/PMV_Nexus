"""What updating this library to a new version will do, and about how long it takes (3.12.0).

    python -m Tools.upgrade_plan                       # this app folder, this version
    python upgrade_plan.py --root <app folder> --to 3.12.0 [--steps upgrade_steps.py] [--report out.txt] [--json]

The Windows Setup runs it before it replaces anything (with the installed app's Python and the
NEW version's steps.py extracted next to this file); install.sh runs it too.
It only reads: it never changes the library. Standard library only.

Exit code: 0 nothing to upgrade (or a new install), 3 there are upgrade steps (the text says
which), 4 the library was upgraded by a newer version (this one shouldn't run on it), 1 error.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_steps(path: Path):
    spec = importlib.util.spec_from_file_location("pmv_upgrade_steps", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod          # dataclasses look the module up while building the classes
    spec.loader.exec_module(mod)
    return mod


def _steps_file(arg: str | None) -> Path:
    # --steps; Setup extracts the new version's steps.py next to this file; else this app's own
    for cand in ([Path(arg)] if arg else []) + [HERE / "steps.py", HERE.parent / "backend" / "upgrades" / "steps.py"]:
        if cand.is_file():
            return cand
    raise SystemExit("upgrade_plan: the upgrade steps (steps.py) weren't found")


def _library_db(root: Path) -> Path:
    """Where this install keeps library.db: its settings when they can be read, else data/library.db."""
    try:
        sys.path.insert(0, str(root))
        from backend.config import get_settings  # the installed app's own settings
        return Path(get_settings().library_db_file)
    except Exception:  # noqa: BLE001 -- an old or broken install: the default place
        return root / "data" / "library.db"
    finally:
        if sys.path and sys.path[0] == str(root):
            sys.path.pop(0)


def _read_version(root: Path) -> str | None:
    try:
        return (root / "VERSION").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(HERE.parent), help="the app's folder (default: this one)")
    ap.add_argument("--to", help="the version being installed (default: this folder's VERSION)")
    ap.add_argument("--steps", help="the new version's steps.py")
    ap.add_argument("--report", help="also write the text here (UTF-8)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        steps = _load_steps(_steps_file(args.steps))
        db = _library_db(root)
        ctx = steps.Ctx.for_data_dir(db.parent, db)
        p = steps.plan(ctx)
        to = args.to or _read_version(HERE.parent) or "the new version"
        seen = _seen_version(db)
        installed = _read_version(root)
        from_version = seen or (installed if installed and installed != to else None)
        text = steps.describe(p, from_version, to)
    except Exception as exc:  # noqa: BLE001 -- the installer shows this and asks
        text = f"Couldn't check what the update changes ({exc.__class__.__name__}: {exc})."
        if args.report:
            Path(args.report).write_text(text, encoding="utf-8")
        print(text)
        return 1
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    print(json.dumps({**p, "text": text, "from": from_version, "to": to}, indent=2) if args.json else text)
    if p.get("unknown"):
        return 4
    return 3 if p["steps"] else 0


def _seen_version(db: Path) -> str | None:
    import sqlite3
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
        try:
            row = con.execute("SELECT value FROM app_state WHERE key = 'app_version_seen'").fetchone()
        finally:
            con.close()
        return json.loads(row[0]) if row else None
    except Exception:  # noqa: BLE001
        return None


if __name__ == "__main__":
    sys.exit(main())
