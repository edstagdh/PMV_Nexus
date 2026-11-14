"""
Video frames under the player (3.2.0, backend/video_frames.py): one queue, one
video at a time. Videos get theirs
  - after they're downloaded or uploaded,
  - the first time their page is opened without them (the page asks),
  - from Management -> Maintenance -> Video frames -> Make missing ones.
The site's own timeline frames are used when the video has them (asked for
once from the site when an older video has none saved, unless it's gone from
the site); otherwise they're made from the file.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections import deque
from pathlib import Path
from typing import Optional

from loguru import logger

from .. import activity, sources, video_frames


class FramesMixin:
    _fr_queue: Optional[deque] = None
    _fr_task: Optional[asyncio.Task] = None
    _fr_stop = False
    _fr_state: dict = {}

    # ---------------------------------------------------------------- files
    def frames_root(self) -> Path:
        return self.settings.cache_dir / video_frames.FOLDER

    def frames_folder(self, video_id: str) -> Path:
        return self.frames_root() / video_id

    def frames_info(self, video_id: str) -> Optional[dict]:
        if video_id not in self._items:  # only library ids ever become a path
            return None
        return video_frames.read_info(self.frames_folder(video_id))

    def frame_file(self, video_id: str, n: int) -> Optional[Path]:
        info = self.frames_info(video_id)
        if not info or not 0 <= n < len(info["frames"]):
            return None
        path = self.frames_folder(video_id) / info["frames"][n]["file"]
        return path if path.is_file() else None

    def remove_frames(self, video_id: str) -> None:
        if not video_id or "/" in video_id or "\\" in video_id or video_id.startswith("."):
            return
        shutil.rmtree(self.frames_folder(video_id), ignore_errors=True)

    def _fr_candidate(self, video_id: str) -> bool:
        item = self._items.get(video_id)
        return bool(item and item.status == "done" and item.video_path and Path(item.video_path).exists())

    # ---------------------------------------------------------------- queue
    def request_frames(self, video_id: str, first: bool = False) -> bool:
        """Queues the video (unless it has them or is queued). `first`: next in line (its page is open)."""
        if self._fr_queue is None:
            self._fr_queue = deque()
        if video_id in self._fr_queue or self._fr_state.get("current") == video_id:
            if first and video_id in self._fr_queue:
                self._fr_queue.remove(video_id)
                self._fr_queue.appendleft(video_id)
            return True
        if self.frames_info(video_id) or not self._fr_candidate(video_id):
            return False
        (self._fr_queue.appendleft if first else self._fr_queue.append)(video_id)
        if self._fr_task is None or self._fr_task.done():
            self._fr_task = asyncio.create_task(self._fr_worker())
        return True

    def queue_missing_frames(self) -> int:
        items = sorted((i for i in self._items.values() if i.status == "done"),
                       key=lambda i: i.download_datetime or "", reverse=True)  # newest first
        return sum(1 for i in items if self.request_frames(i.id))

    def frames_overview(self) -> dict:
        done = [i for i in self._items.values() if self._fr_candidate(i.id)]
        root = self.frames_root()
        made = sum(1 for i in done if (root / i.id / "info.json").is_file())
        size = 0
        if root.is_dir():
            for f in root.rglob("*"):
                try:
                    size += f.stat().st_size if f.is_file() else 0
                except OSError:
                    pass
        return {"videos": len(done), "made": made, "missing": len(done) - made, "size": size,
                "queued": len(self._fr_queue or ()), "running": bool(self._fr_task and not self._fr_task.done())}

    def redo_frames(self, video_id: str) -> bool:
        """Video page -> frames -> "Make them again" (e.g. after the site's frames changed)."""
        if not self._fr_candidate(video_id):
            return False
        self.remove_frames(video_id)
        return self.request_frames(video_id, first=True)

    async def _site_timeline(self, item) -> list[dict]:
        """The site's frames for `item`: the saved list, else asked for once from the site."""
        if item.timeline_thumbnails:
            return list(item.timeline_thumbnails)
        if item.site == "local" or item.site_status == "removed" or not item.url:
            return []
        try:
            v2 = await self._fetch_metadata_v2_or_none(item.id, item.url, item.site)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Video frames: no details from the site for {}: {}", item.id, exc)
            return []
        timeline = v2[0].timeline_thumbnails if v2 else []
        if timeline:
            async with self._lock:
                if item.id in self._items:
                    item.timeline_thumbnails = timeline
                    self.db.save_item(item)
        return timeline

    async def _frames_from_site(self, item, timeline: list[dict]) -> Optional[dict]:
        source = sources.get(item.site)
        blobs: dict[str, bytes] = {}
        with tempfile.TemporaryDirectory(prefix="pmv-frames-") as tmp:
            for n, entry in enumerate(timeline):
                if self._fr_stop or item.id not in self._items:
                    raise InterruptedError
                dest = Path(tmp) / f"{n}.img"
                try:
                    await source.download(entry["url"], dest)
                    blobs[entry["url"]] = dest.read_bytes()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Video frame {} of {}: {}", n, item.id, exc)
        if not blobs:
            return None
        duration = 0.0
        try:
            from .. import media_tools
            info = await asyncio.to_thread(media_tools.probe_streams, item.video_path)
            duration = float(info.get("duration") or 0)
        except Exception:  # noqa: BLE001
            pass
        try:
            return await asyncio.to_thread(video_frames.from_site, timeline, self.frames_folder(item.id),
                                           lambda url: blobs[url], duration)
        except ValueError as exc:
            logger.debug("Video frames: the site's frames for {} can't be used: {}", item.id, exc)
            return None

    async def _fr_worker(self) -> None:
        state = self._fr_state = {"done": 0, "failed": 0, "current": None}
        self._fr_stop = False

        def progress() -> dict:
            left = len(self._fr_queue or ())
            total = state["done"] + state["failed"] + left + (1 if state["current"] else 0)
            item = self._items.get(state["current"] or "")
            return {"stage": "Making", "done": state["done"] + state["failed"], "total": total,
                    "detail": item.title if item else None}

        def stop() -> None:
            self._fr_stop = True
            self._fr_queue.clear()

        act = activity.start("video_frames", "Making video frames", source="auto", progress=progress)
        act.stopper = stop
        try:
            while self._fr_queue and not self._fr_stop:
                video_id = self._fr_queue.popleft()
                item = self._items.get(video_id)
                if not item or not self._fr_candidate(video_id) or self.frames_info(video_id):
                    continue
                state["current"] = video_id
                try:
                    meta = None
                    timeline = await self._site_timeline(item)
                    if timeline and item.site != "local":
                        meta = await self._frames_from_site(item, timeline)
                    if meta is None:
                        meta = await asyncio.to_thread(video_frames.from_video, item.video_path, self.frames_folder(video_id),
                                                       lambda: self._fr_stop or video_id not in self._items)
                    state["done"] += 1
                    logger.info("Video frames for '{}' [{}]: {} from {}", item.title, video_id, len(meta["frames"]),
                                "the site" if meta["source"] == "site" else "the file")
                except InterruptedError:
                    if video_id not in self._items:
                        self.remove_frames(video_id)
                except Exception as exc:  # noqa: BLE001 -- one bad file mustn't stop the rest
                    state["failed"] += 1
                    logger.warning("Couldn't make video frames for '{}' [{}]: {}", item.title, video_id, exc)
                finally:
                    state["current"] = None
            parts = [f"{state['done']} made"] + ([f"{state['failed']} failed (see the log)"] if state["failed"] else [])
            act.finish("cancelled" if self._fr_stop else ("failed" if state["failed"] and not state["done"] else "success"),
                       (f"Stopped by {act.stopped_by} · " if act.stopped_by else "") + ", ".join(parts))
        except BaseException:
            act.finish("failed", "Stopped by an error (see the log)")
            raise
