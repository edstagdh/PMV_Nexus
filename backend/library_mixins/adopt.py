"""Maintenance -> Untracked files -> "Add to the library" (3.10.0).

A video file the app downloaded earlier carries its site video id in its name
("<creator> - <date> - <title> [<24 hex>].mp4"). When the database lost track of it
(a restore from an older backup, a lost database), this makes it a library video
again without downloading it: the details come from the site's API (as for a new
download), the file stays where it is, and leftovers of the same video in the cache
folder -- its thumbnail, preview clip, metadata JSON, a .funscript next to the
video -- are linked again. Then the usual Repair/Update fills in what's still
missing (thumbnail, preview, metadata JSON, technical details, fingerprint), and
seekbar previews / video frames are made.

A video the site no longer has is still added, from its file name and its old
metadata JSON when that's there (marked as removed from the site).

Only files the Untracked files list shows qualify: inside the download folder, not
pointed to by any video, a video file, with an id that isn't in the library yet.
Nothing is moved, renamed or deleted.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from ..i18n import L
from .. import activity, sources
from ..models import LibraryItem
from ._helpers import extract_tags_performers_music_from_meta_dict
from .untracked_files import _kind, _norm

ID_IN_NAME = re.compile(r"\[([a-f0-9]{24})\]", re.IGNORECASE)
API_GAP = 1.0   # seconds between two site look-ups


def video_id_in_name(name: str) -> Optional[str]:
    m = ID_IN_NAME.search(name)
    return m.group(1).lower() if m else None


def _from_name(path: Path) -> dict:
    """"Creator - 2024-05-01 - Title [id].mp4" -> creator, upload date, title (best effort)."""
    stem = ID_IN_NAME.sub("", path.stem).strip()
    parts = [p.strip() for p in stem.split(" - ")]
    out = {"creator": path.parent.name, "title": stem or path.stem, "upload_date": None}
    if len(parts) >= 3 and re.fullmatch(r"\d{4}-\d{2}-\d{2}|unknown-date", parts[1]):
        out.update(creator=parts[0] or out["creator"], title=" - ".join(parts[2:]) or out["title"],
                   upload_date=None if parts[1] == "unknown-date" else parts[1])
    return out


class AdoptMixin:
    _adopt_task: Optional[asyncio.Task] = None
    _adopt_stop = False
    _adopt_queued: set = set()

    def untracked_video_info(self, path: Path) -> dict:
        """Extra fields for an untracked video in the list: its id, and whether it can be added
        (or, 3.11.0, linked back: the library has the video but its own file is missing)."""
        from .file_check import file_state
        vid = video_id_in_name(path.name)
        if not vid:
            return {"video_id": None, "can_add": False}
        item = self._items.get(vid)
        if item is not None:
            if item.status == "done" and file_state(item.video_path):
                return {"video_id": vid, "can_add": False, "can_link": True, "why_not": "library_file_missing"}
            if item.status == "merged":
                return {"video_id": vid, "can_add": False, "why_not": "merged"}
            return {"video_id": vid, "can_add": False, "why_not": "in_library"}
        return {"video_id": vid, "can_add": True}

    def untracked_companion_info(self, path: Path, kind: str) -> Optional[dict]:
        """3.11.1: a thumbnail / preview / details JSON / funscript carrying the id of a library
        video: the one it lacks (link it back), or an older copy (it uses another file)."""
        from .file_check import COMPANION_FIELDS, file_state
        field = COMPANION_FIELDS.get(kind)
        vid = video_id_in_name(path.name)
        item = self._items.get(vid) if vid and field else None
        if item is None or item.status == "merged":
            return None
        if kind == "thumbnail" and path.stem.lower().endswith(("_original", "_custom")):
            return {"video_id": vid, "why_not": "older_copy"}
        if file_state(getattr(item, field, None)):
            return {"video_id": vid, "can_link": True, "why_not": "library_lacks"}
        return {"video_id": vid, "why_not": "older_copy"}

    def _leftovers(self, vid: str, untracked: list[Path]) -> dict:
        """Untracked companions of video `vid` (same [id] in the name), by what they are."""
        found: dict = {}
        for p in untracked:
            if video_id_in_name(p.name) != vid:
                continue
            kind = _kind(p)
            stem = p.stem.lower()
            if kind == "thumbnail" and not stem.endswith(("_original", "_custom")):
                found.setdefault("thumbnail", p)
            elif kind == "preview":
                found.setdefault("preview", p)
            elif kind == "metadata":
                found.setdefault("metadata", p)
            elif kind == "funscript":
                found.setdefault("funscript", p)
        return found

    def adopt_running(self) -> bool:
        return bool(self._adopt_task and not self._adopt_task.done())

    def start_adopting(self, paths: list[str], user_id: Optional[str]) -> dict:
        """Checks the picked files now (refusals come back at once) and adds the rest in the
        background, one job on Tasks. ValueError when it's already running."""
        if self.adopt_running():
            raise ValueError(L("Videos are already being added -- see Tasks"))
        out_root = _norm(self.settings.output_dir)
        tracked, keep = self._tracked_paths()
        ok, refused, seen = [], [], set()
        for raw in paths:
            path = Path(raw)
            key = _norm(path)
            why = None
            if not key.startswith(out_root + os.sep):
                why = L("not in the download folder")
            elif key in tracked or key in keep:
                why = L("already part of the library")
            elif not path.is_file():
                why = L("not there any more")
            elif _kind(path) != "video":
                why = L("not a video file")
            else:
                vid = video_id_in_name(path.name)
                if not vid:
                    why = L("no video id in its name")
                elif vid in self._items:
                    why = L("that video is already in the library (another file)")
                elif vid in seen:
                    why = L("another picked file is the same video")
                else:
                    seen.add(vid)
                    ok.append((vid, path))
            if why:
                refused.append({"path": raw, "reason": why})
        if ok:
            self._adopt_stop = False
            self._adopt_queued = {_norm(p) for _, p in ok}   # Untracked files won't delete these meanwhile
            self._adopt_task = asyncio.create_task(self._adopt_all(ok, user_id))
        logger.info("Add to the library: {} file(s) queued, {} refused", len(ok), len(refused))
        return {"queued": len(ok), "refused": refused}

    async def _adopt_all(self, todo: list[tuple[str, Path]], user_id: Optional[str]) -> None:
        state = {"done": 0, "added": 0, "removed": 0, "failed": 0, "current": None}

        def progress() -> dict:
            return {"done": state["done"], "total": len(todo), "detail": state["current"]}

        def stop() -> None:
            self._adopt_stop = True

        act = activity.start("adopt_files", L("Adding untracked videos to the library"), source="manual", progress=progress)
        act.stopper = stop
        try:
            # leftovers in both folders, looked up once
            tracked, keep = self._tracked_paths()
            untracked = await asyncio.to_thread(self._untracked_with_ids, tracked, keep)
            for i, (vid, path) in enumerate(todo):
                if self._adopt_stop:
                    break
                state["current"] = path.name
                try:
                    found_on_site = await self._adopt_one(vid, path, untracked, user_id)
                    state["added"] += 1
                    if not found_on_site:
                        state["removed"] += 1
                except Exception as exc:  # noqa: BLE001 -- one file mustn't stop the rest
                    state["failed"] += 1
                    logger.warning("Add to the library: {} failed: {}", path, exc)
                state["done"] += 1
                if i + 1 < len(todo):
                    await asyncio.sleep(API_GAP)
            if state["added"]:
                self.request_duplicate_scan_after("download")
            parts = [f"{state['added']} added"]
            if state["removed"]:
                parts.append(f"{state['removed']} no longer on the site (added from the file name)")
            if state["failed"]:
                parts.append(f"{state['failed']} failed (see the log)")
            if self._adopt_stop:
                act.finish("cancelled", (f"Stopped by {act.stopped_by} · " if act.stopped_by else "") + " · ".join(parts))
            else:
                act.finish("failed" if state["failed"] and not state["added"] else "success", " · ".join(parts))
        except BaseException as exc:
            logger.exception("Add to the library stopped by an error: {}", exc)
            act.finish("failed", L("Stopped by an error (see the log)"))
            if not isinstance(exc, Exception):
                raise
        finally:
            self._adopt_queued = set()

    def _untracked_with_ids(self, tracked: set, keep: set) -> list[Path]:
        """BLOCKING: untracked files with a video id in their name, in both folders."""
        out = []
        for root in (self.settings.output_dir, self.settings.cache_dir):
            if not root.is_dir():
                continue
            for dirpath, dirs, names in os.walk(root):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for name in names:
                    if video_id_in_name(name):
                        p = Path(dirpath) / name
                        if _norm(p) not in tracked and _norm(p) not in keep:
                            out.append(p)
        return out

    async def _site_details(self, vid: str):
        """(source key, page url, VideoMetadata, raw JSON) from the first site that has it, else None."""
        for key, src in sources.all_sources().items():
            api = getattr(src, "api_details", None)
            site = self.settings.sites.get(key)
            if api is None or site is None:
                continue
            page = f"{site.base_url.rstrip('/')}/video/{vid}"
            found = await api(vid, page)
            if found:
                return key, page, found[0], found[1]
        return None

    async def _adopt_one(self, vid: str, path: Path, untracked: list[Path], user_id: Optional[str]) -> bool:
        """Adds one file; True when the site still has the video."""
        if vid in self._items:
            raise ValueError("already in the library")
        if not path.is_file():
            raise ValueError("the file isn't there any more")
        leftovers = self._leftovers(vid, untracked)
        named = _from_name(path)
        old_json: dict = {}
        if leftovers.get("metadata"):
            try:
                old_json = json.loads(leftovers["metadata"].read_text(encoding="utf-8"))
            except (OSError, ValueError):
                old_json = {}
        found = await self._site_details(vid)
        first_site = next(iter(self.settings.sites), "pmvhaven")
        if found:
            site_key, page, meta, raw = found
            tags, performers, music = extract_tags_performers_music_from_meta_dict(raw or meta.to_dict())
            fields = dict(url=meta.page_url or page, site=site_key, title=meta.title or named["title"],
                          creator=meta.creator_name or named["creator"], creators=list(meta.creators or []),
                          tags=tags, performers=performers, music=music, ratings=meta.rating_percent,
                          description=meta.description, upload_date=meta.upload_date or named["upload_date"],
                          timeline_thumbnails=meta.timeline_thumbnails or [], site_status="available")
        else:
            site = self.settings.sites.get(first_site)
            base = site.base_url.rstrip("/") if site else "https://pmvhaven.com"
            fields = dict(url=f"{base}/video/{vid}", site=first_site, title=old_json.get("title") or named["title"],
                          creator=named["creator"], upload_date=named["upload_date"], site_status="removed",
                          site_synced_at=time.time())
            if old_json:
                tags, performers, music = extract_tags_performers_music_from_meta_dict(old_json)
                fields.update(tags=tags, performers=performers, music=music, description=old_json.get("description"))
        try:
            acquired = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            acquired = datetime.now(timezone.utc).isoformat()
        item = LibraryItem(id=vid, status="done", video_path=str(path), added_by=user_id, download_datetime=acquired,
                           progress_percent=100, progress_label="Complete", **fields)
        if leftovers.get("thumbnail"):
            item.thumbnail_path = str(leftovers["thumbnail"])
            item.thumbnail_updated_at = time.time()
        if leftovers.get("preview"):
            item.preview_path = str(leftovers["preview"])
            item.preview_updated_at = time.time()
        if leftovers.get("metadata"):
            item.metadata_path = str(leftovers["metadata"])
        if leftovers.get("funscript"):
            item.funscript_path = str(leftovers["funscript"])
        async with self._lock:
            if vid in self._items:
                raise ValueError("already in the library")
            self._items[vid] = item
            self.db.save_item(item)
        logger.info("Added to the library from an untracked file: '{}' [{}] {} ({}; linked again: {})", item.title, vid, path,
                    "on the site" if found else "not on the site any more", ", ".join(sorted(leftovers)) or "nothing")
        # the rest as for any video: thumbnail / preview / metadata JSON / tech info / fingerprint,
        # seekbar previews, frames under the player
        self.request_repair(vid)
        self.request_storyboard(vid)
        self.request_frames(vid, first=False)
        return bool(found)
