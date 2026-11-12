from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from .. import media_tools, open_files, stats_store
from ..config import HEVC_PRESETS
from ..models import LibraryItem
from ..notifications import notify, title_of, video_link

PROGRESS_SAVE_EVERY = 10  # seconds between database writes of a running re-encode's progress

CODEC_LABELS = {"hevc": "HEVC", "av1": "AV1", "h264": "H.264"}


def reencode_options(codec: str, options: Optional[dict]) -> dict:
    """Checks one re-encode's settings for `codec` (HEVC: CRF 0-51 and an x265
    preset name; AV1: CRF 1-63 and preset 0-13); leaves out what isn't set."""
    out = {k: v for k, v in (options or {}).items() if v is not None}
    crf, preset = out.get("crf"), out.get("preset")
    if codec == "av1":
        if crf is not None and not 1 <= crf <= 63:
            raise ValueError("AV1 quality (CRF) is 1-63")
        if preset is not None:
            if not str(preset).isdigit() or not 0 <= int(preset) <= 13:
                raise ValueError("AV1 speed preset is 0-13")
            out["preset"] = str(int(preset))
    else:
        if crf is not None and not 0 <= crf <= 51:
            raise ValueError("HEVC quality (CRF) is 0-51")
        if preset is not None and preset not in HEVC_PRESETS:
            raise ValueError(f"Unknown HEVC preset '{preset}'")
    return out


