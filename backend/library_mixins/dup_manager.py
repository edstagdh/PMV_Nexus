"""Duplicates -> Merged and Not duplicates (3.12.0).

Merged: the copies removed as duplicates, each with the video it was merged into. One can be
brought back (Server Settings -> Duplicate scan -> "Switching a merged pair", or asked each time):
- undo: it becomes a library video again, and the pair is marked "not duplicates";
- swap: it comes back, then the kept video is merged into it (its playlists, favorites, where
  everyone was in it and its watch history move over; its files are removed as for any merge).
Its file is used when it's still in the download folder (a merge leaves a file that was in use);
otherwise it's downloaded again -- only when the site still has it.

Not duplicates: the groups marked "Not duplicates", which can be shown again.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

from loguru import logger

from ..i18n import L
from ..models import LibraryItem

SWAPS_KEY = "dup_swaps"   # app_state: {brought-back id: id to merge into it once it's downloaded}


class DupManagerMixin:
    # ------------------------------------------------------------ lists
    def merged_list(self) -> list[dict]:
        swaps = self.db.get_app_state(SWAPS_KEY, {}) or {}
        out = []
        for item in self._items.values():
            if item.status != "merged":
                continue
            kept = self._items.get(item.merged_into or "")
            out.append({"id": item.id, "url": item.url, "site": item.site, "title": item.merged_title or None,
                        "merged_at": item.merged_at or item.updated_at,
                        "kept": {"id": kept.id, "title": kept.title, "status": kept.status} if kept else None,
                        "swap_pending": item.id in swaps})
        for vid, kept_id in swaps.items():   # brought back, waiting to finish downloading
            item = self._items.get(vid)
            if item and item.status != "merged":
                kept = self._items.get(kept_id)
                out.append({"id": vid, "url": item.url, "site": item.site, "title": item.title, "merged_at": None,
                            "kept": {"id": kept.id, "title": kept.title, "status": kept.status} if kept else None,
                            "swap_pending": True, "coming_back": item.status})
        out.sort(key=lambda r: r["merged_at"] or 9e12, reverse=True)
        return out

    def not_duplicates_list(self) -> list[dict]:
        out = []
        for row in self.db.list_ignored_issues(issue_type="duplicate"):
            ids = row["item_ids"] if isinstance(row["item_ids"], list) else []
            out.append({"item_ids": ids, "items": [{"id": i, "title": getattr(self._items.get(i), "title", None),
                                                    "status": getattr(self._items.get(i), "status", None)} for i in ids]})
        return out

    def show_again(self, item_ids: list[str]) -> None:
        """A group marked "Not duplicates" can be found again by the next scan."""
        self.db.unignore_issue("duplicate", item_ids)
        logger.info("Duplicates: {} can be found as duplicates again", ", ".join(item_ids))

    # ------------------------------------------------------------ bringing one back
    def _leftover_video(self, vid: str) -> Optional[Path]:
        """BLOCKING. A video file in the download folder with this id that no video uses."""
        from .untracked_files import _kind, _norm
        tracked, keep = self._tracked_paths()
        root = _norm(self.settings.output_dir) + os.sep
        for p in self._untracked_with_ids(tracked, keep):
            if _norm(p).startswith(root) and _kind(p) == "video" and self._video_id_of(p.name) == vid and p.is_file() and p.stat().st_size:
                return p
        return None

    async def bring_back_merged(self, video_id: str, mode: str, user_id: Optional[str]) -> dict:
        if mode not in ("undo", "swap"):
            raise ValueError(L("Unknown way to switch"))
        item = self._items.get(video_id)
        if not item or item.status != "merged":
            raise KeyError(video_id)
        kept_id = item.merged_into
        kept = self._items.get(kept_id or "")
        if mode == "swap" and (not kept or kept.status != "done"):
            raise ValueError(L("The video it was merged into isn't in the library any more -- use Get it back instead"))
        if self.adopt_running():
            raise ValueError(L("Videos are being added from Untracked files -- try again when that's done"))
        leftover = await asyncio.to_thread(self._leftover_video, video_id)
        how = "file" if leftover else "download"
        if not leftover:
            found = await self._site_details(video_id)
            if not found:
                raise ValueError(L("The site doesn't have it any more, and its file isn't in the download folder"))
        # from here the merged row is replaced; on any failure it's put back as it was
        async with self._lock:
            self._items.pop(video_id, None)
        try:
            if leftover:
                untracked = await asyncio.to_thread(self._untracked_with_ids, *self._tracked_paths())
                await self._adopt_one(video_id, leftover, untracked, user_id)
            else:
                async with self._lock:
                    new = LibraryItem(id=video_id, url=item.url, site=item.site, status="fetching",
                                      title=item.merged_title or "Fetching details...", added_by=user_id)
                    self._items[video_id] = new
                    self.db.save_item(new)
                self._ensure_metadata_worker()
        except BaseException:
            async with self._lock:
                self._items[video_id] = item
                self.db.save_item(item)
            raise
        if mode == "swap":
            swaps = self.db.get_app_state(SWAPS_KEY, {}) or {}
            swaps[video_id] = kept_id
            self.db.set_app_state(SWAPS_KEY, swaps)
        elif kept:
            self.db.ignore_issue("duplicate", sorted([video_id, kept.id]))   # you want both: not found again
        logger.info("Duplicates: [{}] brought back ({}; {}){}", video_id, "its file was still there" if leftover else "downloading it again",
                    mode, f", then [{kept_id}] is merged into it" if mode == "swap" else "")
        if mode == "swap" and leftover:
            await self.complete_dup_swap(video_id)
        return {"ok": True, "how": how, "mode": mode}

    async def complete_dup_swap(self, video_id: str) -> None:
        """After a brought-back copy is in the library (its file found, or downloaded): the video it
        had been merged into is merged into it. Never raises (it's called after a download)."""
        try:
            swaps = self.db.get_app_state(SWAPS_KEY, {}) or {}
            kept_id = swaps.get(video_id)
            if not kept_id:
                return
            new = self._items.get(video_id)
            kept = self._items.get(kept_id)
            if not new or new.status != "done" or not new.video_path or not Path(new.video_path).is_file():
                return       # not there yet: tried again when its download finishes
            swaps.pop(video_id, None)
            self.db.set_app_state(SWAPS_KEY, swaps)
            if not kept or kept.status != "done":
                logger.info("Duplicates: swap for [{}] done -- [{}] isn't in the library any more", video_id, kept_id)
                return
            result = await self.merge_items(video_id, [kept_id])
            logger.info("Duplicates: swapped -- [{}] kept, [{}] merged into it ({})", video_id, kept_id, result)
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning("Duplicates: the swap for [{}] couldn't be finished: {}", video_id, exc)
