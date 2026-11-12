"""
Admin-only app-wide configuration: download/re-encode settings, the
folder-browser modal's backing routes, "sync database from files", and
bulk repair-all. Everything here is settings.manage-gated -- app
configuration, not a per-user preference (those live in routers/me.py).

Slated to relocate under the Admin Portal (as /admin/download and
friends) in a later roadmap step -- these routes themselves won't change,
just where the frontend links to them from.
"""
from __future__ import annotations

import asyncio

import os
import string
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from .. import upload_safety
from .. import ip_privacy, proxy
from ..config import get_settings, save_overrides
from ..logging_setup import apply_logging
from ..library_manager import library_manager
from ..media_tools import UPLOAD_ENCODERS, available_encoders
from ..models import (
    DownloadDirRequest, DownloadSettingsRequest, ReencodeSettingsRequest, UploadSettingsRequest, PlaybackSettingsRequest, LoggingSettingsRequest, ProxySettingsRequest,
    RepairSyncRequest, BrowseMkdirRequest, DuplicateScanSettingsRequest,
    FeaturesSettingsRequest,
)
from ..security import require_permission, AuthContext
from ._common import safe_folder_name

router = APIRouter()


@router.get("/api/settings")
def get_app_settings(user: AuthContext = Depends(require_permission("settings.manage"))):
    settings = get_settings()
    return {
        "download": {
            "output_dir": str(settings.output_dir),
            "cache_dir": str(settings.cache_dir),
            "max_concurrent_downloads": settings.download.max_concurrent_downloads,
            "max_retries": settings.download.max_retries,
            "chunk_size_kb": settings.download.chunk_size_kb,
            "download_funscripts": settings.download.download_funscripts,
            "save_metadata_json": settings.download.save_metadata_json,
            "min_free_percent": settings.download.min_free_percent,
        },
        "disk": library_manager.disk_guard_status(),
        "reencode": settings.reencode.model_dump(),
        "features": settings.features.model_dump(),
        "playback": settings.playback.model_dump(),
        "upload": {**settings.upload.model_dump(), "scanner": (upload_safety.scanner() or {}).get("name"),
                   "virustotal_configured": bool(settings.virustotal_api_key)},
        "performers": {**settings.performers.model_dump(), "tpdb_configured": bool(settings.tpdb_api_key)},
        "logging": {**settings.logging.model_dump(), "log_dir": str(settings.log_dir)},
        "proxy": {**settings.proxy.model_dump(exclude={"url", "username", "password"}),
                  "url": settings.proxy.url or "", "username": settings.proxy.username or "",
                  "password_set": bool(settings.proxy.password)},
        "duplicate_scan": settings.duplicate_scan.model_dump(),
        "system": {
            "cpu_count": os.cpu_count() or 1,
            # Which re-encode codecs the installed ffmpeg can produce.
            "encoders": {codec: enc in available_encoders() for codec, enc in UPLOAD_ENCODERS.items()},
        },
    }


@router.post("/api/settings/duplicate-scan")
def set_duplicate_scan_settings(payload: DuplicateScanSettingsRequest, user: AuthContext = Depends(require_permission("duplicates.manage"))):
    """Duplicate-scan settings: matcher tuning plus which events start a
    scan (scan_after_*). The recurring scan is a Scheduler system job; the
    explicit "Run scan now" action always works."""
    values = payload.model_dump(exclude_none=True)
    legacy = values.pop("auto_enabled", None)
    if legacy is not None:
        for key in ("scan_after_download", "scan_after_reencode", "scan_after_repair"):
            values.setdefault(key, legacy)
    save_overrides(duplicate_scan=values)
    return {"ok": True}


@router.post("/api/settings/download")
async def set_download_settings(payload: DownloadSettingsRequest, user: AuthContext = Depends(require_permission("settings.manage"))):
    save_overrides(download=payload.model_dump())
    library_manager.check_disk_space()  # the limit may have changed
    library_manager._ensure_worker()  # "Downloads at once" changed: start any new lanes
    return {"ok": True}


@router.post("/api/settings/proxy")
async def set_proxy_settings(payload: ProxySettingsRequest, user: AuthContext = Depends(require_permission("settings.manage"))):
    """Where the proxy is used (its address is a credential, saved with PUT /api/credentials)."""
    save_overrides(proxy=payload.model_dump())
    library_manager._ensure_worker()  # start (or let finish) download lanes for the new mode
    return {"ok": True}


@router.get("/api/settings/proxy/status")
def proxy_status(user: AuthContext = Depends(require_permission("settings.manage"))):
    return {**proxy.status(), "lanes": library_manager.download_lanes_status(), "mode": proxy.for_sites()}


@router.post("/api/settings/proxy/test")
async def proxy_test(user: AuthContext = Depends(require_permission("settings.manage"))):
    """Direct connection vs proxy: works?, address seen, latency, speed (takes up to ~40 s)."""
    return await proxy.speed_test()


@router.post("/api/settings/logging")
def set_logging_settings(payload: LoggingSettingsRequest, user: AuthContext = Depends(require_permission("settings.manage"))):
    """Applied at once, no restart. With logging switched off, nothing --
    this change included -- is recorded."""
    save_overrides(logging=payload.model_dump())
    ip_privacy.reset()
    active = apply_logging()
    if payload.enabled:
        logger.info("Logging settings changed by {}: level {}, outputs {}", user.username, payload.level,
                    ", ".join(k for k, on in active.items() if on) or "none")
    return {"ok": True, "active": active}


