"""Duplicate-video detection engine (pure functions -- no app/DB imports).

Visual matching follows VideoDuplicateFinder (github.com/0x90d/videoduplicatefinder),
re-implemented from its documented behavior:

* Frames are sampled at FIXED RELATIVE positions i/(N+1) of the duration and
  shrunk to 32x32 grayscale. Two files are only ever compared position-by-
  position -- there is no sliding alignment, which is what inflated scores in
  the previous implementation.
* "grayscale" mode: mean absolute pixel difference over all valid positions
  must be <= (1 - percent/100) (VDF default percent = 96).
* "phash" mode: a 64-bit DCT hash per frame (32x32 DCT, 8x8 block skipping the
  DC row/column, median threshold). A position passes when its Hamming
  distance <= floor((1 - percent/100) * 64); at least `phash_quorum` of the
  valid positions must pass.
* "combined": a pair matches if either mode matches.
* Candidates are pre-filtered by duration.

Deliberate differences from VDF:
* Uninformative frames (>=80% near-black, >=80% near-white, or almost
  uniform) are marked INVALID and excluded from the comparison for both files,
  instead of being hashed. Flat frames hash identically in every video, which
  is how two unrelated videos used to score 100%. A pair needs at least half
  its positions valid to be judged at all.
* Audio (not part of VDF's visual match) acts as a veto: if the visuals match
  but both files have audible audio that does NOT match, the pair is rejected
  ("same footage, different music"). Audio is decoded to 11025 Hz mono and
  fingerprinted with a Haitsma-Kalker style 32-bit sub-fingerprint (numpy only).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np

# Bump whenever sampling/fingerprint output changes -- invalidates stored samples.
ALGO_VERSION = 2

SIDE = 32
FRAME_BYTES = SIDE * SIDE
BLACK_LIMIT = 0x20
WHITE_LIMIT = 0xF0
DARK_PERCENT = 80.0
MIN_FRAME_STDDEV = 4.0

AUDIO_RATE = 11025
AUDIO_MAX_SECONDS = 300
_AUDIO_FRAME = 4096
_AUDIO_HOP = 1024
_AUDIO_BANDS = 33
_AUDIO_MAX_OFFSET_FRAMES = 25  # ~2.3 s: covers codec priming, not trimmed edits
_SILENT_PEAK = 200  # of 32768

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


# ------------------------------------------------------------------ ffmpeg I/O
def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW
    )


def probe(path: str) -> tuple[float, bool]:
    """(duration_seconds, has_audio_stream). Raises RuntimeError on failure."""
    r = _run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_entries",
         "format=duration:stream=codec_type", path],
        timeout=60,
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {r.stderr.decode(errors='replace')[-300:]}")
    data = json.loads(r.stdout or b"{}")
    try:
        duration = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        raise RuntimeError("ffprobe returned no duration")
    if not duration or duration <= 0:
        raise RuntimeError("non-positive duration")
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    return duration, has_audio


def sample_positions(n: int) -> list[float]:
    """VDF's BuildSamplePositions: evenly spaced, never at the very ends."""
    return [(i + 1) / (n + 1) for i in range(n)]


def extract_frame(path: str, seconds: float) -> Optional[bytes]:
    """One 32x32 grayscale frame (1024 bytes) at `seconds`, or None."""
    r = _run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
         "-ss", f"{seconds:.3f}", "-i", path, "-frames:v", "1", "-an", "-sn",
         "-vf", f"scale={SIDE}:{SIDE}:flags=area,format=gray",
         "-f", "rawvideo", "pipe:1"],
        timeout=60,
    )
    if r.returncode != 0 or len(r.stdout) != FRAME_BYTES:
        return None
    return r.stdout


def frame_is_informative(frame: bytes) -> bool:
    a = np.frombuffer(frame, dtype=np.uint8)
    if (a <= BLACK_LIMIT).mean() * 100 >= DARK_PERCENT:
        return False
    if (a >= WHITE_LIMIT).mean() * 100 >= DARK_PERCENT:
        return False
    return float(a.std()) >= MIN_FRAME_STDDEV


