"""
Health check endpoint -- reports SQLite reachability, PMV session presence
per configured site, ffmpeg availability, and free disk space on the
output directory. Deliberately unauthenticated (no Depends(get_current_user))
-- meant to be hit by monitoring/uptime tools, not a logged-in person.
"""
from __future__ import annotations

import json
import shutil

from loguru import logger
from fastapi import APIRouter, Header, HTTPException, Request, Response

from ..config import get_settings
from ..library_manager import library_manager

router = APIRouter()


@router.post("/api/local/stop")
def local_stop(request: Request, x_control_token: str = Header("")):
    """Windows Setup closes the app before an update (backend/app_control.py):
    only from this computer, with the token from runtime/control.json."""
    from .. import app_control
    host = request.client.host if request.client else ""
    forwarded = any(h in request.headers for h in ("x-forwarded-for", "x-real-ip", "forwarded"))
    if host not in ("127.0.0.1", "::1") or forwarded or not app_control.check(x_control_token):
        raise HTTPException(status_code=403, detail="Not allowed")
    app_control.request_stop("asked by Setup (an update)")
    return {"ok": True}


@router.get("/api/health")
def health_check():
    settings = get_settings()
    checks: dict[str, dict] = {}

    # SQLite library DB -- cheap read, confirms the file is reachable/not locked
    try:
        library_manager.db.ping()
        checks["database"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        checks["database"] = {"ok": False, "error": str(exc)}

    # ffmpeg on PATH
    checks["ffmpeg"] = {"ok": shutil.which("ffmpeg") is not None}

    # disk space on the output dir
    try:
        usage = shutil.disk_usage(settings.output_dir)
        free_gb = usage.free / (1024 ** 3)
        checks["disk"] = {"ok": free_gb > 1.0, "free_gb": round(free_gb, 1)}
    except Exception as exc:  # noqa: BLE001
        checks["disk"] = {"ok": False, "error": str(exc)}

    overall_ok = all(c["ok"] for c in checks.values())
    status_code = 200 if overall_ok else 503
    # Anyone can call this (no sign-in), so it says only what's ok -- error
    # texts (paths) and free space go to the log instead.
    for name, c in checks.items():
        if not c["ok"] and c.get("error"):
            logger.warning("Health check '{}' failed: {}", name, c["error"])
    public = {name: {"ok": c["ok"]} for name, c in checks.items()}
    return Response(
        content=json.dumps({"status": "ok" if overall_ok else "degraded", "checks": public}),
        media_type="application/json",
        status_code=status_code,
    )
