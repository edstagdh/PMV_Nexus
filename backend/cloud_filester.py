"""Backups that leave this computer (3.8.0): uploaded to a private folder on Filester
(https://filester.me, API: https://filester.me/api-docs) by a scheduled backup job
(backend/backup_jobs.py; Scheduler -> a backup job -> "Upload to Filester").

What may be uploaded -- checked here again before every upload, whatever the job says:
  - only an encrypted backup (AES-256, the Backups page's password): the database holds every
    account's password hash, the watch history and the settings;
  - never the credentials (the "Secrets" part) and never the videos;
  - every file at most MAX_PART_BYTES (Filester takes files up to 10 GB), so a bigger backup is
    made in parts (the job's part size is 1.9 to 9 GB).

Filester's limits (its API docs, November 2026): 10 GB storage per account, 10 GB per file,
1,000 requests an hour, and files nobody downloads for 45 days are deleted. Before uploading,
the account's free space is checked; the job's oldest uploads that would be removed anyway
afterwards (beyond its "keep") go first when that makes room. If it still doesn't fit, nothing
is uploaded and nothing deleted.

Only what this app uploaded is ever deleted there: the uploads are recorded in
data/cloud_backups.json (file ids per backup and job), and pruning goes by that record --
never by listing and matching names.

The API key is a credential (Admin -> Credentials -> Filester); it's never logged.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional

import httpx
from loguru import logger

from . import backup
from .i18n import L

BASE_URL = "https://u1.filester.me"
FOLDER_NAME = "PMV Nexus backups"
MAX_PART_GB = 9.0                                  # a job's biggest part size for Filester
MAX_PART_BYTES = int(MAX_PART_GB * 1024 ** 3)      # 9 GiB = 9.66 GB, under Filester's 10 GB
STATE_FILE = backup.ROOT_DIR / "data" / "cloud_backups.json"
PROVIDER = "filester"
RETRIES = 4


class CloudError(RuntimeError):
    """Something the admin can act on; the message says what."""


def api_key() -> Optional[str]:
    from . import secrets_store
    if not (secrets_store.KEY_FILE.is_file() and secrets_store.DB_FILE.is_file()):
        return None
    try:
        return secrets_store.values().get("filester_api_key") or None
    except Exception:  # noqa: BLE001 -- an unreadable store: no key
        return None


def load_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    entry = data.get(PROVIDER) if isinstance(data, dict) else None
    if not isinstance(entry, dict):
        entry = {}
    entry.setdefault("folder_id", None)
    entry.setdefault("uploads", [])
    return {PROVIDER: entry}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _data(payload: dict):
    """Filester answers {"success": true, "data": ...} -- or, for some calls, the fields at the top."""
    return payload.get("data", payload) if isinstance(payload, dict) else payload


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes")


class _Reader:
    """A file for httpx's multipart upload that reports how much was sent. fileno() lets httpx
    send the size up front (Content-Length) instead of a chunked upload."""

    def __init__(self, fh, report: Callable[[int], None]):
        self._fh, self._report, self._sent = fh, report, 0

    def read(self, n: int = -1) -> bytes:
        chunk = self._fh.read(n)
        self._sent += len(chunk)
        self._report(self._sent)
        return chunk

    def seek(self, *a):
        self._sent = 0
        return self._fh.seek(*a)

    def tell(self) -> int:
        return self._fh.tell()

    def fileno(self) -> int:
        return self._fh.fileno()


class Filester:
    def __init__(self, key: str, transport: Optional[httpx.BaseTransport] = None):
        self.client = httpx.Client(
            base_url=BASE_URL, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            # an upload of several GB: no limit on sending it; the answer can take a while after it
            timeout=httpx.Timeout(60.0, write=None, read=900.0), transport=transport, follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _send(self, method: str, path: str, **kw) -> httpx.Response:
        """Waits and tries again when Filester says "too many requests" (Retry-After) or the
        connection drops (not for uploads: a file is sent once per try of upload())."""
        for attempt in range(RETRIES):
            try:
                r = self.client.request(method, path, **kw)
            except httpx.TransportError as exc:
                if attempt == RETRIES - 1 or "files" in kw:
                    raise CloudError(L("Filester can't be reached: {error}", error=str(exc) or type(exc).__name__)) from exc
                time.sleep(2 ** attempt * 5)
                continue
            if r.status_code == 429 and attempt < RETRIES - 1:
                try:
                    wait = min(300.0, float(r.headers.get("Retry-After", "60")))
                except ValueError:
                    wait = 60.0
                logger.info("Filester: too many requests, waiting {}s", int(wait))
                time.sleep(wait)
                continue
            return r
        return r  # pragma: no cover

    def _call(self, method: str, path: str, **kw):
        r = self._send(method, path, **kw)
        try:
            payload = r.json()
        except ValueError:
            payload = {}
        if r.status_code == 401 or r.status_code == 403:
            raise CloudError(L("Filester refused the API key -- check it under Admin → Credentials → Filester"))
        if r.status_code >= 400 or (isinstance(payload, dict) and payload.get("success") is False):
            msg = payload.get("message") if isinstance(payload, dict) else None
            raise CloudError(L("Filester: {message}", message=msg or f"HTTP {r.status_code}"))
        return payload

    # -- the calls this app uses
    def account(self) -> dict:
        return _data(self._call("GET", "/api/v1/account"))

    def folders(self) -> list[dict]:
        d = _data(self._call("GET", "/api/v1/folders"))
        if isinstance(d, dict):
            d = d.get("folders") or d.get("items") or []
        return [f for f in d if isinstance(f, dict)]

    def create_private_folder(self, name: str) -> str:
        d = _data(self._call("POST", "/api/v1/folder", json={"name": name, "public": 0}))
        ident = d.get("identifier") or d.get("id") if isinstance(d, dict) else None
        if not ident:
            raise CloudError(L("Filester didn't say which folder it made"))
        return str(ident)

    def upload(self, path: Path, folder_id: str, report: Callable[[int], None]) -> dict:
        with open(path, "rb") as fh:
            files = {"file": (path.name, _Reader(fh, report), "application/octet-stream")}
            d = self._call("POST", "/api/v1/upload", files=files, headers={"X-Folder-ID": str(folder_id)})
        ident = d.get("file_id") or d.get("slug")
        if not ident:
            raise CloudError(L("Filester didn't say which file it stored"))
        return {"id": str(ident), "slug": d.get("slug"), "url": d.get("url")}

    def file_info(self, ident: str) -> dict:
        return _data(self._call("GET", f"/api/v1/file/{ident}"))

    def delete(self, idents: list[str]) -> None:
        """The API docs list this one without the /api/v1 prefix; the other spelling is tried
        when that one isn't there."""
        if not idents:
            return
        body = {"identifiers": [str(i) for i in idents]}
        r = self._send("POST", "/file/delete", json=body)
        if r.status_code == 404:
            r = self._send("POST", "/api/v1/file/delete", json=body)
        if r.status_code >= 400:
            raise CloudError(L("Filester: the old backups couldn't be deleted (HTTP {code})", code=r.status_code))


