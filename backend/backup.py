"""Backups: the library database plus settings, optionally the thumbnail/
preview cache and secrets (the encrypted credential store, its key and
extensions' saved sign-ins), packed into one zip under settings.backup_dir.

The database is copied with SQLite's online backup API, so a backup is
consistent even while the app keeps writing. Each zip carries a manifest.json
mapping every entry back to where it is restored. Restoring is staged: the
running app only writes data/restore_pending.json, and run.py applies it on the
next start, before the database is opened (see apply_pending_restore()).

Older backups (2.25.0): every backup since 1.2.0 can be restored into a newer
app. Nothing is converted in the zip: the app upgrades what it restored when
it starts -- the database gets the tables and columns added since (db.py
creates/adds them on every start), and settings files with old names are read
by the settings models (e.g. reencode.limit_cpu_usage). A backup made by a
NEWER version than the running app is refused: an older app can't know what
the newer one changed.

Password (2.25.0): while a backup password is saved (Backups page; kept in the
encrypted credential store), every new backup is a standard AES-256 encrypted
zip -- 7-Zip / WinRAR open it with that password. Only backup_info.json
(version, date, what's inside) stays readable without it, so the Backups page
can list it. Restoring an encrypted backup first writes a decrypted copy to
data/restore_staged.zip, which the next start applies and deletes.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

try:
    import pyzipper  # AES-encrypted zips (2.25.0)
except ImportError:  # in requirements.txt; without it, backups can't have a password
    pyzipper = None

ROOT_DIR = Path(__file__).resolve().parent.parent
PENDING_RESTORE_FILE = ROOT_DIR / "data" / "restore_pending.json"
# 2.28.0: new backups are pmvnexus_backup_…; the older pmvhaven_backup_… names stay valid.
BACKUP_NAME_RE = re.compile(r"^(pmvnexus|pmvhaven)_backup_\d{8}_\d{6}(_\d+)?\.zip$")
MANIFEST = "manifest.json"
INFO = "backup_info.json"  # the readable part of a password-protected backup
STAGED_FILE = ROOT_DIR / "data" / "restore_staged.zip"
MIN_PASSWORD = 8
_NEEDS_PYZIPPER = "Password-protected backups need the pyzipper package: pip install -r requirements.txt"


class PasswordNeeded(ValueError):
    """The backup is protected and the password is missing or wrong."""

    def __init__(self, wrong: bool = False):
        self.wrong = wrong
        super().__init__("Wrong password for this backup" if wrong else "This backup is protected with a password")


def saved_password() -> Optional[str]:
    """The backup password from the credential store, if one is saved. Never
    creates the store (run.py --restore runs this on a fresh install)."""
    from . import secrets_store
    if not (secrets_store.KEY_FILE.is_file() and secrets_store.DB_FILE.is_file()):
        return None
    try:
        return secrets_store.values().get("backup_password") or None
    except Exception:  # noqa: BLE001 -- an unreadable store just means "no saved password"
        return None


def set_password(password: Optional[str]) -> None:
    """Save (or with "" / None: remove) the password new backups are protected with."""
    password = password or ""
    if password and len(password) < MIN_PASSWORD:
        raise ValueError(f"Use at least {MIN_PASSWORD} characters")
    if password and pyzipper is None:
        raise ValueError(_NEEDS_PYZIPPER)
    from . import secrets_store
    secrets_store.write({"backup_password": password})


def _open(path: Path, password: Optional[str] = None):
    if pyzipper is None:
        return zipfile.ZipFile(path)
    zf = pyzipper.AESZipFile(path)
    if password:
        zf.setpassword(password.encode("utf-8"))
    return zf


def _read(zf, arc: str) -> bytes:
    try:
        return zf.read(arc)
    except NotImplementedError as exc:  # AES entry, opened without pyzipper
        raise ValueError(_NEEDS_PYZIPPER) from exc
    except RuntimeError as exc:
        text = str(exc).lower()
        if "password" in text or "encrypted" in text:
            raise PasswordNeeded(wrong="bad password" in text) from exc
        raise


def _unlock(path: Path, password: Optional[str] = None):
    """(open zip, manifest or None) -- opened with the password given, else the
    saved one; PasswordNeeded when neither opens it. Plain zips need none."""
    tries = [p for p in dict.fromkeys([password, saved_password()]) if p] or [None]
    for pw in tries:
        zf = _open(path, pw)
        try:
            return zf, json.loads(_read(zf, MANIFEST))
        except PasswordNeeded:
            zf.close()
        except (KeyError, json.JSONDecodeError):
            return zf, None
        except Exception:
            zf.close()
            raise
    raise PasswordNeeded(wrong=bool(password))


def _arcname_for(path: Path) -> tuple[str, dict]:
    """Archive name + restore target. Paths inside the project are stored
    relative to it (portable across machines); anything else is stored under
    external/ with its absolute path."""
    path = path.resolve()
    try:
        rel = path.relative_to(ROOT_DIR)
        return f"files/{rel.as_posix()}", {"rel": rel.as_posix()}
    except ValueError:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", path.as_posix().strip("/"))
        return f"external/{safe}", {"abs": str(path)}


def create_backup(settings, *, include_cache: bool, include_secrets: bool, trigger: str,
                  progress: Optional[Callable[..., None]] = None, password: Optional[str] = None) -> dict:
    """BLOCKING -- run in a worker thread. Returns the new backup's summary.
    progress(stage, done=None, total=None) reports what it's doing (Tasks page).
    With a password: an AES-256 encrypted zip (see the module docstring)."""
    progress = progress or (lambda *a, **k: None)
    if password and pyzipper is None:
        raise RuntimeError(_NEEDS_PYZIPPER)
    from .app_info import APP_VERSION  # local import: avoids import cycle at startup

    dest_dir = settings.backup_dir
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"pmvnexus_backup_{stamp}.zip"
    n = 1
    while (dest_dir / name).exists():
        name = f"pmvnexus_backup_{stamp}_{n}.zip"
        n += 1
    target = dest_dir / name
    partial = target.with_suffix(".zip.part")
    entries: list[dict] = []

    with tempfile.TemporaryDirectory() as tmp:
        progress("Copying the database")
        db_copy = Path(tmp) / "library.db"
        src = sqlite3.connect(f"file:{settings.library_db_file}?mode=ro", uri=True)
        dst = sqlite3.connect(str(db_copy))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        if password:
            zf = pyzipper.AESZipFile(partial, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES)
            zf.setpassword(password.encode("utf-8"))
        else:
            zf = zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6)
        with zf:
            def add(path: Path, source: Optional[Path] = None, kind: str = "file") -> None:
                arc, where = _arcname_for(path)
                zf.write(source or path, arc)
                entries.append({"arc": arc, "kind": kind, **where})

            progress("Compressing the database")
            add(settings.library_db_file, db_copy, kind="database")
            for p in (ROOT_DIR / "config.yaml", ROOT_DIR / "data" / "settings_overrides.json"):
                if p.is_file():
                    add(p, kind="settings")
            if include_secrets:
                # Encrypted credential store + its key (they only work together),
                # plus a legacy .env if one is still around.
                from . import secrets_store
                for p in (secrets_store.DB_FILE, secrets_store.KEY_FILE, ROOT_DIR / ".env"):
                    if p.is_file():
                        add(p, kind="secrets")
                if settings.session_dir.is_dir():
                    for f in settings.session_dir.rglob("*"):
                        if f.is_file():
                            add(f, kind="secrets")
            # Performer photos added by hand (2.16.0) and playlist cover images (2.18.0):
            # they can't be downloaded again, so always kept.
            for folder in ("performer_photos", "playlist_covers"):
                custom_dir = settings.library_db_file.parent / folder
                if custom_dir.is_dir():
                    for f in custom_dir.iterdir():
                        if f.is_file():
                            add(f, kind="photos")
            if include_cache and settings.cache_dir.is_dir():
                cached = [f for f in settings.cache_dir.rglob("*") if f.is_file()]
                for n, f in enumerate(cached, 1):
                    add(f, kind="cache")
                    if n % 25 == 0 or n == len(cached):
                        progress("Adding thumbnails and previews", n, len(cached))

            manifest = {
                "app_version": APP_VERSION, "created_at": time.time(), "trigger": trigger,
                "root": str(ROOT_DIR),  # where relative folders were, for finding the files on another computer (2.25.1)
                "include_cache": include_cache, "include_secrets": include_secrets, "entries": entries,
            }
            zf.writestr(MANIFEST, json.dumps(manifest, indent=2))
    if password:  # what the Backups page shows, readable without the password
        info = {k: v for k, v in manifest.items() if k != "entries"}
        with zipfile.ZipFile(partial, "a", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(INFO, json.dumps({**info, "encrypted": True}, indent=2))
        manifest = {**manifest, "encrypted": True}
    partial.replace(target)
    prune_backups(settings)
    return _summary(target, manifest)


def _summary(path: Path, manifest: Optional[dict] = None) -> dict:
    st = path.stat()
    info = {"name": path.name, "size_bytes": st.st_size, "created_at": st.st_mtime}
    if manifest is None:
        try:
            with zipfile.ZipFile(path) as zf:
                if INFO in zf.namelist():
                    manifest = {**json.loads(zf.read(INFO)), "encrypted": True}
                else:
                    manifest = json.loads(zf.read(MANIFEST))
        except (KeyError, zipfile.BadZipFile, json.JSONDecodeError, OSError, RuntimeError, NotImplementedError):
            manifest = {}
    info.update({k: manifest.get(k) for k in ("app_version", "trigger", "include_cache", "include_secrets")})
    if manifest.get("created_at"):
        info["created_at"] = manifest["created_at"]
    info["encrypted"] = bool(manifest.get("encrypted"))
    info["valid"] = bool(manifest.get("entries")) or info["encrypted"]
    info["test"] = _load_test_results().get(path.name)
    return info


def list_backups(settings) -> list[dict]:
    files = [p for p in settings.backup_dir.iterdir() if p.is_file() and BACKUP_NAME_RE.match(p.name)]
    return sorted((_summary(p) for p in files), key=lambda b: b["created_at"], reverse=True)


def resolve_backup(settings, name: str) -> Path:
    if not BACKUP_NAME_RE.match(name or ""):
        raise ValueError("Invalid backup name")
    path = settings.backup_dir / name
    if not path.is_file():
        raise FileNotFoundError(name)
    return path


def delete_backup(settings, name: str) -> None:
    resolve_backup(settings, name).unlink()


def prune_backups(settings) -> int:
    backups = list_backups(settings)
    removed = 0
    for b in backups[settings.backup.keep:]:
        try:
            (settings.backup_dir / b["name"]).unlink()
            removed += 1
        except OSError:
            pass
    return removed


def stage_restore(settings, name: str, password: Optional[str] = None) -> dict:
    return stage_restore_file(resolve_backup(settings, name), password)


def _version_tuple(v) -> tuple:
    try:
        return tuple(int(x) for x in str(v).split(".")[:3])
    except ValueError:
        return ()


def version_problem(backup_version) -> Optional[str]:
    """None when this app can restore a backup made by `backup_version`."""
    from .app_info import APP_VERSION
    made, now = _version_tuple(backup_version), _version_tuple(APP_VERSION)
    if made and now and made > now:
        return (f"This backup was made by version {backup_version}, newer than this app ({APP_VERSION}). "
                "Update the app first, then restore it.")
    return None


def stage_restore_file(path: Path, password: Optional[str] = None,
                       progress: Optional[Callable[[int, int], None]] = None) -> dict:
    """Stages any backup zip (e.g. `run.py --restore <zip>` on a new install, 2.25.0).
    A password-protected one is decrypted now into STAGED_FILE (PasswordNeeded
    if the password -- given or saved -- doesn't open it), so the next start
    needs no password."""
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    summary = _summary(path)
    problem = version_problem(summary.get("app_version"))
    if problem:
        raise ValueError(problem)
    if not summary["valid"]:
        raise ValueError("This backup has no manifest and cannot be restored automatically")
    source = path
    if summary["encrypted"]:
        zf, manifest = _unlock(path, password)
        if not manifest:
            zf.close()
            raise ValueError("This backup has no manifest and cannot be restored automatically")
        part = STAGED_FILE.with_suffix(".zip.part")
        part.parent.mkdir(parents=True, exist_ok=True)
        with zf, zipfile.ZipFile(part, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as out:
            items = zf.infolist()
            for n, item in enumerate(items, 1):
                if progress:
                    progress(n, len(items))  # (2.26.2) the installer shows it
                if item.filename != INFO:
                    with zf.open(item) as src, out.open(item.filename, "w") as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
        part.replace(STAGED_FILE)
        source = STAGED_FILE
    PENDING_RESTORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_RESTORE_FILE.write_text(json.dumps({"zip": str(source), "name": path.name, "staged_at": time.time(),
                                                "decrypted_copy": source == STAGED_FILE}), encoding="utf-8")
    return summary


# ------------------------------------------------------------------ restore test
TEST_RESULTS_FILE = ROOT_DIR / "data" / "backup_tests.json"
# In every version since backups exist (1.2.0); newer tables (playlists...) are added on start.
_REQUIRED_TABLES = {"library_items", "users", "roles"}


def _entry_target(entry: dict) -> Optional[Path]:
    """Where an entry would be restored, or None when that isn't a safe place:
    a relative path must stay inside the app folder (no "..")."""
    if "rel" in entry:
        rel = str(entry["rel"])
        if rel.startswith(("/", "\\")) or ".." in Path(rel).parts or ":" in rel:
            return None
        target = (ROOT_DIR / rel).resolve()
        return target if ROOT_DIR in target.parents else None
    if "abs" in entry:
        target = Path(str(entry["abs"]))
        return target if target.is_absolute() and ".." not in target.parts else None
    return None


def verify_backup(settings, name: str, progress: Optional[Callable[..., None]] = None,
                  password: Optional[str] = None) -> dict:
    """Restore test: opens the backup like a restore would, without changing
    anything. {"ok", "checks": [{"name", "ok", "detail"}], "tested_at", "counts"}.
    A password-protected backup opens with `password` or the saved one, else
    PasswordNeeded (nothing is recorded then). BLOCKING -- run in a worker thread."""
    progress = progress or (lambda *a, **k: None)
    path = resolve_backup(settings, name)
    checks: list[dict] = []
    counts: dict = {}

    def check(label: str, ok: bool, detail: str = "") -> bool:
        checks.append({"name": label, "ok": bool(ok), "detail": detail})
        return ok

    try:
        zf, manifest = _unlock(path, password)
        with zf:
            if _summary(path)["encrypted"]:
                check("Opens with its password", True, "AES-256 encrypted")
            progress("Reading every file in the backup")
            bad = zf.testzip()
            check("Every file in the zip is intact", bad is None, f"damaged: {bad}" if bad else f"{len(zf.namelist())} files")
            if manifest is None:
                check("Has a list of its contents (manifest)", False, "missing or unreadable")
            if manifest:
                entries = manifest.get("entries") or []
                names = set(zf.namelist())
                missing = [e.get("arc") for e in entries if e.get("arc") not in names]
                check("Has a list of its contents (manifest)", bool(entries), f"{len(entries)} entries, made by v{manifest.get('app_version')}")
                from .app_info import APP_VERSION
                problem = version_problem(manifest.get("app_version"))
                older = _version_tuple(manifest.get("app_version")) < _version_tuple(APP_VERSION)
                check("This app version can restore it", not problem, problem or (
                    f"made by {manifest.get('app_version')}: brought up to date ({APP_VERSION}) when the app starts after the restore"
                    if older else "made by this version"))
                check("Everything listed is in the zip", not missing, ", ".join(map(str, missing[:5])))
                unsafe = [e.get("arc") for e in entries if _entry_target(e) is None]
                check("Every file restores to a safe place", not unsafe, ", ".join(map(str, unsafe[:5])))
                db_entry = next((e for e in entries if e.get("kind") == "database"), None)
                if check("Has the library database", db_entry is not None) and db_entry["arc"] in names:
                    progress("Checking the database")
                    with tempfile.TemporaryDirectory() as tmp:
                        db = Path(tmp) / "test.db"
                        with zf.open(db_entry["arc"]) as src, open(db, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        try:
                            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                            try:
                                result = con.execute("PRAGMA integrity_check").fetchone()[0]
                                check("The database passes SQLite's integrity check", result == "ok", result if result != "ok" else "")
                                tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                                lacking = _REQUIRED_TABLES - tables
                                check("The database has the app's tables", not lacking, ", ".join(sorted(lacking)))
                                for table, label in (("library_items", "videos"), ("users", "users"), ("playlists", "playlists")):
                                    if table in tables:
                                        counts[label] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                                check("The database can be read", True,
                                      ", ".join(f"{v:,} {k}" for k, v in counts.items()))
                            finally:
                                con.close()
                        except sqlite3.DatabaseError as exc:
                            check("The database can be read", False, str(exc))
                for e in (x for x in entries if x.get("kind") == "settings" and x.get("arc") in names):
                    raw = _read(zf, e["arc"])
                    label = f"{Path(str(e.get('rel') or e.get('abs'))).name} can be read"
                    try:
                        if str(e["arc"]).endswith(".json"):
                            json.loads(raw)
                        else:
                            import yaml
                            yaml.safe_load(raw)
                        check(label, True)
                    except Exception as exc:  # noqa: BLE001
                        check(label, False, str(exc)[:200])
    except (zipfile.BadZipFile, OSError) as exc:
        check("The backup opens as a zip file", False, str(exc))
    result = {"ok": all(c["ok"] for c in checks) and bool(checks), "checks": checks, "counts": counts, "tested_at": time.time()}
    _save_test_result(name, result)
    return result


def _load_test_results() -> dict:
    try:
        return json.loads(TEST_RESULTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_test_result(name: str, result: dict) -> None:
    results = _load_test_results()
    results[name] = {"ok": result["ok"], "tested_at": result["tested_at"],
                     "failed": [c["name"] for c in result["checks"] if not c["ok"]], "counts": result["counts"]}
    try:
        TEST_RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        TEST_RESULTS_FILE.write_text(json.dumps(results, indent=2), encoding="utf-8")
    except OSError:
        pass


def get_pending_restore() -> Optional[dict]:
    try:
        return json.loads(PENDING_RESTORE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def cancel_pending_restore() -> None:
    PENDING_RESTORE_FILE.unlink(missing_ok=True)
    STAGED_FILE.unlink(missing_ok=True)


def restore_plan(zip_path: Path):
    """The folders the backup's settings point at (backend/folders.py), read
    from a plain (not password-protected) backup zip; None if it can't be read."""
    from . import folders
    try:
        with zipfile.ZipFile(zip_path) as zf:
            manifest = json.loads(zf.read(MANIFEST))
            found: dict[str, object] = {}
            for e in manifest.get("entries", []):
                target = _entry_target(e)
                if target == folders.CONFIG:
                    import yaml
                    found["config"] = yaml.safe_load(zf.read(e["arc"])) or {}
                elif target == folders.OVERRIDES:
                    found["overrides"] = json.loads(zf.read(e["arc"]))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, Exception):  # noqa: BLE001 -- yaml errors too
        return None
    if "config" not in found:
        found["config"] = folders.current_settings()[0]  # a backup without config.yaml keeps this one
    return folders.plan_from(found["config"], found.get("overrides") or {}, manifest.get("root"))


