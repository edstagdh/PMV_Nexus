"""
Currently Playing (2.20.0, backend/now_playing.py).

  POST /api/now-playing  {tab, item_id|null}   library.stream   the player: "this tab plays X" / "stopped"
  GET  /api/now-playing                         live.view        the videos being played right now

The page only ever gets the videos: no names, no positions, no times.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..hardening import scrub_item
from ..library_manager import library_manager
from ..now_playing import now_playing
from ..security import AuthContext, require_permission

router = APIRouter()

# The page's live updates: the list changing is a library "change" called "playing".
now_playing.set_on_change(lambda: library_manager.db._note_change(["playing"]))


class PlayingReport(BaseModel):
    tab: str = Field(..., min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    item_id: Optional[str] = Field(None, max_length=64)


@router.post("/api/now-playing")
def report_playing(body: PlayingReport, user: AuthContext = Depends(require_permission("library.stream"))):
    item = library_manager.get(body.item_id) if body.item_id else None
    # Unknown or unfinished videos are treated as "stopped".
    now_playing.report(f"{user.id}:{body.tab}", item.id if item and item.status == "done" else None)
    return {"ok": True}


@router.get("/api/now-playing")
def get_now_playing(user: AuthContext = Depends(require_permission("live.view"))):
    items = []
    for vid in now_playing.video_ids():
        item = library_manager.get_item_dict(vid)
        if item and item.get("status") == "done":
            items.append(scrub_item(item, user))
    return {"items": library_manager.with_watch(items, user)}
