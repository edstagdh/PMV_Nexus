"""Shared scenes (3.9.0, backend/scene_match.py): which videos use the same footage.

One job on Tasks ("Finding shared scenes"): first the frame hashes of every video whose seekbar
previews are new or changed (videos without previews are skipped -- they get previews by
themselves, or from Maintenance -> Seekbar previews), then every pair of videos compared again.
It runs by itself once the seekbar-preview queue empties, and from Maintenance -> Shared scenes.

Shown on the video page ("Shares scenes with"), and videos sharing scenes rank higher among the
related videos. Only database rows are written here -- no file is created or removed.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from loguru import logger

from ..i18n import L
from .. import activity, scene_match


class ScenesMixin:
    _scene_task: Optional[asyncio.Task] = None
    _scene_stop = False
    _scene_again = False
    _scene_links: Optional[dict] = None   # video id -> {other id: row seen from this video}
    _scene_last: dict = {}

    # ---------------------------------------------------------------- reading
    def _scene_load(self) -> dict:
        if self._scene_links is None:
            links: dict = {}
            for r in self.db.scene_link_rows():
                links.setdefault(r["a"], {})[r["b"]] = {"frames": r["frames_a"], "other_frames": r["frames_b"],
                                                        "scenes": r["scenes"]}
                links.setdefault(r["b"], {})[r["a"]] = {"frames": r["frames_b"], "other_frames": r["frames_a"],
                                                        "scenes": [[s[2], s[3], s[0], s[1]] for s in r["scenes"]]}
            self._scene_links = links
        return self._scene_links

    def scene_partners(self, video_id: str) -> dict[str, int]:
        """Other video id -> matching frames of this video (for the related videos)."""
        return {k: v["frames"] for k, v in self._scene_load().get(video_id, {}).items() if k in self._items}

    def shared_scenes_for(self, video_id: str) -> list[dict]:
        if video_id not in self._items:
            raise KeyError(video_id)
        out = []
        for other, row in self._scene_load().get(video_id, {}).items():
            item = self._items.get(other)
            if not item or item.status != "done":
                continue
            out.append({"id": other, "title": item.title, "creator": item.creator, "length": item.tech_duration,
                        "thumb": f"/api/library/{other}/thumbnail?v={item.thumbnail_updated_at or ''}" if item.thumbnail_path else None,
                        "frames": row["frames"], "scenes": row["scenes"][:6],
                        "seconds": round(sum(s[1] - s[0] for s in row["scenes"]) + 2 * len(row["scenes"]))})
        out.sort(key=lambda x: (x["seconds"], x["frames"]), reverse=True)
        return out[:30]

    def shared_scenes_overview(self) -> dict:
        marks = self.db.scene_hash_marks()
        done = [i for i in self._items.values() if i.status == "done"]
        with_previews = sum(1 for i in done if self.storyboard_info(i.id))
        pairs = sum(len(v) for v in self._scene_load().values()) // 2
        return {"videos": len(done), "with_previews": with_previews,
                "hashed": sum(1 for i in done if marks.get(i.id, (0,))[0] == scene_match.VERSION),
                "pairs": pairs, "videos_linked": sum(1 for k, v in self._scene_load().items() if v and k in self._items),
                "size": self.db.scene_data_size(), "running": bool(self._scene_task and not self._scene_task.done()),
                "last": self._scene_last or None}

    # ---------------------------------------------------------------- the job
    def request_shared_scenes(self, source: str = "auto") -> bool:
        """Starts the job (or, if it's running, once more after it). False: it was running already."""
        if self._scene_task and not self._scene_task.done():
            self._scene_again = True
            return False
        self._scene_task = asyncio.create_task(self._scene_worker(source))
        return True

    async def _scene_worker(self, source: str) -> None:
        while True:
            self._scene_again = False
            await self._scene_run(source)
            if not self._scene_again or self._scene_stop:
                return
            source = "auto"

    async def _scene_run(self, source: str) -> None:
        state = {"stage": L("Reading frames"), "done": 0, "total": 0, "current": None}
        self._scene_stop = False

        def progress() -> dict:
            item = self._items.get(state["current"] or "")
            return {"stage": state["stage"], "done": state["done"], "total": state["total"],
                    "detail": item.title if item else None, "item": activity.item_ref(item)}

        def stop() -> None:
            self._scene_stop = True

        act = activity.start("shared_scenes", L("Finding shared scenes"), source=source, progress=progress)
        act.stopper = stop
        started = time.monotonic()
        try:
            marks = self.db.scene_hash_marks()
            gone = [v for v in marks if v not in self._items]
            if gone:
                self.db.delete_scene_hashes(gone)
            todo = []
            for item in self._items.values():
                if item.status != "done":
                    continue
                info = self.storyboard_info(item.id)
                if not info:
                    continue
                mark = marks.get(item.id)
                if not mark or mark[0] != scene_match.VERSION or mark[1] != info.get("made_at"):
                    todo.append((item.id, info))
            state["total"] = len(todo)
            hashed = 0
            for video_id, info in todo:
                if self._scene_stop:
                    break
                state["current"] = video_id
                try:
                    blob = await asyncio.to_thread(scene_match.hash_storyboard, self.storyboard_folder(video_id), info)
                    self.db.set_scene_hash(video_id, scene_match.VERSION, info.get("made_at"), blob)
                    hashed += 1
                except Exception as exc:  # noqa: BLE001 -- one bad sheet mustn't stop the rest
                    logger.warning("Shared scenes: couldn't read the frames of [{}]: {}", video_id, exc)
                state["done"] += 1
            state["current"] = None
            if self._scene_stop:
                act.finish("cancelled", (f"Stopped by {act.stopped_by} · " if act.stopped_by else "") + f"{hashed} videos read")
                return
            state.update(stage=L("Comparing"), done=0, total=1)
            blobs = {k: v for k, v in self.db.scene_hash_blobs().items() if k in self._items}
            links = await asyncio.to_thread(scene_match.find_links, blobs, lambda: self._scene_stop)
            self.db.replace_scene_links([(lk.a, lk.b, lk.frames_a, lk.frames_b, lk.scenes) for lk in links])
            self._scene_links = None
            state["done"] = 1
            took = time.monotonic() - started
            self._scene_last = {"at": time.time(), "pairs": len(links), "videos": len(blobs), "seconds": round(took, 1)}
            logger.info("Shared scenes: {} videos read again, {} compared, {} pairs share footage ({:.1f}s)",
                        hashed, len(blobs), len(links), took)
            act.finish("success", f"{len(links)} pairs of videos share scenes · {len(blobs)} videos compared")
        except InterruptedError:
            act.finish("cancelled", (f"Stopped by {act.stopped_by}" if act.stopped_by else "Stopped"))
        except BaseException as exc:
            logger.exception("Shared scenes stopped by an error: {}", exc)
            act.finish("failed", L('Stopped by an error (see the log)'))
            if not isinstance(exc, Exception):
                raise
