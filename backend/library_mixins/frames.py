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
import hashlib
import shutil
import time
import tempfile
from collections import deque
from pathlib import Path
from typing import Optional

from loguru import logger

from ..i18n import L
from .. import activity, sources, video_frames


class FramesMixin:
    _fr_queue: Optional[deque] = None
    _fr_task: Optional[asyncio.Task] = None
    _fr_stop = False
    _fr_state: dict = {}
    _fr_from_file: set = set()  # ids whose next frames are made from the file ("Redo Frames")

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

    @staticmethod
    def _fr_wants_more(info: dict) -> bool:
        """Vertical with the 8 (made before 3.4.0, or 24 didn't work): only ever queued again by
        seed_portrait_frames (once) or "Make them again" -- request_frames skips videos that have frames."""
        return (info.get("height") or 0) > (info.get("width") or 0) and len(info["frames"]) < video_frames.PORTRAIT_COUNT

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
        """Video page -> "Redo Frames": made again FROM THE FILE (3.4.2) -- the site's are what was
        there already, and the reason to redo them (blank, placeholder or wrong ones)."""
        if not self._fr_candidate(video_id):
            return False
        self._fr_from_file.add(video_id)
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

    async def _portrait_frames(self, item) -> Optional[dict]:
        """A vertical video (3.4.0): 24 frames from the file (video_frames.PORTRAIT_COUNT).
        None when it isn't vertical or that didn't work -- then the usual 8."""
        from .. import media_tools
        info = await asyncio.to_thread(media_tools.probe_streams, item.video_path)
        if not video_frames.is_portrait(info):
            return None
        try:
            return await asyncio.to_thread(
                video_frames.from_video, item.video_path, self.frames_folder(item.id),
                lambda: self._fr_stop or item.id not in self._items, video_frames.PORTRAIT_COUNT,
                video_frames.PORTRAIT_SIDE, video_frames.PORTRAIT_MIN)
        except InterruptedError:
            raise
        except Exception as exc:  # noqa: BLE001 -- the usual 8 instead
            logger.info("Video frames for vertical '{}' [{}]: {} of them didn't work ({}); making the usual {}",
                        item.title, item.id, video_frames.PORTRAIT_COUNT, exc, video_frames.COUNT)
            return None

    def seed_portrait_frames(self) -> None:
        """3.4.0, once: vertical videos that have the 8 frames get 24 instead (in the background)."""
        flag = "portrait_frames_24_340"
        if self.db.get_app_state(flag, False):
            return
        self.db.set_app_state(flag, time.time())
        redo = [i for i in self._items.values() if (info := self.frames_info(i.id))
                and (info.get("height") or 0) > (info.get("width") or 0) and len(info["frames"]) < video_frames.PORTRAIT_COUNT]
        for item in redo:
            if self._fr_candidate(item.id):
                if self._fr_queue is None:
                    self._fr_queue = deque()
                self._fr_queue.append(item.id)  # made again; the old ones stay until the new ones are done
        if redo:
            logger.info("Video frames: {} vertical video(s) get 24 frames instead of 8", len(redo))
            if self._fr_task is None or self._fr_task.done():
                self._fr_task = asyncio.create_task(self._fr_worker())

    def seed_placeholder_frames(self) -> None:
        """3.4.2, once: videos whose saved site frames are all one placeholder picture get
        theirs made from the file (in the background)."""
        flag = "placeholder_frames_342"
        if self.db.get_app_state(flag, False):
            return
        self.db.set_app_state(flag, time.time())

        def find() -> list[str]:
            out = []
            for video_id in list(self._items):
                info = self.frames_info(video_id)
                if not info or info.get("source") != "site" or len(info["frames"]) < 2:
                    continue
                try:
                    pics = {hashlib.sha1((self.frames_folder(video_id) / f["file"]).read_bytes()).hexdigest()
                            for f in info["frames"]}
                except OSError:
                    continue
                if len(pics) * 2 <= len(info["frames"]):
                    out.append(video_id)
            return out

        async def run() -> None:
            ids = await asyncio.to_thread(find)
            for video_id in ids:
                self.redo_frames(video_id)
            if ids:
                logger.info("Video frames: {} video(s) had placeholder frames from the site; making them from the file", len(ids))

        asyncio.create_task(run())

    async def _fr_worker(self) -> None:
        state = self._fr_state = {"done": 0, "failed": 0, "current": None}
        self._fr_stop = False

        def progress() -> dict:
            left = len(self._fr_queue or ())
            total = state["done"] + state["failed"] + left + (1 if state["current"] else 0)
            item = self._items.get(state["current"] or "")
            return {"stage": L('Making'), "done": state["done"] + state["failed"], "total": total,
                    "detail": item.title if item else None, "item": activity.item_ref(item)}

        def stop() -> None:
            self._fr_stop = True
            self._fr_queue.clear()

        act = activity.start("video_frames", L('Making video frames'), source="auto", progress=progress)
        act.stopper = stop
        try:
            while self._fr_queue and not self._fr_stop:
                video_id = self._fr_queue.popleft()
                item = self._items.get(video_id)
                info = self.frames_info(video_id)
                if not item or not self._fr_candidate(video_id) or (info and not self._fr_wants_more(info)):
                    continue
                state["current"] = video_id
                try:
                    from_file = video_id in self._fr_from_file
                    self._fr_from_file.discard(video_id)
                    meta = await self._portrait_frames(item)
                    timeline = await self._site_timeline(item) if meta is None and not from_file else []
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
            act.finish("failed", L('Stopped by an error (see the log)'))
            raise
