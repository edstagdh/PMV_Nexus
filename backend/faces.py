"""Faces (3.12.0): finding faces in pictures and telling whether two are the same person --
completely offline, with OpenCV (already one of the app's packages) and two model files:

- YuNet (face_detection_yunet_2023mar.onnx, MIT license): finds faces and their landmarks;
- SFace (face_recognition_sface_2021dec.onnx, Apache-2.0): 128 numbers per face; two faces of the
  same person point the same way (cosine similarity; OpenCV gives 0.363 as its "same person" line).

Both are in the installers (models/faces); a git checkout gets them with
`python -m Tools.get_face_models`. Nothing here reaches the network.

Scores shown to people are the cosine similarity in percent (0-100): 36 is OpenCV's line for
"same person", the high 60s and up are clear matches of the same photo-shoot quality.
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from loguru import logger

from .face_models import MODEL_DIR, MODELS   # the files, their checksums and where they come from

MODEL_VERSION = 1   # stored with every face: a new model means reading them again
EMBED_DIM = 128
SAME_PERSON = 0.363   # OpenCV's cosine line for "the same person" with SFace

_checked: dict[str, tuple] = {}
_local = threading.local()


class FacesUnavailable(RuntimeError):
    pass


def model_path(kind: str) -> Path:
    return MODEL_DIR / MODELS[kind]["file"]


def models_status() -> dict:
    """{"ok", "missing": [file names], "bad": [file names]} -- checks each file's SHA-256 once per change."""
    missing, bad = [], []
    for kind, m in MODELS.items():
        p = model_path(kind)
        if not p.is_file():
            missing.append(m["file"])
            continue
        st = p.stat()
        mark = (st.st_size, st.st_mtime)
        if _checked.get(kind, (None,))[0] != mark:
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            _checked[kind] = (mark, h.hexdigest() == m["sha256"])
        if not _checked[kind][1]:
            bad.append(m["file"])
    return {"ok": not missing and not bad, "missing": missing, "bad": bad, "folder": str(MODEL_DIR)}


def _engine(score_threshold: float):
    """One detector + recognizer per worker thread (OpenCV's objects aren't shared between threads)."""
    st = models_status()
    if not st["ok"]:
        raise FacesUnavailable("Face models missing or damaged: " + ", ".join(st["missing"] + st["bad"]))
    import cv2
    eng = getattr(_local, "engine", None)
    if eng is None:
        try:   # OpenCV's own notes ("Targets are not supported...") aren't for people
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        except AttributeError:
            pass
        det = cv2.FaceDetectorYN.create(str(model_path("detector")), "", (320, 320), score_threshold, 0.3, 5000)
        rec = cv2.FaceRecognizerSF.create(str(model_path("recognizer")), "")
        eng = _local.engine = {"det": det, "rec": rec, "threshold": score_threshold}
    if eng["threshold"] != score_threshold:
        eng["det"].setScoreThreshold(score_threshold)
        eng["threshold"] = score_threshold
    return eng


def read_image(path: str):
    """BLOCKING. A picture as OpenCV reads it (BGR), or None. Reads through numpy so paths with any
    characters work on Windows."""
    import cv2
    import numpy as np
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def find_faces(img, *, min_size: int = 32, min_quality: float = 0.85, upscale_below: int = 0,
               max_faces: int = 20) -> list[dict]:
    """BLOCKING. The faces in one picture: [{box [x,y,w,h], quality, embedding (128 float32, unit
    length), crop (JPEG bytes, 96 px)}], biggest first. `upscale_below`: pictures narrower than this
    are enlarged 2x first (seekbar tiles)."""
    import cv2
    import numpy as np
    if img is None or img.size == 0:
        return []
    scale = 1.0
    if upscale_below and img.shape[1] < upscale_below:
        scale = 2.0
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    eng = _engine(min_quality)
    det, rec = eng["det"], eng["rec"]
    det.setInputSize((img.shape[1], img.shape[0]))
    _n, faces = det.detect(img)
    if faces is None:
        return []
    out = []
    for f in sorted(faces, key=lambda r: -(r[2] * r[3]))[:max_faces]:
        w, h = float(f[2]) / scale, float(f[3]) / scale
        if min(w, h) < min_size:
            continue
        aligned = rec.alignCrop(img, f)
        emb = rec.feature(aligned).astype(np.float32).flatten()
        norm = float(np.linalg.norm(emb))
        if not norm:
            continue
        emb /= norm
        ok, jpg = cv2.imencode(".jpg", cv2.resize(aligned, (96, 96)), [cv2.IMWRITE_JPEG_QUALITY, 85])
        out.append({"box": [round(float(f[0]) / scale), round(float(f[1]) / scale), round(w), round(h)],
                    "quality": round(float(f[14]), 3), "embedding": emb, "crop": jpg.tobytes() if ok else None})
    return out


