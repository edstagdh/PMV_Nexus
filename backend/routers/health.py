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
from fastapi import APIRouter, Response

from ..config import get_settings
from ..library_manager import library_manager
from ..pmv_client import get_client
from ..sites import list_site_keys

router = APIRouter()


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

    # PMV session presence (not validity -- just "is there a saved cookie jar")
    # -- one health-check entry PER SITE, since a session can exist for one
    # site but not another, and overall_ok below does
    # all(c["ok"] for c in checks.values()) uniformly across every entry.
    for key in list_site_keys():
        # 2.27.0: a site sign-in is only needed when reading web pages is turned on.
        signed_in = get_client(key).has_saved_session()
        checks[f"pmv_session_{key}"] = {"ok": signed_in or not settings.download.website_fallback, "signed_in": signed_in}

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
