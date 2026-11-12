"""
Admin -> Logs: read access to the searchable app log (log_store.py).
Needs the "logs.view" permission (admins have it through "*").
"""
from __future__ import annotations

from typing import Optional

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Response

from .. import ip_privacy
from ..config import get_settings
from ..log_store import get_log_store
from ..security import AuthContext, require_permission

router = APIRouter()


def _store():
    store = get_log_store()
    if store is None:
        raise HTTPException(status_code=503, detail="The log store is not running")
    return store


@router.get("/api/admin/logs")
def list_logs(
    before_id: Optional[int] = None,
    after_id: Optional[int] = None,
    limit: int = 200,
    min_level: str = "INFO",
    kind: Optional[str] = None,
    category: Optional[str] = None,
    event: Optional[str] = None,
    user: Optional[str] = None,
    ip: Optional[str] = None,
    source: Optional[str] = None,
    q: Optional[str] = None,
    _auth: AuthContext = Depends(require_permission("logs.view")),
):
    """Newest first, paged with before_id; after_id returns only newer entries (live tail).
    category "!a,b" means everything except those categories."""
    return _store().query(before_id=before_id, after_id=after_id, limit=limit, min_level=min_level, kind=kind,
                          category=category, event=event, user=user, ip=ip, source=source,
                          q=(q or "").strip() or None)


@router.get("/api/admin/logs/export")
def export_logs(
    min_level: str = "INFO",
    kind: Optional[str] = None,
    category: Optional[str] = None,
    event: Optional[str] = None,
    user: Optional[str] = None,
    ip: Optional[str] = None,
    q: Optional[str] = None,
    _auth: AuthContext = Depends(require_permission("logs.view")),
):
    """The matching entries (up to 100,000, newest first) as JSON Lines: one JSON object per line."""
    rows = _store().export(min_level=min_level, kind=kind, category=category, event=event, user=user, ip=ip,
                           q=(q or "").strip() or None)
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else "")
    name = time.strftime("pmv-nexus-logs-%Y%m%d-%H%M%S.jsonl")
    return Response(body, media_type="application/x-ndjson",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/api/admin/logs/facets")
def log_facets(min_level: str = "INFO", _auth: AuthContext = Depends(require_permission("logs.view"))):
    """Categories, events and users that appear in the log, with counts, for the filter menus."""
    cfg = get_settings().logging
    return {**_store().facets(min_level), "ip_mode": ip_privacy.current_mode(),
            "recording": cfg.enabled and cfg.app_log, "level": cfg.level}