def _ensure_folder(fs: Filester, entry: dict) -> str:
    """The private folder the backups go to: the one recorded, or found by name, or made."""
    found = fs.folders()
    by_id = {str(f.get("identifier") or f.get("id")): f for f in found}
    fid = entry.get("folder_id")
    folder = by_id.get(str(fid)) if fid else None
    if folder is None:
        folder = next((f for f in found if f.get("name") == FOLDER_NAME and not f.get("parent_id") and not f.get("parent")), None)
    if folder is not None:
        if _truthy(folder.get("public")):
            raise CloudError(L("The Filester folder “{name}” is public. Make it private there (or delete it, and a private one is made).",
                               name=folder.get("name") or FOLDER_NAME))
        entry["folder_id"] = str(folder.get("identifier") or folder.get("id"))
        return entry["folder_id"]
    entry["folder_id"] = fs.create_private_folder(FOLDER_NAME)
    logger.info("Filester: made the private folder '{}'", FOLDER_NAME)
    return entry["folder_id"]


def check_uploadable(summary: dict, files: list[Path]) -> None:
    """The rules for what leaves this computer (see the module docstring)."""
    if not summary.get("encrypted"):
        raise CloudError(L("Only password-protected backups are uploaded. Set a backup password on the Backups page."))
    include = set(summary.get("include") or [])
    if summary.get("include_secrets") or "secrets" in include:
        raise CloudError(L("This backup has the credentials in it -- those are never uploaded"))
    if summary.get("include_videos") or "videos" in include:
        raise CloudError(L("Backups with the videos aren't uploaded"))
    for f in files:
        if f.stat().st_size > MAX_PART_BYTES:
            raise CloudError(L("{file} is bigger than Filester takes ({max} GB per file); the job's part size must be {max} GB or less",
                               file=f.name, max=f"{MAX_PART_GB:g}"))


