"""Server Settings -> Download -> Folders (3.12.0): folders of their own for performer photos from
the sites, your own performer photos, and generated files (seekbar previews, video frames, the
pieces of a conversion for watching).

Changing one moves what's there (optional) as a job on Tasks:
1. the new folder is checked: not one of the app's other folders, not inside the old one or the
   other way round, and -- for photos -- empty (so Untracked files never shows someone else's
   files as the app's own);
2. the setting is saved first, so new files already go to the new folder and a move cut off half
   way is finished at the next start (relocate_performer_photos) or simply made again;
3. each file is moved; one that's already in the new folder is left where it was (never
   overwritten, never deleted);
4. the database is pointed at the moved photos.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from loguru import logger

from ..i18n import L
from .. import activity, storyboard, video_frames
from ..config import ROOT_DIR, save_overrides
from ..resources import JOB, resources

KINDS = {
    "performer_photos": ("performer_photos_dir", L("Performer photos from the sites")),
    "own_photos": ("own_photos_dir", L("Your own performer photos")),
    "generated": ("generated_dir", L("Generated files (seekbar previews, video frames)")),
}


def _inside(a: Path, b: Path) -> bool:
    return a == b or b in a.parents


class AppFoldersMixin:
    def app_folders(self) -> dict:
        s = self.settings
        return {"performer_photos": str(s.performer_photos_dir), "own_photos": str(s.own_photos_dir),
                "generated": str(s.generated_dir), "generated_in_cache": not s.download.generated_dir}

    def _check_new_folder(self, kind: str, old: Path, new: Path) -> None:
        s = self.settings
        if new == old:
            raise ValueError(L("That's the folder already in use"))
        others = {ROOT_DIR.resolve(), s.library_db_file.parent, s.output_dir, s.backup_dir, s.log_dir}
        if kind != "generated":
            others.add(s.cache_dir)
        if kind != "performer_photos":
            others.add(s.performer_photos_dir)
        if kind != "own_photos":
            others.add(s.own_photos_dir)
        if kind != "generated" and s.download.generated_dir:
            others.add(s.generated_dir)
        if new in others:
            raise ValueError(L("That's one of the app's other folders -- choose a folder of its own"))
        if _inside(new, old) or _inside(old, new):
            raise ValueError(L("The new folder can't be inside the old one, or the other way round"))
        if kind != "generated" and new.is_dir() and any(new.iterdir()):
            raise ValueError(L("Choose an empty folder: the photos go there, and only them"))

    async def change_app_folder(self, kind: str, new_dir: str, move_existing: bool = True) -> dict:
        if kind not in KINDS:
            raise ValueError(L("Unknown folder"))
        field, label = KINDS[kind]
        s = self.settings
        old = {"performer_photos": s.performer_photos_dir, "own_photos": s.own_photos_dir, "generated": s.generated_dir}[kind]
        reset = kind == "generated" and (not new_dir.strip() or Path(new_dir).expanduser().resolve() == s.cache_dir)
        new = s.cache_dir if reset else Path(new_dir).expanduser().resolve()
        if not new.is_absolute():
            raise ValueError(L("Give the full path of the folder"))
        self._check_new_folder(kind, old, new)
        new.mkdir(parents=True, exist_ok=True)
        state = {"moved": 0, "kept": 0, "failed": 0}
        save_overrides(download={field: "" if reset else str(new)})     # new files go there from now on
        logger.info("Folder for {} changed: {} -> {}{}", kind, old, new, " (moving what's there)" if move_existing else "")
        if move_existing:
            async with activity.track("move_folder", L("Moving {what} to the new folder", what=label),
                                      progress=lambda: {"stage": L("Moving files"), "detail": L("{moved} moved so far", moved=state["moved"])}), \
                    resources.use({"cpu", "disk"}, L("Moving {what} to the new folder", what=label), JOB):
                if kind == "generated":
                    await asyncio.to_thread(self._move_generated, old, new, state)
                else:
                    await asyncio.to_thread(self._move_files, old, new, state)
        if kind == "performer_photos":
            await asyncio.to_thread(self.relocate_performer_photos)     # points the profiles at the new folder
        elif kind == "own_photos":
            await asyncio.to_thread(self._repoint_own_photos, new)
        self._perf_cache_clear()
        logger.info("Folder for {}: {} moved, {} left where they were (already there), {} couldn't be moved",
                    kind, state["moved"], state["kept"], state["failed"])
        return {"ok": True, "folder": str(new), "moved_files": state["moved"], "kept": state["kept"], "failed": state["failed"]}

    # ------------------------------------------------------------ moving (worker thread)
    @staticmethod
    def _move_one(src: Path, dest: Path, state: dict) -> None:
        if dest.exists():
            state["kept"] += 1     # never overwrite: the one in the old folder stays
            return
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            state["moved"] += 1
        except OSError as exc:
            state["failed"] += 1
            logger.warning("Couldn't move {} to {}: {}", src, dest, exc)

    def _move_files(self, old: Path, new: Path, state: dict) -> None:
        """BLOCKING. The photos at the top of the old folder (the app keeps them there, nothing below)."""
        if not old.is_dir():
            return
        for f in sorted(old.iterdir()):
            if f.is_file():
                self._move_one(f, new / f.name, state)
        try:
            if not any(old.iterdir()) and old != self.settings.library_db_file.parent:
                old.rmdir()
        except OSError:
            pass

    def _move_generated(self, old: Path, new: Path, state: dict) -> None:
        """BLOCKING. Each video's seekbar previews and frames (a folder per video); the pieces of a
        conversion for watching aren't moved (they're temporary; the old ones are cleared)."""
        for name in (storyboard.FOLDER, video_frames.FOLDER):
            src_root = old / name
            if not src_root.is_dir():
                continue
            for d in sorted(src_root.iterdir()):
                if d.is_dir() and not d.name.endswith(".part"):   # one being made now is made again
                    self._move_one(d, new / name / d.name, state)
                elif d.is_file():
                    self._move_one(d, new / name / d.name, state)
            try:
                if not any(src_root.iterdir()):
                    src_root.rmdir()
            except OSError:
                pass

    def _repoint_own_photos(self, new: Path) -> None:
        """BLOCKING. Your photos the database points at in the old folder -> the same name in the new one."""
        for c in self.db.custom_photo_rows():
            cur = Path(c["path"])
            cand = new / cur.name
            if cur.parent != new and not cur.is_file() and cand.is_file():
                self.db.set_custom_photo_path(c["id"], str(cand))

    def _perf_cache_clear(self) -> None:
        """Performer profiles are kept in memory with their photo paths: read them again."""
        self._perf_load(force=True)