def quick_hash(path: str, size: int) -> str:
    """Cheap whole-file identity: size + head/middle/tail 256 KB. Replaces the
    old full-file SHA-256 read, which was very slow on large libraries."""
    chunk = 256 * 1024
    h = hashlib.sha256(str(size).encode())
    with open(path, "rb") as f:
        for off in (0, max(0, size // 2 - chunk // 2), max(0, size - chunk)):
            f.seek(off)
            h.update(f.read(chunk))
    return h.hexdigest()


# ----------------------------------------------------------------------- audio
def extract_audio_pcm(path: str) -> Optional[np.ndarray]:
    r = _run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", path,
         "-vn", "-sn", "-ac", "1", "-ar", str(AUDIO_RATE), "-t", str(AUDIO_MAX_SECONDS),
         "-f", "s16le", "pipe:1"],
        timeout=300,
    )
    if r.returncode != 0 or len(r.stdout) < 2 * _AUDIO_FRAME * 2:
        return None
    return np.frombuffer(r.stdout[: len(r.stdout) // 2 * 2], dtype=np.int16)


def fingerprint_audio(pcm: np.ndarray) -> np.ndarray:
    """Haitsma-Kalker style: 33 log-spaced bands (300-2000 Hz); per frame, 32
    bits = sign of the time-and-frequency difference of band energies. Returns
    uint32[frames]. Gain changes and lossy re-encodes barely move these bits."""
    x = pcm.astype(np.float32)
    n_frames = 1 + (len(x) - _AUDIO_FRAME) // _AUDIO_HOP
    idx = np.arange(_AUDIO_FRAME)[None, :] + _AUDIO_HOP * np.arange(n_frames)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * np.hanning(_AUDIO_FRAME).astype(np.float32), axis=1)) ** 2
    edges = np.unique(np.round(np.geomspace(300, 2000, _AUDIO_BANDS + 1) * _AUDIO_FRAME / AUDIO_RATE).astype(int))
    energy = np.stack([spec[:, edges[b]:edges[b + 1]].sum(axis=1) for b in range(len(edges) - 1)], axis=1)
    dfreq = energy[:, :-1] - energy[:, 1:]
    bits = (dfreq[1:] - dfreq[:-1]) > 0  # [frames-1, 32]
    weights = (1 << np.arange(32, dtype=np.uint64))
    return (bits.astype(np.uint64) * weights).sum(axis=1).astype(np.uint32)


def _popcount_sum(x: np.ndarray) -> int:
    if hasattr(np, "bitwise_count"):
        return int(np.bitwise_count(x).sum())
    return int(np.unpackbits(x.view(np.uint8)).sum())


def audio_similarity(fa: np.ndarray, fb: np.ndarray) -> float:
    """Best similarity percent (100 = identical bits) over small time offsets.
    Unrelated audio sits near 50."""
    best = 0.0
    min_overlap = max(40, int(min(len(fa), len(fb)) * 0.6))
    for off in range(-_AUDIO_MAX_OFFSET_FRAMES, _AUDIO_MAX_OFFSET_FRAMES + 1):
        a, b = (fa[off:], fb) if off >= 0 else (fa, fb[-off:])
        n = min(len(a), len(b))
        if n < min_overlap:
            continue
        ber = _popcount_sum(a[:n] ^ b[:n]) / (n * 32.0)
        best = max(best, 100.0 * (1.0 - ber))
    return round(best, 2)


# ------------------------------------------------------------- per-file sample
def sample_file(path: str, num_samples: int, use_audio: bool) -> dict:
    """The expensive, blocking per-file step. Returns everything that gets
    persisted: duration, frames (N*1024 bytes), valid (N bytes of 0/1),
    quick_hash, audio_status ('ok'|'none'|'silent'|'error'|'skipped') and
    audio_fp (uint32 little-endian bytes). Raises RuntimeError if too few
    informative frames could be read for the file to be comparable at all."""
    st = os.stat(path)
    duration, has_audio = probe(path)
    frames = bytearray()
    valid = bytearray()
    for pos in sample_positions(num_samples):
        frame = extract_frame(path, pos * duration)
        if frame is None:
            frames += bytes(FRAME_BYTES)
            valid.append(0)
        else:
            frames += frame
            valid.append(1 if frame_is_informative(frame) else 0)
    if sum(valid) < max(3, math.ceil(num_samples * 0.5)):
        raise RuntimeError(
            f"only {sum(valid)}/{num_samples} informative frames (mostly black/flat or undecodable)"
        )

    audio_status, audio_fp = "skipped", b""
    if use_audio:
        if not has_audio:
            audio_status = "none"
        else:
            pcm = extract_audio_pcm(path)
            if pcm is None:
                audio_status = "error"
            elif int(np.abs(pcm).max()) < _SILENT_PEAK:
                audio_status = "silent"
            else:
                audio_status = "ok"
                audio_fp = fingerprint_audio(pcm).astype("<u4").tobytes()

    return {
        "algo_version": ALGO_VERSION, "source_size": st.st_size, "source_mtime": st.st_mtime,
        "duration": duration, "num_samples": num_samples,
        "frames": bytes(frames), "valid": bytes(valid),
        "quick_hash": quick_hash(path, st.st_size),
        "audio_status": audio_status, "audio_fp": audio_fp,
    }


