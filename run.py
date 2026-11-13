"""Launcher for PMV Nexus.

Run from anywhere:  py run.py [--host 0.0.0.0] [--port 8676] [--restore <backup.zip>]

Host and port come from config.yaml's `server:` section (default 0.0.0.0:8676);
--host / --port override it for one run. --restore puts a backup back before
the app starts (a new install, or another machine) -- see README "Backups".

Why this file lives in the project root: uvicorn imports "backend.main:app",
which only works when the project root (the folder containing `backend/`) is
on sys.path. Running a script from inside backend/ puts backend/ itself on
sys.path instead, which raises "No module named 'backend'".
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_HOST, DEFAULT_PORT = "0.0.0.0", 8676


def configured_address() -> tuple[str, int]:
    """config.yaml -> server: {host, port} (2.25.0); the template's values on a first start."""
    import yaml

    for name in ("config.yaml", "config.example.yaml"):
        path = ROOT / name
        if not path.is_file():
            continue
        try:
            server = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("server") or {}
        except (OSError, yaml.YAMLError) as exc:
            print(f"Couldn't read {name} ({exc}); using {DEFAULT_HOST}:{DEFAULT_PORT}")
            return DEFAULT_HOST, DEFAULT_PORT
        host = str(server.get("host") or DEFAULT_HOST)
        try:
            port = int(server.get("port") or DEFAULT_PORT)
        except (TypeError, ValueError):
            port = 0
        if not 1 <= port <= 65535:
            print(f"config.yaml: server.port must be a number from 1 to 65535; using {DEFAULT_PORT}")
            port = DEFAULT_PORT
        return host, port
    return DEFAULT_HOST, DEFAULT_PORT


def stage_from_command_line(path: Path) -> dict:
    """--restore: stage the backup, asking for its password if it has one (2.25.0)."""
    import getpass

    from backend.backup import PasswordNeeded, stage_restore_file

    password = None
    for attempt in range(4):
        try:
            return stage_restore_file(path, password)
        except PasswordNeeded as exc:
            if attempt == 3 or not sys.stdin.isatty():
                print(f"[restore] Can't restore {path.name}: {exc}")
                sys.exit(1)
            if exc.wrong:
                print("[restore] Wrong password.")
            password = getpass.getpass(f"[restore] {path.name} is protected with a password. Password (not shown): ")
        except (OSError, ValueError) as exc:
            print(f"[restore] Can't restore {path}: {exc}")
            sys.exit(1)
    sys.exit(1)


def restore_with_folder_check() -> None:
    """Applies a staged restore (if any) after checking the folders the backup's
    settings point at -- another computer has other drives (2.25.1, backend/folders.py).
    Then, on every start, makes sure the folders in the settings can be reached."""
    from backend import folders
    from backend.backup import apply_pending_restore, get_pending_restore, restore_plan

    pending = get_pending_restore()
    plan = restore_plan(Path(pending.get("zip", ""))) if pending else None
    if plan:
        if folders.interactive():
            folders.ask(plan, heading="[restore] Folders from the backup's settings -- check them for this computer "
                                      "(relative places are inside the app folder):")
        elif not all_reachable(plan, "[restore] "):
            print("[restore] The restore waits until these are set: start run.py (or start.bat) in a console window.")
            sys.exit(1)
    if apply_pending_restore(relocate=plan.relocate() if plan else None) and plan:
        folders.save(plan)
        folders.queue_relink(plan)
        if plan.changed:
            print("[restore] New folders saved in config.yaml.")

    current = folders.plan_from(*folders.current_settings())
    if not all_reachable(current, "", quiet=True):
        if not folders.interactive():
            all_reachable(current, "")
            print("Set them in config.yaml, or start run.py (or start.bat) in a console window to choose them.")
            sys.exit(1)
        folders.ask(current, heading="Some folders in the settings can't be reached (another drive, or not plugged in?):")
        folders.save(current)
        folders.queue_relink(current)


def all_reachable(plan, prefix: str, quiet: bool = False) -> bool:
    from backend import folders

    ok = True
    for section, key, label, _d, is_file in folders.FOLDERS:
        text, usable = folders.status(plan.values[(section, key)], is_file)
        if not usable:
            ok = False
            if not quiet:
                print(f"{prefix}{label}: {folders.shown(plan.values[(section, key)])} -- {text}")
    return ok


def use_bundled_tools() -> None:
    """FFmpeg as the Windows installer puts it in runtime/ (2.26.0), when it's
    there; otherwise the one on PATH."""
    ffmpeg_bin = ROOT / "runtime" / "ffmpeg" / "bin"
    if ffmpeg_bin.is_dir():
        os.environ["PATH"] = str(ffmpeg_bin) + os.pathsep + os.environ.get("PATH", "")


