"""
Songs (2.14.0, library_mixins/songs.py) -- all need songs.manage:

  POST /api/library/{id}/songs/scan          {"method"?} start finding songs in one video (in the background)
  GET  /api/library/{id}/songs               its scan state, last scan, pending suggestions, current music
  GET  /api/songs                            Management -> Songs: pending suggestions per video + counts
  GET  /api/songs/methods                    the default song-finding method and whether Gemini's key is saved
  POST /api/songs/scan-now                   the Scheduler job, run once now (videos without music)
  POST /api/songs/{suggestion_id}/add        {"label"?} -> into the video's Music (hand edit)
  POST /api/songs/{suggestion_id}/dismiss
  GET/POST /api/settings/songs               the songs: settings (settings.manage)
  POST /api/credentials/test/gemini          checks the Gemini API key (admin; lists models, no quota used)
"""
from __future__ import annotations

import asyncio
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import gemini
from ..config import get_settings, save_overrides
from ..library_manager import library_manager
from ..security import AuthContext, require_permission

router = APIRouter()
_songs = require_permission("songs.manage")


Method = Literal["shazam", "shazam_gemini", "both", "gemini"]  # config.SongsConfig.method (2.34.0)


class ScanRequest(BaseModel):
    method: Optional[Method] = None   # None = the setting


class AddSong(BaseModel):
    label: Optional[str] = Field(None, max_length=200)


class SongsSettings(BaseModel):
    interval: int = Field(30, ge=15, le=300)
    max_samples: int = Field(12, ge=1, le=40)
    shazam_retry: bool = True
    method: Method = "shazam"
    gemini_max_samples: int = Field(3, ge=1, le=20)
    per_run: int = Field(20, ge=1, le=500)
    rescan_days: int = Field(30, ge=0, le=3650)


class KeyTest(BaseModel):
    api_key: Optional[str] = None


@router.post("/api/library/{video_id}/songs/scan")
async def scan_video(video_id: str, payload: Optional[ScanRequest] = None, user: AuthContext = Depends(_songs)):
    method = payload.method if payload else None
    if method and method != "shazam" and not get_settings().gemini_api_key:
        raise HTTPException(status_code=400, detail="Gemini needs its API key first (Server Settings → Credentials → Google Gemini)")
    try:
        return library_manager.start_song_scan(video_id, user.username, method)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/library/{video_id}/songs")
def video_songs(video_id: str, user: AuthContext = Depends(_songs)):
    try:
        return {**library_manager.song_status(video_id), "method": get_settings().songs.method,
                "gemini": bool(get_settings().gemini_api_key)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Video not found") from None


@router.get("/api/songs")
def overview(user: AuthContext = Depends(_songs)):
    out = library_manager.songs_overview()
    out["gemini"] = bool(get_settings().gemini_api_key)
    out["settings"] = get_settings().songs.model_dump()
    return out


@router.get("/api/songs/methods")
def methods(user: AuthContext = Depends(_songs)):
    """The default way of finding songs and whether Gemini can be used (2.34.0)."""
    return {"method": get_settings().songs.method, "gemini": bool(get_settings().gemini_api_key)}


@router.post("/api/songs/scan-now")
async def scan_now(user: AuthContext = Depends(_songs)):
    if library_manager._songs_lock().locked():
        raise HTTPException(status_code=409, detail="Songs are already being looked for — see Tasks")
    asyncio.get_running_loop().create_task(_run_job_quietly())
    return {"ok": True}


async def _run_job_quietly() -> None:
    try:
        await library_manager.run_song_scan_job()
    except BaseException:  # noqa: BLE001 -- the Tasks page and the log show what happened
        pass


@router.post("/api/songs/{suggestion_id}/add")
async def add(suggestion_id: int, payload: AddSong, user: AuthContext = Depends(_songs)):
    try:
        return await library_manager.add_song_suggestion(suggestion_id, user.username, payload.label)
    except KeyError:
        raise HTTPException(status_code=404, detail="That suggestion was already handled") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/songs/{suggestion_id}/dismiss")
def dismiss(suggestion_id: int, user: AuthContext = Depends(_songs)):
    try:
        return library_manager.dismiss_song_suggestion(suggestion_id, user.username)
    except KeyError:
        raise HTTPException(status_code=404, detail="That suggestion was already handled") from None


@router.post("/api/settings/songs")
def save_settings(payload: SongsSettings, user: AuthContext = Depends(require_permission("settings.manage"))):
    save_overrides(songs=payload.model_dump())
    return {"ok": True}


@router.post("/api/credentials/test/gemini")
async def test_gemini(payload: KeyTest, user: AuthContext = Depends(require_permission("settings.manage"))):
    key = (payload.api_key or "").strip() or get_settings().gemini_api_key
    if not key:
        return {"ok": False, "message": "No API key saved yet"}
    return await gemini.test_key(key)
