"""
Admin-only routes for the generic background-job Scheduler -- lets an
admin configure WHEN various maintenance tasks run (duplicate scan,
library repair, and future job types -- see backend/library_mixins/
scheduler_worker.py's JOB_REGISTRY) instead of each one having its own
bespoke always-on timer. Every route requires settings.manage, same as
every other admin-config page reached through the Admin Portal.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from .. import activity, i18n

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
    runs = library_manager.get_job_run_history(job_id)
    for r in runs if isinstance(runs, list) else []:
        _readable_result(r)
    return runs


def _readable_result(run: dict) -> None:
    """3.7.0: "result_text", the run's result in the reader's language (the raw result_summary stays)."""
    stored = run.pop("result_msg", None)
    if stored:
        try:
            run["result_text"] = i18n.from_stored(json.loads(stored), run.get("result_summary") or "")
            return
        except ValueError:
            pass
    if run.get("status") == "success" and run.get("result_summary"):
        try:
            run["result_text"] = activity.summarize({k: v for k, v in json.loads(run["result_summary"]).items() if k != "verbose_log"})
        except (ValueError, AttributeError):
            pass
