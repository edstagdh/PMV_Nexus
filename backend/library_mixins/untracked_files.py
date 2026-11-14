"""
Maintenance -> Untracked files: files in the download and cache folders that
no library item points to -- e.g. a duplicate's video that couldn't be
deleted because it was in use, or files left by a manual move. Listing is
read-only; deleting only ever touches files that are still untracked and
inside those two folders.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from loguru import logger

from .. import activity, open_files, storyboard, video_frames

_PATH_FIELDS = ("video_path", "funscript_path", "metadata_path", "thumbnail_path", "preview_path")
_IGNORED_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}
_VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts"}
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def _norm(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _kind(path: Path) -> str:
    name, ext = path.name.lower(), path.suffix.lower()
    if storyboard.FOLDER in path.parts:
        return "seekbar preview"  # of a video no longer in the library (2.21.0)
    if video_frames.FOLDER in path.parts:
        return "video frame"  # of a video no longer in the library (3.2.0)
    if name.endswith(".part") or ".part." in name:
        return "partial download"
    if ext in _VIDEO_EXT:
        if path.stem.endswith("_preview"):
            return "preview"
        if path.stem.endswith("_old"):
            return "re-encode backup"
        if path.stem.endswith("_temp"):
            return "unfinished re-encode"
        return "video"
    if ext in _IMAGE_EXT or ext == ".gif":
        return "performer photo" if path.parent.name == "performers" else "thumbnail"
    if ext == ".json":
        return "metadata"
    if ext == ".funscript":
        return "funscript"
    return "other"


class UntrackedFilesMixin:

    def _tracked_paths(self) -> tuple[set[str], set[str]]:
        """Paths items point to, plus the companions the app keeps on purpose
        (the original thumbnail saved when a custom one is set)."""
        tracked: set[str] = set()
        keep: set[str] = set()
        for photo in self.performer_photo_files():  # cache/performers (2.15.0)
            tracked.add(_norm(photo))
        for item in self._items.values():
            for field in _PATH_FIELDS:
                value = getattr(item, field, None)
                if value:
                    tracked.add(_norm(value))
            if item.video_path and item.reencode_status == "encoding":
                # The file a running re-encode writes into (media_tools.reencode_video) (2.18.0).
                video = Path(item.video_path)
                keep.add(_norm(video.with_name(f"{video.stem}_temp.mp4")))
            if item.thumbnail_path:
                thumb = Path(item.thumbnail_path)
                base = thumb.stem[:-len("_custom")] if thumb.stem.endswith("_custom") else thumb.stem
                for ext in (".jpg", ".jpeg", ".png", ".webp"):
                    keep.add(_norm(thumb.with_name(f"{base}_original{ext}")))
        return tracked, keep

    def _scan_untracked(self) -> dict:
        """BLOCKING -- walks both folders."""
        tracked, keep = self._tracked_paths()
        roots = [("downloads", self.settings.output_dir), ("cache", self.settings.cache_dir)]
        files, errors = [], []
        for location, root in roots:
            if not root.is_dir():
                continue
            for dirpath, dirs, names in os.walk(root, onerror=lambda e: errors.append(str(e))):
                # uploads still being received/checked; 720p playback pieces (deleted when playback stops)
                dirs[:] = [d for d in dirs if d not in (".incoming", ".transcode")]
                if Path(dirpath).name in (storyboard.FOLDER, video_frames.FOLDER):
                    # Seekbar previews (2.21.0) and video frames (3.2.0): only those of videos no longer in the library
                    # (and none being made right now, "<id>.part").
                    dirs[:] = [d for d in dirs if d not in self._items and not d.endswith(".part")]
                for name in names:
                    if name.lower() in _IGNORED_NAMES:
                        continue
                    path = Path(dirpath) / name
                    key = _norm(path)
                    if key in tracked or key in keep:
                        continue
                    try:
                        st = path.stat()
                    except OSError:
                        continue
                    files.append({
                        "path": str(path), "relative": str(path.relative_to(root)), "location": location,
                        "kind": _kind(path), "size": st.st_size, "modified": st.st_mtime,
                    })
        files.sort(key=lambda f: (f["location"], f["relative"].lower()))
        return {"scanned_at": time.time(), "files": files, "total_size": sum(f["size"] for f in files),
                "errors": errors[:20], "roots": {loc: str(root) for loc, root in roots}}

    async def find_untracked_files(self) -> dict:
        async with activity.track("untracked_files", "Looking for untracked files",
                                  stage="Checking the download and cache folders"):
            result = await asyncio.to_thread(self._scan_untracked)
        logger.info("Untracked files scan: {} file(s), {} bytes", len(result["files"]), result["total_size"])
        return result

    async def delete_untracked_files(self, paths: list[str]) -> dict:
        """Deletes the given files if each is still untracked and inside the
        download or cache folder; anything else is refused, not deleted."""
        tracked, keep = self._tracked_paths()
        roots = [_norm(self.settings.output_dir), _norm(self.settings.cache_dir)]

        def run() -> dict:
            deleted, refused, failed = [], [], []
            for raw in paths:
                key = _norm(raw)
                inside = any(key.startswith(root + os.sep) for root in roots)
                if not inside or key in tracked or key in keep:
                    refused.append(raw)
                    continue
                path = Path(raw)
                open_files.release(path)
                try:
                    path.unlink()
                    deleted.append(raw)
                except FileNotFoundError:
                    deleted.append(raw)
                except OSError as exc:
                    failed.append({"path": raw, "error": str(exc)})
                    continue
                try:  # tidy a creator folder the file leaves empty
                    if path.parent.exists() and not any(path.parent.iterdir()) and _norm(path.parent) not in roots:
                        path.parent.rmdir()
                except OSError:
                    pass
            return {"deleted": deleted, "refused": refused, "failed": failed}

        async with activity.track("untracked_files", f"Deleting {len(paths)} untracked file(s)"):
            result = await asyncio.to_thread(run)
        logger.info("Untracked files: deleted {}, refused {}, failed {}",
                    len(result["deleted"]), len(result["refused"]), len(result["failed"]))
        return result