def apply_pending_restore(log=print, relocate: Optional[list[tuple[str, str]]] = None,
                          progress: Optional[Callable[[int, int], None]] = None) -> bool:
    """Called by run.py BEFORE the app (and its database) is loaded. Every file
    about to be overwritten (except cache files) is first moved aside to
    <name>.pre-restore, so a bad restore can be undone by hand. `relocate`:
    [(old folder, new folder)] -- files the backup puts under an old folder go
    to the new one instead (2.25.1). Returns True if a restore was applied."""
    pending = get_pending_restore()
    if not pending:
        return False
    zip_path = Path(pending.get("zip", ""))
    shown = pending.get("name") or zip_path.name
    PENDING_RESTORE_FILE.unlink(missing_ok=True)  # never retry a failing restore in a loop
    if not zip_path.is_file():
        log(f"[restore] Backup not found, skipping: {zip_path}")
        return False
    try:
        _apply(zip_path, log, [(Path(a), Path(b)) for a, b in (relocate or []) if a != b], progress)
    finally:
        if pending.get("decrypted_copy"):
            zip_path.unlink(missing_ok=True)  # the decrypted copy of a password-protected backup
    log(f"[restore] Restored the files of {shown}")
    return True


def _moved(target: Path, relocate: list[tuple[Path, Path]]) -> Path:
    for old, new in relocate:
        if target == old:
            return new
        if old in target.parents:
            return new / target.relative_to(old)
    return target


