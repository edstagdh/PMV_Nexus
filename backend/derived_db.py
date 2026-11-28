"""data/derived.db (3.12.0): the second database, for data the app can make again from the
videos -- shared-scene fingerprints (library_mixins/scenes.py) and faces found in videos and
performer photos (library_mixins/faces.py).

Why its own file: SQLite lets one connection write at a time, so a long job writing a lot here
no longer makes the main database (library.db: ratings, watch positions, settings) wait, and the
main database and its backups stay small. Nothing here is a decision of yours: your approved or
rejected face matches are in library.db. Losing this file loses nothing that can't be made again
(Maintenance -> Shared scenes -> Find now, Faces -> Scan).

The upgrade step "derived_db" (backend/upgrades/steps.py) moves the 3.9.0 shared-scene tables here
from library.db once.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from .db import _LockedConnection

SCHEMA = """
CREATE TABLE IF NOT EXISTS scene_hashes (
    video_id   TEXT PRIMARY KEY,
    version    INTEGER NOT NULL,
    sb_made_at REAL,                   -- the seekbar previews they were made from
    data       BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS scene_links (
    a        TEXT NOT NULL,
    b        TEXT NOT NULL,
    frames_a INTEGER NOT NULL,
    frames_b INTEGER NOT NULL,
    scenes   TEXT NOT NULL,            -- JSON [[a_from, a_to, b_from, b_to], ...] seconds
    PRIMARY KEY (a, b)
);
CREATE INDEX IF NOT EXISTS idx_scene_links_b ON scene_links(b);
-- faces in the photos of performers (the references a face in a video is compared with)
CREATE TABLE IF NOT EXISTS face_refs (
    performer_id TEXT NOT NULL,
    photo        TEXT NOT NULL,        -- the photo's file name
    photo_mtime  REAL,                 -- read again when the photo changes
    version      INTEGER NOT NULL,     -- faces.MODEL_VERSION
    embedding    BLOB,                 -- 128 float32, NULL: no usable face in that photo
    quality      REAL,
    PRIMARY KEY (performer_id, photo)
);
-- faces found in a video's pictures (seekbar previews, thumbnail, frames)
CREATE TABLE IF NOT EXISTS face_detections (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id   TEXT NOT NULL,
    source     TEXT NOT NULL,          -- storyboard | thumbnail | frame
    at_seconds REAL,                   -- where in the video, when known
    box        TEXT NOT NULL,          -- JSON [x, y, w, h] in the source picture
    quality    REAL NOT NULL,          -- the detector's score, 0..1
    embedding  BLOB NOT NULL,          -- 128 float32
    crop       BLOB                    -- small JPEG of the face, for reviewing matches
);
CREATE INDEX IF NOT EXISTS idx_face_det_video ON face_detections(video_id);
CREATE TABLE IF NOT EXISTS face_scans (
    video_id   TEXT PRIMARY KEY,
    version    INTEGER NOT NULL,       -- faces.MODEL_VERSION
    signature  TEXT NOT NULL,          -- what was read (sources and their dates): read again when it changes
    scanned_at REAL NOT NULL,
    faces      INTEGER NOT NULL
);
"""


class DerivedDB:
    """Like LibraryDB: synchronous, one connection used under one lock."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        self._conn = _LockedConnection(conn)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def size(self) -> int:
        total = 0
        for suffix in ("", "-wal"):
            p = Path(str(self.path) + suffix)
            if p.is_file():
                total += p.stat().st_size
        return total

    # ------------------------------------------------------------ shared scenes (3.9.0)
    def scene_hash_marks(self) -> dict[str, tuple[int, Optional[float]]]:
        return {r["video_id"]: (r["version"], r["sb_made_at"])
                for r in self._conn.execute("SELECT video_id, version, sb_made_at FROM scene_hashes")}

    def scene_hash_blobs(self) -> dict[str, bytes]:
        return {r["video_id"]: bytes(r["data"]) for r in self._conn.execute("SELECT video_id, data FROM scene_hashes")}

    def set_scene_hash(self, video_id: str, version: int, sb_made_at: Optional[float], data: bytes) -> None:
        self._conn.execute("INSERT OR REPLACE INTO scene_hashes (video_id, version, sb_made_at, data) VALUES (?, ?, ?, ?)",
                           (video_id, version, sb_made_at, data))
        self._conn.commit()

    def delete_scene_hashes(self, video_ids: Iterable[str]) -> None:
        self._conn.executemany("DELETE FROM scene_hashes WHERE video_id = ?", [(v,) for v in video_ids])
        self._conn.commit()

    def replace_scene_links(self, rows: list[tuple]) -> None:
        """rows: (a, b, frames_a, frames_b, scenes list) -- the whole set, found again."""
        with self._conn.lock:
            try:
                self._conn.execute("DELETE FROM scene_links")
                self._conn.executemany("INSERT OR REPLACE INTO scene_links (a, b, frames_a, frames_b, scenes) VALUES (?, ?, ?, ?, ?)",
                                       [(a, b, fa, fb, json.dumps(sc)) for a, b, fa, fb, sc in rows])
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    def scene_link_rows(self) -> list[dict]:
        out = []
        for r in self._conn.execute("SELECT * FROM scene_links"):
            d = dict(r)
            d["scenes"] = json.loads(d["scenes"] or "[]")
            out.append(d)
        return out

    def scene_data_size(self) -> int:
        row = self._conn.execute("SELECT COALESCE(SUM(LENGTH(data)), 0) FROM scene_hashes").fetchone()
        return int(row[0] or 0)

    # ------------------------------------------------------------ faces (3.12.0)
    def face_ref_rows(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT performer_id, photo, photo_mtime, version, embedding, quality FROM face_refs")]

    def set_face_ref(self, performer_id: str, photo: str, mtime: Optional[float], version: int,
                     embedding: Optional[bytes], quality: Optional[float]) -> None:
        self._conn.execute("INSERT OR REPLACE INTO face_refs (performer_id, photo, photo_mtime, version, embedding, quality) "
                           "VALUES (?, ?, ?, ?, ?, ?)", (performer_id, photo, mtime, version, embedding, quality))
        self._conn.commit()

    def delete_face_refs(self, keys: Iterable[tuple[str, str]]) -> None:
        self._conn.executemany("DELETE FROM face_refs WHERE performer_id = ? AND photo = ?", list(keys))
        self._conn.commit()

    def face_scan_marks(self) -> dict[str, tuple[int, str]]:
        return {r["video_id"]: (r["version"], r["signature"])
                for r in self._conn.execute("SELECT video_id, version, signature FROM face_scans")}

    def replace_face_detections(self, video_id: str, version: int, signature: str, faces: list[dict]) -> None:
        """One video's faces, read again: the old ones are replaced in one go."""
        with self._conn.lock:
            try:
                self._conn.execute("DELETE FROM face_detections WHERE video_id = ?", (video_id,))
                for f in faces:
                    self._conn.execute(
                        "INSERT INTO face_detections (video_id, source, at_seconds, box, quality, embedding, crop) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (video_id, f["source"], f.get("at"), json.dumps(f["box"]), f["quality"], f["embedding"], f.get("crop")))
                self._conn.execute("INSERT OR REPLACE INTO face_scans (video_id, version, signature, scanned_at, faces) "
                                   "VALUES (?, ?, ?, strftime('%s','now'), ?)", (video_id, version, signature, len(faces)))
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    def face_detections_of(self, video_id: str) -> list[tuple[int, bytes]]:
        return [(r["id"], bytes(r["embedding"])) for r in self._conn.execute(
            "SELECT id, embedding FROM face_detections WHERE video_id = ?", (video_id,))]

    def face_detection(self, detection_id: int) -> Optional[dict]:
        row = self._conn.execute("SELECT id, video_id, source, at_seconds, crop FROM face_detections WHERE id = ?",
                                 (detection_id,)).fetchone()
        return dict(row) if row else None

    def delete_face_videos(self, video_ids: Iterable[str]) -> None:
        ids = [(v,) for v in video_ids]
        self._conn.executemany("DELETE FROM face_detections WHERE video_id = ?", ids)
        self._conn.executemany("DELETE FROM face_scans WHERE video_id = ?", ids)
        self._conn.commit()

    def face_counts(self) -> dict:
        scans = self._conn.execute("SELECT COUNT(*), COALESCE(SUM(faces), 0) FROM face_scans").fetchone()
        refs = self._conn.execute("SELECT COUNT(DISTINCT performer_id) FROM face_refs WHERE embedding IS NOT NULL").fetchone()
        size = self._conn.execute("SELECT COALESCE(SUM(LENGTH(embedding) + COALESCE(LENGTH(crop), 0)), 0) FROM face_detections").fetchone()
        return {"videos": scans[0], "faces": scans[1], "performers": refs[0], "size": int(size[0] or 0)}