def upload_backup(settings, name: str, job: str, keep: int,
                  progress: Optional[Callable[[int, int], None]] = None,
                  transport: Optional[httpx.BaseTransport] = None) -> dict:
    """BLOCKING. Uploads a backup (all its parts) to the private folder, then removes this job's
    uploads beyond `keep`. progress(bytes sent, bytes total)."""
    key = api_key()
    if not key:
        raise CloudError(L("No Filester API key -- add it under Admin → Credentials → Filester"))
    path = backup.resolve_backup(settings, name)
    files = backup.files_of(path)
    summary = backup._summary(path)
    check_uploadable(summary, files)
    total = sum(f.stat().st_size for f in files)
    progress = progress or (lambda *_: None)
    state = load_state()
    entry = state[PROVIDER]
    with Filester(key, transport) as fs:
        folder = _ensure_folder(fs, entry)
        save_state(state)
        mine = sorted((u for u in entry["uploads"] if u.get("job") == job), key=lambda u: u.get("uploaded_at", 0))
        removable = mine[:max(0, len(mine) + 1 - keep)]   # would go after this upload anyway
        acct = fs.account()
        try:
            free = int(acct.get("storage_limit")) - int(acct.get("storage_used"))
        except (TypeError, ValueError):
            free = None
        if free is not None and free < total:
            drop, freed = [], 0
            for u in removable:
                if free + freed >= total:
                    break
                drop.append(u)
                freed += int(u.get("size") or 0)
            if free + freed < total:
                raise CloudError(L("The backup doesn't fit on Filester: it needs {need} MB, {free} MB are free there. "
                                   "Keep fewer backups there, or leave parts out of this job.",
                                   need=total // 1048576, free=max(0, free + freed) // 1048576))
            _remove(fs, entry, drop)
            save_state(state)
        sent_before = 0
        uploaded: list[dict] = []
        try:
            for f in files:
                size = f.stat().st_size
                res = fs.upload(f, folder, lambda n, b=sent_before: progress(b + n, total))
                info = fs.file_info(res["id"])
                stored = info.get("size") if isinstance(info, dict) else None
                uploaded.append({"name": f.name, "size": size, **res})
                if stored is not None and int(stored) != size:
                    raise CloudError(L("Filester stored {file} with the wrong size ({stored} of {size} bytes)",
                                       file=f.name, stored=stored, size=size))
                sent_before += size
        except BaseException:
            try:
                fs.delete([u["id"] for u in uploaded])   # never leave half a backup there
            except Exception:  # noqa: BLE001
                logger.warning("Filester: couldn't remove the parts of an unfinished upload")
            raise
        entry["uploads"].append({"backup": name, "job": job, "size": total, "created_at": summary.get("created_at"),
                                 "uploaded_at": time.time(), "files": uploaded})
        save_state(state)
        logger.info("Filester: uploaded backup {} ({} file(s), {} bytes)", name, len(uploaded), total)
        mine = sorted((u for u in entry["uploads"] if u.get("job") == job), key=lambda u: u.get("uploaded_at", 0))
        old = mine[:max(0, len(mine) - keep)]
        removed = 0
        if old:
            try:
                _remove(fs, entry, old)
                removed = len(old)
            except CloudError as exc:   # the new one is there: a later run tries again
                logger.warning("Filester: old backups not removed: {}", exc)
            save_state(state)
    return {"files": len(uploaded), "bytes": total, "removed": removed}


def _remove(fs: Filester, entry: dict, uploads: list[dict]) -> None:
    """Deletes recorded uploads there and from the record."""
    if not uploads:
        return
    fs.delete([f["id"] for u in uploads for f in u.get("files", [])])
    gone = {id(u) for u in uploads}
    entry["uploads"] = [u for u in entry["uploads"] if id(u) not in gone]
    for u in uploads:
        logger.info("Filester: removed old backup {}", u.get("backup"))


def status(live: bool = False, transport: Optional[httpx.BaseTransport] = None) -> dict:
    """For the Backups page: whether a key is set, what was uploaded, and (live) the account's space."""
    entry = load_state()[PROVIDER]
    out = {"configured": bool(api_key()), "folder_id": entry.get("folder_id"), "max_part_gb": MAX_PART_GB,
           "uploads": sorted(({"backup": u.get("backup"), "job": u.get("job"), "size": u.get("size"),
                               "uploaded_at": u.get("uploaded_at"), "parts": len(u.get("files", [])),
                               "url": (u.get("files") or [{}])[0].get("url")} for u in entry["uploads"]),
                             key=lambda u: u.get("uploaded_at") or 0, reverse=True)}
    if live:
        key = api_key()
        if not key:
            raise CloudError(L("No Filester API key -- add it under Admin → Credentials → Filester"))
        with Filester(key, transport) as fs:
            acct = fs.account()
            out["account"] = {"username": acct.get("username"), "storage_used": acct.get("storage_used"),
                              "storage_limit": acct.get("storage_limit")}
    return out
