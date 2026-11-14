"""
Audio browsers can't play (3.0.1): some videos carry audio a browser can't
decode -- uncompressed PCM, AC-3, DTS, ... -- and play silent in the app's
player. Such audio is converted to AAC (media_tools.convert_audio_to_aac; the
picture is copied as it is):
  - right after a video is downloaded from a site (uploads already get this
    when they're re-packed),
  - for the videos already in the library by the Scheduler job "Make audio
    playable" (created once by 3.0.1 to run at night; Run now starts it at once).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger

from .. import activity, media_tools, open_files
from .core import JobBlockedError

SEED_FLAG = "seeded_job_audio_fix_301"
_ACTIVE = ("queued", "encoding")


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


class AudioFixMixin:

    async def _convert_audio_in_place(self, path: str, info: Optional[dict] = None) -> Optional[tuple[int, int]]:
        """Converts the audio of the file at `path` to AAC when a browser can't
        play it. Returns (bytes before, bytes after), or None when nothing was
        needed. Raises RuntimeError when it was needed but didn't work."""
        if info is None:
            info = await asyncio.to_thread(media_tools.probe_streams, path)
        bad = media_tools.unplayable_audio(info)
        if not bad:
            return None
        size = os.path.getsize(path)
        free = shutil.disk_usage(Path(path).parent).free
        if free < size + 512 * 1024 * 1024:
            raise RuntimeError(f"Not enough free space to convert its audio ({_mb(free)} free, {_mb(size)} needed)")
        tmp = await asyncio.to_thread(media_tools.convert_audio_to_aac, path, info)
        if tmp is None:
            raise RuntimeError(f"FFmpeg couldn't convert its {', '.join(bad)} audio (see the log)")
        try:
            for attempt in range(3):
                open_files.release(path)  # an open stream would block the swap on Windows
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2)
        except OSError as exc:
            Path(tmp).unlink(missing_ok=True)
            raise RuntimeError(f"Couldn't replace the file: {exc}") from exc
        after = os.path.getsize(path)
        logger.info("Audio converted to AAC ({} -> {}, {} -> {}): {}", ", ".join(bad), "aac", _mb(size), _mb(after), path)
        return size, after

    async def fix_item_audio(self, item) -> Optional[tuple[int, int]]:
        """_convert_audio_in_place for a library video, then its details (size,
        metadata file, fingerprint of the file) are brought up to date."""
        sizes = await self._convert_audio_in_place(item.video_path)
        if sizes is None:
            return None
        tech_info = await asyncio.to_thread(media_tools.get_technical_info, item.video_path)
        if item.metadata_path and Path(item.metadata_path).is_file():
            try:
                meta_path = Path(item.metadata_path)
                meta_dict = json.loads(meta_path.read_text(encoding="utf-8"))
                meta_dict.setdefault("video_details", {}).update(tech_info)
                meta_dict["video_details"]["Audio converted to AAC"] = True
                meta_path.write_text(json.dumps(meta_dict, indent=2, ensure_ascii=False), encoding="utf-8")
            except (OSError, ValueError):
                logger.warning("Could not update the metadata file of {} after converting its audio", item.id)
        source_hash = None
        if item.local_phash:  # the picture is the same, so the fingerprint stays; only the file changed
            source_hash = await asyncio.to_thread(media_tools.compute_file_sha256, item.video_path)
        async with self._lock:
            if item.id not in self._items:
                return sizes
            item.tech_filesize = tech_info.get("File Size") or item.tech_filesize
            item.tech_duration = tech_info.get("Duration") or item.tech_duration
            if source_hash:
                item.local_phash_source_hash = source_hash
                item.local_phash_source_size = sizes[1]
            item.touch()
            self.db.save_item(item)
        return sizes

    # ---------------------------------------------------------- Scheduler job
    async def run_audio_fix(self) -> dict:
        """Scheduler job "Make audio playable": checks every downloaded video's
        audio and converts what browsers can't play. Changes files, so it
        doesn't run alongside other jobs that do (duplicate scan, repair...)."""
        if not self._try_acquire_invasive_lock("audio_fix"):
            raise JobBlockedError(f"Blocked: '{self._invasive_lock_holder}' is already running")
        state = {"checked": 0, "converted": 0, "failed": 0, "total": 0, "saved": 0, "current": None}
        stop = {"now": False}
        try:
            items = [i for i in self._items.values()
                     if i.status == "done" and i.video_path and i.reencode_status not in _ACTIVE]
            state["total"] = len(items)

            def progress() -> dict:
                return {"stage": "Checking", "done": state["checked"], "total": state["total"],
                        "detail": f"{state['converted']} converted" + (f" · {state['current']}" if state["current"] else "")}

            def stop_now() -> None:
                stop["now"] = True

            async with activity.track("audio_fix", "Making audio playable", progress=progress, stop=stop_now):
                for item in items:
                    if stop["now"] or self._shutting_down:
                        break
                    state["checked"] += 1
                    if item.id not in self._items or item.reencode_status in _ACTIVE or not Path(item.video_path).is_file():
                        continue
                    state["current"] = item.title
                    try:
                        sizes = await self.fix_item_audio(item)
                        if sizes:
                            state["converted"] += 1
                            state["saved"] += sizes[0] - sizes[1]
                    except Exception as exc:  # noqa: BLE001 -- one bad file mustn't stop the rest
                        state["failed"] += 1
                        logger.warning("Couldn't make the audio of '{}' [{}] playable: {}", item.title, item.id, exc)
                    finally:
                        state["current"] = None
            result = {"checked": state["checked"], "converted": state["converted"], "failed": state["failed"]}
            if state["converted"]:
                result["space_saved"] = _mb(state["saved"]) if state["saved"] < 1024 ** 3 else f"{state['saved'] / 1024 ** 3:.2f} GB"
            logger.info("Make audio playable: {}", result)
            return result
        finally:
            self._release_invasive_lock("audio_fix")

    def seed_audio_fix_job(self) -> None:
        """3.0.1: a one-time "Make audio playable" job for the videos already in
        the library, tonight at 02:00 (only once; not for an empty library)."""
        if self.db.get_app_state(SEED_FLAG, False):
            return
        try:
            if any(i.status == "done" for i in self._items.values()):
                at = datetime.now().replace(hour=2, minute=0, second=0, microsecond=0)
                if at <= datetime.now():
                    at += timedelta(days=1)
                self.create_scheduled_job("audio_fix", "Make audio playable (one time)", True, "simple",
                                          {"kind": "once", "at": at.strftime("%Y-%m-%dT%H:%M")}, {})
                logger.info("Scheduler: added a one-time 'Make audio playable' job for {}", at.strftime("%Y-%m-%d %H:%M"))
            self.db.set_app_state(SEED_FLAG, time.time())
        except Exception:  # noqa: BLE001
            logger.exception("Could not add the one-time 'Make audio playable' job")
