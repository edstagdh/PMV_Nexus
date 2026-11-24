"""What Windows uninstall asks about (3.7.0): which of the app's own files to delete.

    python -m Tools.uninstall_data scan --out FILE                 # INI: what there is, per kind
    python -m Tools.uninstall_data delete --kinds library,cache --out FILE

Kinds:
  library  -- the database and settings: the data folder (database, credentials, settings,
              logs, languages, performer photos, playlist covers...) and config.yaml
  cache    -- thumbnails, previews, seekbar previews and frames (the cache folder)
  backups  -- the backup files in the backup folder
  videos   -- the downloaded and uploaded video files

Only files the app knows are deleted: the videos and cache files its library points to (or
named after its videos), its backup files, its own sub-folders -- never anything else in a
folder you chose yourself (a Videos folder may hold other files). Folders left empty go too.
Deliberately light (no backend.config: that would create the credential store).
"""
from __future__ import annotations

import argparse
import configparser
import json
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import folders  # noqa: E402

KINDS = ("library", "cache", "backups", "videos")
CACHE_SUBFOLDERS = (".storyboards", ".frames", ".transcode", "performers")
VIDEO_ID = re.compile(r"\[[0-9a-f]{24}\]")
BACKUP_NAME = re.compile(r"^(pmvnexus|pmvhaven)_backup_.*\.zip(\.\d{3})?$", re.I)  # (3.7.0) split parts too


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} bytes"


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder)
        return True
    except ValueError:
        return False


def _item_paths(db: Path) -> tuple[list[Path], list[Path], set[str]]:
    """(video files, cache files, video ids) the library points to."""
    videos, cache, ids = [], [], set()
    if not db.is_file():
        return videos, cache, ids
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute("SELECT id, data FROM library_items").fetchall()
        con.close()
    except sqlite3.Error:
        return videos, cache, ids
    for item_id, data in rows:
        ids.add(str(item_id))
        try:
            d = json.loads(data)
        except (TypeError, ValueError):
            continue
        for key, bucket in (("video_path", videos), ("funscript_path", videos), ("metadata_path", cache),
                            ("thumbnail_path", cache), ("preview_path", cache)):
            if d.get(key):
                bucket.append(folders.resolve(d[key]))
    return videos, cache, ids


def _walk(folder: Path):
    for dirpath, _dirs, files in os.walk(folder):
        for name in files:
            yield Path(dirpath, name)


