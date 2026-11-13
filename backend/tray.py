"""The tray icon (2.29.0): `pythonw run.py --tray` runs the app without a console
window, with an icon in the notification area (Windows; works on Linux desktops
with a tray too).

- Click the icon (or "Open PMV Nexus"): opens the app in the browser -- the default
  one, or `tray.browser` from config.yaml (a program + arguments, e.g. a Firefox
  profile; {url} is replaced by the app's address).
- Hover: what it's doing ("2 downloads · 1 re-encode · 1 playing").
- Right-click menu: Open, Copy the setup code (only before the first account
  exists), Open the log folder, Open the app folder, Exit (asks first when work is
  running; then stops the way Ctrl+C does: running work is ended cleanly).

The app server runs in a thread of this same process, so the icon can read the
app's state directly. Everything the app logs still goes to data/logs.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

from loguru import logger

ROOT_DIR = Path(__file__).resolve().parent.parent
ICON_FILE = ROOT_DIR / "installer" / "app.ico"          # installed by Setup.exe
ICON_FALLBACK = ROOT_DIR / "installer" / "windows" / "app.ico"  # a git checkout


# ----------------------------------------------------------------- helpers
def message_box(text: str, title: str = "PMV Nexus", yes_no: bool = False) -> bool:
    """A plain Windows message box (there's no console to print to). True = Yes/OK."""
    if os.name == "nt":
        import ctypes
        flags = 0x00000004 | 0x00000020 if yes_no else 0x00000040  # YESNO+QUESTION / INFORMATION
        return ctypes.windll.user32.MessageBoxW(None, text, title, flags | 0x00010000) in (1, 6)  # topmost
    logger.warning("{}: {}", title, text)
    return not yes_no


def open_url(url: str, browser_command: str = "") -> None:
    """The default browser, or the configured one ("<program> [arguments]", {url} = the address)."""
    command = (browser_command or "").strip()
    if not command:
        webbrowser.open(url)
        return
    try:
        if os.name == "nt":  # Windows takes the command line as written (quotes and all)
            line = command.replace("{url}", url) if "{url}" in command else f'{command} "{url}"'
            subprocess.Popen(line, close_fds=True)
        else:
            args = shlex.split(command)
            args = [a.replace("{url}", url) for a in args] if any("{url}" in a for a in args) else [*args, url]
            subprocess.Popen(args, close_fds=True)
    except (OSError, ValueError) as exc:
        logger.warning("tray.browser ({}) didn't start: {} -- using the default browser", command, exc)
        webbrowser.open(url)


def open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(path))  # noqa: S606 -- a folder the app owns
    else:
        subprocess.Popen(["xdg-open", str(path)])


def copy_text(text: str) -> bool:
    if os.name == "nt":
        try:
            subprocess.run(["clip"], input=text.encode("utf-16-le"), check=True, creationflags=0x08000000)
            return True
        except (OSError, subprocess.SubprocessError):
            return False
    return False


def status_parts() -> list[str]:
    """What the app is doing now, read straight from the running app."""
    parts: list[str] = []
    try:
        from .library_manager import library_manager
        items = list(library_manager._items.values())
        downloads = sum(1 for i in items if i.status in ("fetching", "pending", "downloading"))
        encodes = sum(1 for i in items if i.reencode_status in ("queued", "encoding"))
        if downloads:
            parts.append(f"{downloads} download{'s' if downloads != 1 else ''}")
        if encodes:
            parts.append(f"{encodes} re-encode{'s' if encodes != 1 else ''}")
    except Exception:  # noqa: BLE001 -- a status line must never break the icon
        pass
    try:
        from . import activity
        tasks = activity.count()
        if tasks:
            parts.append(f"{tasks} task{'s' if tasks != 1 else ''}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from .now_playing import now_playing
        playing = now_playing.playing_count()
        if playing:
            parts.append(f"{playing} playing")
    except Exception:  # noqa: BLE001
        pass
    return parts


def setup_code() -> Optional[str]:
    try:
        from . import security
        return security._setup_code if security.setup_required() else None
    except Exception:  # noqa: BLE001
        return None


# ----------------------------------------------------------------- the icon
def run(server, port: int, open_browser: bool = False) -> None:
    """Runs `server` (a uvicorn.Server) in a thread and the tray icon here until Exit."""
    import pystray
    from PIL import Image

    from .config import get_settings

    url = f"http://localhost:{port}/"
    browser = lambda: get_settings().tray.browser  # noqa: E731 -- read each time: config.yaml may change
    thread = threading.Thread(target=server.run, name="app-server", daemon=True)
    stopping = threading.Event()

    def do_open(icon=None, item=None) -> None:
        open_url(url, browser())

    def do_copy_code(icon, item) -> None:
        code = setup_code()
        if code and copy_text(code):
            icon.notify(f"Setup code {code} copied. Paste it on the app's first-run page.", "PMV Nexus")
        elif code:
            message_box(f"The setup code is:\n\n{code}\n\nEnter it on the app's first-run page to create the admin account.")

    def do_exit(icon, item) -> None:
        busy = status_parts()
        if busy and not message_box("PMV Nexus is busy: " + ", ".join(busy) + ".\n\n"
                                    "Stop it anyway? Running downloads and re-encodes are stopped "
                                    "and continue the next time it starts.", yes_no=True):
            return
        stopping.set()
        icon.title = "PMV Nexus - stopping..."
        try:
            from .routers.events import stop_streams
            stop_streams()
        except Exception:  # noqa: BLE001
            pass
        server.should_exit = True
        thread.join(timeout=60)
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open PMV Nexus", do_open, default=True),
        pystray.MenuItem("Copy the setup code", do_copy_code, visible=lambda item: setup_code() is not None),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open the log folder", lambda i, it: open_folder(get_settings().log_dir)),
        pystray.MenuItem("Open the app folder", lambda i, it: open_folder(ROOT_DIR)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit PMV Nexus", do_exit),
    )
    icon_file = ICON_FILE if ICON_FILE.is_file() else ICON_FALLBACK
    image = Image.open(icon_file) if icon_file.is_file() else Image.new("RGB", (64, 64), (249, 115, 22))
    icon = pystray.Icon("PMVNexus", image, "PMV Nexus - starting...", menu)

    def watch(icon) -> None:
        icon.visible = True
        thread.start()
        while not getattr(server, "started", False) and thread.is_alive():
            time.sleep(0.2)
        if not thread.is_alive() and not stopping.is_set():
            icon.title = "PMV Nexus - couldn't start"
            message_box("PMV Nexus couldn't start (is the port in use by another program?).\n\n"
                        f"Details: {get_settings().log_dir}")
            icon.stop()
            return
        code = setup_code()
        if code:
            icon.notify(f"First start: setup code {code} (right-click the icon to copy it).", "PMV Nexus is running")
        else:
            icon.notify(f"Running at {url} -- click the icon to open it.", "PMV Nexus")
        if open_browser:
            do_open()
        last = None
        while not stopping.is_set():
            parts = status_parts()
            title = "PMV Nexus - " + (", ".join(parts) if parts else f"running at {url}")
            if title != last:
                icon.title = title[:127]  # Windows' limit for the hover text
                last = title
            if not thread.is_alive() and not stopping.is_set():
                icon.title = "PMV Nexus - stopped"
                message_box(f"PMV Nexus stopped unexpectedly. Details: {get_settings().log_dir}")
                icon.stop()
                return
            stopping.wait(3)

    icon.run(setup=watch)
