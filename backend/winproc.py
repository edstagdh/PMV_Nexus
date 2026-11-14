"""
Windows: the console programs the app starts (FFprobe, FFmpeg, ClamAV...) open no window
of their own (3.2.0). Run without a console (tray, shortcuts, service) every such program
otherwise flashes a black window for a moment -- e.g. FFprobe checking a hover preview the
first time it's shown.

hide_console_windows() adds CREATE_NO_WINDOW to every child process started through
subprocess / asyncio, unless the caller chose how the window should behave itself (a new
console, or detached). Called once at start by run.py; does nothing elsewhere.
"""
from __future__ import annotations

import os
import subprocess

_done = False


def hide_console_windows() -> None:
    global _done
    if os.name != "nt" or _done:
        return
    no_window = subprocess.CREATE_NO_WINDOW
    own_choice = subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS
    original = subprocess.Popen.__init__

    def __init__(self, *args, **kwargs):
        flags = kwargs.get("creationflags", 0) or 0
        if not flags & own_choice:
            kwargs["creationflags"] = flags | no_window
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = __init__  # asyncio's subprocesses use subprocess.Popen too
    _done = True