@router.post("/api/settings/upload/scanner-test")
async def test_upload_scanner(user: AuthContext = Depends(require_permission("settings.manage"))):
    """Scans a small harmless file and shows exactly what the antivirus answered."""
    return await asyncio.to_thread(upload_safety.scanner_test)


@router.post("/api/settings/upload")
def set_upload_settings(payload: UploadSettingsRequest, user: AuthContext = Depends(require_permission("settings.manage"))):
    save_overrides(upload=payload.model_dump())
    return {"ok": True}


@router.post("/api/settings/playback")
def set_playback_settings(payload: PlaybackSettingsRequest, user: AuthContext = Depends(require_permission("settings.manage"))):
    save_overrides(playback=payload.model_dump())
    return {"ok": True}


@router.post("/api/settings/features")
def set_features(payload: FeaturesSettingsRequest, user: AuthContext = Depends(require_permission("settings.manage"))):
    """Switches re-encoding / sharing on or off everywhere (2.22.0)."""
    changes = payload.model_dump(exclude_none=True)
    save_overrides(features=changes)
    library_manager.db._note_change(["features"])  # open pages hide / show it right away
    for name, on in changes.items():
        logger.info("{} switched {} by {}", "Re-encoding" if name == "reencode" else "Sharing", "on" if on else "off", user.username)
    return {"ok": True, "features": get_settings().features.model_dump()}


@router.post("/api/settings/reencode")
def set_reencode_settings(payload: ReencodeSettingsRequest, user: AuthContext = Depends(require_permission("settings.manage"))):
    save_overrides(reencode=payload.model_dump())
    return {"ok": True}


@router.post("/api/settings/download-dir")
async def set_download_dir(payload: DownloadDirRequest, user: AuthContext = Depends(require_permission("settings.manage"))):
    try:
        result = await library_manager.change_download_dir(payload.new_dir, payload.move_existing)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/api/settings/cache-dir")
async def set_cache_dir(payload: DownloadDirRequest, user: AuthContext = Depends(require_permission("settings.manage"))):
    """Moves settings.download.cache_dir (thumbnails/previews/metadata
    JSON) to a new location. Reuses DownloadDirRequest ({new_dir,
    move_existing}) since the shape is identical to the download-dir
    endpoint above -- see library_manager.change_cache_dir() for the actual
    move logic, which mirrors change_download_dir()."""
    try:
        result = await library_manager.change_cache_dir(payload.new_dir, payload.move_existing)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/api/settings/sync-metadata")
async def sync_metadata(user: AuthContext = Depends(require_permission("settings.manage"))):
    """Re-reads each completed video's metadata JSON on disk and backfills
    tags/performers/tech info onto its library entry -- for videos that were
    downloaded before those fields existed, or with metadata saving off."""
    return await library_manager.sync_metadata_from_disk()


@router.post("/api/settings/repair")
async def repair_all(payload: RepairSyncRequest, user: AuthContext = Depends(require_permission("videos.maintain"))):
    """Triggers a BULK repair-all pass, mutually exclusive with any other
    INVASIVE operation currently running (e.g. a duplicate scan) -- see
    CoreMixin._try_acquire_invasive_lock(). Returns 409 specifically for
    that case, so the frontend can show a clear "something else is
    running" message rather than a generic error."""
    try:
        count = library_manager.start_guarded_repair_all(payload.max_concurrent)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"queued": count}


@router.get("/api/settings/browse-dirs")
def browse_dirs(path: str = "", user: AuthContext = Depends(require_permission("settings.manage"))):
    """Server-side folder listing that backs the folder-browser modal
    (browse.js) -- used for both the download folder and the cache folder.
    No path yet -> show drive roots on Windows, or "/" on POSIX. A path can
    be anything Path() understands, including a UNC network path like
    "\\\\NAS\\Media" on Windows (resolved and listed the same way as a
    local path -- no special-casing needed, pathlib already handles UNC).
    Note this only *lists* a share you already know the path to; it can't
    discover/browse available network shares the way Explorer's network
    neighborhood can."""
    if not path:
        if os.name == "nt":
            drives = [f"{d}:\\" for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
            return {"current": "", "parent": None, "entries": drives}
        else:
            path = "/"

    try:
        current = Path(path).expanduser().resolve()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid path: {exc}") from exc

    if not current.exists() or not current.is_dir():
        raise HTTPException(status_code=400, detail="Path does not exist or is not a directory")

    try:
        entries = sorted(
            (str(p) for p in current.iterdir() if p.is_dir() and not p.name.startswith(".")),
            key=str.lower,
        )
    except PermissionError:
        entries = []

    parent = str(current.parent) if current.parent != current else None
    return {"current": str(current), "parent": parent, "entries": entries}


@router.post("/api/settings/browse-dirs/mkdir")
def browse_dirs_mkdir(payload: BrowseMkdirRequest, user: AuthContext = Depends(require_permission("settings.manage"))):
    """Creates a new subfolder inside parent_path -- backs the "+ New
    folder" button in the folder-browser modal (browse.js), so a download
    or cache folder that doesn't exist yet can be created without leaving
    the app. parent_path must already exist; the new folder must not
    already exist (no silent no-op / merge into an existing folder)."""
    try:
        parent = Path(payload.parent_path).expanduser().resolve()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid parent path: {exc}") from exc
    if not parent.exists() or not parent.is_dir():
        raise HTTPException(status_code=400, detail="Parent folder does not exist")

    try:
        safe_name = safe_folder_name(payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    new_dir = parent / safe_name
    if new_dir.exists():
        raise HTTPException(status_code=400, detail="A folder with that name already exists")

    try:
        new_dir.mkdir(parents=False, exist_ok=False)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Could not create folder: {exc}") from exc

    return {"created": str(new_dir)}
