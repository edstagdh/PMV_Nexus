"""What the installers call (2.26.0): the Windows Setup.exe (installer/windows) and
install.sh / install.bat. Every decision lives here, in Python, so all installers
behave the same; the Windows installer only shows pages and reads/writes INI files
(Inno Setup reads INI natively).

    python -m Tools.setup_core inspect --ini OUT [--zip BACKUP] [--password-file F] [--verify-only] [--progress P]
        Folders of this install (or, with --zip, of the backup's settings, after staging the
        restore -- decrypting it with the password in F, which is then deleted).
        OUT: [result] ok, error, password_needed, wrong_password, app_version, encrypted
             [folders] <key>=<folder shown>, <key>_status, <key>_ok, <key>_label    (keys: see FOLDERS)
             [server]  port
    python -m Tools.setup_core apply --ini IN_OUT
        IN:  [folders] <key>=<folder>  (only the ones to set; the database is given as its folder)
             [server]  port=<n>
        Checks every folder first (nothing changes if one can't be used), then applies a
        staged restore (files of changed folders go to the new ones) or writes config.yaml.
        Writes [result] ok, error back into the same file.
    python -m Tools.setup_core cancel-restore
    python -m Tools.setup_core admin --ini IN_OUT --password-file F [--check-only]
        (2.29.0) IN: [admin] username. Creates the first admin account -- so a Windows install
        needs no setup code -- after `apply` wrote the settings, if no account exists yet.
        --check-only: only checks the username and password against the app's rules.

--verify-only (2.26.2): only checks that the backup can be used and the password opens it
(quick), without unpacking it. --progress P: writes "what it's doing" to P as it goes, which
the installer shows (unpacking / restoring thousands of files can take minutes).
"""
from __future__ import annotations

import argparse
import configparser
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import backup, folders  # noqa: E402


def _ini() -> configparser.ConfigParser:
    ini = configparser.ConfigParser(interpolation=None)
    ini.optionxform = str  # keep key case
    return ini


