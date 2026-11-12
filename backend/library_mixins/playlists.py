"""Playlists (2.2.0): per-user lists of library videos.

- "manual" playlists keep their own order (playlist_items rows, reorderable).
- "smart" playlists store a library query (the same parameters as
  GET /api/library, e.g. {"performers": ["Ava Rose"], "sort_key": "ratings"})
  and are filled from it every time they're read, so new matching downloads
  appear by themselves.
- visibility "private": only the owner sees it; "public": every user can see
  and play it, only the owner (or the admin) can change it.

Every write notes a change "playlist:<id>" in LibraryDB, so open pages get a
live update through /api/events like any other library change.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Optional

# GET /api/library parameters a smart playlist may store. statuses/page are
# fixed (finished videos, everything).
SMART_QUERY_KEYS = {
    "search", "tags", "performers", "codecs", "creators", "match_all", "min_duration", "max_duration",
    "min_rating", "gone", "qualities", "min_quality", "orientation", "sort_key", "sort_dir",
    "p_cups", "p_hair", "p_eyes", "p_age_min", "p_age_max", "p_enhanced",
}
_LIST_KEYS = {"tags", "performers", "codecs", "creators", "qualities", "p_cups", "p_hair", "p_eyes"}
MAX_NAME = 120
MAX_DESCRIPTION = 2000


class PlaylistError(ValueError):
    """User-facing problem (shown as a 400/403/404 by the router)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def clean_smart_query(raw: Optional[dict]) -> dict:
    """Keeps only known library-query keys, with the right shapes."""
    out: dict = {}
    for key, value in (raw or {}).items():
        if key not in SMART_QUERY_KEYS or value is None or value == "" or value == []:
            continue
        if key in _LIST_KEYS:
            value = [str(v) for v in (value if isinstance(value, list) else [value]) if str(v).strip()]
            if not value:
                continue
        elif key in ("match_all", "gone", "p_enhanced"):
            value = value in (True, "true", "1", 1)
        elif key in ("min_duration", "max_duration", "p_age_min", "p_age_max"):
            value = int(float(value))
        elif key == "min_rating":
            value = float(value)
        else:
            value = str(value)
        out[key] = value
    return out


FAVORITES = "favorites"  # system_key of each user's built-in Favorites playlist


