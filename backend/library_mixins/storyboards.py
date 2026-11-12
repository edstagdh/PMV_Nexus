"""
Seekbar previews (2.21.0, backend/storyboard.py): one queue, one video at a
time, at low priority. Videos get theirs
  - after they're downloaded or uploaded,
  - the first time they're played without one (the player asks for it),
  - from Management -> Maintenance -> Seekbar previews -> Make missing ones.
While the queue has work it's one job on Tasks ("Making seekbar previews"),
which can be stopped (the rest is made when those videos are played).
"""
from __future__ import annotations

import asyncio
import shutil
from collections import deque
from pathlib import Path
from typing import Optional

from loguru import logger

from .. import activity, storyboard


class StoryboardsMixin:
    _sb_queue: Optional[deque] = None
    _sb_task: Optional[asyncio.Task] = None
    _sb_stop = False
    _sb_state: dict = {}

    # ---------------------------------------------------------------- files
    def storyboard_root(self) -> Path:
        return self.settings.cache_dir / storyboard.FOLDER

    def storyboard_folder(self, video_id: str) -> Path:
        return self.storyboard_root() / video_id

    def storyboard_info(self, video_id: str) -> Optional[dict]:
        if video_id not in self._items:  # only library ids ever become a path
            return None
        return storyboard.read_info(self.storyboard_folder(video_id))

    def storyboard_sheet(self, video_id: str, n: int) -> Optional[Path]:
        info = self.storyboard_info(video_id)
        if not info or not 0 <= n < info["sheets"]:
            return None
        path = self.storyboard_folder(video_id) / f"{n}.webp"
        return path if path.is_file() else None

    def remove_storyboard(self, video_id: str) -> None:
        if not video_id or "/" in video_id or "\\" in video_id or video_id.startswith("."):
            return
        shutil.rmtree(self.storyboard_folder(video_id), ignore_errors=True)

    def _sb_candidate(self, video_id: str) -> bool:
        item = self._items.get(video_id)
        return bool(item and item.status == "done" and item.video_path and Path(item.video_path).exists())

    # ---------------------------------------------------------------- queue
    def request_storyboard(self, video_id: str, first: bool = False) -> bool:
        """Queues the video (unless it has one or is queued already). `first`: next in line (someone's watching it)."""
        if self._sb_queue is None:
            self._sb_queue = deque()
        if video_id in self._sb_queue or self._sb_state.get("current") == video_id:
            if first and video_id in self._sb_queue:
                self._sb_queue.remove(video_id)
                self._sb_queue.appendleft(video_id)
            return True
        if self.storyboard_info(video_id) or not self._sb_candidate(video_id):
            return False
        (self._sb_queue.appendleft if first else self._sb_queue.append)(video_id)
        self._sb_state["total"] = self._sb_state.get("total", 0) + 1
        if self._sb_task is None or self._sb_task.done():
            self._sb_task = asyncio.create_task(self._sb_worker())
        return True

    def queue_missing_storyboards(self) -> int:
        """Management -> Maintenance: every finished video without seekbar previews."""
        ids = sorted((i for i in self._items.values() if i.status == "done"),
                     key=lambda i: i.download_datetime or "", reverse=True)  # newest first
        return sum(1 for i in ids if self.request_storyboard(i.id))

    def storyboards_overview(self) -> dict:
        done = [i for i in self._items.values() if self._sb_candidate(i.id)]  # finished, file there
        root = self.storyboard_root()
        made = sum(1 for i in done if (root / i.id / "info.json").is_file())
        size = 0
        if root.is_dir():
            for f in root.rglob("*"):
                try:
                    size += f.stat().st_size if f.is_file() else 0
                except OSError:
                    pass
        return {"videos": len(done), "made": made, "missing": len(done) - made, "size": size,
                "queued": len(self._sb_queue or ()), "running": bool(self._sb_task and not self._sb_task.done())}

    async def _sb_worker(self) -> None:
        state = self._sb_state = {"total": len(self._sb_queue), "done": 0, "failed": 0, "current": None}
        self._sb_stop = False

        def progress() -> dict:
            left = len(self._sb_queue or ())
            total = state["done"] + state["failed"] + left + (1 if state["current"] else 0)
            item = self._items.get(state["current"] or "")
            return {"stage": "Making", "done": state["done"] + state["failed"], "total": total,
                    "detail": f"{item.title}" if item else None}

        def stop() -> None:
            self._sb_stop = True
            self._sb_queue.clear()

        act = activity.start("storyboards", "Making seekbar previews", source="auto", progress=progress)
        act.stopper = stop
        try:
            while self._sb_queue and not self._sb_stop:
                video_id = self._sb_queue.popleft()
                item = self._items.get(video_id)
                if not item or not self._sb_candidate(video_id) or self.storyboard_info(video_id):
                    continue
                state["current"] = video_id
                try:
                    meta = await asyncio.to_thread(storyboard.make, item.video_path, self.storyboard_folder(video_id),
                                                   lambda: self._sb_stop or video_id not in self._items)
                    state["done"] += 1
                    logger.info("Seekbar previews made for '{}' [{}]: {} frames every {} s", item.title, video_id,
                                meta["frames"], meta["interval"])
                except InterruptedError:
                    if video_id not in self._items:
                        self.remove_storyboard(video_id)
                except Exception as exc:  # noqa: BLE001 -- one bad file mustn't stop the rest
                    state["failed"] += 1
                    logger.warning("Couldn't make seekbar previews for '{}' [{}]: {}", item.title, video_id, exc)
                finally:
                    state["current"] = None
            parts = [f"{state['done']} made"] + ([f"{state['failed']} failed (see the log)"] if state["failed"] else [])
            act.finish("cancelled" if self._sb_stop else ("failed" if state["failed"] and not state["done"] else "success"),
                       (f"Stopped by {act.stopped_by} · " if act.stopped_by else "") + ", ".join(parts))
        except BaseException:
            act.finish("failed", "Stopped by an error (see the log)")
            raise
