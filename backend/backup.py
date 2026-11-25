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

What's in it (3.7.0): the database, settings, installed languages, own performer photos
and playlist covers always; PARTS can be added one by one (secrets, ThePornDB photos,
thumbnails & previews, video frames, seekbar previews, and the videos themselves).
plan_backup() counts each beforehand -- files, size, and the free space where backups
go -- so the Backups page can show it and ask first, and a backup that wouldn't fit
isn't started. Pictures and videos are stored, not compressed (they're compressed already).

Split backups (3.7.0): with backup.split_gb set (3.9-115 GB), a backup bigger than that is
written as numbered parts -- name.zip.001, name.zip.002, ... (backend/split_file.py): one
normal zip cut into pieces, which 7-Zip opens from the .001 part. The app lists, tests,
restores and deletes the parts together under the backup's name (name.zip).

Where (3.7.0): a backup made on the Backups page can go to another folder than the backups
folder (e.g. a bigger or external drive). Such folders are remembered (data/backup_locations.json)
and their backups listed, tested, restored and deleted like the others; only the backups
folder's own are pruned to backup.keep. A folder that isn't there (a drive unplugged) is skipped.
"""
from __future__ import annotations
from .i18n import L
from .split_file import SplitFile, base_of, part_path, parts_of

import json
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
import zlib
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
        raise ValueError(L('Use at least {n} characters', n=MIN_PASSWORD))
    if password and pyzipper is None:
        raise ValueError(_NEEDS_PYZIPPER)
    from . import secrets_store
    secrets_store.write({"backup_password": password})


SPLIT_MIN_GB, SPLIT_MAX_GB = 1.9, 50.0
LOCATIONS_FILE = ROOT_DIR / "data" / "backup_locations.json"


def _known_locations() -> list[str]:
    try:
        data = json.loads(LOCATIONS_FILE.read_text(encoding="utf-8"))
        return [str(x) for x in data if isinstance(x, str)]
    except (OSError, ValueError):
        return []


def remember_location(settings, folder: Path) -> None:
    folder = Path(folder).resolve()
    if folder == settings.backup_dir.resolve():
        return
    known = _known_locations()
    if str(folder) not in known:
        LOCATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOCATIONS_FILE.write_text(json.dumps(known + [str(folder)], indent=2), encoding="utf-8")


def backup_dirs(settings) -> list[Path]:
    """The backups folder first, then the other folders backups were saved to that are there now."""
    out = [settings.backup_dir]
    for raw in _known_locations():
        p = Path(raw)
        try:
            if p.is_dir() and p.resolve() not in {d.resolve() for d in out}:
                out.append(p)
        except OSError:  # e.g. a network drive that doesn't answer
            continue
    return out


def check_destination(settings, folder: str | Path | None) -> Path:
    """Where a new backup goes: the backups folder, or another folder chosen on the page.
    ValueError for a place a backup mustn't go (relative, or inside the videos / cache folders,
    whose files a later backup would then pick up again)."""
    if not folder:
        return settings.backup_dir
    path = Path(str(folder))
    if not path.is_absolute():
        raise ValueError(L("Choose a full folder path for the backup"))
    path = path.resolve()
    for other in (settings.cache_dir, settings.output_dir):
        other = Path(other).resolve()
        if path == other or other in path.parents:
            raise ValueError(L("Choose a folder outside the videos and thumbnails folders"))
    return path


def files_of(path: Path) -> list[Path]:
    """The file(s) of a backup: name.zip itself, or its parts name.zip.001, .002, ..."""
    path = base_of(Path(path))
    return [path] if path.is_file() else parts_of(path)


def exists(path: Path) -> bool:
    return bool(files_of(path))


def _zip(path: Path, cls=zipfile.ZipFile):
    """A backup opened for reading, split into parts or not."""
    path = base_of(Path(path))
    if path.is_file():
        return cls(path)
    zf = cls(SplitFile(path, "r"))
    zf._filePassed = 0  # closing the zip closes the parts too
    return zf


def _open(path: Path, password: Optional[str] = None):
    if pyzipper is None:
        return _zip(path)
    zf = _zip(path, pyzipper.AESZipFile)
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
    zf, manifest, _pw = _unlock_with(path, password)
    return zf, manifest


def _unlock_with(path: Path, password: Optional[str] = None):
    """_unlock, plus the password that opened it (None: none needed)."""
    tries = [p for p in dict.fromkeys([password, saved_password()]) if p] or [None]
    for pw in tries:
        zf = _open(path, pw)
        try:
            return zf, json.loads(_read(zf, MANIFEST)), pw
        except PasswordNeeded:
            zf.close()
        except (KeyError, json.JSONDecodeError):
            return zf, None, pw
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


# --------------------------------------------------------------- what goes in (3.7.0)
# key -> label; the order is the order they're written (and shown)
PARTS = {
    "secrets": L("Secrets (credentials and extensions' sign-ins)"),
    "performer_photos": L("ThePornDB performer photos"),
    "thumbnails": L("Thumbnails, previews and details files"),
    "frames": L("Video frames"),
    "storyboards": L("Seekbar previews"),
    "videos": L("The videos (the whole library)"),
}
CACHE_PARTS = ("performer_photos", "thumbnails", "frames", "storyboards")  # what "include_cache" meant before
_STORED = {".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".webp", ".jpg", ".jpeg", ".png", ".gif", ".zip"}
SPACE_MARGIN = 200 * 1024 * 1024  # kept free on top of the estimate


def parts_from(include=None, include_cache: bool = False, include_secrets: bool = False) -> set[str]:
    """The optional parts asked for -- a list of PARTS keys, or the older two switches."""
    if include is not None:
        return {k for k in include if k in PARTS}
    return (set(CACHE_PARTS) if include_cache else set()) | ({"secrets"} if include_secrets else set())


def _files(folder: Path, *, skip_dirs: tuple = ()) -> list[Path]:
    if not folder.is_dir():
        return []
    out = []
    for f in folder.rglob("*"):
        rel = f.relative_to(folder).parts
        if rel and rel[0] in skip_dirs:
            continue
        if f.is_file() and not f.name.endswith(".part"):
            out.append(f)
    return out


def _library_videos(settings) -> list[Path]:
    """The video (and funscript) files the library points to, that exist."""
    out = []
    try:
        con = sqlite3.connect(f"file:{settings.library_db_file}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT data FROM library_items").fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return out
    for (data,) in rows:
        try:
            d = json.loads(data)
        except (TypeError, ValueError):
            continue
        for key in ("video_path", "funscript_path"):
            if d.get(key):
                f = Path(d[key])
                f = f if f.is_absolute() else (ROOT_DIR / f)
                if f.is_file():
                    out.append(f.resolve())
    return sorted(set(out))


def _collect(settings) -> dict[str, list[tuple[Path, str]]]:
    """Every file a backup can hold: group -> [(path, entry kind)]. "always" is always in."""
    from . import secrets_store
    data_dir = settings.library_db_file.parent
    always: list[tuple[Path, str]] = []
    for p in (ROOT_DIR / "config.yaml", ROOT_DIR / "data" / "settings_overrides.json"):
        if p.is_file():
            always.append((p, "settings"))
    always += [(p, "settings") for p in sorted((ROOT_DIR / "data" / "languages").glob("*.json"))]
    for folder in ("performer_photos", "playlist_covers"):  # can't be downloaded again
        always += [(f, "photos") for f in sorted((data_dir / folder).glob("*")) if f.is_file()]
    secrets = [(p, "secrets") for p in (secrets_store.DB_FILE, secrets_store.KEY_FILE, ROOT_DIR / ".env") if p.is_file()]
    secrets += [(f, "secrets") for f in _files(settings.session_dir)]
    cache = settings.cache_dir
    return {
        "always": always,
        "secrets": secrets,
        "performer_photos": [(f, "cache") for f in _files(data_dir / "performers")],
        "thumbnails": [(f, "cache") for f in _files(cache, skip_dirs=(".storyboards", ".frames", ".transcode", ".incoming"))],
        "frames": [(f, "cache") for f in _files(cache / ".frames")],
        "storyboards": [(f, "cache") for f in _files(cache / ".storyboards")],
        "videos": [(f, "video") for f in _library_videos(settings)],
    }


def _bytes(files) -> int:
    total = 0
    for f, _kind in files:
        try:
            total += f.stat().st_size
        except OSError:
            pass
    return total


def free_space(settings, folder: Optional[Path] = None) -> dict:
    """Free space where backups are written (live: the Backups page asks every few seconds)."""
    folder = Path(folder) if folder else settings.backup_dir
    probe = folder
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
        return {"dir": str(folder), "free_bytes": usage.free, "total_bytes": usage.total}
    except OSError:
        return {"dir": str(folder), "free_bytes": None, "total_bytes": None}


def plan_backup(settings, include=(), collected: Optional[dict] = None, dest: Optional[Path] = None) -> dict:
    """What a backup with `include` would hold: every part with its files and size (the ones
    not asked for too, so the page can offer them), the estimated zip size, and whether it fits.
    BLOCKING (walks the cache folder) -- run in a worker thread."""
    collected = collected or _collect(settings)
    include = parts_from(include)
    db_size = settings.library_db_file.stat().st_size if settings.library_db_file.is_file() else 0
    always = _bytes(collected["always"])
    parts = []
    for key, label in PARTS.items():
        files = collected[key]
        parts.append({"key": key, "label": label, "files": len(files), "bytes": _bytes(files), "included": key in include})
    chosen = sum(p["bytes"] for p in parts if p["included"])
    # the database shrinks to about a third in a zip; pictures and videos stay their size
    estimate = db_size // 3 + always + chosen
    space = free_space(settings, dest)
    needed = estimate + SPACE_MARGIN
    return {
        "always": {"database_bytes": db_size, "files": len(collected["always"]) + 1, "bytes": always + db_size,
                   "languages": sum(1 for f, _k in collected["always"] if f.parent.name == "languages"),
                   "photos": sum(1 for f, _k in collected["always"] if f.parent.name == "performer_photos"),
                   "covers": sum(1 for f, _k in collected["always"] if f.parent.name == "playlist_covers")},
        "parts": parts, "include": sorted(include), "estimate_bytes": estimate, "needed_bytes": needed,
        "margin_bytes": SPACE_MARGIN,
        **space, "enough": space["free_bytes"] is None or space["free_bytes"] >= needed,
    }


class NotEnoughSpace(RuntimeError):
    pass


def space_problem(plan: dict) -> Optional[str]:
    if plan["enough"]:
        return None
    gb = lambda n: f"{n / 1024 ** 3:.1f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.0f} MB"  # noqa: E731
    return L("Not enough free space for this backup: it needs about {needed}, {free} is free in {folder}",
             needed=gb(plan["needed_bytes"]), free=gb(plan["free_bytes"]), folder=plan["dir"])


def create_backup(settings, *, include=None, include_cache: bool = False, include_secrets: bool = False,
                  trigger: str, progress: Optional[Callable[..., None]] = None, password: Optional[str] = None,
                  dest: Optional[Path] = None) -> dict:
    """BLOCKING -- run in a worker thread. Returns the new backup's summary.
    `include`: PARTS keys (3.7.0); without it the older include_cache / include_secrets.
    progress(stage, done, total, step=, steps=, bytes_done=, bytes_total=) reports what it's
    doing (Tasks page: each step and the whole). With a password: an AES-256 encrypted zip
    (see the module docstring). NotEnoughSpace before anything is written when it wouldn't
    fit, or while writing when the disk gets close to full (the partial file is removed)."""
    progress = progress or (lambda *a, **k: None)
    if password and pyzipper is None:
        raise RuntimeError(_NEEDS_PYZIPPER)
    from .app_info import APP_VERSION  # local import: avoids import cycle at startup

    parts = parts_from(include, include_cache, include_secrets)
    progress(L("Checking what goes in and the free space"))
    collected = _collect(settings)
    dest_dir = check_destination(settings, dest)
    plan = plan_backup(settings, parts, collected, dest_dir)
    problem = space_problem(plan)
    if problem:
        raise NotEnoughSpace(problem)

    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"pmvnexus_backup_{stamp}.zip"
    n = 1
    while exists(dest_dir / name):
        name = f"pmvnexus_backup_{stamp}_{n}.zip"
        n += 1
    target = dest_dir / name
    partial = target.with_suffix(".zip.part")
    split = settings.backup.split_bytes()  # None: one file
    entries: list[dict] = []
    # the steps, in order: (stage text, files)
    steps: list[tuple[str, list]] = [(L("Copying the database"), []), (L("Adding the settings and own pictures"), collected["always"])]
    steps += [(PARTS[k], collected[k]) for k in PARTS if k in parts]
    total_bytes = plan["always"]["bytes"] + sum(p["bytes"] for p in plan["parts"] if p["included"])
    state = {"bytes": 0}

    def report(i: int, done=None, total=None):
        progress(steps[i][0], done, total, step=i + 1, steps=len(steps), bytes_done=state["bytes"], bytes_total=total_bytes)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            report(0)
            db_copy = Path(tmp) / "library.db"
            src = sqlite3.connect(f"file:{settings.library_db_file}?mode=ro", uri=True)
            dst = sqlite3.connect(str(db_copy))
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()

            out = SplitFile(partial, "w", part_size=split) if split else partial
            if password:
                zf = pyzipper.AESZipFile(out, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES)
                zf.setpassword(password.encode("utf-8"))
            else:
                zf = zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6)
            if split:
                zf._filePassed = 0  # closing the zip closes the parts too
            with zf:
                def add(path: Path, source: Optional[Path] = None, kind: str = "file", part: Optional[str] = None) -> None:
                    arc, where = _arcname_for(path)
                    stored = path.suffix.lower() in _STORED
                    zf.write(source or path, arc, compress_type=zipfile.ZIP_STORED if stored else None)
                    size = (source or path).stat().st_size
                    entry = {"arc": arc, "kind": kind, **where, "size": size}
                    if part:
                        entry["part"] = part
                    entries.append(entry)
                    state["bytes"] += size

                def check_space() -> None:
                    free = free_space(settings)["free_bytes"]
                    if free is not None and free < SPACE_MARGIN // 2:
                        raise NotEnoughSpace(L("The disk the backups go to is almost full -- the backup was stopped"))

                add(settings.library_db_file, db_copy, kind="database")
                keys = [None, None] + [k for k in PARTS if k in parts]
                for i in range(1, len(steps)):
                    files = steps[i][1]
                    report(i, 0, len(files))
                    for n, (f, kind) in enumerate(files, 1):
                        try:
                            add(f, kind=kind, part=keys[i])
                        except FileNotFoundError:  # gone since it was counted (e.g. a preview remade)
                            continue
                        if n % 25 == 0 or n == len(files) or kind == "video":
                            report(i, n, len(files))
                            check_space()

                manifest = {
                    "app_version": APP_VERSION, "created_at": time.time(), "trigger": trigger,
                    "root": str(ROOT_DIR),  # where relative folders were, for finding the files on another computer (2.25.1)
                    "include": sorted(parts), "include_cache": any(k in parts for k in CACHE_PARTS),
                    "include_secrets": "secrets" in parts, "include_videos": "videos" in parts, "entries": entries,
                }
                zf.writestr(MANIFEST, json.dumps(manifest, indent=2))
        if password:  # what the Backups page shows, readable without the password
            info = {k: v for k, v in manifest.items() if k != "entries"}
            out = SplitFile(partial, "r+", part_size=split) if split else partial
            with zipfile.ZipFile(out, "a", compression=zipfile.ZIP_DEFLATED) as zf:
                if split:
                    zf._filePassed = 0
                zf.writestr(INFO, json.dumps({**info, "encrypted": True}, indent=2))
            manifest = {**manifest, "encrypted": True}
        if not split:
            partial.replace(target)
        else:
            parts = parts_of(partial)
            if len(parts) == 1:  # it fit in one: a plain name.zip
                parts[0].replace(target)
            else:
                for i, part in enumerate(parts, 1):
                    part.replace(part_path(target, i))
    except BaseException:
        partial.unlink(missing_ok=True)  # never leave half a backup behind
        for part in parts_of(partial):
            part.unlink(missing_ok=True)
        raise
    if dest_dir.resolve() == settings.backup_dir.resolve():
        prune_backups(settings)  # only the backups folder's own are pruned
    else:
        remember_location(settings, dest_dir)
    return _summary(target, manifest)


def _summary(path: Path, manifest: Optional[dict] = None) -> dict:
    path = base_of(Path(path))
    files = files_of(path)
    info = {"name": path.name, "size_bytes": sum(f.stat().st_size for f in files), "created_at": files[0].stat().st_mtime,
            "parts": len(files) if files[0] != path else 0, "part_sizes": [f.stat().st_size for f in files] if files[0] != path else []}
    if manifest is None:
        try:
            with _zip(path) as zf:
                if INFO in zf.namelist():
                    manifest = {**json.loads(zf.read(INFO)), "encrypted": True}
                else:
                    manifest = json.loads(zf.read(MANIFEST))
        except (KeyError, zipfile.BadZipFile, json.JSONDecodeError, OSError, RuntimeError, NotImplementedError):
            manifest = {}
    info.update({k: manifest.get(k) for k in ("app_version", "trigger", "include_cache", "include_secrets", "include_videos", "include")})
    if manifest.get("created_at"):
        info["created_at"] = manifest["created_at"]
    info["encrypted"] = bool(manifest.get("encrypted"))
    info["valid"] = bool(manifest.get("entries")) or info["encrypted"]
    info["test"] = _load_test_results().get(path.name)
    return info


def _backups_in(folder: Path) -> set[Path]:
    names = set()
    try:
        for p in folder.iterdir():
            if p.is_file():
                base = base_of(p)  # the parts of a split backup count once, by name.zip
                if BACKUP_NAME_RE.match(base.name) and (base == p or p.name.endswith(".001")):
                    names.add(base)
    except OSError:
        pass
    return names


def list_backups(settings, only_default: bool = False) -> list[dict]:
    out = []
    for i, folder in enumerate(backup_dirs(settings)[:1] if only_default else backup_dirs(settings)):
        for p in _backups_in(folder):
            out.append({**_summary(p), "dir": str(folder), "default": i == 0})
    return sorted(out, key=lambda b: b["created_at"], reverse=True)


def resolve_backup(settings, name: str) -> Path:
    if not BACKUP_NAME_RE.match(name or ""):
        raise ValueError(L('Invalid backup name'))
    for folder in backup_dirs(settings):
        if exists(folder / name):
            return folder / name
    raise FileNotFoundError(name)


def delete_backup(settings, name: str) -> None:
    for f in files_of(resolve_backup(settings, name)):
        f.unlink()


def prune_backups(settings) -> int:
    backups = list_backups(settings, only_default=True)
    removed = 0
    for b in backups[settings.backup.keep:]:
        try:
            for f in files_of(settings.backup_dir / b["name"]):
                f.unlink()
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
    needs no password -- unless the saved password opens it (3.7.0): then the next
    start uses that one and no copy is written. A split backup: its .001 part or name."""
    path = base_of(Path(path).resolve())  # a split backup: its .001 part or its name
    if not exists(path):
        raise FileNotFoundError(str(path))
    summary = _summary(path)
    problem = version_problem(summary.get("app_version"))
    if problem:
        raise ValueError(problem)
    if not summary["valid"]:
        raise ValueError(L('This backup has no manifest and cannot be restored automatically'))
    source, with_saved = path, False
    if summary["encrypted"]:
        zf, manifest, used = _unlock_with(path, password)
        if not manifest:
            zf.close()
            raise ValueError(L('This backup has no manifest and cannot be restored automatically'))
        with_saved = bool(used) and used == saved_password()
    if summary["encrypted"] and with_saved:
        # (3.7.0) The saved password opens it: the next start opens it with that password,
        # so no decrypted copy (as big as the backup, videos and all) is written first.
        zf.close()
    elif summary["encrypted"]:
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
                                                "decrypted_copy": source == STAGED_FILE,
                                                "saved_password": with_saved}), encoding="utf-8")
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
            # Every file is read back (its checksum checked) -- except the videos of a full
            # library backup (3.7.0): reading hundreds of GB again would take as long as the
            # backup did; their sizes are checked instead.
            videos = {e.get("arc"): e for e in (manifest or {}).get("entries", []) if e.get("kind") == "video"}
            infos = zf.infolist()
            to_read = [i for i in infos if i.filename not in videos]
            total_bytes = sum(i.file_size for i in to_read)
            done_bytes, bad = 0, None
            for n, info in enumerate(to_read, 1):
                try:
                    with zf.open(info) as f:
                        while f.read(1024 * 1024):
                            pass
                except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
                    bad = f"{info.filename} ({exc})"
                    break
                except RuntimeError as exc:  # a protected entry
                    if "password" in str(exc).lower():
                        raise PasswordNeeded(wrong=bool(password)) from exc
                    raise
                done_bytes += info.file_size
                if n % 25 == 0 or n == len(to_read):
                    progress(L("Reading every file in the backup"), n, len(to_read), bytes_done=done_bytes, bytes_total=total_bytes)
            check("Every file in the zip is intact", bad is None, f"damaged: {bad}" if bad else f"{len(to_read)} files")
            if videos:
                by_name = {i.filename: i for i in infos}
                wrong = [a for a, e in videos.items() if a not in by_name or (e.get("size") is not None and by_name[a].file_size != e["size"])]
                check("Every video is in the zip with its size", not wrong,
                      ", ".join(wrong[:5]) if wrong else f"{len(videos)} videos (not read back, to save time)")
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
                    progress(L("Checking the database"))
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
        zf, manifest = _unlock(zip_path)  # plain, split, or opened with the saved password
        with zf:
            if manifest is None:
                return None
            found: dict[str, object] = {}
            for e in manifest.get("entries", []):
                target = _entry_target(e)
                if target == folders.CONFIG:
                    import yaml
                    found["config"] = yaml.safe_load(_read(zf, e["arc"])) or {}
                elif target == folders.OVERRIDES:
                    found["overrides"] = json.loads(_read(zf, e["arc"]))
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
    if not exists(zip_path):
        log(f"[restore] Backup not found, skipping: {zip_path}")
        return False
    password = saved_password() if pending.get("saved_password") else None
    try:
        _apply(zip_path, log, [(Path(a), Path(b)) for a, b in (relocate or []) if a != b], progress, password)
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
           progress: Optional[Callable[[int, int], None]] = None, password: Optional[str] = None) -> None:
    skipped = 0
    unreachable: set[str] = set()
    with _open(zip_path, password) if password else _zip(zip_path) as zf:
        manifest = json.loads(_read(zf, MANIFEST))
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
            if entry.get("kind") == "video" and target.is_file() and entry.get("size") == target.stat().st_size:
                continue  # (3.7.0) the same video is already there
            keep_old = entry.get("kind") not in ("cache", "video")
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
