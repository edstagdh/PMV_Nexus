"""
Maintenance -> Untracked files: files in the download and cache folders that
no library item points to -- e.g. a duplicate's video that couldn't be
deleted because it was in use, or files left by a manual move. Listing is
read-only; deleting only ever touches files that are still untracked and
inside those two folders. 3.10.0: videos with a site video id in their name
can be added back to the library (library_mixins/adopt.py).
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from ..i18n import L
from .. import activity, open_files, storyboard, video_frames

_PATH_FIELDS = ("video_path", "funscript_path", "metadata_path", "thumbnail_path", "preview_path")
_IGNORED_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}
# 3.12.0: what a file in one of these folders is, whatever its name
_LOCATION_KIND = {"performer photos": "performer photo", "own photos": "your performer photo", "covers": "playlist cover"}
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
        # 3.12.0: your own performer photos and playlist covers in use (their folders are scanned too)
        for c in self.db.custom_photo_rows():
            tracked.add(_norm(c["path"]))
        for pl in self.db.list_playlists():
            if pl.get("cover_path"):
                tracked.add(_norm(pl["cover_path"]))
            tracked.add(_norm(self._cover_file(pl["id"])))
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

    def _untracked_roots(self) -> list[tuple[str, Path]]:
        """(location, folder) to look in: the download and cache folders and (3.12.0) the performer
        photo folders, playlist covers and a Generated files folder of its own. A folder inside
        another one in the list is looked at once, as part of that one."""
        s = self.settings
        cand = [("downloads", s.output_dir), ("cache", s.cache_dir), ("performer photos", s.performer_photos_dir),
                ("own photos", s.own_photos_dir), ("covers", s.library_db_file.parent / "playlist_covers")]
        if s.download.generated_dir:   # only the app's own subfolders there: the folder may hold other things
            cand += [("generated", s.generated_dir / storyboard.FOLDER), ("generated", s.generated_dir / video_frames.FOLDER)]
        out: list[tuple[str, Path]] = []
        for loc, root in cand:
            r = root.resolve()
            if any(r == o or o in r.parents for _l, o in out):
                continue
            out = [(l2, o) for l2, o in out if r not in o.parents]   # this one holds an earlier one
            out.append((loc, r))
        return out

    def _scan_untracked(self) -> dict:
        """BLOCKING -- walks the folders of _untracked_roots()."""
        tracked, keep = self._tracked_paths()
        roots = self._untracked_roots()
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
                    entry = {
                        "path": str(path), "relative": str(path.relative_to(root)), "location": location,
                        "kind": _LOCATION_KIND.get(location) or _kind(path), "size": st.st_size, "modified": st.st_mtime,
                    }
                    if location in ("own photos", "covers"):
                        entry["irreplaceable"] = True   # 3.12.0: can't be downloaded again
                    if location in _LOCATION_KIND:
                        pass
                    elif location == "downloads" and entry["kind"] == "video":
                        entry.update(self.untracked_video_info(path))   # 3.10.0: can it be added back?
                    elif entry["kind"] in ("thumbnail", "preview", "metadata", "funscript"):
                        entry.update(self.untracked_companion_info(path, entry["kind"]) or {})   # 3.11.1
                    files.append(entry)
        files.sort(key=lambda f: (f["location"], f["relative"].lower()))
        return {"scanned_at": time.time(), "files": files, "total_size": sum(f["size"] for f in files),
                "errors": errors[:20], "roots": {loc: str(root) for loc, root in roots}}

    async def find_untracked_files(self) -> dict:
        async with activity.track("untracked_files", L('Looking for untracked files'),
                                  stage=L('Checking the download and cache folders')):
            result = await asyncio.to_thread(self._scan_untracked)
        logger.info("Untracked files scan: {} file(s), {} bytes", len(result["files"]), result["total_size"])
        return result

    async def delete_untracked_files(self, paths: list[str]) -> dict:
        """Deletes the given files if each is still untracked and inside the
        download or cache folder; anything else is refused, not deleted."""
        tracked, keep = self._tracked_paths()
        roots = [_norm(r) for _loc, r in self._untracked_roots()]   # 3.12.0: every folder the list shows
        being_added = set(getattr(self, "_adopt_queued", None) or ())

        def run() -> dict:
            deleted, refused, failed, kept = [], [], [], []
            for raw in paths:
                key = _norm(raw)
                inside = any(key.startswith(root + os.sep) for root in roots)
                if not inside or key in tracked or key in keep:
                    refused.append(raw)
                    continue
                # 3.11.0: never the file a library video has lost (link it back instead), nor one being added
                why = self.untracked_protected(Path(raw), being_added)
                if why:
                    kept.append({"path": raw, "reason": why})
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
            return {"deleted": deleted, "refused": refused, "failed": failed, "kept": kept}

        async with activity.track("untracked_files", L('Deleting {count} untracked file(s)', count=len(paths))):
            result = await asyncio.to_thread(run)
        logger.info("Untracked files: deleted {}, refused {}, kept {}, failed {}",
                    len(result["deleted"]), len(result["refused"]), len(result["kept"]), len(result["failed"]))
        for raw in result["deleted"]:   # 3.11.0: which ones, so a deletion can always be traced
            logger.info("Untracked files: deleted {}", raw)
        for k in result["kept"]:
            logger.info("Untracked files: not deleted {} ({})", k["path"], k["reason"])
        return result

    def untracked_protected(self, path: Path, being_added: set) -> Optional[str]:
        """Why this untracked file mustn't be deleted, or None (3.11.0)."""
        if _norm(path) in being_added:
            return L("it's being added to the library")
        if _kind(path) in ("video", "re-encode backup"):
            from .file_check import file_state
            vid = self._video_id_of(path.name)
            item = self._items.get(vid) if vid else None
            if item and item.status == "done" and file_state(item.video_path):
                return L("it's probably the file of a library video whose own file is missing -- link it back instead")
        else:
            info = self.untracked_companion_info(path, _kind(path))
            if info and info.get("can_link"):
                return L("the library video it belongs to has none -- link it back instead")
        return None
