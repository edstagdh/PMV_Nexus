from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Optional

from loguru import logger

from ..config import get_settings, save_overrides
from .. import open_files
from .. import activity
from .. import media_tools
from .. import storyboard, video_frames
from ..media_tools import convert_preview_for_browser, generate_thumbnail, is_browser_playable
from ..models import EDITABLE_DETAIL_FIELDS, EDITABLE_ITEM_FIELDS, LibraryItem
from .. import sources
from ._helpers import extract_tags_performers_music_from_meta_dict, safe_name


def _find_moved(value: str, old_root, new_root: Path) -> Optional[Path]:
    """Where a file recorded as `value` (under old_root, on this or another
    computer) is under new_root -- None if it isn't there."""
    for cls in (PureWindowsPath, PurePosixPath) if old_root else ():
        try:
            rel = cls(value).relative_to(cls(str(old_root)))
        except ValueError:
            continue
        candidate = new_root.joinpath(*rel.parts)
        return candidate if candidate.is_file() else None
    parts = PureWindowsPath(value).parts if "\\" in value else PurePosixPath(value).parts
    for n in (3, 2, 1):  # <creator>/<file> is the usual layout
        if len(parts) > n:
            candidate = new_root.joinpath(*parts[-n:])
            if candidate.is_file():
                return candidate
    return None


