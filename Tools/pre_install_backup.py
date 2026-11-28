"""A backup of the installed app's library before an update is installed (3.13.0).

    python pre_install_backup.py --root <app folder> --to 3.13.0 [--report out.txt] [--progress out.txt]
    python -m Tools.pre_install_backup                       # this folder (install.sh / install.bat)

Setup.exe runs it with the INSTALLED app's Python and code, after closing the app and before it
replaces any file; install.sh and install.bat run it after the packages are installed, before the
app starts. It uses the installed app's own backup code, so the backup is one the app lists, tests
and restores like any other (Management -> Backups): the database, the settings, the languages,
your own performer photos and the playlist covers -- what can't be made again. Thumbnails,
previews, frames and videos aren't in it (Repair/Update and the maintenance jobs make them again).
Encrypted with the backup password when one is set.

It's marked as made by the installer (job "installer", and data/installer_backups.json), so the
app deletes it by itself after Server Settings -> Backups -> "Backups made before installing:
keep for N days" (default 90). Switched off there: nothing is made.

Only reads the library. Exit code: 0 made (or nothing to back up: a new install), 2 switched
off, 1 failed (why in --report and on stderr).
"""
from __future__ import annotations

import argparse
import inspect
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
JOB = "installer"
RECORD = "installer_backups.json"


def _out(path, text: str) -> None:
    if path:
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError:
            pass


def _overrides(root: Path) -> dict:
    try:
        return json.loads((root / "data" / "settings_overrides.json").read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _enabled(settings, root: Path) -> bool:
    """Server Settings -> Backups (3.13.0). An installed version older than that has no such
    setting: then what the overrides file says, else on."""
    value = getattr(getattr(settings, "backup", None), "installer_backup", None)
    if value is None:
        value = (_overrides(root).get("backup") or {}).get("installer_backup", True)
    return bool(value)


def record(data_dir: Path, entry: dict) -> None:
    """data/installer_backups.json: what the installer made (the app prunes from it)."""
    path = data_dir / RECORD
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            rows = []
    except (OSError, ValueError):
        rows = []
    rows = [r for r in rows if isinstance(r, dict) and r.get("name") != entry["name"]] + [entry]
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    tmp.replace(path)


def _seen_version(db: Path):
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
        try:
            row = con.execute("SELECT value FROM app_state WHERE key = 'app_version_seen'").fetchone()
        finally:
            con.close()
        return json.loads(row[0]) if row else None
    except Exception:  # noqa: BLE001
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(HERE.parent), help="the installed app's folder (default: this one)")
    ap.add_argument("--to", default="", help="the version about to be installed")
    ap.add_argument("--report", help="write what happened here (UTF-8)")
    ap.add_argument("--progress", help="write the current step here, as it goes")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    if not (root / "config.yaml").is_file() or not (root / "backend").is_dir():
        _out(args.report, "Nothing to back up: a new install.")
        return 0
    sys.path.insert(0, str(root))
    try:
        from backend.config import get_settings     # the installed app's own code
        from backend import backup
        settings = get_settings()
        if not _enabled(settings, root):
            _out(args.report, "Switched off in Server Settings -> Backups: no backup made.")
            print("Backup before installing: switched off")
            return 2
        if not Path(settings.library_db_file).is_file():
            _out(args.report, "Nothing to back up: there's no library yet.")
            return 0
        # the version that last used this library (install.sh / install.bat run after the new
        # code is in place, so the code's own version can already be the new one)
        installed = _seen_version(Path(settings.library_db_file))
        if not installed:
            try:
                from backend.app_info import APP_VERSION as installed
            except Exception:  # noqa: BLE001
                installed = "?"

        def progress(stage, done=None, total=None, **_kw):
            _out(args.progress, f"Backup before installing: {stage}" + (f" ({done:,} of {total:,})" if done and total else ""))

        password = backup.saved_password() if hasattr(backup, "saved_password") else None
        wanted = {"include": set(), "trigger": "installer", "progress": progress, "password": password, "job": JOB,
                  "keep": 500,   # kept by days, not by count (the app deletes them after N days)
                  "origin": f"Setup / install script, before installing {args.to or 'an update'} over {installed}",
                  "notes": ["Made before an update was installed. Deleted by the app after the days set in "
                            "Server Settings -> Backups."]}
        accepted = inspect.signature(backup.create_backup).parameters
        kwargs = {k: v for k, v in wanted.items() if k in accepted}
        if "include" not in accepted:        # very old versions: database + settings only
            kwargs.update({k: False for k in ("include_cache", "include_secrets") if k in accepted})
        started = time.monotonic()
        made = backup.create_backup(settings, **kwargs)
        name = made["name"]
        _out(args.progress, "Backup before installing: checking it")
        checked = None
        if hasattr(backup, "verify_backup"):
            result = backup.verify_backup(settings, name, password=password) \
                if "password" in inspect.signature(backup.verify_backup).parameters else backup.verify_backup(settings, name)
            checked = bool(result.get("ok"))
            if not checked:
                bad = "; ".join(f"{c.get('name')}: {c.get('detail')}" for c in result.get("checks", []) if not c.get("ok"))
                raise RuntimeError(f"the backup {name} was made but didn't pass its check ({bad})")
        data_dir = Path(settings.library_db_file).parent
        record(data_dir, {"name": name, "dir": str(Path(settings.backup_dir).resolve()), "created_at": time.time(),
                          "from_version": installed, "to_version": args.to or None})
        size = made.get("size_bytes") or 0
        text = (f"Backup made before installing: {name} ({size / 1024 ** 2:,.1f} MB, {time.monotonic() - started:.0f} s"
                + (", checked" if checked else "") + f") in {settings.backup_dir}")
        _out(args.report, text)
        print(text)
        return 0
    except Exception as exc:  # noqa: BLE001 -- Setup shows this and asks
        text = f"{exc.__class__.__name__}: {exc}"
        _out(args.report, text)
        print(f"Backup before installing failed: {text}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
