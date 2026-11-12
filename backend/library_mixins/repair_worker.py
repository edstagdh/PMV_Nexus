from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from ..config import get_settings
from .. import media_tools
from .. import activity, stats_store
from ..models import LibraryItem
from ..notifications import notify, title_of, video_link
from ..pmv_client import get_client
from ..scraper_v1 import parse_metadata
from ..sites import get_site
from ._helpers import extract_tags_performers_music_from_meta_dict, is_broken_file, safe_name


class RepairWorkerMixin:
    _bulk_repair_ids: set = set()  # queued by Repair/Update all (no per-video "done" notifications)

    def request_repair(self, video_id: str) -> LibraryItem:
        item = self._items.get(video_id)
        if not item:
            raise ValueError("Not found")
        if item.status != "done" or not item.video_path:
            raise ValueError("Only completed downloads can be repaired")
        if item.repair_status in ("queued", "running"):
            # No-op, not an error -- the frontend now guards against this
            # itself (see the "repair" case in library.js's
            # handleLibraryAction, and the disabled Repair button in
            # views/list.js) so this branch should mostly only be hit by
            # something bypassing that guard (e.g. the API called directly,
            # or a stale page). Logged as a no-op rather than silently
            # swallowed so a flood of these in the log is a signal
            # something client-side isn't checking state before calling in.
            logger.info(
                "Repair requested for {} ({}) but one is already {} -- ignoring",
                video_id, item.title or item.video_path, item.repair_status,
            )
            return item
        item.repair_status = "queued"
        item.repair_progress_label = "Queued"
        item.repair_error = None
        item.touch()
        self.db.save_item(item)
        if video_id not in self._repair_queue:
            self._repair_queue.append(video_id)
        logger.info(
            "Queued repair for {} ({}) -- {} item(s) now waiting in the repair queue",
            video_id, item.title or item.video_path, len(self._repair_queue),
        )
        self._ensure_repair_worker()
        return item

    def request_repair_all(self, max_concurrent: int = 1) -> int:
        self._repair_max_concurrent = max(1, max_concurrent)
        count = 0
        for item in self._items.values():
            if item.status != "done" or not item.video_path:
                continue
            if item.repair_status in ("queued", "running"):
                continue
            item.repair_status = "queued"
            item.repair_progress_label = "Queued"
            item.repair_error = None
            item.touch()
            self.db.save_item(item)
            if item.id not in self._repair_queue:
                self._repair_queue.append(item.id)
            self._bulk_repair_ids.add(item.id)
            count += 1
        logger.info(
            "Queued {} item(s) for bulk repair (max_concurrent={})",
            count, self._repair_max_concurrent,
        )
        self._ensure_repair_worker()
        return count

    def start_guarded_repair_all(self, max_concurrent: int = 1) -> int:
        """Entry point for the Admin Portal's "Repair/Update all" button
        (see routers/settings.py) -- same as request_repair_all() above,
        but mutually exclusive with any other INVASIVE operation already
        running (e.g. a duplicate scan) via CoreMixin.
        _try_acquire_invasive_lock(). Raises ValueError immediately,
        before queuing anything, if something else invasive is already
        in progress -- the route maps that to a 409 response.

        The lock is held for the ENTIRE bulk-repair duration, not just
        this call -- request_repair_all() itself only enqueues items and
        returns instantly (the real work happens on the repair worker's
        own independent loop), so a background watcher task
        (_watch_and_release_repair_lock()) actually releases the lock
        once the queue and every active repair task have genuinely
        drained. This method itself still returns immediately with just
        the queued count, preserving the button's existing fire-and-
        forget UX."""
        if not self._try_acquire_invasive_lock("library_repair"):
            raise ValueError(
                f"Cannot start a bulk repair while '{self._invasive_lock_holder}' is already running"
            )
        queued = self.request_repair_all(max_concurrent)
        asyncio.create_task(self._watch_and_release_repair_lock(queued))
        return queued

    def _stop_repair_all(self) -> None:
        """Stop button: the videos still waiting leave the queue; the ones being
        repaired right now finish."""
        for item_id in list(self._repair_queue):
            item = self._items.get(item_id)
            if item is not None and item.repair_status == "queued":
                item.repair_status = None
                item.repair_progress_label = None
                item.touch()
                self.db.save_item(item)
        self._repair_queue.clear()
        self._bulk_repair_ids.clear()

    def _repair_all_activity(self, total: int) -> dict:
        left = len(self._repair_queue) + len(self._active_repair_tasks)
        return {"stage": "Repairing videos", "done": max(0, total - left), "total": total}

    async def _watch_and_release_repair_lock(self, queued: int = 0) -> None:
        """Background companion to start_guarded_repair_all() -- polls
        until the repair queue and every active repair task have both
        drained, then releases the invasive-operation lock. Checks
        self._shutting_down each iteration so a server shutdown mid-
        repair doesn't leave this polling forever; the lock is released
        regardless of how the loop exits, via the `finally` block."""
        try:
            async with activity.track("library_repair", "Repairing all videos",
                                      progress=lambda: self._repair_all_activity(queued), stop=self._stop_repair_all):
                await self._wait_for_repairs()
        finally:
            self._release_invasive_lock("library_repair")

    async def _wait_for_repairs(self) -> None:
        while not self._shutting_down:
            async with self._lock:
                if not (self._repair_queue or self._active_repair_tasks):
                    return
            await asyncio.sleep(2)

    def cancel_repair(self, video_id: str) -> None:
        item = self._items.get(video_id)
        if not item:
            raise ValueError("Not found")
        if item.repair_status == "queued":
            if video_id in self._repair_queue:
                self._repair_queue.remove(video_id)
            item.repair_status = None
            item.repair_progress_label = None
            item.touch()
            self.db.save_item(item)
            logger.info("Cancelled queued repair for {} ({})", video_id, item.title or item.video_path)
            return
        if item.repair_status == "running":
            task = self._active_repair_tasks.get(video_id)
            if task and not task.done():
                self._repair_cancel_requested.add(video_id)
                task.cancel()
                logger.info("Cancelling running repair for {} ({})", video_id, item.title or item.video_path)
                return
        raise ValueError("This item's repair isn't currently queued or running")

    def _ensure_repair_worker(self) -> None:
        self._repair_tasks = [t for t in self._repair_tasks if not t.done()]
        n = max(1, self._repair_max_concurrent)
        while len(self._repair_tasks) < n:
            self._repair_tasks.append(asyncio.create_task(self._repair_worker_loop()))

    async def _repair_worker_loop(self) -> None:
        """One of up to _repair_max_concurrent workers pulling from
        self._repair_queue. Each item's repair run is tracked as its own
        asyncio.Task in self._active_repair_tasks (keyed by item id) so
        remove()/_cancel_in_flight() can cancel ONE specific in-flight
        repair without affecting this worker's loop or any other repair
        running concurrently."""
        while True:
            if self._shutting_down:
                return
            async with self._lock:
                video_id = self._repair_queue.pop(0) if self._repair_queue else None
            if video_id is None:
                return
            item = self._items.get(video_id)
            if not item:
                continue
            task = asyncio.create_task(self._repair_item(item))
            self._active_repair_tasks[video_id] = task
            try:
                await task
            except asyncio.CancelledError:
                if video_id in self._repair_cancel_requested:
                    self._repair_cancel_requested.discard(video_id)
                    async with self._lock:
                        if video_id in self._items:
                            it = self._items[video_id]
                            it.repair_status = None
                            it.repair_progress_label = None
                            it.repair_error = None
                            it.touch()
                            self.db.save_item(it)
                    logger.info("Repair/Update cancelled by user for {}", video_id)
                else:
                    logger.info("Repair/Update cancelled for {} (removed while in progress)", video_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Repair failed for {}", video_id)
                async with self._lock:
                    still_exists = video_id in self._items
                if still_exists:
                    await self._finish_repair(item, "failed", error=str(exc))
            finally:
                self._active_repair_tasks.pop(video_id, None)

    async def _set_repair_progress(self, item: LibraryItem, label: str) -> None:
        logger.debug("Repair/Update progress for {}: {}", item.id, label)
        async with self._lock:
            # If this item was removed mid-repair (see remove()/
            # _cancel_in_flight()), don't resurrect its row with a
            # progress-label write for a repair that's being cancelled.
            if item.id not in self._items:
                return
            item.repair_progress_label = label
            item.touch()
            self.db.save_item(item)

    async def _finish_repair(
            self, item: LibraryItem, status: str, fixed: Optional[list[str]] = None, error: Optional[str] = None
    ) -> None:
        async with self._lock:
            if item.id not in self._items:
                return
            item.repair_status = status
            item.repair_error = error
            item.repair_progress_label = (
                f"Fixed: {', '.join(fixed)}" if fixed else ("Nothing to fix" if status == "done" else None)
            )
            item.touch()
            self.db.save_item(item)
        # During "Repair/Update all" only failures are announced (not 60 "done"s).
        bulk = item.id in self._bulk_repair_ids
        stats_store.bump("repairs" if status == "done" else "repair_failures" if status == "failed" else "")
        self._bulk_repair_ids.discard(item.id)
        if status == "failed":
            notify("repair", "error", f"Repair/Update failed: {title_of(item)}", body=(error or "")[:300],
                   link=video_link(item), user_id=item.added_by)
        elif not bulk and status == "done":
            notify("repair", "success", f"Repaired/updated {title_of(item)}", body=item.repair_progress_label,
                   link=video_link(item), user_id=item.added_by)
        if status == "failed":
            logger.warning("Repair/Update failed for {} ({}): {}", item.id, item.title or item.video_path, error)
        else:
            logger.info(
                "Repair/Update finished for {} ({}): {}",
                item.id, item.title or item.video_path,
                f"fixed [{', '.join(fixed)}]" if fixed else "nothing needed fixing",
            )
            # Cumulative Stats counters (see stats_worker.py's
            # compute_library_stats()) -- deliberately NOT derived from
            # repair_status on items (that field only reflects each
            # item's MOST RECENT repair outcome, not how many times it's
            # ever been fixed), so this is the only record of repair
            # activity over time. Only counts here when something was
            # actually fixed -- a routine "checked, nothing needed
            # fixing" pass doesn't inflate the total.
            if status == "done" and fixed:
                self.db.increment_counter("stat_total_repairs_performed", 1)
                self.db.increment_counter("stat_total_repair_fixes_applied", len(fixed))
            if status == "done":
                self.request_duplicate_scan_after("repair")

    async def _repair_item(self, item: LibraryItem) -> None:
        """Checks one completed item's cached assets on disk and repairs
        whatever's missing or 0 bytes: thumbnail, preview clip, and the
        metadata JSON / technical-detail fields. ALWAYS re-fetches this
        video's metadata from PMVHaven (tags/rating/description/etc.),
        even when nothing on disk is broken -- that data can change on
        PMVHaven's side after the initial download (a rating shifts as
        more people vote, a tag gets added or removed, a description gets
        edited), and Repair/Update is now the mechanism for picking that
        up. (Previously this only re-fetched metadata when something was
        ALSO locally broken, so an item with intact files but stale
        tags/rating never got refreshed.) The actual thumbnail/preview
        FILES are still only re-downloaded/regenerated when actually
        broken, not on every run -- only the metadata re-fetch itself is
        now unconditional. If PMVHaven's own copy of the thumbnail/preview
        is itself missing or broken, falls back to generating a
        replacement locally via ffmpeg instead of leaving it blank. Also
        backfills the item's `timeline_thumbnails` list (the alternate-
        thumbnail candidates scraped from PMVHaven's own "Video Timeline"
        scrubber row) for items that predate that field -- see the
        `timeline_missing` check below. Progress is written onto the item
        at each step (see _set_repair_progress) so the Tasks tab shows
        live status. Never touches the main video file -- if that's
        missing/corrupt, this reports it as an error rather than silently
        re-downloading it."""
        logger.info("Repair/Update starting for {} ({})", item.id, item.title or item.video_path)

        async with self._lock:
            item.repair_status = "running"
            item.repair_error = None
            item.repair_progress_label = "Checking files\u2026"
            item.touch()
            self.db.save_item(item)

        video_path = Path(item.video_path)
        if not video_path.exists() or video_path.stat().st_size == 0:
            logger.warning(
                "Repair/Update for {}: video file missing or 0 bytes at {} -- aborting, this needs a manual re-download",
                item.id, video_path,
            )
            await self._finish_repair(
                item, "failed",
                error="Video file is missing or 0 bytes -- needs a manual re-download, not a repair",
            )
            return

        thumb_broken = is_broken_file(item.thumbnail_path)
        preview_broken = is_broken_file(item.preview_path)
        meta_broken = not item.metadata_path or not Path(item.metadata_path).exists()
        # Alternate-thumbnail candidates from PMVHaven's own "Video
        # Timeline" scrubber row -- this field didn't exist on older items,
        # so an empty list here just means "never populated yet" rather
        # than "PMVHaven genuinely has none" (that distinction doesn't
        # matter for repair purposes: either way, a fresh fetch is worth
        # trying).
        timeline_missing = not item.timeline_thumbnails

        # One line summarizing exactly what data on this item was found
        # broken/missing before this run -- logged at INFO (not DEBUG)
        # even though the individual fix steps below are more granular.
        logger.info(
            "Repair/Update check for {} ({}): thumbnail={} preview={} metadata={} tags_missing={} "
            "technical_details_incomplete={} ratings_missing={} timeline_thumbnails_missing={}",
            item.id, item.title or item.video_path,
            "broken" if thumb_broken else "ok",
            "broken" if preview_broken else "ok",
            "broken" if meta_broken else "ok",
            not item.tags,
            not (item.tech_codec and item.tech_resolution and item.tech_filesize and item.tech_duration),
            not item.ratings,
            timeline_missing,
        )

        fixed: list[str] = []

        # Local-only fix: if the database already has timeline thumbnails
        # for this item (e.g. backfilled by an earlier repair run, or added
        # after this field existed) but the metadata JSON *sidecar file* on
        # disk predates the field and is still missing it, patch the file
        # directly -- no PMVHaven fetch needed, since the data is already
        # in hand. Runs unconditionally, before deciding whether a network
        # re-fetch is needed at all, so it takes effect even on a run
        # where nothing else needs repairing.
        if item.timeline_thumbnails and item.metadata_path and Path(item.metadata_path).exists():
            meta_file_path = Path(item.metadata_path)
            try:
                existing_meta_dict = json.loads(meta_file_path.read_text(encoding="utf-8"))
            except Exception:
                existing_meta_dict = None
            if existing_meta_dict is not None and not existing_meta_dict.get("timeline_thumbnails"):
                existing_meta_dict["timeline_thumbnails"] = item.timeline_thumbnails
                try:
                    meta_file_path.write_text(
                        json.dumps(existing_meta_dict, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
                    logger.info(
                        "Repair/Update for {}: synced {} timeline thumbnail(s) from the database into the "
                        "metadata JSON file on disk (no PMVHaven fetch needed)",
                        item.id, len(item.timeline_thumbnails),
                    )
                    fixed.append("timeline thumbnails (metadata file)")
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Repair/Update: could not sync timeline thumbnails into metadata JSON for {}: {}", item.id, exc
                    )

        # Local-only fix: technical details can always be regenerated
        # straight from the file on disk, no network needed. Ratings is
        # deliberately NOT part of this check/regen -- it's PMVHaven's own
        # site rating, not something MediaInfo/ffmpeg can read off the
        # file, so it's refreshed via the metadata re-fetch below instead.
        if not (item.tech_codec and item.tech_resolution and item.tech_filesize and item.tech_duration):
            logger.info("Repair/Update for {}: regenerating technical details from the video file", item.id)
            await self._set_repair_progress(item, "Reading technical details\u2026")
            tech_info = media_tools.get_technical_info(str(video_path))
            async with self._lock:
                item.tech_codec = tech_info.get("Video Codec") or item.tech_codec
                item.tech_resolution = tech_info.get("Resolution") or item.tech_resolution
                item.tech_filesize = tech_info.get("File Size") or item.tech_filesize
                item.tech_duration = tech_info.get("Duration") or item.tech_duration
                item.touch()
                self.db.save_item(item)
            fixed.append("technical details")

        # Recomputes the local phash only when actually needed. Three cases:
        #  1. No phash at all yet -- must compute it now.
        #  2. Has a phash, but no source-hash/size stamp yet (an item
        #     downloaded before this optimization existed) -- the existing
        #     phash is TRUSTED as-is; just backfill the stamp fields
        #     cheaply (one stat() + one file read) so FUTURE repairs get
        #     the fast path. Does NOT recompute the expensive phash
        #     itself -- this is the one-time backfill for the whole
        #     existing library, folded into the normal repair pass rather
        #     than needing a separate migration step.
        #  3. Has both -- compare size first (free), then hash (a full
        #     read, but still far cheaper than the phash computation it
        #     guards) to detect a genuinely changed file; only recompute
        #     the phash if the file has actually changed.
        current_size = video_path.stat().st_size
        backfill_stamp_only = False
        if not item.local_phash:
            needs_phash = True
        elif item.local_phash_source_hash is None or item.local_phash_source_size is None:
            needs_phash = False
            backfill_stamp_only = True
        elif item.local_phash_source_size != current_size:
            needs_phash = True
        else:
            current_hash = await asyncio.to_thread(media_tools.compute_file_sha256, str(video_path))
            needs_phash = current_hash != item.local_phash_source_hash

        if backfill_stamp_only:
            source_hash = await asyncio.to_thread(media_tools.compute_file_sha256, str(video_path))
            async with self._lock:
                item.local_phash_source_hash = source_hash
                item.local_phash_source_size = current_size
                item.touch()
                self.db.save_item(item)
            logger.debug("Repair/Update for {}: backfilled phash fingerprint (existing phash trusted, not recomputed)", item.id)
        elif needs_phash:
            logger.info("Repair/Update for {}: computing a local phash for duplicate detection", item.id)
            await self._set_repair_progress(item, "Computing perceptual hash\u2026")
            local_phash = await asyncio.to_thread(media_tools.generate_stash_phash, str(video_path))
            if local_phash:
                source_hash = await asyncio.to_thread(media_tools.compute_file_sha256, str(video_path))
                async with self._lock:
                    item.local_phash = local_phash
                    item.local_phash_generated_at = time.time()
                    item.local_phash_source_hash = source_hash
                    item.local_phash_source_size = current_size
                    item.touch()
                    self.db.save_item(item)
                fixed.append("perceptual hash")
            else:
                logger.warning("Repair/Update for {}: could not compute a local phash", item.id)
        else:
            logger.debug("Repair/Update for {}: file unchanged since last phash computation -- skipping", item.id)

        # Uploaded from the computer: nothing to fetch, remake what's missing.
        if item.site == "local":
            await self._set_repair_progress(item, "Checking the uploaded file\u2026")
            fixed += await self.repair_local_item(item, thumb_broken, preview_broken)
            await self._finish_repair(item, "done", fixed=fixed)
            return

        # ---- ALWAYS re-fetch metadata from PMVHaven -- this is the main
        # behavior change: no longer gated behind "something is locally
        # broken". Ratings/tags/description can change on PMVHaven's side
        # at any time after the initial download, so every Repair/Update
        # run checks for that, regardless of whether anything was broken.
        await self._set_repair_progress(item, "Re-fetching metadata from PMVHaven\u2026")
        logger.info("Repair/Update for {}: re-fetching metadata for {}", item.id, item.url)
        meta = None
        raw_json = None
        html = None
        v2_result = await self._fetch_metadata_v2_or_none(item.id, item.url, item.site)
        if v2_result is not None:
            meta, raw_json = v2_result
        else:
            try:
                if not get_settings().download.website_fallback:  # 2.27.0: the site's API only
                    raise RuntimeError("the site's API didn't return this video (removed, private, or unreachable)")
                html = await get_client(item.site).fetch_page(item.url)
                meta = parse_metadata(html, item.url, get_site(item.site).cdn_base)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Repair/Update for {}: could not re-fetch metadata: {}", item.id, exc)
                await self._finish_repair(
                    item, "failed",
                    error=(f"Fixed {', '.join(fixed)}, but couldn't re-fetch metadata: {exc}" if fixed else f"Could not re-fetch metadata: {exc}"),
                )
                return

        # Same directory/naming the original download used -- keyed off the
        # existing video filename (not the freshly-fetched title), so a
        # repair never renames files or creates duplicates next to a
        # manually-renamed item.
        base_name = video_path.stem
        cache_creator_dir = None
        for p in (item.thumbnail_path, item.preview_path, item.metadata_path):
            if p:
                cache_creator_dir = Path(p).parent
                break
        if cache_creator_dir is None:
            cache_creator_dir = self.settings.cache_dir / safe_name(meta.creator_name or item.creator or "unknown")
        cache_creator_dir.mkdir(parents=True, exist_ok=True)

        # ---- timeline thumbnails (alternate-thumbnail candidates from
        # PMVHaven's own "Video Timeline" scrubber row) -- this is
        # populate-once data (it doesn't change upstream the way a rating
        # does), so it's still only backfilled if genuinely missing, not
        # re-synced on every run. Only overwrites if the fresh fetch
        # actually found some -- never clobbers existing data with an
        # empty list if PMVHaven's timeline strip happened to be absent on
        # this particular fetch (e.g. a transient page-render issue).
        if timeline_missing and meta.timeline_thumbnails:
            await self._set_repair_progress(item, "Saving timeline thumbnails\u2026")
            logger.info(
                "Repair/Update for {}: backfilling {} timeline thumbnail(s)",
                item.id, len(meta.timeline_thumbnails),
            )
            async with self._lock:
                item.timeline_thumbnails = meta.timeline_thumbnails
                item.touch()
                self.db.save_item(item)
            fixed.append("timeline thumbnails")
        elif timeline_missing:
            # The re-fetch succeeded but found zero timeline thumbnails for
            # this video. `html` is only set if the v1 HTML fallback
            # actually ran (see the block above) -- the v2 JSON path never
            # fetches a page at all, so there's nothing to dump for
            # debugging in that case; a v2 response with genuinely zero
            # timelineThumbnails is just taken at face value.
            debug_path = self._dump_debug_html(item.id, html, suffix="no_timeline_thumbnails") if html else None
            logger.warning(
                "Repair/Update for {}: {} found no timeline thumbnails{}",
                item.id,
                "v1 HTML page re-fetch" if html else "v2 metadata re-fetch",
                f" (raw HTML saved to {debug_path} for debugging)" if debug_path else "",
            )

        # ---- thumbnail: only re-downloaded/regenerated if actually broken
        # -- PMVHaven's copy first, local frame-grab fallback
        if thumb_broken:
            got_remote = False
            if meta.thumbnail_url:
                await self._set_repair_progress(item, "Re-downloading thumbnail\u2026")
                logger.info("Repair/Update for {}: re-downloading thumbnail from PMVHaven", item.id)
                thumb_ext = meta.thumbnail_url.rsplit(".", 1)[-1].split("?")[0].lower()
                if thumb_ext not in ("jpg", "jpeg", "png", "webp"):
                    thumb_ext = "jpg"
                thumb_path = cache_creator_dir / f"{base_name}.{thumb_ext}"
                try:
                    await get_client(item.site).stream_download(meta.thumbnail_url, thumb_path)
                    if not is_broken_file(str(thumb_path)):
                        async with self._lock:
                            item.thumbnail_path = str(thumb_path)
                            item.thumbnail_updated_at = time.time()
                            item.touch()
                            self.db.save_item(item)
                        fixed.append("thumbnail")
                        got_remote = True
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Repair/Update: remote thumbnail fetch failed for {}: {}", item.id, exc)

            if not got_remote:
                # PMVHaven's own thumbnail was missing or itself broken --
                # fall back to grabbing a frame from the downloaded video.
                await self._set_repair_progress(item, "Generating thumbnail locally (ffmpeg)\u2026")
                logger.info(
                    "Repair/Update for {}: PMVHaven's thumbnail unavailable/broken -- generating one locally with ffmpeg",
                    item.id,
                )
                local_thumb_path = cache_creator_dir / f"{base_name}.jpg"
                ok = await asyncio.to_thread(media_tools.generate_thumbnail, str(video_path), str(local_thumb_path))
                if ok:
                    async with self._lock:
                        item.thumbnail_path = str(local_thumb_path)
                        item.thumbnail_updated_at = time.time()
                        item.touch()
                        self.db.save_item(item)
                    fixed.append("thumbnail (generated locally)")
                else:
                    logger.warning("Repair/Update for {}: local thumbnail generation also failed", item.id)

        # ---- preview: only re-downloaded/regenerated if actually broken
        # -- PMVHaven's copy first, local scrub-clip fallback
        if preview_broken:
            got_remote = False
            if meta.preview_url:
                await self._set_repair_progress(item, "Re-downloading preview clip\u2026")
                logger.info("Repair/Update for {}: re-downloading preview clip from PMVHaven", item.id)
                preview_path = cache_creator_dir / f"{base_name}_preview.mp4"
                try:
                    await get_client(item.site).stream_download(meta.preview_url, preview_path)
                    if not is_broken_file(str(preview_path)):
                        async with self._lock:
                            item.preview_path = str(preview_path)
                            item.preview_updated_at = time.time()  # new URL, so browsers don't show the old clip
                            item.touch()
                            self.db.save_item(item)
                        fixed.append("preview clip")
                        got_remote = True
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Repair/Update: remote preview fetch failed for {}: {}", item.id, exc)

            if not got_remote:
                # PMVHaven's own preview was missing or itself broken --
                # generate a sped-up scrub-through clip locally instead.
                await self._set_repair_progress(item, "Generating preview locally (ffmpeg)\u2026")
                logger.info(
                    "Repair/Update for {}: PMVHaven's preview unavailable/broken -- generating one locally with ffmpeg",
                    item.id,
                )
                local_preview_path = cache_creator_dir / f"{base_name}_preview.mp4"
                cfg = self.settings.download
                ok = await asyncio.to_thread(
                    media_tools.generate_preview_clip,
                    str(video_path), str(local_preview_path),
                    num_clips=cfg.generated_preview_num_clips,
                    clip_duration_seconds=cfg.generated_preview_clip_duration_seconds,
                    max_height=cfg.generated_preview_max_height,
                    target_fps=cfg.generated_preview_fps,
                )
                if ok:
                    async with self._lock:
                        item.preview_path = str(local_preview_path)
                        item.preview_updated_at = time.time()  # new URL, so browsers don't show the old clip
                        item.touch()
                        self.db.save_item(item)
                    fixed.append("preview clip (generated locally)")
                else:
                    logger.warning("Repair/Update for {}: local preview generation also failed", item.id)

        # ---- metadata json / tags / performers / music / description /
        # ratings -- ALWAYS refreshed now (meta is always fresh, fetched
        # above), unlike before when this only ran if something was
        # locally broken. Only reported in `fixed` if something actually
        # changed, so a Repair/Update run that finds nothing new upstream
        # doesn't misleadingly say "Fixed: metadata" every single time.
        await self._set_repair_progress(item, "Refreshing metadata\u2026")
        logger.info("Repair/Update for {}: refreshing metadata JSON / tags / performers / ratings", item.id)
        meta_path = cache_creator_dir / f"{base_name}.json"

        if raw_json is not None:
            # Merge onto whatever's already on disk (if readable) so this
            # doesn't clobber video_url/is_hls/funscript_url -- those are
            # only ever resolved during the initial metadata fetch or an
            # actual download, not during repair, which never needs the
            # download URL itself.
            existing: dict = {}
            if meta_path.exists():
                try:
                    existing = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    existing = {}
            meta_dict = {**existing, **raw_json}
        else:
            meta_dict = meta.to_dict()

        tech_info = media_tools.get_technical_info(str(video_path))
        meta_dict.setdefault("video_details", {})
        meta_dict["video_details"].update(tech_info)
        try:
            meta_path.write_text(json.dumps(meta_dict, indent=2, ensure_ascii=False), encoding="utf-8")
            new_tags, new_performers, new_music = extract_tags_performers_music_from_meta_dict(meta_dict)
            new_description = meta_dict.get("description") or item.description
            new_ratings = meta.rating_percent or item.ratings
            # Compare against what was already on the item before this run
            # -- this is what decides whether to report "metadata" as
            # something this Repair/Update actually fixed/changed, versus
            # a routine check that confirmed everything's still current.
            metadata_changed = (
                    meta_broken
                    or new_tags != item.tags
                    or new_performers != item.performers
                    or new_music != item.music
                    or new_description != item.description
                    or new_ratings != item.ratings
            )
            async with self._lock:
                item.metadata_path = str(meta_path)
                item.tags = new_tags
                item.performers = new_performers
                item.music = new_music
                item.description = new_description
                item.ratings = new_ratings
                item.touch()
                self.db.save_item(item)
            if metadata_changed:
                fixed.append("metadata")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Repair/Update: metadata write failed for {}: {}", item.id, exc)

        await self._finish_repair(item, "done", fixed=fixed)