def open_browser_when_up(port: int) -> None:
    """--open-browser (Start-menu shortcut): opens http://localhost:<port> once the app answers,
    in tray.browser when one is set."""
    import socket
    import threading
    import time

    from backend.config import get_settings
    from backend.tray import open_url

    def wait_and_open() -> None:
        for _ in range(240):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    open_url(f"http://localhost:{port}/", get_settings().tray.browser)
                    return
            except OSError:
                time.sleep(0.5)

    threading.Thread(target=wait_and_open, daemon=True).start()


def quiet_streams() -> None:
    """pythonw.exe (the tray) has no console: give prints and the log's console
    output somewhere harmless to go. The log files in data/logs are unaffected."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def already_running(port: int) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def tray_folder_check() -> bool:
    """The tray can't ask questions: when a folder (or a staged restore's folder) can't
    be reached, say so in a message box and offer the console, where run.py asks."""
    import contextlib
    import io

    from backend.tray import message_box

    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            restore_with_folder_check()
        return True
    except SystemExit:
        text = "\n".join(line for line in out.getvalue().splitlines() if line.strip())[-1500:]
        if message_box("PMV Nexus can't start yet:\n\n" + text +
                       "\n\nOpen a console window to choose the folders now?", yes_no=True):
            if os.name == "nt":
                import subprocess
                subprocess.Popen(["cmd", "/c", str(ROOT / "start.bat")], cwd=str(ROOT),
                                 creationflags=subprocess.CREATE_NEW_CONSOLE)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Start PMV Nexus")
    parser.add_argument("--host", help="address to listen on (default: config.yaml server.host, else 0.0.0.0)")
    parser.add_argument("--port", type=int, help="port (default: config.yaml server.port, else 8676)")
    parser.add_argument("--restore", metavar="BACKUP_ZIP",
                        help="put this backup back before starting (new install or another machine)")
    parser.add_argument("--open-browser", action="store_true", help="open the app in the browser once it's up")
    parser.add_argument("--tray", action="store_true",
                        help="no console: run with a tray icon (Windows shortcuts use this; 2.29.0)")
    parser.add_argument("--no-start", action="store_true",
                        help="only restore (--restore) and check the folders, then exit (installers)")
    args = parser.parse_args()
    restore = Path(args.restore).expanduser().resolve() if args.restore else None  # relative to where it was typed

    if args.tray:
        quiet_streams()
    # Relative paths in config.yaml (./data, ./downloads) resolve against cwd.
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    use_bundled_tools()

    # A restore staged from the Backups page is applied here, before the app
    # (and its database) is loaded -- see backend/backup.py.
    from backend.backup import apply_pending_restore

    if restore:
        summary = stage_from_command_line(restore)
        print(f"[restore] Restoring {summary.get('name') or restore.name} (made by version {summary.get('app_version') or '?'}). "
              "Files it replaces are kept as <name>.pre-restore.")
    if args.tray:
        if not tray_folder_check():
            return
    else:
        restore_with_folder_check()
    if args.no_start:
        print("Done. Start the app with run.py (or start.bat).")
        return

    # After a restore: the restored config.yaml may set another port.
    host, port = configured_address()
    host, port = args.host or host, args.port or port
    print(f"PMV Nexus: http://{'localhost' if host in ('0.0.0.0', '::') else host}:{port}")

    if args.tray and already_running(port):
        from backend.tray import open_url
        from backend.config import get_settings
        open_url(f"http://localhost:{port}/", get_settings().tray.browser)  # the shortcut, clicked again
        return
    if args.open_browser and not args.tray:
        open_browser_when_up(port)

    import uvicorn

    class Server(uvicorn.Server):
        def handle_exit(self, sig, frame):
            # Live-update streams (/api/events, one per open browser tab)
            # never finish on their own; tell them to end so closing the app
            # doesn't wait on them.
            from backend.routers.events import stop_streams

            stop_streams()
            super().handle_exit(sig, frame)

    config = uvicorn.Config("backend.main:app", host=host, port=port, log_config=None,
                            timeout_graceful_shutdown=5, server_header=False,  # don't announce the server software
                            h11_max_incomplete_event_size=64 * 1024)  # headers larger than this are refused
    if args.tray:
        from backend.tray import run as run_tray
        run_tray(Server(config), port, open_browser=args.open_browser)
        return
    try:
        Server(config).run()
    except KeyboardInterrupt:
        # uvicorn re-raises Ctrl+C after a clean shutdown (uvicorn.run() swallows
        # it the same way); without this, a normal stop ends with a traceback.
        pass


if __name__ == "__main__":
    main()
