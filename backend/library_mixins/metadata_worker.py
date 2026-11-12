from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from ..config import get_settings
from ..models import LibraryItem
from ..pmv_client import get_client
from ..scraper_v1 import parse_metadata, VideoMetadata
from ..scraper_v2 import build_api_url, parse_metadata_from_json, is_metadata_usable
from ..sites import get_site
from ..log_store import get_log_store
from ._helpers import cleanup_partial_download_file, safe_name


class MetadataWorkerMixin:

    def _ensure_metadata_worker(self) -> None:
        self._metadata_tasks = [t for t in self._metadata_tasks if not t.done()]
        n = max(1, self.settings.download.max_concurrent_metadata_fetches)
        while len(self._metadata_tasks) < n:
            self._metadata_tasks.append(asyncio.create_task(self._metadata_worker_loop()))

    async def _metadata_worker_loop(self) -> None:
        """One of up to max_concurrent_metadata_fetches workers pulling
        from items still in 'fetching' -- entirely independent of the
        download worker/slot above, so a queued item's title/tags/
        performers/thumbnail show up right away instead of waiting behind
        whatever's currently downloading.

        Each item's metadata fetch runs as its own asyncio.Task (tracked
        in self._active_metadata_tasks, keyed by item id), same reasoning
        as _worker_loop's download task above -- lets remove()/
        _cancel_in_flight() cancel ONE specific in-flight fetch without
        killing this whole worker's loop."""
        while True:
            if self._shutting_down:
                return
            item = await self._next_fetching_item()
            if item is None:
                return
            task = asyncio.create_task(self._fetch_metadata(item))
            self._active_metadata_tasks[item.id] = task
            try:
                await task
            except asyncio.CancelledError:
                logger.info("Metadata fetch cancelled for {} (removed while in progress)", item.id)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error fetching metadata for {}", item.id)
                async with self._lock:
                    still_exists = item.id in self._items
                if still_exists:
                    await self._mark_failed(item, str(exc), retry_status="fetching")
            finally:
                self._active_metadata_tasks.pop(item.id, None)
                async with self._lock:
                    self._metadata_claimed.discard(item.id)

    async def _next_fetching_item(self) -> LibraryItem | None:
        async with self._lock:
            for item in self._items.values():
                if item.status == "fetching" and item.id not in self._metadata_claimed:
                    self._metadata_claimed.add(item.id)
                    return item
            return None

    async def _fetch_metadata_v2_or_none(self, video_id: str, page_url: str, site_key: str) -> Optional[tuple[VideoMetadata, dict]]:
        """Tries the site's v2 JSON metadata API first (richer/more reliable
        than the v1 HTML scrape, e.g. a correct per-video rating). Returns
        (meta, raw_json) on success, or None on failure/incomplete data --
        callers should fall back to v1's parse_metadata() in that case.
        video_url/is_hls are never set here (v2 has no access to the actual
        downloadable file) -- callers about to download must still resolve
        those via v1's parse_metadata()."""
        try:
            api_url = build_api_url(video_id, get_site(site_key).base_url)
            response = await get_client(site_key).fetch_json(api_url)
            json_data = response.get("data") if isinstance(response, dict) else None
            if not json_data:
                logger.warning("v2 metadata API for {} returned no usable data envelope", video_id)
                return None
            meta = parse_metadata_from_json(json_data, page_url)
            if not is_metadata_usable(meta):
                logger.warning("v2 metadata API for {} returned incomplete data, falling back to v1", video_id)
                return None
            return meta, json_data
        except Exception as exc:  # noqa: BLE001
            logger.warning("v2 metadata API failed for {}: {} -- falling back to v1 HTML scrape", video_id, exc)
            return None

    async def _site_metadata(self, item: LibraryItem) -> tuple[VideoMetadata, Optional[dict]]:
        """Details + download link of a site video (2.27.0): from the site's API only --
        the details JSON and its /api/videos/<_id>/download endpoint. Only when
        download.website_fallback is on (config.yaml) is the web page read as well,
        as before; the site's terms forbid scraping, so it's off by default."""
        v2_result = await self._fetch_metadata_v2_or_none(item.id, item.url, item.site)
        if v2_result is not None and v2_result[0].video_url:
            return v2_result
        if not get_settings().download.website_fallback:
            raise RuntimeError(
                "The site's API didn't return this video (removed, private, a link the API doesn't know, "
                "or the site is unreachable). Reading its web page instead is off (download.website_fallback).")
        html = await get_client(item.site).fetch_page(item.url)
        v1_meta = parse_metadata(html, item.url, get_site(item.site).cdn_base)
        raw_json: Optional[dict] = None
        if v2_result is not None:
            meta, raw_json = v2_result
            meta.video_url = v1_meta.video_url
            meta.is_hls = v1_meta.is_hls
            if not meta.funscript_url:
                meta.funscript_url = v1_meta.funscript_url
        else:
            meta = v1_meta
        if not meta.video_url:
            debug_path = self._dump_debug_html(item.id, html)
            raise RuntimeError(
                "Could not find a downloadable video URL on the page"
                + (f" (raw HTML saved to {debug_path} for debugging)" if debug_path else "")
            )
        return meta, raw_json

    async def _fetch_metadata(self, item: LibraryItem) -> None:
        # 2.27.0: the site's API only (see _site_metadata).
        meta, raw_json = await self._site_metadata(item)

        tags = list(dict.fromkeys(meta.tags or []))
        performers = [m.get("name") for m in (meta.models or []) if m.get("name")]

        # Everything below (thumbnail / preview clip / metadata JSON) is
        # already known in full at this point -- title, description, tags,
        # models, music, rating, views, timeline thumbnails, and both the
        # thumbnail and preview CDN URLs all come straight from the
        # metadata fetch above, well before the actual video file starts
        # downloading. Grabbing them now means the item shows real details
        # (and a working hover-preview clip) while it's still sitting in
        # "fetching"/"pending"/"downloading", instead of only once the full
        # video download finishes. Every step here is best-effort: if any
        # of it fails or is skipped, _download() still has its own
        # thumbnail/preview/metadata-JSON steps as a fallback afterward, so
        # nothing here is required for the item to complete successfully.
        creator_name = safe_name(meta.creator_name or "unknown")
        cache_creator_dir = self.settings.cache_dir / creator_name
        cache_creator_dir.mkdir(parents=True, exist_ok=True)
        base_name = safe_name(f"{meta.creator_name} - {meta.upload_date or 'unknown-date'} - {meta.title} [{item.id}]")

        # Precomputed thumbnail/preview destination paths -- known up
        # front (even though the actual download for each only happens
        # below, if at all) so the cancellation-cleanup step in the
        # `finally` block further down always knows exactly which
        # file(s) COULD have been mid-write, regardless of exactly where
        # a cancellation/removal landed. None means "this asset wasn't
        # going to be fetched this run anyway" (no thumbnail_url/
        # preview_url on this metadata) -- nothing to clean up for it.
        thumb_dl_path: Optional[Path] = None
        if meta.thumbnail_url:
            thumb_ext = meta.thumbnail_url.rsplit(".", 1)[-1].split("?")[0].lower()
            if thumb_ext not in ("jpg", "jpeg", "png", "webp"):
                thumb_ext = "jpg"
            thumb_dl_path = cache_creator_dir / f"{base_name}.{thumb_ext}"

        preview_dl_path: Optional[Path] = (
            cache_creator_dir / f"{base_name}_preview.mp4" if meta.preview_url else None
        )

        try:
            thumbnail_path: Optional[str] = None
            if thumb_dl_path is not None:
                try:
                    await get_client(item.site).stream_download(meta.thumbnail_url, thumb_dl_path)
                    thumbnail_path = str(thumb_dl_path)
                except Exception:
                    logger.warning("Early thumbnail fetch failed for {}, will retry during download", item.id)

            preview_path: Optional[str] = None
            if preview_dl_path is not None:
                try:
                    await get_client(item.site).stream_download(meta.preview_url, preview_dl_path)
                    preview_path = str(preview_dl_path)
                except Exception:
                    logger.warning("Early preview fetch failed for {}, will retry during download", item.id)

            # Provisional metadata JSON. If v2 succeeded, the raw API response
            # (raw_json) becomes the file's content verbatim, PLUS video_url/
            # is_hls/funscript_url added in for reference -- this is the "raw
            # JSON as the main metadata file" behavior: it carries a lot more
            # than the curated VideoMetadata fields alone (hasExtremeContent,
            # hasTransContent, likes/dislikes/favorites, engagementScore,
            # phash, aspectRatio, contentType, filename, etc.), all preserved
            # instead of discarded. Falls back to the older curated
            # VideoMetadata.to_dict() shape only if v2 failed (rare). Either
            # way, "video_details" gets overwritten by _download() once the
            # file is actually downloaded and real technical details (codec/
            # exact bitrate/verified resolution/filesize/duration) can be read
            # back off it via MediaInfo -- this early version only has
            # whatever PMVHaven's page/API claims.
            metadata_path: Optional[str] = None
            if self.settings.download.save_metadata_json:
                try:
                    if raw_json is not None:
                        meta_dict = dict(raw_json)
                        meta_dict["video_url"] = meta.video_url
                        meta_dict["is_hls"] = meta.is_hls
                        meta_dict["funscript_url"] = meta.funscript_url
                    else:
                        meta_dict = meta.to_dict()
                    meta_path = cache_creator_dir / f"{base_name}.json"
                    meta_path.write_text(json.dumps(meta_dict, indent=2, ensure_ascii=False), encoding="utf-8")
                    metadata_path = str(meta_path)
                except Exception:
                    logger.warning("Early metadata JSON write failed for {}, will retry during download", item.id)

            async with self._lock:
                # Guard against a race with remove()/_cancel_in_flight(): if
                # this item was already removed while this fetch was in
                # flight (normally it would have been cancelled instead, but
                # this is a defensive backstop), don't resurrect it by saving
                # to a row that was just intentionally deleted.
                if item.id not in self._items:
                    return
                item.title = meta.title or item.title
                item.creator = meta.creator_name
                item.tags = tags
                item.performers = performers
                item.ratings = meta.rating_percent
                item.timeline_thumbnails = meta.timeline_thumbnails
                item.description = meta.description or item.description
                item.music = [m.get("label") for m in (meta.music or []) if m.get("label")]
                item.upload_date = meta.upload_date or item.upload_date
                if thumbnail_path:
                    item.thumbnail_path = thumbnail_path
                    item.thumbnail_updated_at = time.time()
                if preview_path:
                    item.preview_path = preview_path
                    item.preview_updated_at = time.time()  # new URL, so browsers don't show the old clip
                if metadata_path:
                    item.metadata_path = metadata_path
                item.status = "pending"
                item.touch()
                self.db.save_item(item)
            store = get_log_store()
            if store is not None and meta.title:
                store.retarget(item.id, meta.title)  # "Add to library · <link>" -> the title
                logger.info("Fetched metadata for '{}' [{}] -- queued for download", item.title or item.url, item.id)
        finally:
            async with self._lock:
                still_exists = item.id in self._items
            if not still_exists:
                # This metadata fetch was cancelled, or the item was
                # removed mid-flight (see remove()/_cancel_in_flight()) --
                # clean up whichever partial thumbnail/preview ".part"
                # file was left behind by stream_download(), so
                # cancelling during the "fetching" stage doesn't leave
                # orphaned partial files either (mirrors the same cleanup
                # _download() does further below for the main video/
                # thumbnail/funscript/preview files).
                cleanup_partial_download_file(thumb_dl_path)
                cleanup_partial_download_file(preview_dl_path)

        # stash parsed metadata (and the raw JSON, if v2 succeeded) on the
        # instance for the download step
        item._meta = meta  # type: ignore[attr-defined]
        item._meta_raw = raw_json  # type: ignore[attr-defined]
        self._ensure_worker()  # a download slot might be free and waiting