class ItemExtrasMixin:

    async def rename(self, video_id: str, new_name: str) -> LibraryItem:
        async with self._lock:
            item = self._items.get(video_id)
            if not item:
                raise ValueError("Not found")
            if item.status != "done" or not item.video_path:
                raise ValueError("Only completed downloads can be renamed")

            old_path = Path(item.video_path)
            safe_new_name = safe_name(new_name)
            new_path = old_path.with_name(f"{safe_new_name}{old_path.suffix}")

            if new_path.exists():
                raise ValueError("A file with that name already exists")

            # Our own open streams would block the rename on Windows.
            for p in (item.video_path, item.preview_path):
                if p:
                    open_files.release(p)
            old_path.rename(new_path)
            item.video_path = str(new_path)

            # keep sidecar files (funscript/json) named to match the new title
            if item.funscript_path:
                old_fs = Path(item.funscript_path)
                if old_fs.exists():
                    new_fs = old_fs.with_name(f"{safe_new_name}{old_fs.suffix}")
                    old_fs.rename(new_fs)
                    item.funscript_path = str(new_fs)
            if item.metadata_path:
                old_meta = Path(item.metadata_path)
                if old_meta.exists():
                    new_meta = old_meta.with_name(f"{safe_new_name}.json")
                    old_meta.rename(new_meta)
                    item.metadata_path = str(new_meta)
            if item.thumbnail_path:
                old_thumb = Path(item.thumbnail_path)
                if old_thumb.exists():
                    new_thumb = old_thumb.with_name(f"{safe_new_name}{old_thumb.suffix}")
                    old_thumb.rename(new_thumb)
                    item.thumbnail_path = str(new_thumb)
            if item.preview_path:
                old_preview = Path(item.preview_path)
                if old_preview.exists():
                    new_preview = old_preview.with_name(f"{safe_new_name}_preview.mp4")
                    old_preview.rename(new_preview)
                    item.preview_path = str(new_preview)
                    item.preview_updated_at = time.time()  # new URL, so browsers don't show the old clip

            item.title = new_name
            item.touch()
            self.db.save_item(item)
            logger.info("Renamed item [{}] -> '{}'", item.id, new_name)
            return item

    async def change_download_dir(self, new_dir: str, move_existing: bool = True) -> dict:
        if self._current_reencode_id is not None:
            raise ValueError(
                "Can't change the download folder while a re-encode is in progress. "
                "Wait for it to finish or cancel it first."
            )
        new_path = Path(new_dir).expanduser().resolve()
        new_path.mkdir(parents=True, exist_ok=True)

        old_output_dir = self.settings.output_dir
        moved = 0

        if move_existing and old_output_dir != new_path:
            async with activity.track("move_folder", "Moving videos to the new download folder",
                                      progress=lambda: {"stage": "Moving files", "detail": f"{moved} moved so far"}), self._lock:
                changed_items: dict[str, LibraryItem] = {}
                for item in self._items.values():
                    for attr in ("video_path", "funscript_path", "metadata_path", "thumbnail_path", "preview_path"):
                        old_file = getattr(item, attr)
                        if not old_file:
                            continue
                        old_file_path = Path(old_file)
                        if not old_file_path.exists():
                            continue
                        try:
                            rel = old_file_path.relative_to(old_output_dir)
                        except ValueError:
                            # file lives outside the current output dir (e.g. moved
                            # manually) -- leave it where it is rather than guess
                            continue
                        dest = new_path / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        open_files.release(old_file_path)
                        await asyncio.to_thread(shutil.move, str(old_file_path), str(dest))
                        setattr(item, attr, str(dest))
                        moved += 1
                        changed_items[item.id] = item
                if changed_items:
                    self.db.save_items(changed_items.values())

        relinked = 0
        if not move_existing and old_output_dir != new_path:
            relinked = await self._relink_paths(old_output_dir, new_path, ("video_path", "funscript_path"))

        save_overrides(download={"output_dir": str(new_path)})
        self.settings = get_settings()  # pick up the freshly-cleared cache

        return {"moved_files": moved, "relinked_files": relinked, "new_dir": str(new_path)}

    async def run_pending_relink(self) -> None:
        """After a restore (2.25.1, backend/folders.py queue_relink): look for the
        videos, thumbnails and previews whose recorded place is gone in the
        folders now set -- the backup may come from another computer."""
        from ..folders import RELINK_FILE
        try:
            jobs = json.loads(RELINK_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        RELINK_FILE.unlink(missing_ok=True)
        for job in jobs:
            try:
                found = await self._relink_paths(job.get("old"), Path(job["new"]), tuple(job["attrs"]))
                logger.info("After the restore: {} file(s) found in {}", found, job["new"])
            except Exception as exc:  # noqa: BLE001 -- never stop the start over this
                logger.warning("After the restore: couldn't look for files in {}: {}", job.get("new"), exc)

    async def _relink_paths(self, old_root, new_root: Path, attrs: tuple[str, ...]) -> int:
        """Folder changed without moving (2.12.0) -- e.g. after restoring a backup on
        a new machine, with the videos copied to another drive: every file that's
        missing at its old place but exists at the same spot under the new folder
        is pointed there. Files still at their old place are left alone.
        2.25.1: old_root may be a path of another computer (Windows or Linux) or
        None (unknown); then the file is looked for by its last folders
        (<creator>/<file>) under the new folder."""
        relinked = 0
        async with activity.track("move_folder", "Finding the videos in the new folder",
                                  progress=lambda: {"stage": "Checking files", "detail": f"{relinked} found so far"}), self._lock:
            changed: dict[str, LibraryItem] = {}
            for item in self._items.values():
                for attr in attrs:
                    value = getattr(item, attr)
                    if not value:
                        continue
                    old_file = Path(value)
                    if await asyncio.to_thread(old_file.exists):
                        continue
                    candidate = await asyncio.to_thread(_find_moved, value, old_root, new_root)
                    if candidate is not None:
                        setattr(item, attr, str(candidate))
                        relinked += 1
                        changed[item.id] = item
            if changed:
                self.db.save_items(changed.values())
        if relinked:
            logger.info("Pointed {} file(s) at {} (they were missing at {})", relinked, new_root, old_root)
        return relinked

    async def change_cache_dir(self, new_dir: str, move_existing: bool = True) -> dict:
        """Moves settings.download.cache_dir (thumbnails, preview clips,
        metadata JSON) to a new location -- same pattern as
        change_download_dir() above, just walking thumbnail_path/
        preview_path/metadata_path instead of video_path/funscript_path.
        Blocked while a re-encode is running for the same reason: a
        re-encode temporarily rewrites the metadata JSON sidecar (see
        _reencode_item), and moving the cache directory out from under
        that mid-write could corrupt or orphan it."""
        if self._current_reencode_id is not None:
            raise ValueError(
                "Can't change the cache folder while a re-encode is in progress. "
                "Wait for it to finish or cancel it first."
            )
        new_path = Path(new_dir).expanduser().resolve()
        new_path.mkdir(parents=True, exist_ok=True)

        old_cache_dir = self.settings.cache_dir
        moved = 0

        if move_existing and old_cache_dir != new_path:
            async with activity.track("move_folder", "Moving thumbnails and previews to the new cache folder",
                                      progress=lambda: {"stage": "Moving files", "detail": f"{moved} moved so far"}), self._lock:
                changed_items: dict[str, LibraryItem] = {}
                for item in self._items.values():
                    for attr in ("thumbnail_path", "preview_path", "metadata_path"):
                        old_file = getattr(item, attr)
                        if not old_file:
                            continue
                        old_file_path = Path(old_file)
                        if not old_file_path.exists():
                            continue
                        try:
                            rel = old_file_path.relative_to(old_cache_dir)
                        except ValueError:
                            # file lives outside the current cache dir (e.g. moved
                            # manually) -- leave it where it is rather than guess
                            continue
                        dest = new_path / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        open_files.release(old_file_path)
                        await asyncio.to_thread(shutil.move, str(old_file_path), str(dest))
                        setattr(item, attr, str(dest))
                        moved += 1
                        changed_items[item.id] = item
                if changed_items:
                    self.db.save_items(changed_items.values())
                # Seekbar previews (2.21.0): one folder, found by video id.
                # ... and the video frames under the player (3.2.0).
                for folder in (storyboard.FOLDER, video_frames.FOLDER):
                    if (old_cache_dir / folder).is_dir() and not (new_path / folder).exists():
                        await asyncio.to_thread(shutil.move, str(old_cache_dir / folder), str(new_path / folder))

        relinked = 0
        if not move_existing and old_cache_dir != new_path:
            relinked = await self._relink_paths(old_cache_dir, new_path, ("thumbnail_path", "preview_path", "metadata_path"))

        save_overrides(download={"cache_dir": str(new_path)})
        self.settings = get_settings()  # pick up the freshly-cleared cache

        return {"moved_files": moved, "relinked_files": relinked, "new_dir": str(new_path)}

    async def get_metadata(self, video_id: str) -> Optional[dict]:
        async with self._lock:
            item = self._items.get(video_id)
        if not item or not item.metadata_path:
            return None
        path = Path(item.metadata_path)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    async def get_thumbnail_options(self, video_id: str) -> dict:
        """Returns what the thumbnail-picker modal needs: the currently-set
        thumbnail (path + whether it's PMVHaven's own "original" or a
        "custom" pick) and every Video Timeline scrubber frame available as
        an alternate. Timeline thumbnails are normally already on the item
        (populated at metadata-fetch time, or backfilled by a prior repair)
        -- a fresh page fetch is only attempted here as a fallback for
        older items that predate the field, mirroring the same fallback
        _repair_item() already does for timeline_missing."""
        async with self._lock:
            item = self._items.get(video_id)
            if not item:
                raise ValueError("Not found")
            if item.status != "done" or not item.video_path:
                raise ValueError("Only completed downloads have thumbnail options")
            timeline = list(item.timeline_thumbnails)
            page_url = item.url
            site = item.site

        if not timeline and site != "local":  # an uploaded video has no site page
            try:
                v2 = await self._fetch_metadata_v2_or_none(video_id, page_url, site)  # from the source (2.30.0)
                if v2 is not None:
                    timeline = v2[0].timeline_thumbnails
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not fetch timeline thumbnails for {}: {}", video_id, exc)
                timeline = []
            if timeline:
                async with self._lock:
                    item = self._items.get(video_id)
                    if item:
                        item.timeline_thumbnails = timeline
                        item.touch()
                        self.db.save_item(item)

        async with self._lock:
            item = self._items.get(video_id)
            current_path = item.thumbnail_path if item else None
            current_source = item.thumbnail_source if item else "original"

        return {
            "current_thumbnail_path": current_path,
            "thumbnail_source": current_source,
            "timeline_thumbnails": timeline,
        }

    async def set_thumbnail(self, video_id: str, source: str, url: Optional[str] = None,
                            seconds: Optional[float] = None, image: Optional[bytes] = None,
                            image_ext: Optional[str] = None) -> dict:
        """Switches a completed item's thumbnail between PMVHaven's own
        image ("original") and a frame picked from the Video Timeline strip
        ("timeline", identified by `url`). On the FIRST switch away from
        "original", the true original file is preserved on disk as
        "<base_name>_original.<ext>" instead of being deleted -- so
        reverting later doesn't need a re-download. A second (or third,
        etc.) custom pick simply replaces whatever custom file is currently
        in place; the preserved "_original" backup is left untouched once
        it exists. Reverting ("original") deletes the current custom file
        and renames the backup back to the canonical "<base_name>.<ext>"
        name.

        Raises ValueError for anything that can't be resolved (item not
        found/not downloaded, missing url for a "timeline" pick, no backup
        to revert to)."""
        # "frame" grabs the picture at `seconds` from the downloaded video itself
        # -- for when the site's own images are broken or all black.
        # "upload" stores an image file sent by the user (`image`, `image_ext`).
        if source not in ("original", "timeline", "frame", "upload"):
            raise ValueError("source must be 'original', 'timeline', 'frame' or 'upload'")
        if source == "upload" and (not image or image_ext not in ("jpg", "png", "webp")):
            raise ValueError("Send a JPEG, PNG or WebP image")
        if source == "timeline" and not url:
            raise ValueError("A timeline thumbnail URL is required")
        if source == "frame" and (seconds is None or seconds < 0):
            raise ValueError("A position in the video is required")

        async with self._lock:
            item = self._items.get(video_id)
            if not item:
                raise ValueError("Not found")
            if item.status != "done" or not item.video_path:
                raise ValueError("Only completed downloads can have their thumbnail changed")
            current_path = Path(item.thumbnail_path) if item.thumbnail_path else None
            current_source = item.thumbnail_source
            site = item.site
            creator = item.creator
            video_path_str = item.video_path

        # Every thumbnail file for this item -- original, custom, and the
        # preserved "_original" backup -- lives in the same cache folder
        # and shares the same base_name as whatever's currently on record
        # (or, if there's no thumbnail at all yet, the video's own filename
        # stem).
        if current_path:
            cache_dir = current_path.parent
            base_name = current_path.stem
        else:
            cache_dir = self.settings.cache_dir / safe_name(creator or "unknown")
            cache_dir.mkdir(parents=True, exist_ok=True)
            base_name = Path(video_path_str).stem

        # NOT cache_dir.glob() -- base_name routinely contains literal
        # square brackets (every filename ends with "... [<video_id>]",
        # see safe_name()'s allowed character set), and glob/fnmatch
        # treats "[...]" as a character class, not a literal bracket pair.
        # That silently made a glob pattern built from base_name never
        # match the real "<base_name>_original.<ext>" file on disk --
        # which is every item, since every filename has brackets in it.
        # Scanning directory entries and comparing the stem directly avoids
        # glob syntax entirely, so this works regardless of what
        # characters end up in the title/filename.
        backup_path = None
        if cache_dir.exists():
            for candidate in cache_dir.iterdir():
                if candidate.is_file() and candidate.stem == f"{base_name}_original":
                    backup_path = candidate
                    break

        # ---- revert to PMVHaven's original ----
        if source == "original":
            if current_source == "original":
                return self.get_item_dict(video_id)  # already showing it -- no-op
            if not backup_path or not backup_path.exists():
                raise ValueError("No original thumbnail backup found to revert to")

            restored_path = cache_dir / f"{base_name}{backup_path.suffix}"
            async with self._lock:
                item = self._items.get(video_id)
                if not item:
                    raise ValueError("Not found")
                if item.thumbnail_path:
                    stale = Path(item.thumbnail_path)
                    if stale.exists() and stale != backup_path:
                        try:
                            stale.unlink()
                        except OSError:
                            logger.warning("Could not delete custom thumbnail {} while reverting", stale)
                backup_path.rename(restored_path)
                item.thumbnail_path = str(restored_path)
                item.thumbnail_source = "original"
                item.thumbnail_updated_at = time.time()
                item.touch()
                self.db.save_item(item)
            logger.info("Reverted thumbnail for '{}' [{}] -> original", item.title, video_id)
            return self.get_item_dict(video_id)

        # ---- pick a timeline frame / grab a frame from the video ----
        if source == "upload":
            ext = image_ext
        else:
            ext = "jpg" if source == "frame" else url.rsplit(".", 1)[-1].split("?")[0].lower()
        if ext not in ("jpg", "jpeg", "png", "webp"):
            ext = "jpg"
        new_thumb_path = cache_dir / f"{base_name}.{ext}"

        backed_up_this_call = False
        if (
                current_source == "original"
                and current_path
                and current_path.exists()
                and not backup_path
                and current_path != new_thumb_path
        ):
            preserved = cache_dir / f"{base_name}_original{current_path.suffix}"
            current_path.rename(preserved)
            backup_path = preserved
            backed_up_this_call = True

        try:
            # stream_download() writes to a .part temp file and replaces
            # atomically on success, so this safely overwrites an existing
            # file at new_thumb_path too (e.g. re-picking with the same
            # extension as the current custom thumbnail).
            if source == "frame":
                if not Path(video_path_str).exists():
                    raise ValueError("The video file is missing on disk")
                if not await asyncio.to_thread(generate_thumbnail, video_path_str, str(new_thumb_path), seconds):
                    raise ValueError("Could not grab a frame at that position")
            elif source == "upload":
                tmp = new_thumb_path.with_name(new_thumb_path.name + ".part")
                await asyncio.to_thread(tmp.write_bytes, image)
                tmp.replace(new_thumb_path)
            else:
                await sources.get(site).download(url, new_thumb_path)
        except Exception:
            if backed_up_this_call:  # roll back so the original isn't stranded mid-rename
                backup_path.rename(current_path)
            raise

        async with self._lock:
            item = self._items.get(video_id)
            if not item:
                raise ValueError("Not found")
            old_path = Path(item.thumbnail_path) if item.thumbnail_path else None
            # An old custom pick at a DIFFERENT extension than the new one
            # is now orphaned (stream_download only touched new_thumb_path)
            # -- clean it up. Never touches the preserved backup.
            if old_path and old_path.exists() and old_path != new_thumb_path and old_path != backup_path:
                try:
                    old_path.unlink()
                except OSError:
                    pass
            item.thumbnail_path = str(new_thumb_path)
            item.thumbnail_source = "custom"
            item.thumbnail_updated_at = time.time()
            item.touch()
            self.db.save_item(item)

        logger.info("Changed thumbnail for '{}' [{}] -> custom ({})", item.title, video_id,
                    f"video frame at {seconds:.2f}s" if source == "frame" else "uploaded image" if source == "upload" else "timeline frame")
        return self.get_item_dict(video_id)

    def _video_file(self, video_id: str) -> str:
        item = self._items.get(video_id)
        if not item:
            raise ValueError("Not found")
        if item.status != "done" or not item.video_path or not Path(item.video_path).exists():
            raise ValueError("The video file isn't there")
        return item.video_path

    async def grab_frame(self, video_id: str, seconds: float, width: int = 480) -> bytes:
        """A JPEG of the frame at `seconds` (thumbnail choices on the Upload page)."""
        data = await asyncio.to_thread(media_tools.grab_frame_jpeg, self._video_file(video_id), seconds, width)
        if not data:
            raise ValueError("Could not read a frame there")
        return data

    async def preview_plan(self, video_id: str) -> dict:
        """The video's length and suggested preview start times (Upload page)."""
        return await asyncio.to_thread(media_tools.default_preview_timestamps, self._video_file(video_id))

    async def make_preview(self, video_id: str, timestamps: list[float], clip_seconds: float = 2.0) -> dict:
        """(Re)makes the hover preview from the given start times."""
        video = self._video_file(video_id)
        async with self._lock:
            item = self._items[video_id]
            if item.preview_path:
                target = Path(item.preview_path)
            else:
                base = Path(item.thumbnail_path).stem if item.thumbnail_path else Path(video).stem
                folder = Path(item.thumbnail_path).parent if item.thumbnail_path else self.settings.cache_dir / safe_name(item.creator or "unknown")
                target = folder / f"{base}_preview.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        ok = await asyncio.to_thread(media_tools.generate_preview_clip, video, str(target), len(timestamps),
                                     clip_seconds, 480, 24, timestamps)
        if not ok:
            raise ValueError("Could not make the preview (see the log)")
        async with self._lock:
            item = self._items.get(video_id)
            if item:
                item.preview_path = str(target)
                item.preview_updated_at = time.time()
                item.touch()
                self.db.save_item(item)
        self._preview_ok.pop(str(target), None)
        logger.info("Made the preview of '{}' [{}] from {} chosen clips", item.title if item else video_id, video_id, len(timestamps))
        return self.get_item_dict(video_id)

    # path -> mtime of preview files already known to play in a browser
    _preview_ok: dict = {}
    _preview_locks: dict = {}

    async def ensure_playable_preview(self, video_id: str) -> Optional[str]:
        """Returns the item's preview path, first converting the file in place
        if browsers can't decode it (e.g. 10-bit H.264 from the site). Checked
        once per file version, so later requests cost one dict lookup."""
        item = self._items.get(video_id)
        path = item.preview_path if item else None
        if not path or not Path(path).exists():
            return path
        if self._preview_ok.get(path) == Path(path).stat().st_mtime:
            return path
        lock = self._preview_locks.setdefault(video_id, asyncio.Lock())
        async with lock:
            if self._preview_ok.get(path) == Path(path).stat().st_mtime:
                return path
            playable = await asyncio.to_thread(is_browser_playable, path)
            if playable is False:
                logger.info("Preview of [{}] can't play in a browser; converting it to 8-bit H.264", video_id)
                try:
                    converted = await asyncio.to_thread(convert_preview_for_browser, path, path)
                except OSError as exc:  # e.g. the file is open elsewhere on Windows
                    logger.warning("Could not replace the preview of [{}]: {}", video_id, exc)
                    converted = False
                if not converted:
                    return path
            self._preview_ok[path] = Path(path).stat().st_mtime
        return path

    async def set_item_details(self, video_id: str, changes: dict) -> dict:
        """Hand edits from the video page. `changes` maps a field in
        EDITABLE_DETAIL_FIELDS to a list (keep this, whatever syncs/repairs
        bring) or None (drop the edit and go back to the site's value)."""
        async with self._lock:
            item = self._items.get(video_id)
            if not item:
                raise ValueError("Not found")
            for field, value in changes.items():
                if field not in EDITABLE_DETAIL_FIELDS:
                    continue
                if value is None:
                    item.user_edits.pop(field, None)
                    if field in EDITABLE_ITEM_FIELDS:
                        setattr(item, field, item.site_values.pop(field, getattr(item, field)))
                    continue
                cleaned = list(dict.fromkeys(v.strip()[:120] for v in value if v and v.strip()))[:300]
                if field in EDITABLE_ITEM_FIELDS and field not in item.user_edits:
                    item.site_values[field] = list(getattr(item, field))
                item.user_edits[field] = cleaned
            item.touch()
            self.db.save_item(item)
            logger.info("Edited details of '{}' [{}]: {}", item.title, video_id,
                        ", ".join(f"{f} {'reset' if v is None else 'set'}" for f, v in changes.items()))
        return self.get_item_dict(video_id)

    async def resolve_repair_thumbnail(self, video_id: str, choice: str) -> dict:
        """Resolves a repair that paused waiting on a decision about a
        custom thumbnail found broken during a repair pass.

        NOTE: _repair_item() does not currently set repair_pending_action
        or pause for this case -- it still overwrites a broken thumbnail
        unconditionally regardless of thumbnail_source. This method exists
        so the API route doesn't 500 if called, and so the plumbing is
        ready once that pause behavior is added to _repair_item(); until
        then, calling this will raise "No repair is currently waiting..."
        for any item, since repair_pending_action is never actually set.

        choice is "reset_default" (fetch PMVHaven's own thumbnail, clearing
        the custom pick -- delegates to set_thumbnail) or "keep_custom"
        (leave the existing broken file alone; the user picks a
        replacement later via the thumbnail picker instead)."""
        async with self._lock:
            item = self._items.get(video_id)
            if not item:
                raise ValueError("Not found")
            if not item.repair_pending_action:
                raise ValueError("No repair is currently waiting on a decision for this item")
            item.repair_pending_action = None
            item.touch()
            self.db.save_item(item)

        if choice == "reset_default":
            return await self.set_thumbnail(video_id, "original")
        if choice == "keep_custom":
            return self.get_item_dict(video_id)
        raise ValueError("choice must be 'reset_default' or 'keep_custom'")

    async def sync_metadata_from_disk(self) -> dict:
        """Backfills tags/performers/music/description/tech info on every
        completed item from its metadata JSON on disk. This exists because
        those fields were added to LibraryItem after some videos were
        already downloaded (or were downloaded with save_metadata_json off
        at the time), so they never got populated -- there was previously
        no way to catch those items up short of re-downloading them. Safe
        to re-run any time; items already in sync are left untouched.

        Handles both metadata JSON shapes transparently (raw v2 API
        response vs. the older curated v1 shape) via
        _extract_tags_performers_music_from_meta_dict() -- "video_details"
        and "description" are the same key names in both shapes, so those
        two parts need no branching. Ratings needs its own shape branching
        though: the curated v1 shape stores the already-formatted
        "rating_percent" string ("83%"), while the raw v2 shape only has
        the raw numeric "bayesianRating" (83) -- formatted here to match."""
        checked = 0
        updated = 0
        missing = 0

        async with activity.track("metadata_sync", "Syncing details from metadata files",
                                  stage="Reading each video's metadata file"), self._lock:
            changed_items: dict[str, LibraryItem] = {}
            for item in self._items.values():
                if item.status != "done":
                    continue
                checked += 1

                meta_path_str = item.metadata_path
                if not meta_path_str and item.video_path:
                    # Older items may have a metadata JSON on disk without
                    # metadata_path ever having been recorded on the item --
                    # same naming convention _download() itself uses.
                    candidate = Path(item.video_path).with_suffix(".json")
                    if candidate.exists():
                        meta_path_str = str(candidate)

                if not meta_path_str:
                    missing += 1
                    continue

                meta_path = Path(meta_path_str)
                if not meta_path.exists():
                    missing += 1
                    continue

                try:
                    meta_dict = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    logger.warning("Could not read metadata JSON for {} during sync", item.id)
                    missing += 1
                    continue

                changed = False

                if not item.metadata_path:
                    item.metadata_path = str(meta_path)
                    changed = True

                tags, performers, music = extract_tags_performers_music_from_meta_dict(meta_dict)
                if tags != item.tags:
                    item.tags = tags
                    changed = True
                if performers != item.performers:
                    item.performers = performers
                    changed = True
                if music != item.music:
                    item.music = music
                    changed = True

                description = meta_dict.get("description") or ""
                if description and item.description != description:
                    item.description = description
                    changed = True

                video_details = meta_dict.get("video_details", {}) or {}
                tech_map = {
                    "tech_codec": video_details.get("Video Codec"),
                    "tech_resolution": video_details.get("Resolution"),
                    "tech_filesize": video_details.get("File Size"),
                    "tech_duration": video_details.get("Duration"),
                }
                for attr, value in tech_map.items():
                    if value and getattr(item, attr) != value:
                        setattr(item, attr, value)
                        changed = True

                if "rating_percent" in meta_dict:  # curated v1 shape
                    rating = meta_dict.get("rating_percent")
                elif meta_dict.get("bayesianRating") is not None:  # raw v2 shape
                    try:
                        rating = f"{round(float(meta_dict['bayesianRating']))}%"
                    except (TypeError, ValueError):
                        rating = None
                else:
                    rating = None
                if rating and item.ratings != rating:
                    item.ratings = rating
                    changed = True

                if changed:
                    item.touch()
                    changed_items[item.id] = item
                    updated += 1

            if changed_items:
                self.db.save_items(changed_items.values())

        return {"checked": checked, "updated": updated, "missing_metadata": missing}