class ReencodeWorkerMixin:

    async def request_reencode(self, video_id: str, codec: Optional[str] = None,
                               options: Optional[dict] = None) -> LibraryItem:
        """Queues a re-encode to `codec` ("hevc" / "av1"; default: the codec
        chosen in Server settings -> Re-encode), with `options` for this one
        re-encode (Re-encode dialog; see reencode_options()). Refused when the
        file is already in that codec (re-encoding it again would only lose
        quality), or when re-encoding is switched off."""
        if not self.settings.features.reencode:
            raise ValueError("Re-encoding is switched off (Server settings → Re-encode)")
        target = codec or self.settings.reencode.codec
        if target not in media_tools.REENCODE_ENCODERS:
            raise ValueError(f"Unknown codec '{target}'")
        options = reencode_options(target, options)
        async with self._lock:
            item = self._items.get(video_id)
            if not item:
                raise ValueError("Not found")
            if item.status != "done" or not item.video_path:
                raise ValueError("Only completed downloads can be re-encoded")
            if item.reencode_status in ("queued", "encoding"):
                return item
            video_path = item.video_path

        # Blocking disk I/O (MediaInfo parses the file) -- done outside the
        # lock and off the event loop thread so a burst of many reencode
        # requests in a row doesn't serialize behind each other's file
        # probing. Also cheaper: no reason to hold the lock during it.
        source = await asyncio.to_thread(media_tools.video_codec, video_path)
        if source == target:
            raise ValueError(
                f"This file is already {CODEC_LABELS[target]} -- re-encoding it again would only reduce quality"
            )
        encoders = await asyncio.to_thread(media_tools.available_encoders)
        if encoders and media_tools.REENCODE_ENCODERS[target] not in encoders:
            raise ValueError(
                f"Your FFmpeg has no {CODEC_LABELS[target]} encoder ({media_tools.REENCODE_ENCODERS[target]}) -- "
                "install a full FFmpeg build or pick another codec"
            )

        async with self._lock:
            # Re-fetch and re-check: state may have changed (or the item may
            # have been removed) while we were probing the file above.
            item = self._items.get(video_id)
            if not item:
                raise ValueError("Not found")
            if item.reencode_status in ("queued", "encoding"):
                return item

            item.reencode_status = "queued"
            item.reencode_target = target
            item.reencode_options = options or None
            item.reencode_progress_percent = 0
            item.reencode_error = None
            item.touch()

            # Queue first, persist second. If db.save_item() below raises for
            # any reason (e.g. a transient SQLite error during a burst of
            # concurrent requests), the item is still appended to the
            # in-memory queue and will actually get processed by the worker
            # -- instead of being left showing "queued" forever with nothing
            # backing it, which is the bug this ordering used to cause.
            if video_id not in self._reencode_queue:
                self._reencode_queue.append(video_id)
            try:
                self.db.save_item(item)
            except Exception:
                logger.exception(
                    "Failed to persist queued re-encode state for {} -- "
                    "it is still queued in memory and will be processed; "
                    "state will be persisted once encoding starts.",
                    video_id,
                )
        self._ensure_reencode_worker()
        return item

    def _ensure_reencode_worker(self) -> None:
        if self._reencode_task is None or self._reencode_task.done():
            self._reencode_task = asyncio.create_task(self._reencode_worker_loop())

    async def _reencode_worker_loop(self) -> None:
        while True:
            if self._shutting_down:
                return
            async with self._lock:
                video_id = self._reencode_queue.pop(0) if self._reencode_queue else None
            if video_id is None:
                return
            item = self._items.get(video_id)
            if not item:
                continue
            try:
                await self._reencode_item(item)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Re-encode failed for {}", video_id)
                async with self._lock:
                    item.reencode_status = "failed"
                    item.reencode_error = str(exc)
                    item.touch()
                    self.db.save_item(item)
                notify("reencode", "error", f"Re-encode failed: {title_of(item)}", body=str(exc)[:300],
                       link=video_link(item), user_id=item.added_by)

    async def _reencode_item(self, item: LibraryItem) -> None:
        cfg = self.settings.reencode
        codec, crf, preset = cfg.codec_params(item.reencode_target)
        # This re-encode's own settings (Re-encode dialog), over the server's.
        opts = item.reencode_options or {}
        crf = opts.get("crf", crf)
        preset = str(opts.get("preset", preset))
        downscale = opts.get("downscale", cfg.downscale)
        copy_audio = opts.get("copy_audio", cfg.copy_audio)
        remove_chapters = opts.get("remove_chapters", cfg.remove_chapters)
        cores = cfg.cpu_cores()
        video_path = Path(item.video_path)

        # Consecutive-failure count decides whether this run uses a
        # normal single-attempt encode, or escalates straight to the
        # corruption-tolerant recovery pipeline -- see LibraryItem.
        # reencode_attempts' docstring in models.py and media_tools.
        # reencode_video()'s tolerant= docstring for what "recovery"
        # actually changes about the ffmpeg command/probing.
        attempt_number = (item.reencode_attempts or 0) + 1
        use_recovery = attempt_number >= 3

        async with self._lock:
            item.reencode_status = "encoding"
            item.reencode_progress_percent = 0
            item.touch()
            self.db.save_item(item)

        if use_recovery:
            logger.warning(
                "Re-encoding '{}' [{}] -> attempt {} (3+ consecutive failures) -- "
                "switching to corruption-tolerant recovery mode; container may be damaged",
                item.title, item.id, attempt_number,
            )
        else:
            logger.info("Re-encoding '{}' [{}] -> {} CRF {}, preset {}{}", item.title, item.id, CODEC_LABELS[codec], crf, preset,
                        f", {cores} cores" if cores else "")

        self._current_reencode_id = item.id
        self._current_reencode_proc = {}
        self._cancel_requested = False

        loop = asyncio.get_running_loop()

        last_saved = [0.0]

        def on_progress(data: dict) -> None:
            # The progress lives on the item in memory (what Tasks shows); it's
            # written to the database at most every PROGRESS_SAVE_EVERY seconds.
            # In between only a "progress" signal goes out: Tasks follows it, while
            # library pages showing this video don't reload every second (2.17.0).
            item.reencode_progress_percent = data["percent"]
            item.reencode_eta_seconds = data["eta_seconds"]
            item.reencode_speed = data["speed"]
            now = time.monotonic()
            if now - last_saved[0] >= PROGRESS_SAVE_EVERY:
                last_saved[0] = now
                item.touch()
                loop.call_soon_threadsafe(self.db.save_item, item)
            else:
                loop.call_soon_threadsafe(self.db._note_change, ["progress"])

        if not use_recovery:
            temp_output = await asyncio.to_thread(
                media_tools.reencode_video,
                str(video_path), codec, crf, preset, downscale,
                cores, remove_chapters, copy_audio,
                on_progress, self._current_reencode_proc, False,
            )
        else:
            # Recovery pass 1: the person's own normal settings, but with
            # corruption-tolerant demuxing/probing (tolerant=True).
            temp_output = await asyncio.to_thread(
                media_tools.reencode_video,
                str(video_path), codec, crf, preset, downscale,
                cores, remove_chapters, copy_audio,
                on_progress, self._current_reencode_proc, True,
            )
            if not temp_output and not self._cancel_requested:
                logger.warning(
                    "Recovery pass 1 (normal settings, tolerant mode) failed for {} -- "
                    "trying pass 2 with safe near-lossless recovery settings",
                    item.id,
                )
                # Recovery pass 2: fixed, conservative near-lossless
                # settings -- deliberately NOT the person's own settings
                # this time, since the goal is maximizing the odds of a
                # clean decode/re-mux, not matching their usual
                # compression target. copy_audio is passed as False but
                # has no real effect here anyway -- tolerant=True always
                # forces an AAC transcode regardless (see
                # reencode_video()'s tolerant= docstring).
                temp_output = await asyncio.to_thread(
                    media_tools.reencode_video,
                    str(video_path),
                    codec,
                    16 if codec == "hevc" else 20,  # crf -- near-lossless
                    "medium" if codec == "hevc" else "8",  # preset
                    False,  # downscale
                    cores,
                    False,  # remove_chapters
                    False,  # copy_audio (ignored when tolerant=True)
                    on_progress, self._current_reencode_proc, True,
                )

        was_cancelled = self._cancel_requested
        self._current_reencode_id = None
        self._current_reencode_proc = {}
        self._cancel_requested = False

        if not temp_output:
            async with self._lock:
                if item.id not in self._items:
                    return
                if was_cancelled:
                    item.reencode_status = "cancelled"
                    item.reencode_error = None
                else:
                    item.reencode_attempts = (item.reencode_attempts or 0) + 1
                    item.reencode_status = "failed"
                    notify("reencode", "error", f"Re-encode failed: {title_of(item)}",
                           body="FFmpeg couldn't re-encode it; the original is kept.", link=video_link(item),
                           user_id=item.added_by)
                    item.reencode_error = (
                        "FFmpeg re-encode failed even after corruption-tolerant recovery attempts -- "
                        "this file may have real data corruption, not just an encoding-settings issue. "
                        "Check the server log for details."
                        if use_recovery else
                        "FFmpeg re-encode failed -- check the server log for details"
                    )
                item.reencode_progress_percent = None
                item.reencode_eta_seconds = None
                item.touch()
                self.db.save_item(item)
            return

        # Captured BEFORE the swap below -- video_path still points at
        # the ORIGINAL (pre-re-encode) file up to this exact point. Used
        # to track cumulative net bytes saved by re-encoding across the
        # whole library (see stats_worker.py's compute_library_stats()) --
        # this counter only exists going forward from when this line was
        # added, so it can't reflect re-encodes that already happened
        # before it.
        original_size = video_path.stat().st_size if video_path.exists() else None

        # Swap the re-encoded file into place. Using Path.replace() instead of
        # unlink()+move() makes this a single atomic filesystem operation --
        # there's never a moment where video_path doesn't exist on disk, which
        # matters if something is mid-stream reading from it.
        #
        # keep_original is forced True whenever this run went through the
        # recovery pipeline, REGARDLESS of the person's own
        # keep_original_file setting -- a recovery re-encode is a
        # best-effort attempt against a possibly-damaged source; if the
        # output turns out to be subtly wrong, there needs to be
        # something to fall back to or hand off for manual inspection.
        keep_original = opts.get("keep_original_file", cfg.keep_original_file) or use_recovery
        backup_video = None
        open_files.release(video_path)  # an open stream would block the swap on Windows
        try:
            if keep_original:
                backup_video = video_path.with_name(f"{video_path.stem}_old{video_path.suffix}")
                video_path.rename(backup_video)
                Path(temp_output).replace(video_path)
            else:
                Path(temp_output).replace(video_path)
        except Exception as exc:
            if backup_video and backup_video.exists() and not video_path.exists():
                backup_video.rename(video_path)
            raise RuntimeError(f"Could not replace video file: {exc}") from exc

        # Refresh technical details in the metadata JSON for the new file.
        # "video_details" is the same key name regardless of whether the
        # file is the raw v2 shape or the curated v1-fallback shape, so
        # this needs no shape branching.
        tech_info = media_tools.get_technical_info(str(video_path))

        # Net bytes saved by this specific re-encode (can be negative --
        # e.g. a near-lossless recovery-mode encode may end up larger
        # than the damaged original) -- rolled into the running library-
        # wide total. See original_size's own comment above for why this
        # is cumulative-from-now, not retroactive.
        if original_size is not None:
            try:
                new_size = video_path.stat().st_size
                self.db.increment_counter("stat_total_reencode_bytes_saved", original_size - new_size)
                stats_store.bump("reencode_saved_bytes", original_size - new_size)
            except OSError:
                pass
        self.db.increment_counter("stat_total_reencodes_performed", 1)

        if item.metadata_path:
            meta_path = Path(item.metadata_path)
            try:
                meta_dict = json.loads(meta_path.read_text(encoding="utf-8"))
                meta_dict.setdefault("video_details", {})
                for key in list(meta_dict["video_details"].keys()):
                    if "hash" in key.lower():
                        value = meta_dict["video_details"].pop(key)
                        meta_dict["video_details"][f"{key} (pre-encode, stale)"] = value
                meta_dict["video_details"].update(tech_info)
                meta_dict["video_details"]["Re-encoded"] = True
                if use_recovery:
                    meta_dict["video_details"]["Re-encoded via corruption recovery"] = True
                meta_path.write_text(json.dumps(meta_dict, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception:
                logger.warning("Could not update metadata JSON after re-encode for {}", item.id)

        # keep the item's badge fields in sync with the freshly re-encoded file
        item.tech_codec = tech_info.get("Video Codec")
        item.tech_resolution = tech_info.get("Resolution")
        item.tech_filesize = tech_info.get("File Size")
        item.tech_duration = tech_info.get("Duration")

        # Recompute the local phash from the NEW (re-encoded) file -- the
        # pixels changed, so the pre-encode hash no longer describes what's
        # actually on disk.
        new_phash = await asyncio.to_thread(media_tools.generate_stash_phash, str(video_path))
        if new_phash:
            item.local_phash = new_phash
            item.local_phash_generated_at = time.time()
            item.local_phash_source_hash = await asyncio.to_thread(media_tools.compute_file_sha256, str(video_path))
            item.local_phash_source_size = video_path.stat().st_size
        else:
            logger.warning("Could not recompute the local phash for {} after re-encode", item.id)

        async with self._lock:
            if item.id not in self._items:
                return
            item.reencode_status = "done"
            item.reencode_progress_percent = 100
            item.reencode_eta_seconds = 0
            item.reencode_attempts = 0
            item.touch()
            self.db.save_item(item)
        notify("reencode", "success", f"Re-encoded {title_of(item)}", link=video_link(item), user_id=item.added_by)

        logger.info(
            "Re-encode complete for '{}' [{}] -> {}{}",
            item.title, item.id, video_path, " (via corruption recovery)" if use_recovery else "",
        )
        # The file's pixels just changed: re-check it for duplicates if
        # "Scan after a re-encode" is on (Server settings -> Duplicate scan).
        self.request_duplicate_scan_after("reencode")

    async def cancel_reencode(self, video_id: str) -> LibraryItem:
        async with self._lock:
            item = self._items.get(video_id)
            if not item:
                raise ValueError("Not found")

            if item.reencode_status == "queued":
                if video_id in self._reencode_queue:
                    self._reencode_queue.remove(video_id)
                item.reencode_status = None
                item.reencode_progress_percent = None
                item.touch()
                self.db.save_item(item)
                return item

        if item.reencode_status == "encoding" and self._current_reencode_id == video_id:
            self._cancel_requested = True
            proc = self._current_reencode_proc.get("proc")
            if proc and proc.poll() is None:
                proc.terminate()
            return item

        raise ValueError("This item is not currently queued or encoding")
