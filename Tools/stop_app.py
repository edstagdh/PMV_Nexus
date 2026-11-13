"""Closes a running PMV Nexus before Windows Setup updates it (2.33.0).

Setup runs this file from its own temporary folder with the installed app's
Python, so it only uses the standard library (and psutil when it's there):

    python stop_app.py --root "C:\\Program Files\\PMV Nexus" --check   -> exit 3 if it's running, else 0
    python stop_app.py --root "C:\\Program Files\\PMV Nexus"           -> closes it: exit 0 when nothing's left

"Running" = any process whose program or working folder is the app folder: the
app itself (tray or console window -- on Windows the .venv's python.exe only
starts the real Python, which runs in the app folder), the Windows service
wrapper, FFmpeg encodes. Closing asks the app first (POST
/api/local/stop with the token from runtime/control.json -- the same as the
tray's Exit: work in progress stops safely and continues at the next start),
waits for it, and only then ends what's left.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

try:
    import psutil
except ImportError:  # an old install without it: only the polite request is possible
    psutil = None


def _inside(path: str, root: str) -> bool:
    """`root` is normcased with a trailing separator."""
    try:
        return (os.path.normcase(os.path.abspath(path)) + os.sep).startswith(root)
    except (TypeError, ValueError):
        return False


# A console the app was started from has the app folder as its working folder too;
# it isn't the app and isn't closed (the app inside it is).
SHELLS = {"cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "conhost.exe", "openconsole.exe",
          "explorer.exe", "bash", "zsh", "sh", "fish"}


def _ours(proc, root: str) -> bool:
    if (proc.info.get("name") or "").lower() in SHELLS:
        return False
    try:
        if proc.info["exe"] and _inside(proc.info["exe"], root):
            return True
        return _inside(proc.cwd(), root)
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return False


def app_processes(root: Path) -> list:
    """The processes running a program from inside the app folder (not this one)."""
    if psutil is None:
        return []
    prefix = os.path.normcase(os.path.abspath(str(root))) + os.sep
    # This script and what started it: on Windows the .venv's python.exe is a small
    # launcher that starts the real Python as its child, so the launcher is our parent.
    me = {os.getpid()}
    try:
        me |= {p.pid for p in psutil.Process().parents()}
    except psutil.Error:
        me.add(os.getppid())
    found = []
    for proc in psutil.process_iter(["pid", "exe", "name"]):
        if proc.info["pid"] not in me and _ours(proc, prefix):
            found.append(proc)
    return found


def ask_to_close(root: Path) -> bool:
    """The polite way (backend/app_control.py). False when the app can't be asked."""
    try:
        info = json.loads((root / "runtime" / "control.json").read_text(encoding="utf-8"))
        req = urllib.request.Request(f"http://127.0.0.1:{int(info['port'])}/api/local/stop", data=b"",
                                     headers={"X-Control-Token": str(info["token"])}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except (OSError, ValueError, KeyError):
        return False


def close(root: Path, timeout: float) -> int:
    asked = ask_to_close(root)
    print("Asked PMV Nexus to close." if asked else "PMV Nexus couldn't be asked to close.", flush=True)
    deadline = time.time() + (timeout if asked else 3)
    while time.time() < deadline:
        if not app_processes(root):
            return 0
        time.sleep(1)
    left = app_processes(root)
    if left:
        print(f"Ending {len(left)} process(es) that are still running.", flush=True)
        for proc in left:
            try:
                proc.terminate()
            except psutil.Error:
                pass
        _, alive = psutil.wait_procs(left, timeout=10)
        for proc in alive:
            try:
                proc.kill()
            except psutil.Error:
                pass
        psutil.wait_procs(alive, timeout=5)
    if psutil is None:
        return 0 if asked else 2
    return 0 if not app_processes(root) else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", required=True, help="the app folder")
    parser.add_argument("--check", action="store_true", help="only say whether it runs (exit 3) or not (0)")
    parser.add_argument("--timeout", type=float, default=60, help="seconds to wait for it to close itself")
    args = parser.parse_args()
    root = Path(args.root)
    if args.check:
        running = bool(app_processes(root)) or (psutil is None and (root / "runtime" / "control.json").is_file())
        print("running" if running else "not running", flush=True)
        return 3 if running else 0
    return close(root, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
