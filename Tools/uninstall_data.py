"""What Windows uninstall asks about (3.7.0): which of the app's own files to delete.

    python -m Tools.uninstall_data scan --out FILE                 # INI: what there is, per kind
    python -m Tools.uninstall_data delete --kinds library,cache --out FILE

Kinds, each limited to the folder the uninstall window shows for it:
  library  -- the app's data folder (database, credentials, settings, logs, languages, performer
              photos, playlist covers...), the database file, the app's log files, config.yaml
  cache    -- thumbnails, previews, seekbar previews and frames, in the cache folder
  backups  -- the app's backup files in the backups folder -- never the other folders backups
              were saved to (Backups -> Save to): those are listed as kept
  videos   -- the downloaded and uploaded video files in the videos folder

The rules (3.7.0 fix -- an earlier version also deleted backups in the other folders backups
were saved to, which the window didn't show):
  - nothing outside the folder shown for its kind (a video the library points to elsewhere stays);
  - backup files only by "backups", only in the backups folder; video files only by "videos";
  - in a folder you chose yourself only the app's own files (a Videos or logs folder may hold others).
Folders left empty go too. Deliberately light (no backend.config: that would create the credential store).
"""
from __future__ import annotations

import argparse
import configparser
import json
import os
import re
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
VIDEO_EXT = {".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".wmv", ".flv", ".ts", ".funscript"}
APP_LOG = re.compile(r"^(backend(\..+)?\.log(\.zip)?|app_logs\.db(-wal|-shm|-journal)?|install\.log)$", re.I)


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


def _is_backup(p: Path) -> bool:
    return bool(BACKUP_NAME.match(p.name))


def _is_video(p: Path) -> bool:
    return p.suffix.lower() in VIDEO_EXT


def _other_locations() -> list[Path]:
    """The other folders backups were saved to (Backups -> Save to, data/backup_locations.json)."""
    try:
        raw = json.loads((ROOT / "data" / "backup_locations.json").read_text(encoding="utf-8"))
        return [Path(x).resolve() for x in raw if isinstance(x, str) and x]
    except (OSError, ValueError, TypeError):
        return []


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
        self.log_dir = folders.resolve(value[("logging", "log_dir")])
        self.session_dir = folders.resolve(value[("download", "session_dir")])
        self.other_backups = [d for d in _other_locations() if d != self.backup_dir]
        videos, cache, ids = _item_paths(self.db)
        self.files: dict[str, set[Path]] = {k: set() for k in KINDS}
        self.dirs: dict[str, set[Path]] = {k: set() for k in KINDS}   # removed when left empty
        self.kept: dict[str, list[str]] = {k: [] for k in KINDS}      # shown: what's never touched

        # videos: what the library points to inside the videos folder, plus unfinished uploads
        for p in videos:
            if p.is_file() and _inside(p, self.videos_dir) and not _is_backup(p):
                self.files["videos"].add(p)
                self.dirs["videos"].add(p.parent)
        incoming = self.videos_dir / ".incoming"
        if incoming.is_dir():
            self.files["videos"].update(p for p in _walk(incoming) if not _is_backup(p))
            self.dirs["videos"].add(incoming)
        self.dirs["videos"].add(self.videos_dir)

        # cache (inside the cache folder only): what the library points to, files named after
        # its videos, the app's sub-folders
        if self.cache_dir.is_dir():
            for p in cache:
                if p.is_file() and _inside(p, self.cache_dir):
                    self.files["cache"].add(p)
                    self.dirs["cache"].add(p.parent)
            for p in _walk(self.cache_dir):
                m = VIDEO_ID.search(p.name)
                if (m and m.group(0)[1:-1] in ids) or any(part in CACHE_SUBFOLDERS for part in p.relative_to(self.cache_dir).parts[:1]):
                    self.files["cache"].add(p)
                    self.dirs["cache"].add(p.parent)
            self.dirs["cache"].add(self.cache_dir)

        # backups: the app's backup files directly in the backups folder -- nothing else
        if self.backup_dir.is_dir():
            for p in self.backup_dir.iterdir():
                if p.is_file() and _is_backup(p):
                    self.files["backups"].add(p)
            self.dirs["backups"].add(self.backup_dir)
        self.kept["backups"] = [str(d) for d in self.other_backups if d.is_dir()]

        # library: the app's data folder (minus the other kinds' folders), the database file
        # wherever it is, the app's log files, config.yaml
        protected = [self.videos_dir, self.cache_dir, self.backup_dir, *self.other_backups]
        if self.data.is_dir():
            for p in _walk(self.data):
                if not any(_inside(p, o) for o in protected):
                    self.files["library"].add(p)
                    self.dirs["library"].add(p.parent)
            self.dirs["library"].add(self.data)
        for p in (self.db, self.db.with_name(self.db.name + "-wal"), self.db.with_name(self.db.name + "-shm")):
            if p.is_file():
                self.files["library"].add(p)
        if not _inside(self.log_dir, self.data) and self.log_dir.is_dir():   # a logs folder you chose
            for p in self.log_dir.iterdir():
                if p.is_file() and APP_LOG.match(p.name):
                    self.files["library"].add(p)
            self.dirs["library"].add(self.log_dir)
        if not _inside(self.session_dir, self.data) and self.session_dir.is_dir():
            self.kept["library"].append(str(self.session_dir))   # sign-ins in a folder you chose: yours to delete
        if (ROOT / "config.yaml").is_file():
            self.files["library"].add(ROOT / "config.yaml")

        # the rules above, once more for every kind: backups only by "backups", videos only by "videos"
        for kind in KINDS:
            if kind != "backups":
                self.files[kind] = {p for p in self.files[kind] if not _is_backup(p)}
            if kind != "videos":   # (the cache's own re-encode leftovers are cache)
                transcode = self.cache_dir / ".transcode"
                self.files[kind] = {p for p in self.files[kind]
                                    if not _is_video(p) or (kind == "cache" and _inside(p, transcode))}
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
    ini = configparser.ConfigParser(interpolation=None)
    for kind in KINDS:
        n, size = len(plan.files[kind]), plan.size(kind)
        ini[kind] = {"count": str(n), "bytes": str(size), "size": _size(size), "path": str(plan.where(kind)),
                     "kept": "; ".join(plan.kept[kind])}
    with out.open("w", encoding="utf-8") as f:
        ini.write(f)
    return 0


def delete(kinds: list[str], out: Path | None) -> int:
    plan = Plan()
    ini = configparser.ConfigParser(interpolation=None)
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
        _remove_empty(plan.dirs[kind], plan.where(kind), everything=kind == "library")
        if kind == "library" and plan.log_dir in plan.dirs["library"]:
            _remove_empty({plan.log_dir}, plan.log_dir, everything=False)
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
