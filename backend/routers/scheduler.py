"""
Admin-only routes for the generic background-job Scheduler -- lets an
admin configure WHEN various maintenance tasks run (duplicate scan,
library repair, and future job types -- see backend/library_mixins/
scheduler_worker.py's JOB_REGISTRY) instead of each one having its own
bespoke always-on timer. Every route requires settings.manage, same as
every other admin-config page reached through the Admin Portal.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..library_manager import library_manager
from ..library_mixins.scheduler_worker import JOB_REGISTRY
from ..models import ScheduledJobCreateRequest, ScheduledJobUpdateRequest
from ..security import require_permission, AuthContext

router = APIRouter()


@router.get("/api/scheduler/job-types")
def list_job_types(user: AuthContext = Depends(require_permission("settings.manage"))):
    return [
        {"job_type": key, "label": entry["label"], "category": entry.get("category", "invasive")}
        for key, entry in JOB_REGISTRY.items()
    ]


@router.get("/api/scheduler/jobs")
def list_jobs(user: AuthContext = Depends(require_permission("settings.manage"))):
    return library_manager.list_scheduled_jobs()


@router.post("/api/scheduler/jobs")
def create_job(payload: ScheduledJobCreateRequest, user: AuthContext = Depends(require_permission("settings.manage"))):
    try:
        return library_manager.create_scheduled_job(
            payload.job_type, payload.name, payload.enabled, payload.schedule_mode, payload.schedule_config,
            payload.job_config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/api/scheduler/jobs/{job_id}")
def update_job(job_id: str, payload: ScheduledJobUpdateRequest, user: AuthContext = Depends(require_permission("settings.manage"))):
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    try:
        return library_manager.update_scheduled_job(job_id, updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/scheduler/jobs/{job_id}")
def delete_job(job_id: str, user: AuthContext = Depends(require_permission("settings.manage"))):
    try:
        library_manager.delete_scheduled_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/api/scheduler/jobs/{job_id}/run-now")
async def run_job_now(job_id: str, user: AuthContext = Depends(require_permission("settings.manage"))):
    # MUST be async, not a plain sync def -- FastAPI dispatches a sync
    # route handler onto a worker thread (via anyio.to_thread), which has
    # no running asyncio event loop at all. request_scheduled_job_run_now()
    # calls asyncio.create_task() internally, which requires being called
    # from inside a running loop -- an async route, by contrast, FastAPI
    # runs directly on the event loop thread itself, which is what
    # actually fixes this (calling the same, still-synchronous, method
    # from here works fine now that it's on the right thread).
    try:
        library_manager.request_scheduled_job_run_now(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"triggered": True}


@router.get("/api/scheduler/jobs/{job_id}/runs")
def get_job_runs(job_id: str, user: AuthContext = Depends(require_permission("settings.manage"))):
    return library_manager.get_job_run_history(job_id)
