"""Helper for install.bat (2.25.0): creates config.yaml, asks for the folders, sets the port.

    python -m Tools.first_install --folders              # show / choose the folders (2.25.1)
    python -m Tools.first_install --port 8676            # config.yaml from the template, port set
    python -m Tools.first_install --check-backup <zip>   # exit 0 = a backup this app can restore

Only the lines it changes are touched in config.yaml (backend/folders.py
set_yaml_value); comments and everything else stay as they are.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.yaml"
TEMPLATE = ROOT / "config.example.yaml"


def ensure_config() -> bool:
    """True if config.yaml was just created from the template."""
    if CONFIG.exists():
        return False
    shutil.copyfile(TEMPLATE, CONFIG)
    return True


def set_port(port: int) -> None:
    from backend.folders import set_yaml_value
    CONFIG.write_text(set_yaml_value(CONFIG.read_text(encoding="utf-8"), "server", "port", port), encoding="utf-8")


def check_backup(path: Path) -> int:
    from backend.backup import _summary, version_problem

    if not path.is_file():
        print(f"Not found: {path}")
        return 1
    try:
        zipfile.ZipFile(path).close()
    except (zipfile.BadZipFile, OSError) as exc:
        print(f"Not a backup zip: {exc}")
        return 1
    info = _summary(path)
    problem = version_problem(info.get("app_version"))
    if problem or not info["valid"]:
        print(problem or "This zip isn't a PMV Nexus backup (it has no manifest).")
        return 1
    print(f"Backup made by version {info.get('app_version') or '?'}"
          + (" - protected with a password (you'll be asked for it)" if info.get("encrypted") else ""))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int)
    parser.add_argument("--check-backup", metavar="ZIP")
    parser.add_argument("--folders", action="store_true")
    args = parser.parse_args()
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if args.check_backup:
        return check_backup(Path(args.check_backup).expanduser().resolve())
    if ensure_config():
        print("Created config.yaml from config.example.yaml")
    if args.folders:
        from backend.folders import check_install
        try:
            check_install()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
    if args.port is not None:
        if not 1 <= args.port <= 65535:
            print("The port must be a number from 1 to 65535")
            return 1
        set_port(args.port)
        print(f"config.yaml: the app will use port {args.port}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
