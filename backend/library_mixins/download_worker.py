from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from .. import media_tools, proxy, stats_store
from ..models import LibraryItem
from ..notifications import notify, title_of, video_link
from .. import sources
from ._helpers import (
    cleanup_partial_download_file,
    extract_tags_performers_music_from_meta_dict,
    parse_duration_seconds,
    parse_iso_to_epoch,
    safe_name,
)


class DownloadWorkerMixin:

    def _wanted_download_lanes(self) -> list[str]:
        """Lanes to run, from the settings: "Downloads at once" direct lanes;
        with the proxy in "split" mode also `proxy_downloads` proxy lanes;
        in "all" mode the same number of lanes, all through the proxy."""
        n = max(1, self.settings.download.max_concurrent_downloads)
        mode = proxy.for_sites()
        if mode == "all":
            return [f"proxy-{i}" for i in range(n)]
        lanes = [f"direct-{i}" for i in range(n)]
        if mode == "split":
            lanes += [f"proxy-{i}" for i in range(self.settings.proxy.proxy_downloads)]
        return lanes

    def download_lanes_status(self) -> dict:
        """Which lanes are running and what each is downloading (Proxy tab)."""
        busy = {i.id: i for i in self._items.values() if i.status == "downloading"}
        out = {}
        for lane, task in self._download_lanes.items():
            if task.done():
                continue
            item_id = getattr(task, "pmv_item_id", None)
            item = busy.get(item_id) if item_id else None
            out[lane] = {"item_id": item_id, "title": item.title if item else None}
        return out

    async def _worker_loop(self, lane: str = "direct-0") -> None:
        """One download lane: handles 'pending' -> 'downloading' -> 'done',
        one video at a time. Metadata fetching ('fetching' -> 'pending') is
        the separate metadata worker pool's job (_ensure_metadata_worker()).
        A lane whose settings went away (e.g. the proxy was turned off)
        finishes its current video and stops; a proxy lane also stops while
        the proxy fails its check, leaving the queue to the direct lanes.

        Each item's download runs as its own asyncio.Task (tracked in
        self._active_download_tasks, keyed by item id) rather than being
        awaited inline -- this is what lets remove()/_cancel_in_flight()
        actually cancel ONE specific in-flight download instead of the
        only alternative being cancelling this whole loop (which would
        kill every future download too, not just the one being removed)."""
        on_proxy = lane.startswith("proxy")
        while True:
            if self._shutting_down or lane not in self._wanted_download_lanes():
                return
            if on_proxy and not await proxy.usable():
                logger.warning("Download lane {} paused: the proxy isn't working (Server Settings → Proxy)", lane)
                return
            if self.check_disk_space():
                return  # low disk space: the guard restarts the lanes when there's room again
            item = await self._next_actionable(on_proxy)
            if item is None:
                return
            token = proxy.via_proxy.set(on_proxy)  # the task below copies this context
            try:
                task = asyncio.create_task(self._download(item))
            finally:
                proxy.via_proxy.reset(token)
            task.pmv_item_id = item.id
            self._active_download_tasks[item.id] = task
            lane_task = self._download_lanes.get(lane)
            if lane_task is not None:
                lane_task.pmv_item_id = item.id
            try:
                await task
            except asyncio.CancelledError:
                if item.id in self._space_paused:  # the disk-space guard stopped it: back to the queue
                    self._space_paused.discard(item.id)
                    async with self._lock:
                        if item.id in self._items:
                            item.status = "pending"
                            item.progress_percent = None
                            item.progress_label = "Waiting for disk space"
                            item.speed = ""
                            item.touch()
                            self.db.save_item(item)
                    logger.info("Download of {} stopped: low disk space (back in the queue)", item.id)
                    continue
                # Cancelled via remove()/_cancel_in_flight() -- the item
                # is being deleted, so there is nothing further to do
                # here (no _mark_failed, no resurrecting it as "failed").
                logger.info("Download cancelled for {} (removed while in progress)", item.id)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error downloading {}{}", item.id, " (through the proxy)" if on_proxy else "")
                # Only mark it failed if it's still actually in the
                # library -- if it was removed while this was running
                # (normally that's the CancelledError path above, but a
                # real failure can still race a concurrent removal),
                # calling _mark_failed here would call self.db.save_item()
                # and silently resurrect the just-deleted row.
                async with self._lock:
                    still_exists = item.id in self._items
                if still_exists:
                    await self._mark_failed(item, str(exc), retry_status="pending")
            finally:
                self._active_download_tasks.pop(item.id, None)
                self._claimed_downloads.discard(item.id)
                if lane_task is not None:
                    lane_task.pmv_item_id = None

    async def _next_actionable(self, on_proxy: bool = False) -> LibraryItem | None:
        """The next pending video, claimed for the calling lane (so two lanes
        never take the same one). A SOCKS proxy lane skips HLS videos --
        FFmpeg can't download through SOCKS -- and leaves them to a direct lane."""
        skip_hls = on_proxy and proxy.for_sites() == "split" and proxy.ffmpeg_args(proxy.address()) is None
        async with self._lock:
            pending = [i for i in self._items.values() if i.status == "pending" and i.id not in self._claimed_downloads
                       and not (skip_hls and getattr(getattr(i, "_meta", None), "is_hls", False))]
            if not pending:
                return None
            # Lowest queue position first (the order shown on the Tasks page).
            item = min(pending, key=lambda i: i.download_rank)
            self._claimed_downloads.add(item.id)
            return item

    async def _mark_failed(self, item: LibraryItem, error: str, retry_status: str) -> None:
        async with self._lock:
            item.attempts += 1
            item.error = error
            item.status = "failed" if item.attempts >= self.settings.download.max_retries else retry_status
            item.touch()
            self.db.save_item(item)
        if item.status == "failed":
            named = item.title and not item.title.startswith("Fetching")
            what = "Couldn't get the details of" if retry_status == "fetching" else "Download failed:"
            notify("download", "error", f"{what} {title_of(item) if named else item.url}",
                   body=error[:300], link="/tasks", user_id=item.added_by)

    async def _download_hls(self, item: LibraryItem, meta, video_path: Path) -> None:
        """Downloads an HLS-only video via ffmpeg instead of the plain
        httpx byte-stream path, since a .m3u8 URL is a text playlist, not a
        single downloadable file. progress_cb runs on ffmpeg's monitoring
        thread (via asyncio.to_thread below), not the event loop -- same
        cross-thread pattern as the re-encode worker's on_progress: direct
        attribute writes are fine from the thread, but the actual DB write
        is marshalled back onto the loop via call_soon_threadsafe.

        Registers the underlying ffmpeg Popen handle in
        self._active_download_procs[item.id] for the duration of the call
        -- this is what lets _cancel_in_flight() actually stop an
        in-progress HLS remux. A plain asyncio task.cancel() does NOT work
        for this path on its own: this runs via asyncio.to_thread, and a
        thread already inside a blocking call (ffmpeg's stderr-reading
        loop) can't be interrupted that way -- only terminating the real
        OS process makes it return promptly. media_tools.
        download_hls_to_mp4() already detects the non-zero return code
        that termination produces and cleans up its own '.part.mp4' temp
        file before returning False, the same as any other download
        failure -- see _cancel_in_flight()."""
        duration_seconds = parse_duration_seconds(meta.duration)
        loop = asyncio.get_running_loop()

        def on_progress(data: dict) -> None:
            item.progress_percent = data["percent"]
            item.progress_label = f"{item.progress_percent}%" if item.progress_percent is not None else f"{data['seconds_done']}s"
            item.touch()
            loop.call_soon_threadsafe(self.db.save_item, item)

        input_args: list[str] = []
        mode = proxy.for_sites()
        if mode == "all" or (mode == "split" and proxy.via_proxy.get()):
            input_args = proxy.ffmpeg_args(proxy.address())
            if input_args is None:  # a SOCKS proxy: FFmpeg can't use it
                raise RuntimeError("This video is an HLS stream, which FFmpeg can only download through an HTTP proxy, "
                                   "not SOCKS -- use an HTTP proxy, or don't send all site traffic through the proxy")
        proc_holder: dict = {}
        self._active_download_procs[item.id] = proc_holder
        try:
            ok = await asyncio.to_thread(
                media_tools.download_hls_to_mp4,
                meta.video_url,
                str(video_path),
                duration_seconds,
                on_progress,
                proc_holder,
                input_args,
            )
        finally:
            self._active_download_procs.pop(item.id, None)

        if not ok:
            raise RuntimeError("HLS video download failed -- check the server log for ffmpeg output")

    async def _download(self, item: LibraryItem) -> None:
        meta = getattr(item, "_meta", None)
        raw_json = getattr(item, "_meta_raw", None)
        if meta is None:
            # No cached metadata (e.g. the server restarted between the
            # metadata fetch and the download starting) -- re-fetch. Same
            # v2-first/v1-fallback + "still need the HTML page for the
            # actual video URL" pattern as _fetch_metadata() above.
            meta, raw_json = await self._site_metadata(item)   # 2.27.0: the site's API only
            item.title = meta.title or item.title
            item.creator = meta.creator_name
            item.timeline_thumbnails = meta.timeline_thumbnails
            item.description = meta.description or item.description
            item.music = [m.get("label") for m in (meta.music or []) if m.get("label")]
            item.upload_date = meta.upload_date or item.upload_date

        async with self._lock:
            item.status = "downloading"
            item.touch()
            self.db.save_item(item)
            logger.info("Downloading '{}' [{}] from {}{}", meta.title or item.url, item.id, meta.video_url or "(HLS playlist)",
                        " through the proxy" if proxy.via_proxy.get() or proxy.for_sites() == "all" else "")

        creator_name = safe_name(meta.creator_name or "unknown")
        creator_dir = self.settings.output_dir / creator_name
        cache_creator_dir = self.settings.cache_dir / creator_name
        cache_creator_dir.mkdir(parents=True, exist_ok=True)
        base_name = safe_name(f"{meta.creator_name} - {meta.upload_date or 'unknown-date'} - {meta.title} [{item.id}]")
        # What we write to disk is always a real video file, but the source
        # URL doesn't always look like one: HLS sources are a .m3u8
        # *playlist* URL (remuxed to .mp4 by ffmpeg, not saved as-is), and
        # PMVHaven's own original-quality download endpoint is a path like
        # /api/videos/<id>/download with no file extension in it at all. In
        # both cases, naively taking "the part after the last dot" would
        # produce a nonsense extension (or, for the API URL, the entire URL
        # itself, since there's no dot to split on) -- fall back to mp4
        # unless the URL actually ends in something that looks like one.
        if getattr(meta, "is_hls", False):
            ext = "mp4"
        else:
            raw_ext = meta.video_url.rsplit(".", 1)[-1].split("?")[0] if meta.video_url else "mp4"
            ext = raw_ext if re.fullmatch(r"[A-Za-z0-9]{2,5}", raw_ext) else "mp4"
        video_path = creator_dir / f"{base_name}.{ext}"

        # Precomputed destination paths for the optional thumbnail/
        # funscript/preview downloads further below -- known up front
        # (even though the actual download for each only happens later,
        # if at all) purely so the cancellation-cleanup step in the
        # `finally` block at the end of this method always knows exactly
        # which file(s) COULD have been mid-write at the moment of a
        # cancellation/removal, regardless of which one actually was.
        # None means "this asset wasn't going to be downloaded this run
        # anyway" (no thumbnail_url, or one was already fetched earlier
        # by _fetch_metadata()) -- nothing to clean up for it either way.
        thumb_path: Optional[Path] = None
        if meta.thumbnail_url and not item.thumbnail_path:
            thumb_ext = meta.thumbnail_url.rsplit(".", 1)[-1].split("?")[0].lower()
            if thumb_ext not in ("jpg", "jpeg", "png", "webp"):
                thumb_ext = "jpg"
            thumb_path = cache_creator_dir / f"{base_name}.{thumb_ext}"

        fs_path: Optional[Path] = (
            video_path.with_suffix(".funscript")
            if (self.settings.download.download_funscripts and meta.funscript_url)
            else None
        )

        preview_dl_path: Optional[Path] = (
            cache_creator_dir / f"{base_name}_preview.mp4"
            if (meta.preview_url and not item.preview_path)
            else None
        )

        try:
            if getattr(meta, "is_hls", False):
                await self._download_hls(item, meta, video_path)
            else:
                def on_progress(done: int, total: int | None) -> None:
                    if total:
                        item.progress_percent = round(done / total * 100, 1)
                        item.progress_label = f"{item.progress_percent}%"
                    else:
                        item.progress_percent = None
                        item.progress_label = f"{done / (1024 * 1024):.1f} MB"
                    item.touch()

                try:
                    await sources.get(item.site).download(meta.video_url, video_path, on_progress, headers={"Referer": item.url})
                except httpx.HTTPStatusError as exc:
                    # A failing URL whose id is neither this item's nor its API _id
                    # means the details pointed at another video -- logged as such
                    # rather than as a plain access error.
                    url_id_match = re.search(r"[a-f0-9]{24}", meta.video_url or "")
                    url_id = url_id_match.group(0) if url_id_match else None
                    if url_id and url_id not in (item.id, getattr(meta, "id", None)):  # API: _id differs from the link's id
                        logger.error(
                            "Download for {} got {} on {} -- the download link has a DIFFERENT id ({}) "
                            "than this video, so the site's details pointed at another video.",
                            item.id, exc.response.status_code, meta.video_url, url_id,
                        )
                    else:
                        logger.error(
                            "Download for {} got {} on {}", item.id, exc.response.status_code, meta.video_url,
                        )
                    raise
            item.video_path = str(video_path)

            if thumb_path is not None:
                # Only reached if the early fetch in _fetch_metadata() didn't
                # already grab this (failed, or the item skipped straight to
                # _download via the restart-fallback branch above).
                try:
                    await sources.get(item.site).download(meta.thumbnail_url, thumb_path)
                    item.thumbnail_path = str(thumb_path)
                    item.thumbnail_updated_at = time.time()
                except Exception:
                    logger.warning("Thumbnail download failed for {}, continuing", item.id)

            if fs_path is not None:
                try:
                    await sources.get(item.site).download(meta.funscript_url, fs_path)
                    item.funscript_path = str(fs_path)
                except Exception:
                    logger.warning("Funscript download failed for {}, continuing", item.id)

            if preview_dl_path is not None:
                # Only reached if the early fetch in _fetch_metadata() didn't
                # already grab this (failed, was skipped, or the item skipped
                # straight to _download via the restart-fallback branch above).
                try:
                    await sources.get(item.site).download(meta.preview_url, preview_dl_path)
                    item.preview_path = str(preview_dl_path)
                    item.preview_updated_at = time.time()  # new URL, so browsers don't show the old clip
                except Exception:
                    logger.warning("Preview download failed for {}, continuing", item.id)

            # Audio browsers can't play (PCM, AC-3, DTS...) becomes AAC (3.2.0),
            # before the file's details and fingerprints below are read.
            try:
                await self._convert_audio_in_place(str(video_path))
            except Exception as exc:  # noqa: BLE001 -- the download itself is fine
                logger.warning("Download for {}: the audio couldn't be made playable: {}", item.id, exc)

            if self.settings.download.save_metadata_json:
                # Always (re)written here -- even if _fetch_metadata() already
                # wrote a provisional version early, this merges in the real
                # technical details (codec/exact bitrate/verified resolution/
                # filesize/duration), read back off the actual downloaded file
                # via MediaInfo rather than trusting whatever PMVHaven's
                # API/page claimed. If v2 succeeded earlier, raw_json (the
                # verbatim API response) is still the base -- see the "raw v2
                # JSON as the main metadata file" note in _fetch_metadata().
                if raw_json is not None:
                    meta_dict = dict(raw_json)
                    meta_dict["video_url"] = meta.video_url
                    meta_dict["is_hls"] = meta.is_hls
                    meta_dict["funscript_url"] = meta.funscript_url
                else:
                    meta_dict = meta.to_dict()
                tech_info = media_tools.get_technical_info(str(video_path))
                meta_dict.setdefault("video_details", {})
                meta_dict["video_details"].update(tech_info)
                meta_path = cache_creator_dir / f"{base_name}.json"
                meta_path.write_text(json.dumps(meta_dict, indent=2, ensure_ascii=False), encoding="utf-8")
                item.metadata_path = str(meta_path)

                # surface the key technical facts directly on the item so the
                # library list can show them without a second request per card
                item.tech_codec = tech_info.get("Video Codec")
                item.tech_resolution = tech_info.get("Resolution")
                item.tech_filesize = tech_info.get("File Size")
                item.tech_duration = tech_info.get("Duration")
                item.ratings = meta.rating_percent
                tags, performers, music = extract_tags_performers_music_from_meta_dict(meta_dict)
                item.tags = tags
                item.performers = performers
                item.music = music
                item.description = meta_dict.get("description") or meta.description or item.description
                item.timeline_thumbnails = meta.timeline_thumbnails

            # Perceptual hash for duplicate detection -- reuse PMVHaven's own
            # phash (from the v2 API's "phash"/"phashGeneratedAt" fields) if
            # they gave us one, rather than spending an ffmpeg pass
            # regenerating one ourselves right after every single download.
            # We only need OUR OWN recomputed hash once the file on disk no
            # longer matches what PMVHaven hashed -- i.e. after a re-encode
            # (see _reencode_item) or when repair explicitly needs to
            # backfill one (see _repair_item). NOTE: PMVHaven's algorithm
            # isn't guaranteed to be identical to generate_stash_phash()'s --
            # both happen to produce 64-bit/16-hex-char hashes, but if
            # duplicate detection seems to miss a match between an original
            # download and a re-encoded copy of the same video, that's the
            # likely reason; running Repair/Update on the affected item(s)
            # replaces PMVHaven's value with our own locally-computed one
            # for a consistent comparison.
            source_hash = await asyncio.to_thread(media_tools.compute_file_sha256, str(video_path))
            source_size = video_path.stat().st_size
            if meta.phash:
                item.local_phash = meta.phash
                item.local_phash_generated_at = parse_iso_to_epoch(meta.phash_generated_at) or time.time()
                item.local_phash_source_hash = source_hash
                item.local_phash_source_size = source_size
            else:
                # No phash on record from PMVHaven (e.g. an unprocessed/new
                # video, or the v1 HTML-fallback path, which has no access to
                # this field at all) -- compute our own right away so the
                # item isn't left out of duplicate detection entirely.
                local_phash = await asyncio.to_thread(media_tools.generate_stash_phash, str(video_path))
                if local_phash:
                    item.local_phash = local_phash
                    item.local_phash_generated_at = time.time()
                    item.local_phash_source_hash = source_hash
                    item.local_phash_source_size = source_size
                else:
                    logger.warning("Could not compute a local phash for {} after download", item.id)

            async with self._lock:
                if item.id not in self._items:
                    # Removed mid-download (see remove()/_cancel_in_flight())
                    # -- don't resurrect the row by writing a "done" state
                    # for an item that was just intentionally deleted. The
                    # `finally` block below still runs and cleans up
                    # whatever partial file was left behind.
                    return
                item.status = "done"
                item.progress_percent = 100
                item.progress_label = "Complete"
                item.download_datetime = datetime.now(timezone.utc).isoformat()
                item.touch()
                self.db.save_item(item)
                logger.info("Download complete for '{}' [{}] -> {}", item.title, item.id, video_path)
            notify("download", "success", f"Downloaded {title_of(item)}", link=video_link(item), user_id=item.added_by)
            try:
                stats_store.bump("download_bytes", Path(video_path).stat().st_size)
            except OSError:
                pass
            # A new video exists: check it for duplicates if "Scan after a
            # download" is on (Server Settings -> Duplicate scan).
            self.request_duplicate_scan_after("download")
            self.request_storyboard(item.id)  # seekbar previews (2.21.0)
            self.request_frames(item.id)  # video frames under the player (3.2.0)
        finally:
            async with self._lock:
                still_exists = item.id in self._items
            if not still_exists or item.id in self._space_paused:
                # This download was cancelled or the item was removed
                # mid-flight (see remove()/_cancel_in_flight()) -- clean up
                # whichever ".part" (or, for an HLS remux, ".part.mp4")
                # temp file was left behind, so a cancelled download
                # doesn't leave an orphaned partial file on disk forever.
                # Every asset this run could plausibly have been
                # mid-writing is checked -- whichever one was actually in
                # progress at the moment of cancellation is the one that
                # actually gets found; the rest are harmless no-ops.
                cleanup_partial_download_file(video_path, is_hls_video=getattr(meta, "is_hls", False))
                cleanup_partial_download_file(thumb_path)
                cleanup_partial_download_file(fs_path)
                cleanup_partial_download_file(preview_dl_path)
