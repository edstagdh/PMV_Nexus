"""
Video frames (3.2.0): a row of small pictures from across the video, shown
under the player with their time -- clicking one jumps there (like the
"Video Timeline" on PMVHaven's video pages).

Where they come from:
  - the site's own timeline frames (the API's timelineThumbnails, usually 8,
    each with its time), downloaded once;
  - otherwise -- uploaded videos, videos gone from the site, sites without
    them, or a download that failed -- COUNT frames made from the file,
    evenly spread (the middle of each eighth of the video).
  - vertical videos (3.4.0): PORTRAIT_COUNT frames made from the file, even
    when the site has its 8 -- 24 narrow frames fill the row at about the
    height of a horizontal video's 8. When that fails, the usual 8 above.

Files: <cache folder>/.frames/<video id>/{0..n}.webp + info.json. Every frame
is WebP and fits in MAX_SIDE x MAX_SIDE (bigger ones are scaled down, smaller
ones are kept as they are). Everything here is BLOCKING -- call via
asyncio.to_thread.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from .resources import run_background
from .i18n import L
from . import images, media_tools

FOLDER = ".frames"
COUNT = 8            # frames made from the file
PORTRAIT_COUNT = 24  # vertical videos (3.4.0)
PORTRAIT_MIN = 20    # ... fewer made than this counts as failed (the usual 8 then)
MAX_SIDE = 320       # pixels, long side: 320 x 180 landscape, 180 x 320 portrait
PORTRAIT_SIDE = 240  # long side of the 24 vertical ones (shown smaller): 136 x 240
QUALITY = 75         # WebP quality


def read_info(folder: Path) -> Optional[dict]:
    try:
        info = json.loads((folder / "info.json").read_text(encoding="utf-8"))
        return info if info.get("frames") else None
    except (OSError, ValueError):
        return None


def _work_folder(folder: Path) -> Path:
    work = folder.with_name(folder.name + ".part")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    return work


def _finish(work: Path, folder: Path, frames: list[dict], source: str, duration: float) -> dict:
    w, h = _size_of(work / frames[0]["file"])
    meta = {"source": source, "frames": frames, "duration": duration, "width": w, "height": h, "made_at": time.time()}
    (work / "info.json").write_text(json.dumps(meta), encoding="utf-8")
    shutil.rmtree(folder, ignore_errors=True)
    os.replace(work, folder)
    return meta


def _even_times(duration: float, count: int) -> list[float]:
    """The middle of each of `count` equal parts (never the black first frame)."""
    return [round((i + 0.5) * duration / count, 2) for i in range(count)]


def _size_of(path: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(path) as im:
        return im.size


def fit_webp(data: bytes) -> bytes:
    """A frame from the site as WebP within MAX_SIDE. A WebP that already fits is kept as it is."""
    from PIL import Image, UnidentifiedImageError
    import io
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format == "WEBP" and max(im.size) <= MAX_SIDE:
                return data
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(L('Not a usable image ({name})', name=exc.__class__.__name__)) from exc
    return images.to_webp(data, max_side=MAX_SIDE, quality=QUALITY)


def from_site(entries: list[dict], folder: Path, fetch: Callable[[str], bytes], duration: float) -> dict:
    """The site's frames: `entries` [{"url", "seconds"}], `fetch(url)` -> bytes.
    Raises ValueError when not one of them could be used."""
    work = _work_folder(folder)
    try:
        usable = [e for e in entries if e.get("url")]
        times = _even_times(duration, len(usable)) if duration > 0 else [None] * len(usable)
        frames = []
        seen = set()  # 3.4.2: a site without real frames sends one placeholder picture for all of them
        for n, entry in enumerate(usable):
            try:
                raw = fetch(entry["url"])
                seen.add(hashlib.sha1(raw).hexdigest())
                data = fit_webp(raw)
            except Exception as exc:  # noqa: BLE001 -- one bad frame mustn't lose the others
                logger.debug("Video frame {} couldn't be downloaded: {}", n, exc)
                continue
            name = f"{len(frames)}.webp"
            (work / name).write_bytes(data)
            seconds = entry.get("seconds")
            frames.append({"file": name, "seconds": float(seconds) if seconds is not None else times[n]})
        if not frames:
            raise ValueError(L("None of the site's frames could be downloaded"))
        if len(frames) > 1 and len(seen) * 2 <= len(frames):
            raise ValueError(L("The site's frames are placeholders ({count} frames, {total} different picture(s))", count=len(frames), total=len(seen)))
        if any(f["seconds"] is None for f in frames):
            raise ValueError(L("The site's frames have no times"))
        frames.sort(key=lambda f: f["seconds"])
        return _finish(work, folder, frames, "site", duration)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def is_portrait(info: dict) -> bool:
    """`info` from media_tools.probe_streams: taller than wide."""
    return bool(info.get("width") and info.get("height") and info["height"] > info["width"])


def from_video(video_path: str, folder: Path, stop=None, count: int = COUNT, max_side: int = MAX_SIDE,
               at_least: int = 1) -> dict:
    """`count` frames made from the file, evenly spread. `stop()` -> True ends it
    (InterruptedError). Fewer than `at_least` made: ValueError (nothing is replaced)."""
    info = media_tools.probe_streams(video_path)
    duration = float(info.get("duration") or 0)
    if duration <= 0:
        raise ValueError(L("Couldn't read the video's length"))
    work = _work_folder(folder)
    try:
        frames = []
        vf = f"scale={max_side}:{max_side}:force_original_aspect_ratio=decrease:force_divisible_by=2"
        for seconds in _even_times(duration, count):
            if stop and stop():
                raise InterruptedError
            name = f"{len(frames)}.webp"
            cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{seconds:.2f}",
                   "-i", video_path, "-map", "0:V:0", "-frames:v", "1", "-an", "-sn", "-dn", "-vf", vf,
                   "-c:v", "libwebp", "-quality", str(QUALITY), str(work / name)]
            try:
                result = run_background(cmd, capture_output=True, timeout=60)   # 3.12.0: background share, low priority
            except (OSError, subprocess.SubprocessError) as exc:
                logger.debug("Frame at {}s of {}: {}", seconds, video_path, exc)
                continue
            if result.returncode == 0 and (work / name).is_file() and (work / name).stat().st_size:
                frames.append({"file": name, "seconds": seconds})
            else:
                logger.debug("Frame at {}s of {}: {}", seconds, video_path, result.stderr.decode(errors="replace")[-300:])
        if len(frames) < at_least:
            raise ValueError(L('FFmpeg made {count} of {total} frames', count=len(frames), total=count) if frames else L('FFmpeg made no frames'))
        return _finish(work, folder, frames, "video", duration)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise

