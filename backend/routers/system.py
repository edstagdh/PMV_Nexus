"""App-level routes: version/update check, docs (guide, changelog, license),
tool status and backups. See backend/app_info.py and backend/backup.py."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from .. import app_info, backup
from ..config import get_settings
from ..library_manager import library_manager
from ..security import AuthContext, get_current_user, require_permission

router = APIRouter()


class BackupRequest(BaseModel):
    include_cache: bool = False
    include_secrets: bool = False


class BackupPassword(BaseModel):
    password: str | None = None  # opening a protected backup; never logged or stored


# 423 Locked: "this backup needs its password" (the page asks for it and tries again).
# Not 401, which the UI reads as "signed out".
def _locked(exc: backup.PasswordNeeded) -> HTTPException:
    return HTTPException(status_code=423, detail=str(exc))


@router.get("/api/system/info")
def get_system_info(user: AuthContext = Depends(get_current_user)):
    info = {**app_info.system_info(), "update": library_manager.get_update_status()}
    if not user.has_permission("settings.manage"):
        # Exact Python / OS / FFmpeg versions are for the admin only (they help an attacker pick an exploit).
        for key in ("python", "platform", "ffmpeg"):
            info.pop(key, None)
    return info


@router.post("/api/system/check-update")
async def check_for_update(user: AuthContext = Depends(get_current_user)):
    """Manual check (header version label). Any signed-in user may click it;
    repeated clicks within a minute return the cached result instead of
    hitting GitHub again."""
    return await library_manager.run_update_check(force=False)


@router.get("/api/system/docs/{key}", response_class=PlainTextResponse)
def get_doc(key: str, user: AuthContext = Depends(get_current_user)):
    text = app_info.read_doc(key)
    if text is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return PlainTextResponse(text)


@router.get("/api/system/tools")
def get_tool_status(user: AuthContext = Depends(require_permission("settings.manage"))):
    # Sync route -> FastAPI runs it in its threadpool, so the short
    # subprocess calls inside don't block the event loop.
    return app_info.tool_status()


# ------------------------------------------------------------------- backups
@router.get("/api/backups")
def list_backups(user: AuthContext = Depends(require_permission("settings.manage"))):
    settings = get_settings()
    return {
        "backups": backup.list_backups(settings),
        "dir": str(settings.backup_dir),
        "keep": settings.backup.keep,
        "pending_restore": backup.get_pending_restore(),
        "password_set": bool(backup.saved_password()),
    }


@router.post("/api/backups")
async def create_backup(payload: BackupRequest, user: AuthContext = Depends(require_permission("settings.manage"))):
    try:
        return await library_manager.run_backup(
            include_cache=payload.include_cache, include_secrets=payload.include_secrets, trigger="manual",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Backup failed: {exc}")


@router.post("/api/backups/{name}/test")
async def test_backup(name: str, payload: BackupPassword | None = None,
                      user: AuthContext = Depends(require_permission("settings.manage"))):
    """Restore test: opens the backup like a restore would (zip, database, settings) without changing anything."""
    try:
        return await library_manager.run_backup_test(name, password=payload.password if payload else None)
    except backup.PasswordNeeded as exc:
        raise _locked(exc) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Backup not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/backups/{name}/download")
def download_backup(name: str, user: AuthContext = Depends(require_permission("settings.manage"))):
    try:
        path = backup.resolve_backup(get_settings(), name)
    except (ValueError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="Backup not found")
    return FileResponse(str(path), media_type="application/zip", filename=path.name)


@router.delete("/api/backups/{name}")
def delete_backup(name: str, user: AuthContext = Depends(require_permission("settings.manage"))):
    try:
        backup.delete_backup(get_settings(), name)
    except (ValueError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="Backup not found")
    return {"ok": True}


@router.post("/api/backups/{name}/restore")
def stage_restore(name: str, payload: BackupPassword | None = None,
                  user: AuthContext = Depends(require_permission("settings.manage"))):
    try:
        summary = backup.stage_restore(get_settings(), name, payload.password if payload else None)
    except backup.PasswordNeeded as exc:
        raise _locked(exc) from exc
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "backup": summary}


@router.put("/api/backups-password")
def set_backup_password(payload: BackupPassword, user: AuthContext = Depends(require_permission("settings.manage"))):
    """Save the password new backups are protected with; empty removes it (2.25.0)."""
    try:
        backup.set_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "password_set": bool(payload.password)}


@router.delete("/api/backups-restore")
def cancel_restore(user: AuthContext = Depends(require_permission("settings.manage"))):
    backup.cancel_pending_restore()
    return {"ok": True}