def to_blob(emb) -> bytes:
    import numpy as np
    return np.asarray(emb, dtype=np.float32).tobytes()


def from_blob(blob: bytes):
    import numpy as np
    a = np.frombuffer(blob, dtype=np.float32)
    return a if a.size == EMBED_DIM else None


class RefIndex:
    """The performers' reference faces as one matrix, for comparing a video's faces with all of
    them at once. references: {performer_id: [embeddings]}."""

    def __init__(self, references: dict):
        import numpy as np
        ids, rows, starts = [], [], []
        for pid, embs in references.items():
            if not embs:
                continue
            ids.append(pid)
            starts.append(len(rows))
            rows.extend(embs)
        self.ids = ids
        self._starts = np.asarray(starts, dtype=np.intp)
        self._R = np.stack(rows).astype(np.float32) if rows else np.zeros((0, EMBED_DIM), dtype=np.float32)

    def __len__(self) -> int:
        return len(self.ids)

    def best_matches(self, detections: list, skip: frozenset = frozenset()) -> dict:
        """detections: [(detection id, embedding)] of one video.
        -> {performer_id: {"score": best cosine*100, "support": faces of the video at or above the
        "same person" line, "detection": id of the best face}}; performers in `skip` left out."""
        import numpy as np
        if not detections or not self.ids:
            return {}
        det_ids = [d for d, _e in detections]
        D = np.stack([e for _d, e in detections]).astype(np.float32)     # faces x 128
        per_perf = np.maximum.reduceat(D @ self._R.T, self._starts, axis=1)   # faces x performers
        best_face = per_perf.argmax(axis=0)
        best = per_perf[best_face, np.arange(len(self.ids))]
        support = (per_perf >= SAME_PERSON).sum(axis=0)
        out = {}
        for j, pid in enumerate(self.ids):
            if pid in skip:
                continue
            out[pid] = {"score": round(float(best[j]) * 100, 1), "support": int(support[j]),
                        "detection": det_ids[int(best_face[j])]}
        return out


    def assign(self, detections: list, skip: frozenset = frozenset(), line: float = 0.36,
               margin: float = 0.05) -> dict:
        """3.13.0: each face of the video counts for ONE performer only -- the one it looks most
        like, when that's at or above `line` (cosine) and at least `margin` more than the next
        performer. A face that looks most like a performer the video already lists counts for
        them (so it can't suggest anyone else). -> {performer_id: {"score", "support", "detection"}}
        for the others (`skip` left out); score = the best of its faces, support = how many."""
        import numpy as np
        if not detections or not self.ids:
            return {}
        det_ids = [d for d, _e in detections]
        D = np.stack([e for _d, e in detections]).astype(np.float32)
        per_perf = np.maximum.reduceat(D @ self._R.T, self._starts, axis=1)   # faces x performers
        if per_perf.shape[1] > 1:
            top2 = np.sort(per_perf, axis=1)[:, -2:]
            best_s, second = top2[:, 1], top2[:, 0]
        else:
            best_s, second = per_perf[:, 0], np.full(per_perf.shape[0], -1.0, dtype=np.float32)
        best_j = per_perf.argmax(axis=1)
        out: dict = {}
        for f in range(len(det_ids)):
            if best_s[f] < line or best_s[f] - second[f] < margin:
                continue
            pid = self.ids[int(best_j[f])]
            if pid in skip:
                continue
            m = out.setdefault(pid, {"score": -1.0, "support": 0, "detection": None})
            m["support"] += 1
            if best_s[f] > m["score"]:
                m["score"], m["detection"] = float(best_s[f]), det_ids[f]
        for m in out.values():
            m["score"] = round(m["score"] * 100, 1)
        return out


def best_matches(detections: list, references: dict) -> dict:
    return RefIndex(references).best_matches(detections)


# One thread reads faces: one copy of the models in memory, and OpenCV's objects stay in it.
_POOL = None


def run(fn, *args):
    """Awaitable: fn(*args) on the faces thread."""
    import asyncio
    import concurrent.futures
    global _POOL
    if _POOL is None:
        _POOL = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="faces")
    return asyncio.get_running_loop().run_in_executor(_POOL, lambda: fn(*args))


def log_once_unavailable(exc: Exception) -> None:
    if not getattr(_local, "warned", False):
        logger.warning("Faces: {}", exc)
        _local.warned = True


def model_lines() -> list[str]:
    return [f"{m['file']} ({m['license']})" for m in MODELS.values()]


__all__ = ["MODELS", "MODEL_DIR", "MODEL_VERSION", "FacesUnavailable", "models_status", "read_image", "find_faces",
           "to_blob", "from_blob", "best_matches", "RefIndex", "run", "SAME_PERSON"]