def _write(ini: configparser.ConfigParser, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        ini.write(f)


def _folders_section(ini, plan) -> None:
    ini["folders"] = {}
    for section, key, label, _d, is_file in folders.FOLDERS:
        value = plan.values[(section, key)]
        text, ok = folders.status(value, is_file)
        shown = folders.shown(value)
        if is_file and not folders.other_os(value):
            shown = str(Path(shown).parent)  # the installer picks the database's folder
        ini["folders"][key] = shown
        ini["folders"][f"{key}_status"] = text
        ini["folders"][f"{key}_ok"] = "1" if ok else "0"
        ini["folders"][f"{key}_label"] = label


def _section_get(config: dict, section: str, key: str):
    value = (config or {}).get(section)
    return value.get(key) if isinstance(value, dict) else None


def _read_password(path: str | None) -> str | None:
    if not path:
        return None
    p = Path(path)
    try:
        return p.read_text(encoding="utf-8").strip("\r\n") or None
    finally:
        p.unlink(missing_ok=True)


def _progress_writer(path: str | None, verb: str):
    """progress(n, total) -> "<verb> 1,200 of 3,400 files" in `path`, at most twice a second."""
    if not path:
        return None
    last = [0.0]

    def write(n: int, total: int) -> None:
        now = time.monotonic()
        if n == total or now - last[0] >= 0.5:
            last[0] = now
            try:
                Path(path).write_text(f"{verb} {n:,} of {total:,} files", encoding="utf-8")
            except OSError:
                pass
    return write


def verify(out: Path, zip_path: str, password_file: str | None) -> int:
    """Quick check: a backup this app can restore, and the password (if any) opens it."""
    ini = _ini()
    result = {"ok": "1", "error": "", "password_needed": "0", "wrong_password": "0", "app_version": "", "encrypted": "0"}
    password = _read_password(password_file)
    try:
        path = Path(zip_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Not found: {path}")
        summary = backup._summary(path)
        result["app_version"] = str(summary.get("app_version") or "")
        result["encrypted"] = "1" if summary.get("encrypted") else "0"
        problem = backup.version_problem(summary.get("app_version"))
        if problem:
            raise ValueError(problem)
        if not summary["valid"]:
            raise ValueError("This zip isn't a PMV Nexus backup (it has no manifest)")
        if summary["encrypted"]:
            zf, _manifest = backup._unlock(path, password)
            zf.close()
    except backup.PasswordNeeded as exc:
        result.update(ok="0", error=str(exc), password_needed="1", wrong_password="1" if exc.wrong else "0")
    except (OSError, ValueError) as exc:
        result.update(ok="0", error=str(exc))
    ini["result"] = result
    _write(ini, out)
    return 0 if result["ok"] == "1" else 1


def inspect(out: Path, zip_path: str | None, password_file: str | None, progress_file: str | None = None) -> int:
    ini = _ini()
    result = {"ok": "1", "error": "", "password_needed": "0", "wrong_password": "0",
              "app_version": "", "encrypted": "0"}
    password = _read_password(password_file)
    plan = None
    if zip_path:
        try:
            summary = backup.stage_restore_file(Path(zip_path), password,
                                                progress=_progress_writer(progress_file, "Unpacking the backup:"))
            result["app_version"] = str(summary.get("app_version") or "")
            result["encrypted"] = "1" if summary.get("encrypted") else "0"
            pending = backup.get_pending_restore() or {}
            plan = backup.restore_plan(Path(pending.get("zip", "")))
            if plan is None:
                raise ValueError("The backup's settings can't be read")
        except backup.PasswordNeeded as exc:
            result.update(ok="0", error=str(exc), password_needed="1", wrong_password="1" if exc.wrong else "0")
        except (OSError, ValueError) as exc:
            result.update(ok="0", error=str(exc))
    else:
        plan = folders.plan_from(*folders.current_settings())
    ini["result"] = result
    if plan:
        _folders_section(ini, plan)
        port = _section_get(plan.config, "server", "port") or 8676
        ini["server"] = {"port": str(port)}
    _write(ini, out)
    return 0 if result["ok"] == "1" else 1


USERSCRIPT = ROOT / "userscript" / "pmv-nexus.user.js"


def set_userscript_port(port: int) -> None:
    """The userscript's default address gets the chosen port (2.26.2; Windows installer).
    It's only the default offered the first time -- an installed userscript keeps its own,
    changed from its menu: Change Download Manager Address / API Key."""
    import re
    try:
        text = USERSCRIPT.read_text(encoding="utf-8")
    except OSError:
        return
    new = re.sub(r'(const DEFAULT_BASE_URL = "http://localhost:)\d+(")', rf"\g<1>{port}\g<2>", text, count=1)
    if new != text:
        USERSCRIPT.write_text(new, encoding="utf-8")


def apply(path: Path, progress_file: str | None = None) -> int:
    ini = _ini()
    ini.read(path, encoding="utf-8")
    wanted = dict(ini["folders"]) if ini.has_section("folders") else {}
    port = ini.get("server", "port", fallback="").strip()
    pending = backup.get_pending_restore()
    plan = backup.restore_plan(Path(pending.get("zip", ""))) if pending else None
    if pending and plan is None:
        return _result(ini, path, "The staged backup can't be read any more -- choose it again.")
    plan = plan or folders.plan_from(*folders.current_settings())

    problems = []
    for section, key, label, _d, is_file in folders.FOLDERS:
        new = (wanted.get(key) or "").strip()
        if new:
            if is_file:  # a folder was picked: keep the database's file name
                new = str(Path(new) / Path(plan.values[(section, key)].replace("\\", "/")).name)
            old = plan.values[(section, key)]
            same = new == old or (not folders.other_os(old) and not folders.other_os(new)
                                  and folders.resolve(new) == folders.resolve(old))
            if not same:
                plan.values[(section, key)] = new
                plan.changed.add((section, key))
        text, ok = folders.status(plan.values[(section, key)], is_file)
        if not ok:
            problems.append(f"{label}: {folders.shown(plan.values[(section, key)])} -- {text}")
    if port and (not port.isdigit() or not 1 <= int(port) <= 65535):
        problems.append("The port must be a number from 1 to 65535")
    if problems:
        return _result(ini, path, "\n".join(problems))

    if pending:
        if not backup.apply_pending_restore(log=lambda *_: None, relocate=plan.relocate(),
                                            progress=_progress_writer(progress_file, "Restoring:")):
            return _result(ini, path, "The backup couldn't be restored (it's no longer there).")
        folders.queue_relink(plan)
    folders.save(plan)
    if port:
        if not folders.CONFIG.is_file():
            folders.CONFIG.write_text(folders.TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
        text = folders.CONFIG.read_text(encoding="utf-8")
        folders.CONFIG.write_text(folders.set_yaml_value(text, "server", "port", int(port)), encoding="utf-8")
        set_userscript_port(int(port))
    if not folders.CONFIG.is_file():
        folders.CONFIG.write_text(folders.TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    return _result(ini, path, "")


def admin(path: Path, password_file: str | None, check_only: bool) -> int:
    ini = _ini()
    ini.read(path, encoding="utf-8")
    username = ini.get("admin", "username", fallback="").strip()
    password = _read_password(password_file) or ""
    from backend.security import validate_password_policy
    if not username:
        return _result(ini, path, "Enter a username")
    problem = validate_password_policy(password, username)
    if problem:
        return _result(ini, path, problem)
    if check_only:
        return _result(ini, path, "")
    import uuid

    from backend.config import get_settings
    from backend.db import LibraryDB
    from backend.models import User
    from backend.security import hash_password
    db = LibraryDB(get_settings().library_db_file)
    if not db.is_users_empty():
        return _result(ini, path, "")  # accounts exist (e.g. restored from a backup): nothing to do
    db.create_user(User(id=str(uuid.uuid4()), username=username, password_hash=hash_password(password),
                        role="admin", display_name="Administrator"))
    return _result(ini, path, "")


def _result(ini, path: Path, error: str) -> int:
    ini["result"] = {"ok": "0" if error else "1", "error": error.replace("\n", " | ")}
    _write(ini, path)
    return 1 if error else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inspect")
    p.add_argument("--ini", required=True)
    p.add_argument("--zip")
    p.add_argument("--password-file")
    p.add_argument("--verify-only", action="store_true")
    p.add_argument("--progress")
    p = sub.add_parser("apply")
    p.add_argument("--ini", required=True)
    p.add_argument("--progress")
    sub.add_parser("cancel-restore")
    p = sub.add_parser("admin")
    p.add_argument("--ini", required=True)
    p.add_argument("--password-file")
    p.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if args.cmd == "inspect":
        if args.verify_only:
            return verify(Path(args.ini), args.zip or "", args.password_file)
        return inspect(Path(args.ini), args.zip, args.password_file, args.progress)
    if args.cmd == "apply":
        return apply(Path(args.ini), args.progress)
    if args.cmd == "admin":
        return admin(Path(args.ini), args.password_file, args.check_only)
    backup.cancel_pending_restore()
    return 0


if __name__ == "__main__":
    sys.exit(main())