class Plan:
    """Every file to delete per kind, worked out once for scan and delete alike."""

    def __init__(self):
        config, overrides = folders.current_settings()
        value = folders.effective(config, overrides)
        self.data = (ROOT / "data").resolve()
        self.videos_dir = folders.resolve(value[("download", "output_dir")])
        self.cache_dir = folders.resolve(value[("download", "cache_dir")])
        self.backup_dir = folders.resolve(value[("backup", "dir")])
        self.db = folders.resolve(value[("download", "library_db_file")])
        self.extra_dirs = [folders.resolve(value[("logging", "log_dir")]), folders.resolve(value[("download", "session_dir")])]
        videos, cache, ids = _item_paths(self.db)
        self.files: dict[str, set[Path]] = {k: set() for k in KINDS}
        self.dirs: dict[str, set[Path]] = {k: set() for k in KINDS}   # removed when left empty

        # videos: what the library points to, plus unfinished uploads
        for p in videos:
            if p.is_file():
                self.files["videos"].add(p)
                self.dirs["videos"].add(p.parent)
        incoming = self.videos_dir / ".incoming"
        if incoming.is_dir():
            self.files["videos"].update(_walk(incoming))
            self.dirs["videos"].add(incoming)
        self.dirs["videos"].add(self.videos_dir)

        # cache: what the library points to, files named after its videos, the app's sub-folders
        for p in cache:
            if p.is_file():
                self.files["cache"].add(p)
                self.dirs["cache"].add(p.parent)
        if self.cache_dir.is_dir():
            for p in _walk(self.cache_dir):
                m = VIDEO_ID.search(p.name)
                if (m and m.group(0)[1:-1] in ids) or any(part in CACHE_SUBFOLDERS for part in p.relative_to(self.cache_dir).parts[:1]):
                    self.files["cache"].add(p)
                    self.dirs["cache"].add(p.parent)
            self.dirs["cache"].add(self.cache_dir)

        # backups: the app's backup files only
        if self.backup_dir.is_dir():
            for p in self.backup_dir.iterdir():
                if p.is_file() and BACKUP_NAME.match(p.name):
                    self.files["backups"].add(p)
            self.dirs["backups"].add(self.backup_dir)

        # library: everything in the app's data folder that isn't one of the other kinds' folders,
        # the database (wherever it is), logs, sign-ins, config.yaml
        others = [self.videos_dir, self.cache_dir, self.backup_dir]
        if self.data.is_dir():
            for p in _walk(self.data):
                if not any(_inside(p, o) for o in others):
                    self.files["library"].add(p)
                    self.dirs["library"].add(p.parent)
            self.dirs["library"].add(self.data)
        for p in (self.db, self.db.with_name(self.db.name + "-wal"), self.db.with_name(self.db.name + "-shm")):
            if p.is_file():
                self.files["library"].add(p)
        for d in self.extra_dirs:
            if d.is_dir():
                self.files["library"].update(_walk(d))
                self.dirs["library"].add(d)
        if (ROOT / "config.yaml").is_file():
            self.files["library"].add(ROOT / "config.yaml")
        # a file counted twice (e.g. a video inside the data folder) belongs to its own kind
        for kind in ("videos", "cache", "backups"):
            self.files["library"] -= self.files[kind]

    def where(self, kind: str) -> Path:
        return {"library": self.data, "cache": self.cache_dir, "backups": self.backup_dir, "videos": self.videos_dir}[kind]

    def size(self, kind: str) -> int:
        total = 0
        for p in self.files[kind]:
            try:
                total += p.stat().st_size
            except OSError:
                pass
        return total


def _remove_empty(dirs: set[Path], root: Path, everything: bool) -> None:
    """Removes the folders files were deleted from when they're empty now, and their empty
    parents up to `root` (included). `everything` (the app's own data folder): every empty
    folder in `root` too."""
    todo = set(dirs)
    if everything and root.is_dir():
        todo.update(Path(d) for d, _s, _f in os.walk(root))
    for d in sorted(todo, key=lambda p: len(p.parts), reverse=True):
        while _inside(d, root):
            try:
                d.rmdir()  # only an empty one
            except OSError:
                break
            if d == root:
                break
            d = d.parent


def scan(out: Path) -> int:
    plan = Plan()
    ini = configparser.ConfigParser()
    for kind in KINDS:
        n, size = len(plan.files[kind]), plan.size(kind)
        ini[kind] = {"count": str(n), "bytes": str(size), "size": _size(size), "path": str(plan.where(kind))}
    with out.open("w", encoding="utf-8") as f:
        ini.write(f)
    return 0


def delete(kinds: list[str], out: Path | None) -> int:
    plan = Plan()
    ini = configparser.ConfigParser()
    failed_total = 0
    for kind in kinds:
        done = failed = 0
        for p in sorted(plan.files[kind]):
            try:
                p.unlink()
                done += 1
            except FileNotFoundError:
                pass
            except OSError:
                failed += 1
        if kind == "cache":
            for sub in CACHE_SUBFOLDERS:
                shutil.rmtree(plan.cache_dir / sub, ignore_errors=True)
        _remove_empty(plan.dirs[kind], plan.where(kind), everything=kind == "library")
        if kind == "library":
            for d in plan.extra_dirs:
                _remove_empty({d}, d, everything=True)
        ini[kind] = {"deleted": str(done), "failed": str(failed)}
        failed_total += failed
    if out:
        with out.open("w", encoding="utf-8") as f:
            ini.write(f)
    return 1 if failed_total else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan")
    p.add_argument("--out", required=True)
    p = sub.add_parser("delete")
    p.add_argument("--kinds", required=True)
    p.add_argument("--out")
    a = ap.parse_args()
    if a.cmd == "scan":
        return scan(Path(a.out))
    kinds = [k for k in a.kinds.split(",") if k in KINDS]
    return delete(kinds, Path(a.out) if a.out else None)


if __name__ == "__main__":
    sys.exit(main())
