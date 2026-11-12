"""
Performer profiles (ThePornDB): the profile page, photos, and for the
admin the review list and the matching actions. See library_mixins/performers.py.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import activity, tpdb
from ..config import save_overrides
from ..library_manager import library_manager
from ..library_mixins.performers import PerformerError
from ..security import AuthContext, require_permission

router = APIRouter()
_viewer = require_permission("library.view")
_manager = require_permission("performers.manage")


class NameBody(BaseModel):
    name: str


class LinkBody(BaseModel):
    name: str
    tpdb_id: str


class PerformerSettings(BaseModel):
    merge_aliases: bool = True
    refresh_days: int = 30


async def _run(coro):
    try:
        return await coro
    except PerformerError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except tpdb.TPDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/api/performers/profile")
def performer_profile(name: str, user: AuthContext = Depends(_viewer)):
    """Her profile by any of her names: details, photo, names in the library, similar performers."""
    return library_manager.performer_profile(name)


@router.get("/api/performers/{performer_id}/photo")
def performer_photo(performer_id: str, n: int = Query(0, ge=0, le=80), user: AuthContext = Depends(_viewer)):
    path = library_manager.performer_photo_path(performer_id, n)
    if not path:
        raise HTTPException(status_code=404, detail="No photo")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


@router.get("/api/performers/browse")
def performers_browse(user: AuthContext = Depends(_viewer)):
    """The Performers page (2.17.0): everyone in the library, with photo or placeholder gender."""
    return {"performers": library_manager.performers_browse(), "configured": tpdb.configured()}


class GenderBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    gender: str = Field(..., pattern="^(female|male)$")


@router.post("/api/performers/gender")
def performers_set_gender(body: GenderBody, user: AuthContext = Depends(_manager)):
    """Placeholder silhouette of a performer without a ThePornDB profile."""
    try:
        return library_manager.set_performer_gender(body.name, body.gender)
    except PerformerError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.get("/api/performers/overview")
def performers_overview(user: AuthContext = Depends(_manager)):
    return library_manager.performers_overview()


@router.get("/api/performers/search")
async def performers_search(q: str = Query(min_length=1), user: AuthContext = Depends(_manager)):
    """ThePornDB search, for picking a profile by hand."""
    return {"results": await _run(library_manager.search_tpdb(q))}


@router.post("/api/performers/link")
async def performers_link(body: LinkBody, user: AuthContext = Depends(_manager)):
    await _run(library_manager.link_performer(body.name, body.tpdb_id))
    return library_manager.performer_profile(body.name)


@router.post("/api/performers/skip")
def performers_skip(body: NameBody, user: AuthContext = Depends(_manager)):
    library_manager.skip_performer(body.name)
    return {"ok": True}


@router.post("/api/performers/split")
async def performers_split(body: NameBody, user: AuthContext = Depends(_manager)):
    """Undo a wrong alias merge for this name."""
    await _run(library_manager.split_performer_name(body.name))
    return library_manager.performer_profile(body.name)


@router.post("/api/performers/{performer_id}/refresh")
async def performers_refresh(performer_id: str, user: AuthContext = Depends(_manager)):
    return await _run(library_manager.refresh_performer(performer_id))


# ---- photos added by hand (2.16.0): the raw image as the body, converted to WebP
_MAX_PHOTO_BYTES = 30 * 1024 * 1024


@router.put("/api/performers/{performer_id}/photos/upload")
async def performers_add_photo(performer_id: str, request: Request, user: AuthContext = Depends(_manager)):
    if not (request.headers.get("content-type") or "").lower().startswith("image/"):
        raise HTTPException(status_code=415, detail="Send a picture")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > _MAX_PHOTO_BYTES:
            raise HTTPException(status_code=413, detail="That picture is too big (30 MB at most)")
    try:
        return await library_manager.add_custom_photo(performer_id, bytes(data), user.username)
    except PerformerError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.get("/api/performers/{performer_id}/custom-photo/{photo_id}")
def performers_custom_photo(performer_id: str, photo_id: str, user: AuthContext = Depends(_viewer)):
    path = library_manager.custom_photo_file(performer_id, photo_id)
    if not path:
        raise HTTPException(status_code=404, detail="No photo")
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})


@router.delete("/api/performers/{performer_id}/photos/{photo_id}")
def performers_remove_photo(performer_id: str, photo_id: str, user: AuthContext = Depends(_manager)):
    try:
        library_manager.remove_custom_photo(performer_id, photo_id)
    except PerformerError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return {"ok": True}


class MainPhoto(BaseModel):
    photo_id: str | None = None


@router.post("/api/performers/{performer_id}/photos/main")
def performers_main_photo(performer_id: str, body: MainPhoto, user: AuthContext = Depends(_manager)):
    """A photo added by hand shown first (her main photo); none: ThePornDB's again."""
    try:
        library_manager.set_main_custom_photo(performer_id, body.photo_id)
    except PerformerError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/api/performers/photos/convert")
async def performers_convert_photos(user: AuthContext = Depends(_manager)):
    """Converts the saved photos to WebP (2.15.0), in the background (Tasks)."""
    count = library_manager.performer_photos_to_convert()
    if not count:
        return {"ok": True, "count": 0}
    if any(a.get("kind") == "performer_photos" for a in activity.running()):
        raise HTTPException(status_code=409, detail="Already converting — see Tasks")
    asyncio.get_running_loop().create_task(_convert_quietly())
    return {"ok": True, "count": count}


async def _convert_quietly() -> None:
    try:
        await library_manager.convert_performer_photos()
    except BaseException:  # noqa: BLE001 -- Tasks and the log show it
        pass


@router.post("/api/performers/match-now")
async def performers_match_now(retry_none: bool = False, user: AuthContext = Depends(_manager)):
    """Runs the Scheduler job "Match & refresh performers" now (with retry_none
    the names not found before are looked up again too)."""
    if not tpdb.configured():
        raise HTTPException(status_code=400, detail="Add the ThePornDB API key in Server settings → Credentials first")
    if retry_none:
        library_manager._perf_load()
        for row in list(library_manager._perf_names.values()):
            if row["status"] == "none":
                library_manager._perf_names.pop(row["name_key"], None)
    job = library_manager.performer_job()
    if not job:
        raise HTTPException(status_code=404, detail="The performer job is missing from the Scheduler — restart the app")
    try:
        library_manager.request_scheduled_job_run_now(job["id"])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/api/settings/performers")
def performer_settings(body: PerformerSettings, user: AuthContext = Depends(require_permission("settings.manage"))):
    save_overrides(performers={**body.model_dump(), "refresh_days": max(7, min(365, body.refresh_days))})
    library_manager.invalidate_performer_names()
    return {"ok": True}
