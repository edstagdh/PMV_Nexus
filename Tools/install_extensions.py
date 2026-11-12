"""Installs the Python packages extensions need (2.30.0): every
extensions/<name>/requirements.txt, with this Python (run it with the app's .venv):

    python -m Tools.install_extensions

The Windows Setup runs it too. Nothing happens when there's no extensions folder.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    folder = ROOT / "extensions"
    files = sorted(folder.glob("*/requirements.txt")) if folder.is_dir() else []
    if not files:
        print("No extension requirements to install.")
        return 0
    failed = 0
    for req in files:
        print(f"Installing the packages of extension '{req.parent.name}'...")
        code = subprocess.call([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(req)])
        failed += code != 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
