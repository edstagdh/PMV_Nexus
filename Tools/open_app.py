"""The Start-menu / desktop shortcut and "Start now" of the Windows installer (2.26.0).

Opens the app in the browser. If it isn't running yet: as a Windows service it's
only waited for (the service starts it); otherwise (2.29.0) it's started with its
tray icon and no console window (run.py --tray), and the browser opens once it
answers -- unless tray.open_browser is off (2.31.0: Setup / Server settings ->
Opening the app), then it only starts. Run with pythonw.exe, so nothing here has a
window of its own.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICE_EXE = ROOT / "runtime" / "service" / "PMVNexusService.exe"


def answering(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from run import configured_address

    from backend.config import get_settings
    from backend.tray import open_url

    port = configured_address()[1]
    url = f"http://localhost:{port}/"
    tray = get_settings().tray
    if answering(port):  # already running: the shortcut opens it
        open_url(url, tray.browser)
        return 0
    if SERVICE_EXE.is_file():
        if not tray.open_browser:
            return 0  # the service starts it
        for _ in range(60):  # the service may still be starting
            if answering(port):
                open_url(url, tray.browser)
                return 0
            time.sleep(1)
        open_url(url, tray.browser)  # the browser shows "can't connect"; the service log says why
        return 1
    extra = ["--open-browser"] if tray.open_browser else []
    if os.name == "nt":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        subprocess.Popen([str(pythonw if pythonw.is_file() else sys.executable), str(ROOT / "run.py"), "--tray", *extra],
                         cwd=str(ROOT), creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        subprocess.Popen([sys.executable, str(ROOT / "run.py"), *extra], cwd=str(ROOT), start_new_session=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
