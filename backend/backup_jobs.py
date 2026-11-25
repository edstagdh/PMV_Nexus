"""What a scheduled backup job does (3.8.0): its options ("job_config" of a Scheduler job of
type "backup") and where its backups go besides the backups folder.

    include   PARTS keys (backend/backup.py) added to the database + settings; [] = just those
    keep      how many of this job's backups stay -- in the backups folder, in each copy folder
              and on Filester alike (1 to 500)
    copy_to   other folders (an external drive, a NAS share...) each backup is copied to,
              byte-checked (backup.copy_backup); up to MAX_COPY_FOLDERS
    upload    "filester": also uploaded to a private Filester folder (backend/cloud_filester.py).
              Then: never the credentials (Secrets) or the videos, a backup password must be
              set (the upload is the encrypted zip), and parts of at most 9 GB.
    split_gb  the job's part size (0: Server Settings' split size); 1.9 to 50 GB, with upload
              1.9 to 9 GB (default 9)

A job from before 3.8.0 has an empty job_config: it does what the backup job always did
(Server Settings' "include thumbnails" and keep) until it's saved with options.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import backup
from .i18n import L

MAX_COPY_FOLDERS = 5
DEFAULT_KEEP = 7
UPLOAD_PROVIDERS = ("filester",)
# The built-in jobs' options for a new install (system_worker.SYSTEM_JOBS)
DAILY_DEFAULT = {"include": [], "keep": 7, "copy_to": [], "upload": "", "split_gb": 0}
WEEKLY_DEFAULT = {"include": ["thumbnails", "frames", "storyboards"], "keep": 4, "copy_to": [], "upload": "", "split_gb": 0}


def is_configured(job_config: Optional[dict]) -> bool:
    """A job saved with options (3.8.0) -- not an older job's empty config."""
    return bool(job_config) and "include" in job_config


def normalize(job_config: dict, settings=None, check_ready: bool = True) -> dict:
    """The options, checked. ValueError (shown in the job dialog) for anything that can't be.
    check_ready: an upload also needs a saved backup password and a Filester API key."""
    from .cloud_filester import MAX_PART_GB, api_key
    cfg = job_config or {}
    include = cfg.get("include") or []
    if not isinstance(include, list) or any(k not in backup.PARTS for k in include):
        raise ValueError(L("Unknown backup part"))
    include = [k for k in backup.PARTS if k in set(include)]   # in the Backups page's order
    try:
        keep = int(DEFAULT_KEEP if cfg.get("keep") in (None, "") else cfg["keep"])
    except (TypeError, ValueError):
        raise ValueError(L("Keep is a number from 1 to 500")) from None
    if not 1 <= keep <= 500:
        raise ValueError(L("Keep is a number from 1 to 500"))
    raw = cfg.get("copy_to") or []
    if not isinstance(raw, list):
        raise ValueError(L("Copy folders must be a list"))
    copy_to: list[str] = []
    for p in raw:
        p = str(p or "").strip()
        if not p:
            continue
        if not Path(p).is_absolute():
            raise ValueError(L("A copy folder needs its full path (e.g. E:\\PMV backups): {folder}", folder=p))
        if settings is not None:
            backup.check_copy_folder(settings, p)
        if p.lower() not in (x.lower() for x in copy_to):
            copy_to.append(p)
    if len(copy_to) > MAX_COPY_FOLDERS:
        raise ValueError(L("At most {n} copy folders", n=MAX_COPY_FOLDERS))
    upload = str(cfg.get("upload") or "").strip().lower()
    if upload and upload not in UPLOAD_PROVIDERS:
        raise ValueError(L("Unknown upload service"))
    try:
        split_gb = round(float(cfg.get("split_gb") or 0), 1)
    except (TypeError, ValueError):
        raise ValueError(L("The part size is a number of GB")) from None
    if upload:
        if "secrets" in include:
            raise ValueError(L("Backups uploaded to Filester never include the credentials: untick “{part}”.",
                               part=backup.PARTS["secrets"]))
        if "videos" in include:
            raise ValueError(L("Backups with the videos can't be uploaded to Filester: untick “{part}”.",
                               part=backup.PARTS["videos"]))
        split_gb = split_gb or MAX_PART_GB
        if not backup.SPLIT_MIN_GB <= split_gb <= MAX_PART_GB:
            raise ValueError(L("With the upload to Filester, the part size is {min} to {max} GB (Filester takes files up to 10 GB).",
                               min=f"{backup.SPLIT_MIN_GB:g}", max=f"{MAX_PART_GB:g}"))
        if check_ready and not backup.saved_password():
            raise ValueError(L("Set a backup password on the Backups page first: a backup is only uploaded encrypted."))
        if check_ready and not api_key():
            raise ValueError(L("Add the Filester API key under Admin → Credentials → Filester first."))
    elif split_gb and not backup.SPLIT_MIN_GB <= split_gb <= backup.SPLIT_MAX_GB:
        raise ValueError(L("The part size is 0 (Server Settings' size) or {min} to {max} GB",
                           min=f"{backup.SPLIT_MIN_GB:g}", max=f"{backup.SPLIT_MAX_GB:g}"))
    return {"include": include, "keep": keep, "copy_to": copy_to, "upload": upload, "split_gb": split_gb}


def part_bytes(cfg: dict) -> Optional[int]:
    """The job's part size in bytes, or None for Server Settings' size."""
    return int(cfg["split_gb"] * 1024 ** 3) if cfg.get("split_gb") else None


def manual_upload(settings, parts) -> dict:
    """A backup made on the Backups page that's also uploaded to Filester (3.8.0): checked like a
    job's upload (no credentials or videos, a password, the key), in parts of at most 9 GB
    (the Backups page's split size when smaller). Its uploads keep backup.keep. ValueError."""
    from .cloud_filester import MAX_PART_GB
    configured = settings.backup.split_gb or MAX_PART_GB
    cfg = normalize({"include": sorted(parts), "keep": settings.backup.keep, "upload": "filester",
                     "split_gb": min(configured, MAX_PART_GB) if configured >= backup.SPLIT_MIN_GB else MAX_PART_GB},
                    settings)
    return cfg


def planned_copies(cfg: Optional[dict]) -> list[str]:
    """Where a backup goes after its restore test, in words (its backup_report.md)."""
    if not cfg:
        return []
    out = [f"Copied to `{f}` (checked byte for byte)" for f in cfg.get("copy_to") or []]
    if cfg.get("upload") == "filester":
        out.append("Uploaded to the private Filester folder")
    if out:
        out.append(f"Keeps the newest {cfg['keep']} of these backups in each place")
    return out