def _apply(zip_path: Path, log, relocate: list[tuple[Path, Path]],
           progress: Optional[Callable[[int, int], None]] = None) -> None:
    skipped = 0
    unreachable: set[str] = set()
    with zipfile.ZipFile(zip_path) as zf:
        manifest = json.loads(zf.read(MANIFEST))
        entries = manifest.get("entries", [])
        for n, entry in enumerate(entries, 1):
            if progress:
                progress(n, len(entries))
            target = _entry_target(entry)
            if target is None:
                log(f"[restore] Skipped an entry that would be written outside the app's folders: {entry.get('arc')}")
                continue
            target = _moved(target, relocate)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
            except OSError:  # e.g. a drive this computer doesn't have
                skipped += 1
                unreachable.add(str(target.parent))
                continue
            keep_old = entry.get("kind") != "cache"
            if target.exists() and keep_old:
                shutil.move(str(target), str(target) + ".pre-restore")
            if entry.get("kind") == "database":
                # Keep the old WAL with the old database (SQLite pairs
                # "<db>-wal" with "<db>"), so library.db.pre-restore is complete.
                for suffix in ("-wal", "-shm"):
                    side = Path(str(target) + suffix)
                    if side.exists():
                        shutil.move(str(side), str(target) + ".pre-restore" + suffix)
            with zf.open(entry["arc"]) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
    log(f"[restore] {len(manifest.get('entries', [])) - skipped} file(s) written"
        + (f", {skipped} skipped -- their folder can't be reached here: "
           + ", ".join(sorted(unreachable)[:3]) + (" …" if len(unreachable) > 3 else "") if skipped else ""))
