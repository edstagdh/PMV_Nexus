"""
Closing the running app from the same computer (2.33.0) -- used by Windows Setup
before an update (Tools/stop_app.py), so files aren't in use and the update
isn't left running the old version.

While the app runs, runtime/control.json holds {"pid", "port", "token"}: a random
token only readable by whoever can read the app folder. POST /api/local/stop with
the header X-Control-Token: <token>, from the computer itself, closes the app the
same way the tray's "Exit PMV Nexus" does (work in progress stops safely and is
picked up again at the next start). The file is removed when the app closes.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
from typing import Callable, Optional

from loguru import logger

from .config import ROOT_DIR

CONTROL_FILE = ROOT_DIR / "runtime" / "control.json"
_token: Optional[str] = None
_stop: Optional[Callable[[], None]] = None


def register(port: int, stop: Callable[[], None]) -> None:
    """run.py: how to close this app; writes the control file."""
    global _token, _stop
    _token, _stop = secrets.token_urlsafe(32), stop
    try:
        CONTROL_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONTROL_FILE.write_text(json.dumps({"pid": os.getpid(), "port": port, "token": _token}), encoding="utf-8")
    except OSError as exc:
        logger.warning("Couldn't write {} ({}): Setup will have to close the app the hard way", CONTROL_FILE, exc)


def unregister() -> None:
    """At the end: removes the control file when it's still this process's."""
    try:
        if json.loads(CONTROL_FILE.read_text(encoding="utf-8")).get("pid") == os.getpid():
            CONTROL_FILE.unlink()
    except (OSError, ValueError):
        pass


def check(token: str) -> bool:
    return bool(_token and token and hmac.compare_digest(token, _token))


def request_stop(reason: str) -> None:
    logger.info("Closing the app: {}", reason)
    if _stop:
        threading.Timer(0.5, _stop).start()  # after the answer has gone out
