from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from ..models import LibraryItem, ShareDestinationResult
from .. import share_backends
from ..notifications import notify, title_of, video_link


class ShareWorkerMixin:

    async def request_share(
            self,
            video_id: str,
            destinations: list[str],
            telegram_target: str = "channel",
            caption: Optional[str] = None,
    ) -> LibraryItem:
        if not self.settings.features.share:
            raise ValueError("Sharing is switched off (Server settings → Credentials)")
        async with self._lock:
            item = self._items.get(video_id)
            if not item:
                raise ValueError("Not found")
            if item.status != "done" or not item.video_path:
                raise ValueError("Only completed downloads can be shared")

            valid = {"telegram", "gofile", "pixeldrain"}
            unknown = set(destinations) - valid
            if unknown:
                raise ValueError(f"Unknown share destination(s): {', '.join(sorted(unknown))}")
            if not destinations:
                raise ValueError("Select at least one destination to share to")

            # fail fast on missing config rather than queuing then failing later
            cfg = self.settings.share
            if "telegram" in destinations:
                if not (cfg.telegram_api_id and cfg.telegram_api_hash):
                    raise ValueError("Telegram is not configured -- set the API ID/hash in Admin \u2192 Credentials")
                target_id = cfg.telegram_channel_id if telegram_target == "channel" else cfg.telegram_group_id
                if not target_id:
                    raise ValueError(f"No Telegram {telegram_target} configured (Admin \u2192 Credentials)")
            if "gofile" in destinations and not cfg.gofile_api_key:
                raise ValueError("Gofile is not configured -- set its API key in Admin \u2192 Credentials")
            if "pixeldrain" in destinations and not cfg.pixeldrain_api_key:
                raise ValueError("Pixeldrain is not configured -- set its API key in Admin \u2192 Credentials")

            for dest in destinations:
                existing = item.shares.get(dest)
                if existing and existing.status in ("queued", "sharing"):
                    continue  # already in flight -- don't double-queue it
                item.shares[dest] = ShareDestinationResult(status="queued")
            item.touch()
            self.db.save_item(item)
            self._share_queue.append((video_id, list(destinations), telegram_target, caption))

        self._ensure_share_worker()
        return item

    def _ensure_share_worker(self) -> None:
        if self._share_task is None or self._share_task.done():
            self._share_task = asyncio.create_task(self._share_worker_loop())

    async def _share_worker_loop(self) -> None:
        while True:
            if self._shutting_down:
                return
            async with self._lock:
                job = self._share_queue.pop(0) if self._share_queue else None
            if job is None:
                return

            video_id, destinations, telegram_target, caption = job
            item = self._items.get(video_id)
            if not item:
                continue

            # each destination is an independent upload -- run them concurrently
            # rather than making the user wait for Telegram before Gofile starts
            await asyncio.gather(
                *(self._share_one(item, dest, telegram_target, caption) for dest in destinations)
            )

    async def _share_one(
            self,
            item: LibraryItem,
            destination: str,
            telegram_target: str,
            caption: Optional[str],
    ) -> None:
        async with self._lock:
            item.shares[destination] = ShareDestinationResult(status="sharing", progress_percent=0)
            item.touch()
            self.db.save_item(item)

        logger.info("Uploading '{}' [{}] to {}", item.title, item.id, destination)

        loop = asyncio.get_running_loop()

        def _apply_progress(pct: float) -> None:
            # Runs on the event loop thread only (see on_progress below) --
            # every progress save in this class now funnels through here, so
            # two writers can never race on the same SQLite row again.
            try:
                result = item.shares.get(destination)
                if not result or result.progress_percent == pct:
                    return
                now = time.time()
                result.status = "sharing"
                result.progress_percent = pct
                item.touch()
                last_saved = self._share_progress_last_save.get((item.id, destination), 0.0)
                if now - last_saved >= 1.0:
                    self._share_progress_last_save[(item.id, destination)] = now
                    self.db.save_item(item)
            except Exception:  # noqa: BLE001
                logger.exception("Error while recording share progress for {}/{}", item.id, destination)

        def on_progress(pct: float) -> None:
            # IMPORTANT: Pyrogram calls this from its own executor thread
            # while uploading (unlike Gofile/Pixeldrain, whose progress
            # fires from an async generator already on the event loop). Any
            # blocking work done directly here -- like a SQLite write --
            # races with the event loop's own writes (e.g. a download in
            # progress) and can corrupt/stall Pyrogram's own upload thread.
            # Hop back onto the loop instead of touching shared state or
            # disk from this thread.
            loop.call_soon_threadsafe(_apply_progress, pct)

        try:
            video_path = Path(item.video_path)
            if destination == "telegram":
                thumb_path = Path(item.thumbnail_path) if item.thumbnail_path and Path(item.thumbnail_path).exists() else None
                link = await share_backends.share_via_telegram(
                    video_path,
                    self.settings,
                    telegram_target,
                    caption or self._build_telegram_caption(item),
                    on_progress,
                    thumbnail_path=thumb_path,
                    file_name=video_path.name,
                )
            elif destination == "gofile":
                link = await share_backends.share_via_gofile(video_path, self.settings, on_progress)
            elif destination == "pixeldrain":
                link = await share_backends.share_via_pixeldrain(video_path, self.settings, on_progress)
            else:
                raise ValueError(f"Unknown share destination: {destination}")

            async with self._lock:
                item.shares[destination] = ShareDestinationResult(status="done", progress_percent=100, link=link)
                item.touch()
                self.db.save_item(item)
                logger.info("Share to {} succeeded for '{}' [{}] -> {}", destination, item.title, item.id, link)
            notify("share", "success", f"Shared {title_of(item)} to {destination.capitalize()}", link=video_link(item),
                   user_id=item.added_by)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Share to {} failed for {}", destination, item.id)
            async with self._lock:
                item.shares[destination] = ShareDestinationResult(status="failed", error=str(exc))
                item.touch()
                self.db.save_item(item)
            notify("share", "error", f"Sharing to {destination.capitalize()} failed: {title_of(item)}",
                   body=str(exc)[:300], link=video_link(item), user_id=item.added_by)
        finally:
            self._share_progress_last_save.pop((item.id, destination), None)

    def _build_telegram_caption(self, item: LibraryItem) -> str:
        """Builds a caption from the video's own metadata -- title, creator,
        the technical badges already shown on the library card, and (if the
        metadata JSON is still on disk) its tags -- so shares don't go out
        as a bare filename. "tags" is the same top-level key name in both
        the raw v2 and curated v1-fallback metadata JSON shapes."""
        lines = [item.title or Path(item.video_path).stem]

        details = []
        if item.creator:
            details.append(f"Creator: {item.creator}")
        if item.tech_duration:
            details.append(f"Duration: {item.tech_duration}")
        if item.ratings:
            details.append(f"Ratings: {item.ratings}")
        if item.tech_resolution:
            details.append(f"Resolution: {item.tech_resolution}")
        if item.tech_codec:
            details.append(f"Codec: {item.tech_codec}")
        if item.tech_filesize:
            details.append(f"Size: {item.tech_filesize}")
        if details:
            lines.append(" | ".join(details))

        if item.metadata_path:
            try:
                meta = json.loads(Path(item.metadata_path).read_text(encoding="utf-8"))
                tags = [t for t in meta.get("tags", []) if t][:10]  # keep well under Telegram's caption limit
                if tags:
                    lines.append(" ".join(f"#{re.sub(r'[^0-9A-Za-z_]', '', t)}" for t in tags))
            except Exception:
                logger.debug("Could not read tags from metadata JSON for {}", item.id)

        if item.url:
            lines.append(item.url)

        return "\n\n".join(lines)[:1024]  # Telegram's hard cap on media captions
