"""Playlists API (see backend/library_mixins/playlists.py). Every signed-in
user (library.view) has their own playlists; public ones are readable by
everyone, editable only by their owner or the admin."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..hardening import scrub_item
from ..library_manager import library_manager
from ..library_mixins.playlists import PlaylistError
from ..security import AuthContext, require_permission

router = APIRouter()
_viewer = require_permission("library.view")


class PlaylistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    visibility: str = Field("private", pattern="^(private|public)$")
    kind: str = Field("manual", pattern="^(manual|smart)$")
    smart_query: Optional[dict] = None   # smart playlists: GET /api/library parameters
    item_ids: list[str] = []             # manual playlists: videos to start with


class PlaylistUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = None
    visibility: Optional[str] = Field(None, pattern="^(private|public)$")
    smart_query: Optional[dict] = None


class PlaylistAdd(BaseModel):
    item_ids: Optional[list[str]] = None
    query: Optional[dict] = None  # add every video matching this library query (all pages)


class PlaylistItems(BaseModel):
    item_ids: list[str]


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PlaylistError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.get("/api/playlists")
def list_playlists(user: AuthContext = Depends(_viewer)):
    return {"playlists": _run(library_manager.list_playlists, user)}


@router.post("/api/playlists")
def create_playlist(body: PlaylistCreate, user: AuthContext = Depends(_viewer)):
    return _run(library_manager.create_playlist, user, body.name, body.description, body.visibility,
                body.kind, body.smart_query, body.item_ids)


@router.get("/api/playlists/for-item/{item_id}")
def playlists_for_item(item_id: str, user: AuthContext = Depends(_viewer)):
    """Which of the user's own manual playlists already contain this video."""
    return {"playlist_ids": _run(library_manager.playlists_for_item, item_id, user)}


class FavoriteToggle(BaseModel):
    item_id: str
    on: bool


@router.get("/api/playlists/favorites")
def favorites(item_id: str = "", user: AuthContext = Depends(_viewer)):
    """The user's built-in Favorites playlist id, and whether item_id is in it."""
    fav = _run(library_manager.favorites_playlist, user)
    on = bool(item_id) and fav["id"] in library_manager.db.playlists_containing(item_id)
    return {"id": fav["id"], "favorite": on}


@router.post("/api/playlists/favorites")
def set_favorite(body: FavoriteToggle, user: AuthContext = Depends(_viewer)):
    return {"favorite": _run(library_manager.set_favorite, body.item_id, user, body.on)}


@router.get("/api/playlists/{playlist_id}")
def get_playlist(playlist_id: str, user: AuthContext = Depends(_viewer)):
    out = _run(library_manager.get_playlist, playlist_id, user)
    out["items"] = library_manager.with_watch([scrub_item(i, user) for i in out.get("items") or []], user)  # no server paths; my place in each
    return out


@router.patch("/api/playlists/{playlist_id}")
def update_playlist(playlist_id: str, body: PlaylistUpdate, user: AuthContext = Depends(_viewer)):
    return _run(library_manager.update_playlist, playlist_id, user, body.name, body.description,
                body.visibility, body.smart_query)


@router.delete("/api/playlists/{playlist_id}")
def delete_playlist(playlist_id: str, user: AuthContext = Depends(_viewer)):
    _run(library_manager.delete_playlist, playlist_id, user)
    return {"ok": True}


@router.post("/api/playlists/{playlist_id}/items")
def add_items(playlist_id: str, body: PlaylistAdd, user: AuthContext = Depends(_viewer)):
    if body.item_ids is None and body.query is None:
        raise HTTPException(status_code=400, detail="Send item_ids or a query")
    return _run(library_manager.add_to_playlist, playlist_id, user, body.item_ids, body.query)


@router.post("/api/playlists/{playlist_id}/items/remove")
def remove_items(playlist_id: str, body: PlaylistItems, user: AuthContext = Depends(_viewer)):
    return {"removed": _run(library_manager.remove_from_playlist, playlist_id, user, body.item_ids)}


@router.put("/api/playlists/{playlist_id}/order")
def reorder(playlist_id: str, body: PlaylistItems, user: AuthContext = Depends(_viewer)):
    _run(library_manager.reorder_playlist, playlist_id, user, body.item_ids)
    return {"ok": True}


# ---- covers (2.18.0): the collage of the first 4 videos (default), one video's cover, or your own image
class CoverChoice(BaseModel):
    mode: str = Field(..., pattern="^(collage|video)$")
    item_id: Optional[str] = None


@router.put("/api/playlists/{playlist_id}/cover")
def set_playlist_cover(playlist_id: str, body: CoverChoice, user: AuthContext = Depends(_viewer)):
    return _run(library_manager.set_playlist_cover, playlist_id, user, body.mode, body.item_id)


_MAX_COVER_BYTES = 20 * 1024 * 1024


@router.put("/api/playlists/{playlist_id}/cover/image")
async def upload_playlist_cover(playlist_id: str, request: Request, user: AuthContext = Depends(_viewer)):
    if not (request.headers.get("content-type") or "").lower().startswith("image/"):
        raise HTTPException(status_code=415, detail="Send a picture")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > _MAX_COVER_BYTES:
            raise HTTPException(status_code=413, detail="That picture is too big (20 MB at most)")
    try:
        return await library_manager.set_playlist_cover_image(playlist_id, user, bytes(data))
    except PlaylistError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.get("/api/playlists/{playlist_id}/cover/image")
def playlist_cover_image(playlist_id: str, user: AuthContext = Depends(_viewer)):
    path = _run(library_manager.playlist_cover_file, playlist_id, user)
    if not path:
        raise HTTPException(status_code=404, detail="No cover image")
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})