# ------------------------------------------------------------------ comparison
_DCT = None


def _dct_rows() -> np.ndarray:
    """Rows 1..8 of the orthonormal 32-point DCT-II (row 0 = DC is skipped)."""
    global _DCT
    if _DCT is None:
        k = np.arange(1, 9)[:, None]
        i = np.arange(SIDE)[None, :]
        _DCT = (math.sqrt(2.0 / SIDE) * np.cos((2 * i + 1) * k * math.pi / (2 * SIDE))).astype(np.float32)
    return _DCT


def phash_frames(frames: np.ndarray) -> np.ndarray:
    """frames uint8 [..., 1024] -> uint64 [...]."""
    shape = frames.shape[:-1]
    imgs = frames.reshape(-1, SIDE, SIDE).astype(np.float32)
    c = _dct_rows()
    coeff = np.einsum("ki,nij,lj->nkl", c, imgs, c).reshape(-1, 64)
    bits = coeff > np.median(coeff, axis=1, keepdims=True)
    out = (bits.astype(np.uint64) << np.arange(64, dtype=np.uint64)).sum(axis=1, dtype=np.uint64)
    return out.reshape(shape)


def _popcount64(x: np.ndarray) -> np.ndarray:
    if hasattr(np, "bitwise_count"):
        return np.bitwise_count(x).astype(np.int32)
    return np.unpackbits(x.view(np.uint8).reshape(*x.shape, 8), axis=-1).sum(axis=-1).astype(np.int32)


def duration_window(d: float, tolerance_percent: float) -> float:
    """Upper duration bound for candidates of a file with duration d."""
    return max(d + 2.0, d / max(1e-6, 1.0 - tolerance_percent / 100.0))


def durations_compatible(da: float, db: float, tolerance_percent: float) -> bool:
    return abs(da - db) <= max(2.0, tolerance_percent / 100.0 * max(da, db))


def compare_one_to_many(
        frames_a: np.ndarray, valid_a: np.ndarray, phash_a: np.ndarray,
        frames_b: np.ndarray, valid_b: np.ndarray, phash_b: np.ndarray,
        *, algorithm: str, percent: float, phash_quorum: float,
) -> list[Optional[dict]]:
    """Compare one file (frames_a [N,1024]) against M candidates
    (frames_b [M,N,1024]). Returns one entry per candidate: None when there
    are too few commonly valid positions to judge, else a dict with
    matched (bool), similarity_pct, gray_pct, phash_pct, positions (per-position
    gray difference in percent, None where invalid), valid_positions."""
    m, n = frames_b.shape[0], frames_b.shape[1]
    max_diff = 1.0 - percent / 100.0
    max_bits = int(math.floor((1.0 - percent / 100.0) * 64.0))
    min_valid = max(3, math.ceil(n * 0.5))
    results: list[Optional[dict]] = []
    a16 = frames_a.astype(np.int16)

    for start in range(0, m, 256):
        fb = frames_b[start:start + 256]
        vb = valid_b[start:start + 256]
        pb = phash_b[start:start + 256]
        gray = np.abs(fb.astype(np.int16) - a16[None]).mean(axis=2) / 255.0  # [m,N]
        ham = _popcount64(phash_a[None] ^ pb)  # [m,N]
        for j in range(fb.shape[0]):
            v = valid_a & vb[j]
            nv = int(v.sum())
            if nv < min_valid:
                results.append(None)
                continue
            g = gray[j][v]
            gray_avg = float(g.mean())
            gray_ok = gray_avg <= max_diff
            h = ham[j][v]
            ph_ok = int((h <= max_bits).sum()) >= max(1, math.ceil(nv * phash_quorum))
            ph_pct = 100.0 * (1.0 - float(h.mean()) / 64.0)
            gray_pct = 100.0 * (1.0 - gray_avg)
            if algorithm == "phash":
                ok, sim = ph_ok, ph_pct
            elif algorithm == "combined":
                ok = gray_ok or ph_ok
                sim = max([s for s, flag in ((gray_pct, gray_ok), (ph_pct, ph_ok)) if flag] or [max(gray_pct, ph_pct)])
            else:
                ok, sim = gray_ok, gray_pct
            positions = [round(float(gray[j][k] * 100), 2) if v[k] else None for k in range(n)]
            results.append({
                "matched": bool(ok), "similarity_pct": round(sim, 2),
                "gray_pct": round(gray_pct, 2), "phash_pct": round(ph_pct, 2),
                "positions": positions, "valid_positions": nv,
            })
    return results
