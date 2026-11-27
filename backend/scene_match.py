"""Shared scenes (3.9.0): videos that use the same footage, found by comparing their frames.

Frames: the seekbar previews already made for every video (backend/storyboard.py: a frame every
2 seconds, at most 400 per video, 256 px wide) -- nothing is decoded from the video files again.

  1. hash_storyboard(): a 64-bit perceptual hash (pHash: 32 x 32 grey, DCT, the 8 x 8 lowest
     frequencies against their median) per frame. Left out: nearly flat frames (black, white,
     a fade -- little contrast) and the first / last INTRO_SECONDS (logos, title cards).
     About 10 bytes per frame: ~4 KB per video.
  2. find_links(): every frame against every other video's frames through a multi-index (the
     hash cut in 4 bands of 16 bits; frames sharing a band are compared in full, up to
     MAX_DISTANCE differing bits). A frame that turns up in many videos (COMMON_VIDEOS or more:
     an intro, a watermark card, a test pattern) is left out. Two videos are linked when at
     least MIN_FRAMES frames of each match inside scenes: runs of 2+ matches that play in step
     in both videos (same time offset), so lookalike moments scattered around don't link them.

Everything here is CPU work on numpy arrays, without the event loop: call via asyncio.to_thread.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image

INTRO_SECONDS = 4.0
MIN_CONTRAST = 14.0      # grey-level spread (std dev, 0-255) below which a frame is "flat"
MAX_DISTANCE = 8         # differing bits (of 64) still the same picture
BAND_BITS = 16
COMMON_VIDEOS = 12       # a frame matching this many other videos is an intro / card: ignored
MAX_BUCKET = 400         # a band value shared by more frames than this is too common to use
MIN_FRAMES = 3           # matching frames needed in each video
SCENE_GAP = 12.0         # seconds: matches closer than this (in both videos) are one scene
OFFSET_TOLERANCE = 4.0   # seconds: the same footage plays at the same offset (frames are 2+ s apart)
VERSION = 1              # bump when the hash changes: every video is hashed again
_RECORD = np.dtype([("t", "<f4"), ("h", "<u8")])


def _phash(tile: np.ndarray) -> Optional[int]:
    small = cv2.resize(tile, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    if float(small.std()) < MIN_CONTRAST:
        return None
    low = cv2.dct(small)[:8, :8].flatten()
    bits = low > np.median(low[1:])
    return int(np.packbits(bits.astype(np.uint8)).view(">u8")[0])


def hash_storyboard(folder: Path, info: dict) -> bytes:
    """The frames' (seconds, hash) records, packed. Empty when nothing usable."""
    w, h, cols, rows = int(info["width"]), int(info["height"]), int(info["cols"]), int(info["rows"])
    interval, frames, duration = float(info["interval"]), int(info["frames"]), float(info.get("duration") or 0)
    out = []
    for sheet in range(int(info["sheets"])):
        path = folder / f"{sheet}.webp"
        try:
            with Image.open(path) as im:
                grey = np.asarray(im.convert("L"))
        except (OSError, ValueError):
            continue
        for k in range(cols * rows):
            n = sheet * cols * rows + k
            if n >= frames:
                break
            seconds = n * interval
            if seconds < INTRO_SECONDS or (duration and seconds > duration - INTRO_SECONDS):
                continue
            r, c = divmod(k, cols)
            tile = grey[r * h:(r + 1) * h, c * w:(c + 1) * w]
            if tile.shape[0] < 8 or tile.shape[1] < 8:
                continue
            value = _phash(tile)
            if value is not None:
                out.append((seconds, value))
    return np.array(out, dtype=_RECORD).tobytes()


def unpack(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob or b"", dtype=_RECORD)


@dataclass
class Link:
    a: str
    b: str
    frames_a: int          # distinct frames of a that match b
    frames_b: int
    scenes: list           # [[a_from, a_to, b_from, b_to], ...] seconds, longest first


