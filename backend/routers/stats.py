"""
Admin-only Stats routes -- backs the Admin Portal's Stats page (System +
Library breakdowns, plus historical trend data). See backend/
library_mixins/stats_worker.py for what each of these actually computes.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..library_manager import library_manager
from ..security import require_permission, AuthContext

router = APIRouter()


@router.get("/api/stats/live")
def get_live_stats(user: AuthContext = Depends(require_permission("stats.view"))):
    return {
        "system": library_manager.compute_system_stats(),
        "library": library_manager.compute_library_stats(),
    }


@router.get("/api/stats/history")
def get_stats_history(range: str = Query("30d", pattern="^(7d|30d|90d|1y|all)$"),
                      user: AuthContext = Depends(require_permission("stats.view"))):
    """Library growth, activity per day, watching (top videos / performers / tags, per user) and disk space over a range."""
    return library_manager.stats_history(range)


@router.get("/api/stats/machine")
def get_machine_stats(user: AuthContext = Depends(require_permission("stats.view"))):
    """The quick part of the Stats page, polled every few seconds: CPU, memory, network, disks, workers."""
    return {"system": library_manager.compute_system_stats(), "machine": library_manager.live_machine()}


@router.get("/api/stats/trends")
def get_stats_trends(days: int = Query(30, ge=1, le=365), user: AuthContext = Depends(require_permission("stats.view"))):
    return library_manager.get_stats_trends(days)


@router.post("/api/stats/snapshot")
async def capture_snapshot_now(user: AuthContext = Depends(require_permission("stats.view"))):
    result = await library_manager.capture_stats_snapshot()
    return {"ok": True, **result}
