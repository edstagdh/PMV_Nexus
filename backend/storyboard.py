"""
Seekbar previews (2.21.0): a "storyboard" per video -- small frames every few
seconds, tiled 10 x 10 into WebP sheets, plus the WebVTT index the player reads
(<track kind="metadata" label="thumbnails">, Video.js shows the frame above the
seek bar while you point at it or drag it).

Files: <cache folder>/.storyboards/<video id>/{0,1,...}.webp + info.json.
The VTT is built from info.json on request, so moving the cache folder never
breaks it. Everything here is BLOCKING -- call via asyncio.to_thread.

Speed: when the video has key frames at least about as often as the previews
(PMVs cut often, and encoders put a key frame at each cut), only those are
decoded (-skip_frame nokey) -- 10-50x faster than decoding every frame; each
preview then shows the nearest key frame at or before its time. Videos with few
key frames are decoded fully (slower, but every preview is the right moment).
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from . import media_tools

FOLDER = ".storyboards"
MAX_FRAMES = 400        # per video; longer videos get a frame less often
MIN_INTERVAL = 2        # seconds between frames, at most this often
COLS = ROWS = 10        # frames per sheet: 100
LANDSCAPE_WIDTH = 256   # frame size: 256 x 144 for 16:9
PORTRAIT_HEIGHT = 192   # vertical videos: 108 x 192
QUALITY = 55
THREADS = 2
TIMEOUT = 30 * 60


def interval_for(duration: float) -> int:
    return max(MIN_INTERVAL, math.ceil(duration / MAX_FRAMES))


def frame_size(width: int, height: int) -> tuple[int, int]:
    even = lambda n: max(2, int(round(n / 2)) * 2)  # noqa: E731
    if not width or not height:
        return LANDSCAPE_WIDTH, 144
    if width >= height:
        return LANDSCAPE_WIDTH, even(LANDSCAPE_WIDTH * height / width)
    return even(PORTRAIT_HEIGHT * width / height), PORTRAIT_HEIGHT


def read_info(folder: Path) -> Optional[dict]:
    try:
        info = json.loads((folder / "info.json").read_text(encoding="utf-8"))
        return info if info.get("sheets") else None
    except (OSError, ValueError):
        return None


def make(video_path: str, folder: Path, stop=None) -> dict:
    """Makes the storyboard of `video_path` in `folder` (replacing one there).
    `stop()` returning True ends it early (raises InterruptedError)."""
    info = media_tools.probe_streams(video_path)
    duration = float(info.get("duration") or 0)
    if duration <= 0:
        raise ValueError("Couldn't read the video's length")
    interval = interval_for(duration)
    w, h = frame_size(int(info.get("width") or 0), int(info.get("height") or 0))
    gap = _keyframe_gap(video_path, duration)
    fast = gap is not None and gap <= interval * 2
    try:
        return _run(video_path, folder, duration, interval, w, h, fast, stop)
    except _NoFrames:
        if not fast:
            raise ValueError("FFmpeg made no frames")
        return _run(video_path, folder, duration, interval, w, h, False, stop)


class _NoFrames(Exception):
    pass


def _keyframe_gap(video_path: str, duration: float) -> Optional[float]:
    """Average seconds between key frames (reads the packet list, decodes nothing)."""
    cmd = ["ffprobe", "-v", "error", "-select_streams", "V:0", "-show_entries", "packet=flags", "-of", "csv=p=0", video_path]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                             creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    keys = sum(1 for line in out.splitlines() if line.startswith("K"))
    return duration / keys if keys else None


def _run(video_path: str, folder: Path, duration: float, interval: int, w: int, h: int, fast: bool, stop) -> dict:
    work = folder.with_name(folder.name + ".part")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    vf = (f"fps=1/{interval},scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,tile={COLS}x{ROWS}")
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
           *(["-skip_frame", "nokey"] if fast else []), "-threads", str(THREADS), "-i", video_path,
           "-map", "0:V:0", "-an", "-sn", "-dn", "-vf", vf,
           "-c:v", "libwebp", "-quality", str(QUALITY), "-compression_level", "4",
           "-start_number", "0", str(work / "%d.webp")]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=flags)
    _lower_priority(proc.pid)
    started = time.monotonic()
    try:
        while proc.poll() is None:
            if stop and stop():
                proc.kill()
                raise InterruptedError
            if time.monotonic() - started > TIMEOUT:
                proc.kill()
                raise ValueError("Making the seekbar previews took too long")
            time.sleep(0.25)
        err = proc.stderr.read().decode(errors="replace")[-1500:] if proc.stderr else ""
        sheets = len(list(work.glob("*.webp")))
        if proc.returncode != 0:
            raise ValueError(f"FFmpeg couldn't make the seekbar previews: {err.strip() or proc.returncode}")
        if not sheets:
            raise _NoFrames
        frames = min(math.ceil(duration / interval), sheets * COLS * ROWS)
        meta = {"interval": interval, "width": w, "height": h, "cols": COLS, "rows": ROWS, "sheets": sheets,
                "frames": frames, "duration": duration, "key_frames_only": fast, "made_at": time.time()}
        (work / "info.json").write_text(json.dumps(meta), encoding="utf-8")
        shutil.rmtree(folder, ignore_errors=True)
        os.replace(work, folder)
        return meta
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        shutil.rmtree(work, ignore_errors=True)
        raise
    finally:
        if proc.stderr:
            proc.stderr.close()


def vtt(info: dict, sheet_url: str) -> str:
    """WebVTT for the player: one cue per frame -> "<sheet url>?v=..#xywh=x,y,w,h".
    `sheet_url` has "{n}" where the sheet number goes."""
    def ts(sec: float) -> str:
        ms = int(round(sec * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"
    w, h, step, per = info["width"], info["height"], info["interval"], info["cols"] * info["rows"]
    out = ["WEBVTT", ""]
    for i in range(info["frames"]):
        start, end = i * step, min((i + 1) * step, info["duration"])
        if end <= start:
            break
        sheet, n = divmod(i, per)
        x, y = (n % info["cols"]) * w, (n // info["cols"]) * h
        out += [f"{ts(start)} --> {ts(end)}", f"{sheet_url.format(n=sheet)}#xywh={x},{y},{w},{h}", ""]
    return "\n".join(out)


def _lower_priority(pid: int) -> None:
    try:
        import psutil
        p = psutil.Process(pid)
        p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if sys.platform == "win32" else 10)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not lower the priority of FFmpeg {}: {}", pid, exc)
