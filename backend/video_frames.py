"""
Video frames (3.0.1): a row of small pictures from across the video, shown
under the player with their time -- clicking one jumps there (like the
"Video Timeline" on PMVHaven's video pages).

Where they come from:
  - the site's own timeline frames (the API's timelineThumbnails, usually 8,
    each with its time), downloaded once;
  - otherwise -- uploaded videos, videos gone from the site, sites without
    them, or a download that failed -- COUNT frames made from the file,
    evenly spread (the middle of each eighth of the video).

Files: <cache folder>/.frames/<video id>/{0..n}.webp + info.json. Every frame
is WebP and fits in MAX_SIDE x MAX_SIDE (bigger ones are scaled down, smaller
ones are kept as they are). Everything here is BLOCKING -- call via
asyncio.to_thread.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

from . import images, media_tools

FOLDER = ".frames"
COUNT = 8            # frames made from the file
MAX_SIDE = 320       # pixels, long side: 320 x 180 landscape, 180 x 320 portrait
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
        raise ValueError(f"Not a usable image ({exc.__class__.__name__})") from exc
    return images.to_webp(data, max_side=MAX_SIDE, quality=QUALITY)


def from_site(entries: list[dict], folder: Path, fetch: Callable[[str], bytes], duration: float) -> dict:
    """The site's frames: `entries` [{"url", "seconds"}], `fetch(url)` -> bytes.
    Raises ValueError when not one of them could be used."""
    work = _work_folder(folder)
    try:
        usable = [e for e in entries if e.get("url")]
        times = _even_times(duration, len(usable)) if duration > 0 else [None] * len(usable)
        frames = []
        for n, entry in enumerate(usable):
            try:
                data = fit_webp(fetch(entry["url"]))
            except Exception as exc:  # noqa: BLE001 -- one bad frame mustn't lose the others
                logger.debug("Video frame {} couldn't be downloaded: {}", n, exc)
                continue
            name = f"{len(frames)}.webp"
            (work / name).write_bytes(data)
            seconds = entry.get("seconds")
            frames.append({"file": name, "seconds": float(seconds) if seconds is not None else times[n]})
        if not frames:
            raise ValueError("None of the site's frames could be downloaded")
        if any(f["seconds"] is None for f in frames):
            raise ValueError("The site's frames have no times")
        frames.sort(key=lambda f: f["seconds"])
        return _finish(work, folder, frames, "site", duration)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def from_video(video_path: str, folder: Path, stop=None) -> dict:
    """COUNT frames made from the file, evenly spread. `stop()` -> True ends it (InterruptedError)."""
    info = media_tools.probe_streams(video_path)
    duration = float(info.get("duration") or 0)
    if duration <= 0:
        raise ValueError("Couldn't read the video's length")
    work = _work_folder(folder)
    try:
        frames = []
        vf = f"scale={MAX_SIDE}:{MAX_SIDE}:force_original_aspect_ratio=decrease:force_divisible_by=2"
        for seconds in _even_times(duration, COUNT):
            if stop and stop():
                raise InterruptedError
            name = f"{len(frames)}.webp"
            cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{seconds:.2f}",
                   "-i", video_path, "-map", "0:V:0", "-frames:v", "1", "-an", "-sn", "-dn", "-vf", vf,
                   "-c:v", "libwebp", "-quality", str(QUALITY), str(work / name)]
            try:
                result = subprocess.run(cmd, capture_output=True, timeout=60)
            except (OSError, subprocess.SubprocessError) as exc:
                logger.debug("Frame at {}s of {}: {}", seconds, video_path, exc)
                continue
            if result.returncode == 0 and (work / name).is_file() and (work / name).stat().st_size:
                frames.append({"file": name, "seconds": seconds})
            else:
                logger.debug("Frame at {}s of {}: {}", seconds, video_path, result.stderr.decode(errors="replace")[-300:])
        if not frames:
            raise ValueError("FFmpeg made no frames")
        return _finish(work, folder, frames, "video", duration)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise

