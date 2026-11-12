"""Sync with PMVHaven (Admin -> Library Maintenance). The scheduled variant is
the Scheduler job type "site_sync"; see library_mixins/site_sync_worker.py."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..library_manager import library_manager
from ..library_mixins.core import JobBlockedError
from ..security import AuthContext, require_permission

router = APIRouter()


# async: request_site_sync() schedules a task on the running event loop,
# which a sync route (run in the threadpool) does not have.
class SiteSyncRequest(BaseModel):
    dry_run: bool = True


@router.get("/api/library/site-sync")
def site_sync_status(user: AuthContext = Depends(require_permission("maintenance.run"))):
    return library_manager.get_site_sync_status()


@router.post("/api/library/site-sync")
async def start_site_sync(payload: SiteSyncRequest, user: AuthContext = Depends(require_permission("maintenance.run"))):
    try:
        library_manager.request_site_sync(payload.dry_run)
    except JobBlockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}
