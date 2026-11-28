"""
Faces (3.12.0, library_mixins/faces.py) -- Management -> Faces needs performers.manage:

  GET  /api/faces                                   overview: models, counts, what's running, the settings
  POST /api/faces/scan            {"again"?}         start "Finding faces" (videos not read yet; again: all of them)
  POST /api/faces/stop
  GET  /api/faces/matches         ?status=&performer=&min_score=&q=&sort=&desc=&offset=&limit=   (3.13.0: sorted, filtered)
  POST /api/faces/matches/{video_id}/{performer_id} {"action": approve | reject | undo}
  POST /api/faces/matches/bulk    {"pairs": [[video, performer]], "action"}   (3.13.0: a page at once)
  POST /api/faces/approve-above   {"score"}          every waiting suggestion at or above it
  POST /api/faces/remove-below    {"score"}          (3.13.0) every waiting suggestion below it is removed
  GET  /api/faces/crop/{detection_id}                the face found in the video (small JPEG)
  GET  /api/faces/picture/{detection_id}?kind=face|frame&size=   (3.13.0) bigger: the face, or the picture it's in
  GET  /api/library/{id}/faces                       (library.view) performers approvals added; waiting ones for managers
  GET  /api/performers/faces?name=                   (library.view) waiting suggestions with this performer (count)
  POST /api/settings/faces                           the faces: settings (settings.manage)

3.13.0: Approve adds the performer to the video's performers; Not them / Undo of an approval takes
it off again (library_mixins/faces.py).
"""
from __future__ import annotations

import asyncio
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from loguru import logger
from pydantic import BaseModel, Field

from ..i18n import L
from .. import faces
from ..config import FacesConfig, get_settings, save_overrides
from ..library_manager import library_manager
from ..security import AuthContext, require_permission

router = APIRouter()
_manage = require_permission("performers.manage")
_view = require_permission("library.view")


class ScanRequest(BaseModel):
    again: bool = False


class Decision(BaseModel):
    action: Literal["approve", "reject", "undo"]


class BulkDecision(BaseModel):
    pairs: list[tuple[str, str]] = Field(..., min_length=1, max_length=500)
    action: Literal["approve", "reject", "undo"]


class ScoreLine(BaseModel):
    score: float = Field(..., ge=0, le=100)


def _check_ready() -> None:
    if not get_settings().faces.enabled:
        raise HTTPException(status_code=400, detail=L("Faces is switched off (Management → Faces → Settings)"))
    st = faces.models_status()
    if not st["ok"]:
        raise HTTPException(status_code=400, detail=L("The face models are missing or damaged: {files}",
                                                      files=", ".join(st["missing"] + st["bad"])))


@router.get("/api/faces")
async def overview(user: AuthContext = Depends(_manage)):
    return {**library_manager.faces_overview(), "settings": get_settings().faces.model_dump()}


@router.post("/api/faces/scan")
async def scan_faces(payload: Optional[ScanRequest] = None, user: AuthContext = Depends(_manage)):
    _check_ready()
    again = bool(payload and payload.again)
    started = library_manager.request_face_scan("manual", None, again=again)
    return {"ok": True, "started": started}


@router.post("/api/faces/stop")
async def stop_face_scan(user: AuthContext = Depends(_manage)):
    library_manager.stop_face_scan()
    return {"ok": True}


@router.get("/api/faces/matches")
async def matches(status: str = "suggested", performer: Optional[str] = None, min_score: float = Query(0, ge=0, le=100),
                  q: str = Query("", max_length=200), sort: str = "score", desc: bool = True,
                  offset: int = Query(0, ge=0), limit: int = Query(60, ge=1, le=500), user: AuthContext = Depends(_manage)):
    try:
        return library_manager.face_matches(status, performer_id=performer, min_score=min_score, q=q, sort=sort, desc=desc,
                                            offset=offset, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/api/faces/matches/bulk")
async def decide_face_matches(payload: BulkDecision, user: AuthContext = Depends(_manage)):
    try:
        return await library_manager.decide_face_matches([tuple(p) for p in payload.pairs], payload.action, user.username)
    except KeyError:
        raise HTTPException(status_code=404, detail=L("Those matches aren't there any more")) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/api/faces/matches/{video_id}/{performer_id}")
async def decide_face_match(video_id: str, performer_id: str, payload: Decision, user: AuthContext = Depends(_manage)):
    try:
        return await library_manager.decide_face_match(video_id, performer_id, payload.action, user.username)
    except KeyError:
        raise HTTPException(status_code=404, detail=L("That match isn't there any more")) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/api/faces/approve-above")
async def approve_faces_above(payload: ScoreLine, user: AuthContext = Depends(_manage)):
    return await library_manager.approve_face_matches_above(payload.score, user.username)


@router.post("/api/faces/remove-below")
async def remove_faces_below(payload: ScoreLine, user: AuthContext = Depends(_manage)):
    return library_manager.remove_face_matches_below(payload.score, user.username)


@router.get("/api/faces/crop/{detection_id}")
def face_crop(detection_id: int, user: AuthContext = Depends(_manage)):
    data = library_manager.face_crop(detection_id)
    if data is None:
        raise HTTPException(status_code=404, detail=L("Not found"))
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/api/faces/picture/{detection_id}")
async def face_picture(detection_id: int, kind: Literal["face", "frame"] = "face", size: int = Query(240, ge=64, le=1280),
                       user: AuthContext = Depends(_manage)):
    data = await asyncio.to_thread(library_manager.face_picture, detection_id, kind, size)
    if data is None:
        raise HTTPException(status_code=404, detail=L("Not found"))
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/api/library/{video_id}/faces")
async def video_faces(video_id: str, user: AuthContext = Depends(_view)):
    try:
        return library_manager.faces_for_video(video_id, user.has_permission("performers.manage"))
    except KeyError:
        raise HTTPException(status_code=404, detail=L("Video not found")) from None


@router.get("/api/performers/faces")
async def performer_faces(name: str = Query(..., min_length=1, max_length=300), user: AuthContext = Depends(_view)):
    if not user.has_permission("performers.manage"):
        return {"waiting": 0, "performer_id": None}
    return library_manager.faces_waiting_for_performer(name)


@router.post("/api/settings/faces")
async def set_faces_settings(payload: FacesConfig, user: AuthContext = Depends(require_permission("settings.manage"))):
    before = get_settings().faces
    save_overrides(faces=payload.model_dump())
    logger.info("Faces settings changed by {}: {}", user.username,
                ", ".join(f"{k} {getattr(before, k)} -> {v}" for k, v in payload.model_dump().items() if getattr(before, k) != v) or "nothing")
    compare = ("suggest_score", "auto_approve_score", "min_support", "margin", "max_per_video")
    if payload.enabled and any(getattr(before, k) != getattr(payload, k) for k in compare):
        library_manager.request_face_scan("manual", [], rematch=True)   # the videos read so far, compared again
    library_manager.db._note_change(["faces"])
    return {"ok": True, "faces": get_settings().faces.model_dump()}