def find_links(videos: dict[str, bytes], stop=None) -> list[Link]:
    """Every pair of videos sharing footage. `videos`: id -> hash_storyboard() blob."""
    names, times, hashes = [], [], []
    for vid, blob in videos.items():
        rec = unpack(blob)
        if len(rec):
            names.append(vid)
            times.append(rec["t"].astype(np.float32))
            hashes.append(rec["h"].astype(np.uint64))
    if len(names) < 2:
        return []
    owner = np.concatenate([np.full(len(t), i, dtype=np.int32) for i, t in enumerate(times)])
    t_all = np.concatenate(times)
    h_all = np.concatenate(hashes)
    pairs = []
    tri = {}
    for band in range(64 // BAND_BITS):
        if stop and stop():
            raise InterruptedError
        key = ((h_all >> np.uint64(band * BAND_BITS)) & np.uint64((1 << BAND_BITS) - 1)).astype(np.int64)
        order = np.argsort(key, kind="stable")
        sk = key[order]
        starts = np.flatnonzero(np.r_[True, sk[1:] != sk[:-1]])
        sizes = np.diff(np.r_[starts, len(sk)])
        for s, n in zip(starts[sizes >= 2], sizes[sizes >= 2]):
            if n > MAX_BUCKET:
                continue
            if n not in tri:
                tri[n] = np.triu_indices(n, 1)
            members = order[s:s + n]
            i, j = members[tri[n][0]], members[tri[n][1]]
            keep = owner[i] != owner[j]
            if keep.any():
                pairs.append(np.stack([i[keep], j[keep]], axis=1))
    if not pairs:
        return []
    p = np.concatenate(pairs)
    p.sort(axis=1)
    p = np.unique(p, axis=0)
    dist = np.bitwise_count(h_all[p[:, 0]] ^ h_all[p[:, 1]])
    p = p[dist <= MAX_DISTANCE]
    if not len(p):
        return []
    # frames matching many different videos: intros, cards -- out
    both = np.concatenate([np.stack([p[:, 0], owner[p[:, 1]]], 1), np.stack([p[:, 1], owner[p[:, 0]]], 1)])
    both = np.unique(both, axis=0)
    frame_ids, spread = np.unique(both[:, 0], return_counts=True)
    common = frame_ids[spread >= COMMON_VIDEOS]
    if len(common):
        p = p[~(np.isin(p[:, 0], common) | np.isin(p[:, 1], common))]
        if not len(p):
            return []
    # per pair of videos
    va, vb = owner[p[:, 0]], owner[p[:, 1]]
    flip = va > vb
    fa = np.where(flip, p[:, 1], p[:, 0])
    fb = np.where(flip, p[:, 0], p[:, 1])
    va, vb = np.minimum(va, vb), np.maximum(va, vb)
    order = np.lexsort((t_all[fa], vb, va))
    va, vb, fa, fb = va[order], vb[order], fa[order], fb[order]
    cuts = np.flatnonzero(np.r_[True, (va[1:] != va[:-1]) | (vb[1:] != vb[:-1]), True])
    links = []
    for s, e in zip(cuts[:-1], cuts[1:]):
        if e - s < MIN_FRAMES:
            continue
        scenes, frames_a, frames_b = _scenes(t_all[fa[s:e]].tolist(), t_all[fb[s:e]].tolist())
        if frames_a >= MIN_FRAMES and frames_b >= MIN_FRAMES:
            links.append(Link(names[int(va[s])], names[int(vb[s])], frames_a, frames_b, scenes))
    return links


def _scenes(ta: list, tb: list) -> tuple[list, int, int]:
    """Scenes: matches with the same time offset between the two videos (the same footage plays
    in step), close together. Lookalike moments scattered around (a dark frame, a static shot
    matching several moments) don't line up like that. Returns the scenes (longest first) and
    the distinct frames of each video in them."""
    pts = sorted(zip(ta, tb), key=lambda p: (p[1] - p[0], p[0]))
    groups, cur = [], [pts[0]]
    for a, b in pts[1:]:
        if (b - a) - (cur[0][1] - cur[0][0]) <= OFFSET_TOLERANCE:  # against the group's first: no drift
            cur.append((a, b))
        else:
            groups.append(cur)
            cur = [(a, b)]
    groups.append(cur)
    out, used_a, used_b = [], set(), set()
    for g in groups:
        g.sort()
        run = [g[0]]
        for a, b in g[1:] + [(float("inf"), float("inf"))]:
            if a - run[-1][0] <= SCENE_GAP:
                run.append((a, b))
                continue
            xs, ys = {x for x, _ in run}, {y for _, y in run}
            if len(xs) >= 2 and len(ys) >= 2:
                out.append([round(min(xs), 1), round(max(xs), 1), round(min(ys), 1), round(max(ys), 1)])
                used_a |= xs
                used_b |= ys
            run = [(a, b)]
    out.sort(key=lambda sc: sc[1] - sc[0], reverse=True)
    return out[:20], len(used_a), len(used_b)
