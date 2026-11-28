"""Maintenance -> Check video files (3.11.0).

Goes through every downloaded video and checks its file: it's there, it isn't empty, and
ffprobe can read a video track from it ("Thorough" also reads every packet and decodes a
few seconds, as the re-encode's own check does -- media_tools.check_video_integrity).

For a video whose file is missing it looks in the download folder for it: a file not used
by any other video with the same name (moved to another folder), or with the video's id in
its name (renamed), or the "_old" copy a re-encode keeps. Found files can be linked back
(link_video_file); the others can be downloaded again (redownload). Nothing is deleted,
moved or renamed here.

The last result is kept in data/file_check.json, so the list is still there after a restart.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from ..i18n import L
from .. import activity, media_tools
from .adopt import video_id_in_name
from .untracked_files import _kind, _norm

PROBLEMS = ("missing", "empty", "unreadable", "damaged")
# Untracked files kinds -> the field of the library video that uses such a file (3.11.1)
COMPANION_FIELDS = {"thumbnail": "thumbnail_path", "preview": "preview_path", "metadata": "metadata_path",
                    "funscript": "funscript_path"}


def file_state(path: Optional[str]) -> Optional[str]:
    """BLOCKING-light: "missing" / "empty" / None (there, not empty)."""
    if not path:
        return "missing"
    try:
        st = os.stat(path)
    except OSError:
        return "missing"
    if not os.path.isfile(path):
        return "missing"
    return "empty" if st.st_size == 0 else None


class FileCheckMixin:
    _fc_task: Optional[asyncio.Task] = None
    _fc_stop = False
    _fc_result: Optional[dict] = None

    # ------------------------------------------------------------ the saved result
    def _fc_file(self) -> Path:
        return self.settings.library_db_file.parent / "file_check.json"

    def _fc_load(self) -> dict:
        if self._fc_result is None:
            try:
                self._fc_result = json.loads(self._fc_file().read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._fc_result = {}
        return self._fc_result

    def _fc_save(self, result: dict) -> None:
        self._fc_result = result
        try:
            path = self._fc_file()
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("Check video files: couldn't save the result: {}", exc)

    def file_check_report(self) -> dict:
        """The last check, without the videos fixed since (linked, downloaded again, removed)."""
        last = self._fc_load()
        problems = []
        for p in last.get("problems", []):
            item = self._items.get(p["id"])
            if not item or item.status == "merged":
                continue
            if item.status in ("pending", "fetching", "downloading"):
                problems.append({**p, "downloading": True})
                continue
            if item.video_path != p.get("path"):
                continue   # linked to another file, or downloaded again
            if p["problem"] in ("missing", "empty") and file_state(item.video_path) is None:
                continue
            if p.get("found") and not os.path.isfile(p["found"]):
                p = {**p, "found": None}
            problems.append({**p, "title": item.title, "site_status": item.site_status})
        return {"running": self.file_check_running(), "checked_at": last.get("checked_at"),
                "thorough": last.get("thorough", False), "checked": last.get("checked", 0),
                "stopped": last.get("stopped", False), "problems": problems}

    # ------------------------------------------------------------ the job
    def file_check_running(self) -> bool:
        return bool(self._fc_task and not self._fc_task.done())

    def request_file_check(self, thorough: bool = False) -> bool:
        """Starts the check in the background (False: it's running already)."""
        if self.file_check_running():
            return False
        self._fc_task = asyncio.create_task(self.run_file_check(thorough=thorough))
        return True

    async def run_file_check(self, thorough: bool = False) -> dict:
        """Scheduler job "Check video files" (and the Maintenance button)."""
        items = [i for i in self._items.values() if i.status == "done" and i.video_path]
        state = {"done": 0, "total": len(items), "current": None, "item": None, "bad": 0}
        self._fc_stop = False

        def progress() -> dict:
            return {"stage": L("Checking") if not thorough else L("Checking thoroughly"),
                    "done": state["done"], "total": state["total"],
                    "detail": (f"{state['bad']} with a problem" + (f" · {state['current']}" if state["current"] else "")),
                    "item": state["item"] if state["current"] else None}

        holder: dict = {}

        def stop() -> None:
            self._fc_stop = True
            holder["cancel"] = True

        problems: list[dict] = []
        async with activity.track("file_check", L("Checking video files"), progress=progress, stop=stop) as act:
            started = time.monotonic()
            for item in items:
                if self._fc_stop or self._shutting_down:
                    break
                state["current"] = item.title
                state["item"] = activity.item_ref(item)
                found = await asyncio.to_thread(self._check_one_file, item, thorough, holder)
                if found:
                    problems.append(found)
                    state["bad"] += 1
                state["done"] += 1
            state["current"] = None
            missing = [p for p in problems if p["problem"] in ("missing", "empty")]
            if missing and not self._fc_stop:
                await asyncio.to_thread(self._fc_find_files, missing)
            self._fc_save({"checked_at": time.time(), "thorough": thorough, "checked": state["done"],
                           "stopped": bool(self._fc_stop), "problems": problems})
            took = time.monotonic() - started
            counts = {k: sum(1 for p in problems if p["problem"] == k) for k in PROBLEMS}
            found_n = sum(1 for p in missing if p.get("found"))
            logger.info("Check video files{}: {} checked in {:.0f}s -- {} missing ({} found elsewhere in the download "
                        "folder), {} empty, {} unreadable, {} damaged", " (thorough)" if thorough else "",
                        state["done"], took, counts["missing"], found_n, counts["empty"], counts["unreadable"],
                        counts["damaged"])
            for p in problems:
                logger.info("Check video files: [{}] {}: {}{}", p["id"], p["problem"], p["path"],
                            f" -- found at {p['found']}" if p.get("found") else "")
            result = {"checked": state["done"], "missing": counts["missing"], "found_elsewhere": found_n,
                      "empty": counts["empty"], "unreadable": counts["unreadable"], "damaged": counts["damaged"]}
            act.summary = activity.summarize(result)
        return result

    def _check_one_file(self, item, thorough: bool, holder: dict) -> Optional[dict]:
        """BLOCKING. One video: None when its file is fine, else what's wrong with it."""
        path = item.video_path
        entry = {"id": item.id, "title": item.title, "creator": item.creator, "path": path, "site": item.site}
        state = file_state(path)
        if state:
            return {**entry, "problem": state, "detail": None}
        info = media_tools.probe_streams(path)
        if not info or not info.get("video"):
            return {**entry, "problem": "unreadable", "detail": L("ffprobe can't read a video track from it")}
        if thorough:
            verdict = media_tools.check_video_integrity(path, holder)
            if holder.get("cancel"):
                return None
            if not verdict.get("ok"):
                return {**entry, "problem": "damaged", "detail": "; ".join(verdict.get("problems") or [])[:500]}
        return None

    def _fc_find_files(self, missing: list[dict]) -> None:
        """BLOCKING. Looks for the missing files in the download folder (one walk)."""
        root = self.settings.output_dir
        tracked, keep = self._tracked_paths()
        by_name: dict[str, list[Path]] = {}
        by_id: dict[str, list[Path]] = {}
        if root.is_dir():
            for dirpath, dirs, names in os.walk(root):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for name in names:
                    p = Path(dirpath) / name
                    if _kind(p) != "video" or _norm(p) in tracked or _norm(p) in keep:
                        continue
                    by_name.setdefault(name.lower(), []).append(p)
                    vid = video_id_in_name(name)
                    if vid:
                        by_id.setdefault(vid, []).append(p)
        for entry in missing:
            old = Path(entry["path"]) if entry.get("path") else None
            choices: list[tuple[Path, str]] = []
            if old is not None:
                backup = old.with_name(f"{old.stem}_old{old.suffix}")
                if entry["problem"] == "missing" and os.path.isfile(backup):
                    choices.append((backup, "reencode_backup"))
                choices += [(p, "same_name") for p in by_name.get(old.name.lower(), [])]
            choices += [(p, "same_id") for p in by_id.get(entry["id"], []) if not any(p == c for c, _ in choices)]
            good = [(p, why) for p, why in choices if file_state(str(p)) is None]
            if good:
                entry["found"], entry["found_how"] = str(good[0][0]), good[0][1]
                entry["found_more"] = len(good) - 1

    # ------------------------------------------------------------ fixing one
    async def link_video_file(self, video_id: str, path: str) -> dict:
        """Points a library video whose file is missing (or empty) at another file in the
        download folder -- one the check (or Untracked files) found for it. ValueError when it
        can't be that video's file."""
        item = self._items.get(video_id)
        if not item or item.status == "merged":
            raise KeyError(video_id)
        if item.status not in ("done", "failed"):
            raise ValueError(L("That video is being downloaded right now"))
        if file_state(item.video_path) is None and item.status == "done":
            raise ValueError(L("That video's own file is there -- nothing to link"))
        new = Path(path)
        key = _norm(new)
        out_root = _norm(self.settings.output_dir)
        if not key.startswith(out_root + os.sep):
            raise ValueError(L("not in the download folder"))
        if file_state(path) is not None or _kind(new) not in ("video", "re-encode backup"):
            raise ValueError(L("not a video file"))
        tracked, keep = self._tracked_paths()
        own = _norm(item.video_path) if item.video_path else None
        if (key in tracked and key != own) or key in keep:
            raise ValueError(L("another library video uses that file"))
        old = Path(item.video_path) if item.video_path else None
        same_name = old is not None and (new.name.lower() == old.name.lower()
                                         or new.name.lower() == f"{old.stem}_old{old.suffix}".lower())
        if key == _norm(item.video_path or "") and item.status == "failed":
            pass   # a download again that failed, and its file turned up where it was
        elif video_id_in_name(new.name) != video_id and not same_name:
            raise ValueError(L("its name has neither this video's id nor its old file name"))
        async with self._lock:
            before = item.video_path
            item.video_path = str(new)
            item.status = "done"
            item.error = None
            # read again from the file now used
            item.tech_codec = item.tech_resolution = item.tech_filesize = item.tech_duration = None
            item.touch()
            self.db.save_item(item)
        logger.info("Linked '{}' [{}] to {} (its file {} was missing)", item.title, video_id, new, before)
        self.request_repair(video_id)
        self.request_storyboard(video_id)
        return item.model_dump()

    async def link_file(self, video_id: str, path: str) -> dict:
        """Link back from Untracked files / the check: a video file, or (3.11.1) a thumbnail,
        preview, details JSON or funscript the library video doesn't have."""
        kind = _kind(Path(path))
        if kind not in COMPANION_FIELDS:
            return await self.link_video_file(video_id, path)
        item = self._items.get(video_id)
        if not item or item.status == "merged":
            raise KeyError(video_id)
        field = COMPANION_FIELDS[kind]
        if file_state(getattr(item, field, None)) is None:
            raise ValueError(L("That video has one already -- this is an older copy"))
        new = Path(path)
        key = _norm(new)
        roots = [_norm(self.settings.output_dir), _norm(self.settings.cache_dir)]
        if not any(key.startswith(r + os.sep) for r in roots):
            raise ValueError(L("not in the download or cache folder"))
        if file_state(path) is not None:
            raise ValueError(L("not there any more, or empty"))
        tracked, keep = self._tracked_paths()
        if key in tracked or key in keep:
            raise ValueError(L("another library video uses that file"))
        if video_id_in_name(new.name) != video_id:
            raise ValueError(L("its name doesn't have this video's id"))
        async with self._lock:
            setattr(item, field, str(new))
            if field == "thumbnail_path":
                item.thumbnail_updated_at = time.time()
            elif field == "preview_path":
                item.preview_updated_at = time.time()
            item.touch()
            self.db.save_item(item)
        logger.info("Linked the {} {} back to '{}' [{}]", kind, new, item.title, video_id)
        return item.model_dump()

    async def redownload(self, video_id: str, replace_damaged: bool = False) -> dict:
        """Downloads a library video again when its file is missing or empty (or, with
        replace_damaged, when the check found it damaged). The details, tags, ratings and
        watch history stay. ValueError when it can't."""
        item = self._items.get(video_id)
        if not item or item.status == "merged":
            raise KeyError(video_id)
        if item.status in ("pending", "fetching", "downloading"):
            raise ValueError(L("It's being downloaded already"))
        if item.site == "local":
            raise ValueError(L("Uploaded from a computer -- there's nothing to download it from"))
        if item.site_status == "removed":
            raise ValueError(L("The site doesn't have this video any more"))
        if file_state(item.video_path) is None and not replace_damaged and item.status != "failed":
            raise ValueError(L("Its file is there -- use Repair / Update, or Check video files (thorough)"))
        await self._queue_redownload(item)
        return item.model_dump()

    async def _queue_redownload(self, item) -> None:
        async with self._lock:
            item.status = "pending"
            item.error = None
            item.progress_percent = 0
            item.progress_label = "Queued again"
            item.touch()
            self.db.save_item(item)
        logger.info("Downloading '{}' [{}] again (its file {} was missing or broken)", item.title, item.id, item.video_path)
        self._ensure_worker()
