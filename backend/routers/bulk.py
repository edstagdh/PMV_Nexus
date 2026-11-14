"""
Library -> Select: actions on several picked videos at once (2.18.0).

  POST /api/bulk/songs     {ids}                                   songs.manage
  POST /api/bulk/repair    {ids}                                   videos.maintain
  POST /api/bulk/reencode  {ids, codec?}                           videos.maintain

Each queues the same work as the single-video action; a video that can't take
part (already in that codec, already queued, file missing...) is skipped and
reported: {"queued": n, "skipped": [{"id", "title", "reason"}]}. One request (and
one line in the log) for the whole selection, not one per video.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import get_settings
from ..library_manager import library_manager
from ..models import ReencodeOptions
from ..security import AuthContext, require_any

router = APIRouter()

MAX_IDS = 1000
_PERMISSION = {"songs": "songs.manage", "repair": "videos.maintain", "reencode": "videos.maintain"}


class BulkRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1, max_length=MAX_IDS)
    codec: Optional[str] = Field(None, pattern="^(hevc|av1)$")
    options: Optional[ReencodeOptions] = None  # this re-encode's settings (2.22.0)
    method: Optional[str] = Field(None, pattern="^(shazam|shazam_gemini|both|gemini)$")  # songs (2.34.0)


def _title(video_id: str) -> str:
    item = library_manager._items.get(video_id)
    return (item.title or item.url) if item else video_id


@router.post("/api/bulk/{action}")
async def bulk_action(action: str, body: BulkRequest,
                      user: AuthContext = Depends(require_any("songs.manage", "videos.maintain"))):
    permission = _PERMISSION.get(action)
    if permission is None:
        raise HTTPException(status_code=404, detail="Unknown action")
    if not user.has_permission(permission):
        raise HTTPException(status_code=403, detail="You don't have permission for this")
    ids = list(dict.fromkeys(body.ids))
    features = get_settings().features
    if action == "reencode" and not features.reencode:
        raise HTTPException(status_code=403, detail="That's switched off in Server Settings")

    if action == "songs":
        result = library_manager.start_song_scan_many(ids, user.username, body.method)
        return {"queued": result["queued"], "skipped": [], "skipped_count": result["skipped"]}

    queued, skipped = 0, []
    for video_id in ids:
        try:
            if action == "repair":
                library_manager.request_repair(video_id)
            else:
                await library_manager.request_reencode(video_id, codec=body.codec,
                                                       options=body.options.model_dump() if body.options else None)
            queued += 1
        except (ValueError, KeyError) as exc:
            skipped.append({"id": video_id, "title": _title(video_id), "reason": str(exc) or "not possible"})
    return {"queued": queued, "skipped": skipped[:50], "skipped_count": len(skipped)}