class PlaylistsMixin:

    # ---------------------------------------------------------------- reading
    def _playlist_or_404(self, playlist_id: str) -> dict:
        row = self.db.get_playlist(playlist_id)
        if not row:
            raise PlaylistError("Playlist not found", 404)
        return row

    # Someone's Favorites belong to them alone: always private, and not shown to
    # or changeable by anyone else -- the admin included (2.10.1).
    @staticmethod
    def _can_see(row: dict, user) -> bool:
        if row.get("system_key") == FAVORITES:
            return row["owner_id"] == user.id
        return row["visibility"] == "public" or row["owner_id"] == user.id or user.has_permission("*")

    @staticmethod
    def _can_edit(row: dict, user) -> bool:
        if row.get("system_key") == FAVORITES:
            return row["owner_id"] == user.id
        return row["owner_id"] == user.id or user.has_permission("*")

    def _done(self, item_id: str):
        it = self._items.get(item_id)
        return it if it is not None and it.status == "done" else None

    def query_all_item_ids(self, query: dict) -> list[str]:
        """Every finished video matching a library query, in its sort order."""
        ids: list[str] = []
        page = 1
        while True:
            res = self.query_items(page=page, page_size=200, statuses=["done"], **clean_smart_query(query))
            ids.extend(i["id"] for i in res["items"])
            if page >= res["total_pages"]:
                return ids
            page += 1

    def _playlist_ids(self, row: dict) -> list[str]:
        if row["kind"] == "smart":
            return self.query_all_item_ids(json.loads(row["smart_query"] or "{}"))
        return [i for i in self.db.playlist_item_ids(row["id"]) if self._done(i)]

    def _playlist_public(self, row: dict, user, owners: dict[str, str], covers: Optional[list[str]] = None,
                         count: Optional[int] = None) -> dict:
        return {
            "id": row["id"], "name": row["name"], "description": row["description"],
            "visibility": row["visibility"], "kind": row["kind"],
            "smart_query": json.loads(row["smart_query"]) if row["smart_query"] else None,
            "owner_id": row["owner_id"], "owner": owners.get(row["owner_id"], "(deleted user)"),
            "mine": row["owner_id"] == user.id, "can_edit": self._can_edit(row, user),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "item_count": count if count is not None else row.get("item_count", 0),
            "cover_ids": covers or [],
            # 2.18.0: the cover chosen -- the page shows cover_ids as a collage unless this says otherwise
            "cover": self._cover_info(row),
            "system": row.get("system_key"),  # "favorites": built in, can't be deleted or renamed
        }

    # ---------------------------------------------------------------- covers (2.18.0)
    def _cover_file(self, playlist_id: str):
        """Your own cover picture: data/playlist_covers/<id>.webp (found by id, so it
        still works after a backup is restored into another folder)."""
        return self.settings.library_db_file.parent / "playlist_covers" / f"{playlist_id}.webp"

    def _remove_cover_file(self, playlist_id: str) -> None:
        try:
            os.remove(self._cover_file(playlist_id))
        except OSError:
            pass

    def _cover_info(self, row: dict) -> dict:
        mode = row.get("cover_mode") or "collage"
        if mode == "video" and row.get("cover_item") and self._done(row["cover_item"]):
            return {"mode": "video", "item_id": row["cover_item"]}
        path = self._cover_file(row["id"])
        if mode == "image" and path.is_file():
            return {"mode": "image", "url": f"/api/playlists/{row['id']}/cover/image?v={int(path.stat().st_mtime)}"}
        return {"mode": "collage"}

    def set_playlist_cover(self, playlist_id: str, user, mode: str, item_id: Optional[str] = None) -> dict:
        row = self._editable(playlist_id, user)
        if mode == "video":
            if not item_id or item_id not in self._playlist_ids(row):
                raise PlaylistError("Pick one of the playlist's videos")
            self.db.update_playlist(playlist_id, {"cover_mode": "video", "cover_item": item_id})
        elif mode == "collage":
            self.db.update_playlist(playlist_id, {"cover_mode": "collage"})
        else:
            raise PlaylistError("Unknown cover type")
        if row.get("cover_path"):  # your picture isn't the cover any more: don't keep (and back up) it
            self._remove_cover_file(row["id"])
            self.db.update_playlist(playlist_id, {"cover_path": None})
        return self._cover_info(self._playlist_or_404(playlist_id))

    async def set_playlist_cover_image(self, playlist_id: str, user, data: bytes) -> dict:
        """Your own picture as the cover, converted to WebP (backend/images.py); kept in
        data/playlist_covers (in every backup)."""
        from .. import images
        row = self._editable(playlist_id, user)
        try:
            webp = await asyncio.to_thread(images.to_webp, data)
        except ValueError:
            raise PlaylistError("That file isn't a picture the app can read (JPEG, PNG, WebP…)") from None
        dest = self._cover_file(row["id"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(dest.write_bytes, webp)
        self.db.update_playlist(playlist_id, {"cover_mode": "image", "cover_path": str(dest)})
        return self._cover_info(self._playlist_or_404(playlist_id))

    def playlist_cover_file(self, playlist_id: str, user) -> Optional[str]:
        row = self._playlist_or_404(playlist_id)
        if not self._can_see(row, user):
            raise PlaylistError("Playlist not found", 404)
        path = self._cover_file(row["id"])
        return str(path) if path.is_file() else None

    def _owner_names(self) -> dict[str, str]:
        return {u.id: u.username for u in self.db.list_users()}

    def favorites_playlist(self, user) -> dict:
        """The user's built-in Favorites playlist (made on first use)."""
        for row in self.db.list_playlists():
            if row["owner_id"] == user.id and row.get("system_key") == FAVORITES:
                return row
        now = time.time()
        row = {"id": uuid.uuid4().hex, "owner_id": user.id, "name": "Favorites", "description": "",
               "visibility": "private", "kind": "manual", "smart_query": None, "created_at": now, "updated_at": now,
               "system_key": FAVORITES}
        self.db.create_playlist(row)
        return {**row, "item_count": 0}

    def set_favorite(self, item_id: str, user, on: bool) -> bool:
        """Adds the video to / removes it from the user's Favorites. Returns the new state."""
        if not self._done(item_id):
            raise PlaylistError("Only downloaded videos can be favorites")
        fav = self.favorites_playlist(user)
        if on:
            self.db.add_playlist_items(fav["id"], [item_id])
        else:
            self.db.remove_playlist_items(fav["id"], [item_id])
        return on

    def list_playlists(self, user) -> list[dict]:
        self.favorites_playlist(user)
        owners = self._owner_names()
        firsts = self.db.playlist_first_items()
        out = []
        for row in self.db.list_playlists():
            if not self._can_see(row, user):
                continue
            if row["kind"] == "smart":
                ids = self._playlist_ids(row)
                out.append(self._playlist_public(row, user, owners, ids[:4], len(ids)))
            else:
                covers = [i for i in firsts.get(row["id"], []) if self._done(i)]
                out.append(self._playlist_public(row, user, owners, covers))
        # Your Favorites first, then the rest by name (as the database sorts them).
        out.sort(key=lambda p: 0 if p["system"] == FAVORITES and p["mine"] else 1)
        return out

    def get_playlist(self, playlist_id: str, user) -> dict:
        row = self._playlist_or_404(playlist_id)
        if not self._can_see(row, user):
            raise PlaylistError("Playlist not found", 404)
        ids = self._playlist_ids(row)
        items = [self._items[i].model_dump() for i in ids if self._done(i)]
        return {**self._playlist_public(row, user, self._owner_names(), ids[:4], len(items)), "items": items}

    def playlists_for_item(self, item_id: str, user) -> list[str]:
        """Ids of the user's own manual playlists that contain item_id."""
        self.favorites_playlist(user)
        mine = {r["id"] for r in self.db.list_playlists() if r["owner_id"] == user.id and r["kind"] == "manual"}
        return sorted(self.db.playlists_containing(item_id) & mine)

    # ---------------------------------------------------------------- writing
    def _editable(self, playlist_id: str, user, manual: bool = False) -> dict:
        row = self._playlist_or_404(playlist_id)
        if not self._can_see(row, user):
            raise PlaylistError("Playlist not found", 404)
        if not self._can_edit(row, user):
            raise PlaylistError("Only the owner can change this playlist", 403)
        if manual and row["kind"] != "manual":
            raise PlaylistError("A smart playlist is filled by its filter; edit the filter instead")
        return row

    @staticmethod
    def _clean_fields(name=None, description=None, visibility=None) -> dict:
        out = {}
        if name is not None:
            name = name.strip()
            if not name:
                raise PlaylistError("Give the playlist a name")
            out["name"] = name[:MAX_NAME]
        if description is not None:
            out["description"] = description.strip()[:MAX_DESCRIPTION]
        if visibility is not None:
            if visibility not in ("private", "public"):
                raise PlaylistError("Visibility must be private or public")
            out["visibility"] = visibility
        return out

    def create_playlist(self, user, name: str, description: str = "", visibility: str = "private",
                        kind: str = "manual", smart_query: Optional[dict] = None,
                        item_ids: Optional[list[str]] = None) -> dict:
        if kind not in ("manual", "smart"):
            raise PlaylistError("Unknown playlist type")
        fields = self._clean_fields(name, description, visibility)
        query = clean_smart_query(smart_query) if kind == "smart" else None
        now = time.time()
        row = {
            "id": uuid.uuid4().hex, "owner_id": user.id, "name": fields["name"],
            "description": fields.get("description", ""), "visibility": fields.get("visibility", "private"),
            "kind": kind, "smart_query": json.dumps(query) if query is not None else None,
            "created_at": now, "updated_at": now,
        }
        self.db.create_playlist(row)
        if kind == "manual" and item_ids:
            self.db.add_playlist_items(row["id"], [i for i in item_ids if self._done(i)])
        return self.get_playlist(row["id"], user)

    def update_playlist(self, playlist_id: str, user, name=None, description=None, visibility=None,
                        smart_query: Optional[dict] = None) -> dict:
        row = self._editable(playlist_id, user)
        if row.get("system_key") and name is not None and name.strip() != row["name"]:
            raise PlaylistError(f"“{row['name']}” is built in and can't be renamed")
        fields = self._clean_fields(name, description, visibility)
        if row.get("system_key") == FAVORITES and fields.get("visibility", "private") != "private":
            raise PlaylistError("Favorites are always private")
        if smart_query is not None:
            if row["kind"] != "smart":
                raise PlaylistError("Only a smart playlist has a filter")
            fields["smart_query"] = json.dumps(clean_smart_query(smart_query))
        fields["updated_at"] = time.time()
        self.db.update_playlist(playlist_id, fields)
        return self.get_playlist(playlist_id, user)

    def delete_playlist(self, playlist_id: str, user) -> None:
        row = self._editable(playlist_id, user)
        if row.get("system_key"):
            raise PlaylistError(f"“{row['name']}” is built in and can't be deleted")
        self.db.delete_playlist(playlist_id)
        self._remove_cover_file(row["id"])

    def add_to_playlist(self, playlist_id: str, user, item_ids: Optional[list[str]] = None,
                        query: Optional[dict] = None) -> dict:
        """Appends videos (by id, or everything matching a library query --
        every page, not just the visible one). Already-present videos are
        skipped. Returns {"added", "skipped"}."""
        self._editable(playlist_id, user, manual=True)
        ids = self.query_all_item_ids(query) if query is not None else [i for i in (item_ids or []) if self._done(i)]
        added = self.db.add_playlist_items(playlist_id, ids)
        return {"added": added, "skipped": len(ids) - added}

    def remove_from_playlist(self, playlist_id: str, user, item_ids: list[str]) -> int:
        self._editable(playlist_id, user, manual=True)
        return self.db.remove_playlist_items(playlist_id, item_ids)

    def reorder_playlist(self, playlist_id: str, user, item_ids: list[str]) -> None:
        self._editable(playlist_id, user, manual=True)
        self.db.set_playlist_order(playlist_id, item_ids)
