"""
Technical media inspection + HEVC re-encoding, adapted from a standalone
script to run as an async background task: one function extracts real
codec/fps/bitrate info from a downloaded file, the other re-encodes it to
HEVC with the same ffmpeg parameters as the original script, reporting
progress via a callback instead of a tqdm bar.
"""
from __future__ import annotations

import json
import os

# MUST be set before `import cv2` below -- OpenCV's ffmpeg-backed video
# I/O (used throughout this module: cv2.VideoCapture in
# get_video_duration()/sample_frame_hashes()/etc) links against a real
# ffmpeg build that writes its own decoder warnings/errors DIRECTLY to
# the process's raw console, completely bypassing this app's loguru
# setup -- no timestamp tagging, no per-file attribution, no severity
# filtering on our end. This is what caused a wall of unattributable
# "[libaom-av1 ...] Failed to decode frame" spam during a duplicate scan:
# a file encoded with a newer AV1 profile level than this OpenCV build's
# bundled AV1 decoder recognizes. That's a per-FRAME decode gap, not
# file corruption or a hashing failure -- sample_frame_hashes() already
# just skips whatever individual frames fail and keeps going with the
# rest, so this is expected, non-fatal noise, not something worth
# surfacing per line. "-8" is ffmpeg's own AV_LOG_QUIET level -- silences
# it at the source rather than trying to filter/attribute lines we have
# no way to tag after the fact.
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")

import contextlib
import random
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional
import hashlib
import cv2
import numpy as np
from pymediainfo import MediaInfo
from loguru import logger


@contextlib.contextmanager
def _suppress_native_av_output():
    """Temporarily redirects the process's raw stderr (OS file
    descriptor 2) to os.devnull for the duration of the wrapped block.

    Why this exists: OPENCV_FFMPEG_LOGLEVEL (set above) does NOT
    reliably silence this in practice -- confirmed against a real file
    that kept printing "[libaom-av1 ...] Failed to decode frame" spam to
    the console even with that env var set. OpenCV's bundled ffmpeg/
    libaom decoder (used by cv2.VideoCapture -- get_video_duration() and
    sample_frame_hashes() below) writes decode warnings/errors DIRECTLY
    via C's fprintf(stderr, ...), completely bypassing Python's own
    stdio layer -- no Python-level suppression (redirecting sys.stderr,
    contextlib.redirect_stderr, logging filters, this env var) can touch
    output written that way. Only an OS-level dup2() redirect works,
    since it operates below Python entirely.

    NOTE: this is about OpenCV's OWN BUNDLED ffmpeg libraries (compiled
    into the opencv-python wheel), entirely separate from whatever
    ffmpeg.exe is on the system PATH -- every OTHER function in this
    module that shells out to the real ffmpeg CLI (get_technical_info,
    reencode_video, sample_frame_hashes_ffmpeg_fallback, etc.) already
    captures its subprocess output into memory via subprocess.run(...,
    capture_output=True) and never prints to the console at all -- this
    suppression is needed ONLY for the cv2.VideoCapture-based functions.
    Upgrading the system ffmpeg install would not change any of this.

    TRADEOFF: file descriptor 2 is PROCESS-WIDE, not per-thread -- while
    active, anything else writing to the console (including this app's
    own loguru console sink) is also silently dropped. loguru's FILE
    sink (backend.log) is unaffected, since it writes through its own
    file handle rather than stderr -- nothing is ever lost from the
    actual log file, only briefly from the live console view."""
    stderr_fd = sys.stderr.fileno()
    saved_fd = os.dup(stderr_fd)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull_fd, stderr_fd)
        yield
    finally:
        os.dup2(saved_fd, stderr_fd)
        os.close(devnull_fd)
        os.close(saved_fd)


def get_video_duration(filepath: str) -> tuple[int, float]:
    with _suppress_native_av_output():
        cap = cv2.VideoCapture(filepath)
        if not cap.isOpened():
            return 0, 0.0
        fps = cap.get(cv2.CAP_PROP_FPS)
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        cap.release()
    if fps <= 0:
        return 0, 0.0
    return int(frames // fps), fps


def get_audio_codec(file_path: str) -> Optional[str]:
    try:
        mi = MediaInfo.parse(file_path)
    except Exception:
        return None
    audio = next((t for t in mi.tracks if t.track_type == "Audio"), None)
    if not audio:
        return None
    return (audio.format or "").lower() or None


def is_video_hevc_or_av1(file_path: str) -> bool:
    try:
        mi = MediaInfo.parse(file_path)
    except Exception:
        return False
    video = next((t for t in mi.tracks if t.track_type == "Video"), None)
    if not video:
        return False
    codec = (video.format or "").lower().replace(" ", "").replace("-", "")
    return codec in {"hevc", "hvc1", "hev1", "av1"}


def generate_video_phash(file_path: str, num_frames: int = 9, hash_size: int = 8) -> Optional[str]:
    """Locally-computed perceptual hash, for re-encoded files whose only
    "phash" on record is the one PMVHaven put in the site metadata JSON
    for the ORIGINAL (pre-re-encode) upload -- that value describes
    content that no longer matches what's on disk once a video's been
    re-encoded, since a re-encode changes the file's pixels/codec even
    though the visual content is meant to stay the same.

    Deliberately NOT PMVHaven's own algorithm (unknown/proprietary), so
    this will never match their stored phash value byte-for-byte, even
    for the *original* file -- it's a separate, self-consistent hash
    meant for comparing this app's own files against each other (e.g.
    "is this re-encode still visually the same video as before"), not
    for matching against the site's value.

    Samples `num_frames` frames evenly across the video (skipping the
    very start/end, where intro/outro black frames or logos could throw
    the comparison off), and for each one computes a simple average-hash
    (aHash): downscale to hash_size x hash_size grayscale, then a bit per
    pixel for "brighter than the frame's own average". Concatenating
    several frames' hashes instead of using just one makes the result far
    more resistant to a single unlucky frame (a flash, a scene cut
    landing exactly on the sample point) skewing the whole hash.

    Returns None (never raises) if the file can't be opened or no frames
    could be read -- callers should treat that as "couldn't hash this
    one" and move on, not as a hard failure."""
    try:
        cap = cv2.VideoCapture(file_path)
        if not cap.isOpened():
            return None
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            cap.release()
            return None

        # even spacing across the middle of the video, e.g. for
        # num_frames=9 this lands at 10%, 20%, ..., 90% -- never the
        # literal first/last frame.
        positions = [int(total_frames * (i + 1) / (num_frames + 1)) for i in range(num_frames)]

        frame_hex_chunks: list[str] = []
        for pos in positions:
            cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
            ok, frame = cap.read()
            if not ok:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(gray, (hash_size, hash_size), interpolation=cv2.INTER_AREA)
            bits = (small > small.mean()).flatten()
            hash_int = 0
            for bit in bits:
                hash_int = (hash_int << 1) | int(bit)
            # hash_size**2 bits -> hash_size**2/4 hex digits (each hex
            # digit is 4 bits) -- for the default hash_size=8 that's 16
            # hex chars per sampled frame.
            frame_hex_chunks.append(f"{hash_int:0{hash_size * hash_size // 4}x}")
        cap.release()

        if not frame_hex_chunks:
            logger.warning("Could not read any sample frames to hash for {}", file_path)
            return None
        return "".join(frame_hex_chunks)
    except Exception:
        logger.warning("Could not generate a perceptual hash for {}", file_path)
        return None


_FFMPEG_VERSION: Optional[tuple[int, int]] = None


def ffmpeg_version() -> tuple[int, int]:
    """(major, minor) of the installed ffmpeg, checked once. Git builds and
    unreadable output count as (99, 0), i.e. "current"."""
    global _FFMPEG_VERSION
    if _FFMPEG_VERSION is None:
        major, minor = 99, 0
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-version"], capture_output=True, text=True, timeout=15).stdout
            m = re.search(r"ffmpeg version n?(\d+)\.(\d+)", out)
            if m:
                major, minor = int(m.group(1)), int(m.group(2))
        except (OSError, subprocess.SubprocessError):
            pass
        _FFMPEG_VERSION = (major, minor)
    return _FFMPEG_VERSION


def _vfr_args() -> list[str]:
    """ffmpeg arguments for variable-frame-rate output (one output frame per
    selected input frame). FFmpeg 5.1 added -fps_mode and FFmpeg 7+ removed
    the old -vsync."""
    return ["-fps_mode", "vfr"] if ffmpeg_version() >= (5, 1) else ["-vsync", "vfr"]


def generate_stash_phash(file_path: str) -> Optional[str]:
    """Computes a 64-bit perceptual hash of the video using the same
    method Stash (github.com/stashapp/stash) uses for duplicate
    detection: sample a 5x5 grid of frames evenly across the video's
    duration, tile them into one "sprite" image via ffmpeg, then run a
    standard DCT-based image hash (the same algorithm as the
    goimagehash library's PerceptionHash, which Stash uses) on that
    sprite. Returns a 16-character lowercase hex string (64 bits), or
    None if anything fails.

    Unlike generate_video_phash() (a simple per-frame average-hash),
    this is comparable across different encodes/resolutions of the same
    source content -- that's the whole point of using it for dedup.
    Note: this is a faithful port of the *method*, not a guarantee of
    bit-for-bit identical output to an actual Stash installation (Go's
    DCT implementation vs. OpenCV's may round a handful of bits
    differently) -- comparisons between hashes computed by THIS
    function are fully consistent with each other, which is what
    duplicate detection within this app actually needs.

    BLOCKING (ffmpeg subprocess + image decode) -- call via
    asyncio.to_thread, not directly from async code."""
    total_duration, _fps = get_video_duration(file_path)
    if total_duration <= 0:
        logger.warning("Could not read duration for {} while generating a Stash-style phash", file_path)
        return None

    columns, rows = 5, 5
    num_frames = columns * rows
    interval = total_duration / num_frames
    scale_width = 160

    # select=... picks one frame every `interval` seconds; scale to a
    # fixed width (-2 auto-rounds height to the nearest even number);
    # tile=5x5 buffers 25 selected frames and emits ONE combined image.
    # -frames:v 1 takes just that one tiled output frame.
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", file_path,
        "-vf", (
            f"select=isnan(prev_selected_t)+gte(t-prev_selected_t\\,{interval:.6f}),"
            f"scale={scale_width}:-2,tile={columns}x{rows}"
        ),
        *_vfr_args(),
        "-frames:v", "1",
        "-an",
        "-f", "image2pipe",
        "-vcodec", "png",
        "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=300)
    except subprocess.TimeoutExpired:
        logger.error("Stash-style phash sprite generation timed out for {}", file_path)
        return None

    if result.returncode != 0 or not result.stdout:
        logger.error(
            "Stash-style phash sprite generation failed for {}: {}",
            file_path, result.stderr.decode(errors="replace")[-1000:],
        )
        return None

    sprite = cv2.imdecode(np.frombuffer(result.stdout, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if sprite is None:
        logger.error("Could not decode the generated sprite image for {}", file_path)
        return None

    resized = cv2.resize(sprite, (64, 64), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    dct = cv2.dct(resized)
    low_freq = dct[:8, :8].flatten()  # top-left 8x8 = the 64 lowest-frequency coefficients
    median = float(np.median(low_freq))

    bits = 0
    for i, value in enumerate(low_freq):
        if value > median:
            bits |= 1 << (63 - i)
    return f"{bits:016x}"


def phash_hamming_distance(hash_a: Optional[str], hash_b: Optional[str]) -> Optional[int]:
    """Bit-difference count between two hex-string phashes produced by
    generate_stash_phash() -- 0 means identical, higher means less
    similar (max 64). Returns None if either hash is missing or
    malformed, so callers can treat that as "not comparable" instead of
    crashing. A difference of roughly 10 bits or fewer commonly still
    indicates the same underlying video content (e.g. across a
    re-encode or resolution change)."""
    if not hash_a or not hash_b:
        return None
    try:
        a = int(hash_a, 16)
        b = int(hash_b, 16)
    except ValueError:
        return None
    return bin(a ^ b).count("1")


def format_size(kib: float) -> str:
    return f"{kib / 1024:.0f} MB" if kib >= 1024 else f"{int(kib)} KB"


def format_bitrate(kib: float, sec: float) -> str:
    if sec <= 0:
        return "N/A"
    return f"{((kib * 8) / sec) / 1000:.2f} Mbps"


def get_technical_info(file_path: str) -> dict:
    """Reads real technical details from the file on disk -- used to enrich
    or override whatever PMVHaven's page happened to show, which can be
    stale, rounded, or simply missing for some fields."""
    info: dict = {}
    try:
        mi = MediaInfo.parse(file_path)
    except Exception:
        logger.warning("Could not read media info for {}", file_path)
        return info

    video = next((t for t in mi.tracks if t.track_type == "Video"), None)
    audio = next((t for t in mi.tracks if t.track_type == "Audio"), None)
    general = next((t for t in mi.tracks if t.track_type == "General"), None)

    if video:
        codec = (video.format or "").upper()
        profile = getattr(video, "format_profile", None)
        info["Video Codec"] = f"{codec} ({profile})" if profile else (codec or "Unknown")
        if video.width and video.height:
            info["Resolution"] = f"{video.width}x{video.height}"
        try:
            info["FPS"] = f"{float(video.frame_rate):.2f}"
        except (TypeError, ValueError):
            pass
        try:
            info["Video Bitrate"] = f"{int(video.bit_rate) / 1000:.0f} kbps"
        except (TypeError, ValueError):
            pass

    if audio:
        info["Audio Codec"] = (audio.format or "Unknown").upper()
        try:
            info["Audio Bitrate"] = f"{int(audio.bit_rate) / 1000:.0f} kbps"
        except (TypeError, ValueError):
            pass
        if audio.channel_s:
            info["Audio Channels"] = str(audio.channel_s)

    try:
        size_bytes = os.path.getsize(file_path)
        info["File Size"] = format_size(size_bytes / 1024)
    except OSError:
        pass

    if general is not None:
        try:
            info["Overall Bitrate"] = f"{int(general.overall_bit_rate) / 1000:.0f} kbps"
        except (TypeError, ValueError, AttributeError):
            pass
        try:
            total_seconds = int(float(general.duration) // 1000)  # general.duration is in ms
            minutes, seconds = divmod(total_seconds, 60)
            info["Duration"] = f"{minutes}:{seconds:02d}"
        except (TypeError, ValueError, AttributeError):
            pass

    return info


_TIME_RE = re.compile(r"time=(\d+:\d+:\d+\.\d+)")
_SIZE_RE = re.compile(r"size=\s*(\d+)\s*(?:KiB|kB)")  # FFmpeg 7+: KiB, older: kB
_SPEED_RE = re.compile(r"speed=([\d.]+)x")


def _parse_ffmpeg_time(t: str) -> float:
    try:
        h, m, s = t.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        return 0.0


_ENCODERS: Optional[set[str]] = None
REENCODE_ENCODERS = {"hevc": "libx265", "av1": "libsvtav1"}
# Converting an upload may also go to AVC (x264): the most compatible choice.
UPLOAD_ENCODERS = {"h264": "libx264", **REENCODE_ENCODERS}


def available_encoders() -> set[str]:
    """Video encoders the installed ffmpeg has (checked once), e.g. {"libx265", "libsvtav1", ...}."""
    global _ENCODERS
    if _ENCODERS is None:
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=15).stdout
            _ENCODERS = {parts[1] for line in out.splitlines()
                         if (parts := line.split()) and len(parts) > 1 and parts[0].startswith("V")}
        except (OSError, subprocess.SubprocessError):
            _ENCODERS = set()
    return _ENCODERS


def video_codec(file_path: str) -> Optional[str]:
    """"hevc", "av1", "h264", ... for the file's first video track (None if unreadable)."""
    try:
        mi = MediaInfo.parse(file_path)
    except Exception:
        return None
    video = next((t for t in mi.tracks if t.track_type == "Video"), None)
    if not video:
        return None
    codec = (video.format or "").lower().replace(" ", "").replace("-", "")
    if codec in {"hevc", "hvc1", "hev1"}:
        return "hevc"
    if codec == "avc":
        return "h264"
    return codec or None


def limit_process_cores(pid: int, cores: Optional[int]) -> None:
    """Restricts a running process to the first `cores` CPU cores (any
    encoder, any codec). Best effort: Linux (sched_setaffinity), Windows
    (SetProcessAffinityMask, up to 64 cores) or psutil when installed."""
    if not cores:
        return
    cpus = list(range(cores))
    try:
        if hasattr(os, "sched_setaffinity"):
            os.sched_setaffinity(pid, cpus)
            return
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0200 | 0x0400, False, pid)  # SET_INFORMATION | QUERY_INFORMATION
            if handle:
                try:
                    kernel32.SetProcessAffinityMask(handle, ctypes.c_size_t((1 << min(cores, 64)) - 1))
                finally:
                    kernel32.CloseHandle(handle)
            return
        import psutil  # type: ignore
        psutil.Process(pid).cpu_affinity(cpus)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not limit ffmpeg to {} cores: {}", cores, exc)


def reencode_video(
        file_path: str,
        codec: str,
        crf: int,
        preset: str,
        downscale: bool,
        cpu_cores: Optional[int],
        remove_chapters: bool,
        copy_audio: bool,
        progress_cb: Optional[Callable[[dict], None]] = None,
        proc_holder: Optional[dict] = None,
        tolerant: bool = False,
        subtitles: bool = True,
) -> Optional[str]:
    """Re-encodes file_path to `codec` ("hevc" with x265, "av1" with
    SVT-AV1, "h264" with x264 -- the last only for uploads) at `crf`/`preset`. cpu_cores limits the encode to that many
    cores (None = all). BLOCKING -- call via asyncio.to_thread from async
    code. Returns the temp output path on success, or None on failure.

    tolerant=True switches this into corruption-recovery mode, used by
    reencode_worker.py only after an item has already failed a normal
    re-encode 3+ times in a row (see that file's _reencode_item()) --
    NOT the default path for an ordinary re-encode. In this mode:
      - Duration/fps probing falls back through progressively more
        tolerant methods (ffprobe, then a full tolerant decode pass) if
        the normal cv2-based probe fails outright -- see the real-world
        case this was built for: a file with an internally inconsistent
        MP4 sample-to-chunk/chunk-offset index (ffprobe error
        'contradictionary STSC and STCO'), which every index-trusting
        tool (OpenCV, ffprobe's own header parser) refuses to open, even
        though the file may still play back tolerably in players that
        don't validate the index strictly.
      - `-err_detect ignore_err -fflags +genpts+igndts` are passed to
        ffmpeg's demuxer for this input, telling it to push through
        header/index inconsistencies and regenerate timestamps while
        decoding, rather than hard-failing the way a normal encode does.
      - Audio codec detection (get_audio_codec(), which also depends on
        MediaInfo successfully parsing the same damaged header) is not
        trusted -- audio is always transcoded to AAC rather than
        conditionally copied, since a wrong "no audio track" read here
        would otherwise silently drop audio that's actually present.
    Recovery is NOT guaranteed to succeed -- if the corruption extends
    into actual frame data (not just the container's index tables),
    ffmpeg may still fail outright, or produce a file with visible
    glitches. This is a best-effort data-recovery attempt, not a
    guaranteed fix."""
    if not tolerant:
        total_duration, fps = get_video_duration(file_path)
        if total_duration <= 0 or fps <= 0:
            logger.error("Could not read duration/fps for {}", file_path)
            return None
    else:
        total_duration, fps = get_video_duration(file_path)
        if total_duration <= 0:
            try:
                total_duration = get_video_duration_ffprobe(file_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Recovery mode: ffprobe duration also failed for {}: {}", file_path, exc)
                total_duration = 0
        if fps <= 0:
            try:
                fps = get_fps_ffprobe(file_path)
            except Exception:
                fps = 0
        if total_duration <= 0:
            # Last resort: a full tolerant decode pass, counting elapsed
            # time via ffmpeg's own progress output -- slow (decodes the
            # entire file sequentially), only reached when the file's
            # header/index is damaged badly enough that even ffprobe
            # can't read a duration from it at all.
            logger.warning("Recovery mode: falling back to a full tolerant decode pass to determine duration for {}", file_path)
            total_duration = probe_duration_tolerant(file_path) or 0
        if fps <= 0:
            fps = 24.0
            logger.warning("Recovery mode: could not determine fps for {} -- assuming {}fps for GOP sizing only", file_path, fps)
        if total_duration <= 0:
            logger.error("Recovery mode: still could not determine any duration for {} -- giving up", file_path)
            return None

    width, height, is_vertical = None, None, False
    try:
        mi = MediaInfo.parse(file_path)
        v = next(t for t in mi.tracks if t.track_type == "Video")
        width, height = v.width, v.height
        is_vertical = height > width
    except Exception:
        pass

    directory, filename = os.path.split(file_path)
    name, _ = os.path.splitext(filename)
    temp_output = os.path.join(directory, f"{name}_temp.mp4")
    keyint = int(fps * 2)

    if codec == "av1":
        video_args = ["-c:v", "libsvtav1", "-preset", str(preset), "-crf", str(crf), "-g", str(keyint),
                      "-pix_fmt", "yuv420p10le"]
    elif codec == "h264":
        # 8-bit 4:2:0: browsers can't decode 10-bit or 4:4:4 H.264.
        video_args = ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-g", str(keyint),
                      "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        if cpu_cores:
            video_args += ["-threads", str(cpu_cores)]
    else:
        x265_params = f"crf={crf}:ref=3:limit-refs=2:keyint={keyint}"
        if cpu_cores:
            x265_params += f":pools={cpu_cores}"
        video_args = ["-c:v", "libx265", "-vtag", "hvc1", "-preset", preset, "-x265-params", x265_params]

    cmd = ["ffmpeg", "-hide_banner", "-y"]
    if tolerant:
        # Input-side demuxer flags -- MUST come before -i to apply to
        # THIS input specifically. Tells ffmpeg to push through
        # header/index inconsistencies (see this function's tolerant=
        # docstring) instead of hard-failing the way a normal decode does.
        cmd += ["-err_detect", "ignore_err", "-fflags", "+genpts+igndts"]
    cmd += [
        "-i", file_path,
        "-map", "0:V:0",  # the video itself, never embedded cover art
        "-map", "0:a?",
        *(["-map", "0:s?"] if subtitles else []),
        *video_args,
        "-map_metadata", "-1",
        "-map_chapters", "-1" if remove_chapters else "0",
        "-dn",
        *(["-c:s", "mov_text"] if subtitles else ["-sn"]),
    ]

    if tolerant:
        # Audio-codec detection depends on MediaInfo successfully parsing
        # the same header that's already known to be damaged for this
        # file -- a wrong "no audio track" read (get_audio_codec()
        # returning None) would silently drop real audio. Always
        # transcode instead of conditionally copying; -map 0:a? above
        # already makes the audio map optional, so this is a no-op if
        # the file genuinely has no audio stream.
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ac", "2"]
    else:
        audio_codec = get_audio_codec(file_path)
        if copy_audio:
            if audio_codec == "aac":
                cmd += ["-c:a", "copy"]
            elif audio_codec is None:
                cmd += ["-an"]
            else:
                cmd += ["-c:a", "aac", "-b:a", "128k", "-ac", "2"]
        else:
            cmd += ["-c:a", "aac", "-b:a", "128k", "-ac", "2"]

    if downscale and width and height:
        if not is_vertical and (width > 1920 or height > 1080):
            cmd += ["-vf", "scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2"]
        elif is_vertical and height > 1080:
            cmd += ["-vf", "scale=-2:1080"]  # already safe, -2 auto-rounds to even

    cmd.append(temp_output)
    logger.debug("FFmpeg command: {}", " ".join(cmd))

    proc = subprocess.Popen(
        cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, errors="replace"
    )

    if proc_holder is not None:
        proc_holder["proc"] = proc
    limit_process_cores(proc.pid, cpu_cores)

    start = time.time()
    stderr_lines: list[str] = []
    last_pct = -1

    for line in proc.stderr:
        stderr_lines.append(line)
        t_match = _TIME_RE.search(line)
        s_match = _SIZE_RE.search(line)
        sp_match = _SPEED_RE.search(line)
        if t_match is None or s_match is None:
            continue

        cur = min(total_duration, _parse_ffmpeg_time(t_match.group(1)))
        current_kib = int(s_match.group(1))
        speed = float(sp_match.group(1)) if sp_match else 1.0
        remaining = max(0.0, total_duration / speed - (time.time() - start)) if speed > 0 else 0.0
        pct = round((cur / total_duration) * 100, 1) if total_duration else 0

        if progress_cb and pct != last_pct:
            last_pct = pct
            projected_kib = (current_kib / cur) * total_duration if cur > 0 else 0
            progress_cb({
                "percent": pct,
                "eta_seconds": int(remaining),
                "speed": f"{speed:.2f}x",
                "estimated_size": format_size(projected_kib) if cur > 0 else "estimating…",
                "bitrate": format_bitrate(current_kib, cur),
            })

    proc.wait()

    if proc.returncode != 0:
        logger.error("FFmpeg re-encode failed for {}", file_path)
        logger.error("".join(stderr_lines[-50:]))
        if os.path.exists(temp_output):
            try:
                os.remove(temp_output)
            except OSError:
                pass
        return None

    if not os.path.exists(temp_output):
        logger.error("FFmpeg finished but output file missing for {}", file_path)
        return None

    return temp_output


def download_hls_to_mp4(
        hls_url: str,
        output_path: str,
        duration_seconds: Optional[int] = None,
        progress_cb: Optional[Callable[[dict], None]] = None,
        proc_holder: Optional[dict] = None,
        input_args: Optional[list[str]] = None,
) -> bool:
    """Downloads and remuxes an HLS stream (.m3u8 -- a text playlist that
    points at many small video segments, not a single downloadable file)
    into one .mp4 via ffmpeg. Used when a site serves a video only as HLS
    with no direct progressive-download URL (PMVHaven does this for at
    least some videos). `-c copy` means this is a container remux, not a
    re-encode -- ffmpeg fetches the segments and stitches them together
    losslessly, so it's as fast as the network allows rather than
    CPU-bound. BLOCKING -- call via asyncio.to_thread from async code.
    Returns True on success, False on failure (details in the server log)."""
    tmp_output = f"{output_path}.part.mp4"
    cmd = [
        "ffmpeg", "-y", "-hide_banner",
        *(input_args or []),  # e.g. -http_proxy (Server Settings -> Proxy)
        "-i", hls_url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",  # HLS audio (ADTS AAC) needs this to remux cleanly into an mp4 container
        tmp_output,
    ]
    logger.debug("FFmpeg HLS download command: {}", " ".join("<proxy>" if i and cmd[i - 1] == "-http_proxy" else a
                                                             for i, a in enumerate(cmd)))

    proc = subprocess.Popen(
        cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, errors="replace"
    )
    if proc_holder is not None:
        proc_holder["proc"] = proc

    stderr_lines: list[str] = []
    last_pct = -1

    for line in proc.stderr:
        stderr_lines.append(line)
        t_match = _TIME_RE.search(line)
        if t_match is None:
            continue

        cur = _parse_ffmpeg_time(t_match.group(1))
        pct = round(min(100.0, (cur / duration_seconds) * 100), 1) if duration_seconds else None

        if progress_cb and pct != last_pct:
            last_pct = pct
            progress_cb({"percent": pct, "seconds_done": round(cur)})

    proc.wait()

    if proc.returncode != 0:
        logger.error("FFmpeg HLS download failed for {}", hls_url)
        logger.error("".join(stderr_lines[-50:]))
        if os.path.exists(tmp_output):
            try:
                os.remove(tmp_output)
            except OSError:
                pass
        return False

    if not os.path.exists(tmp_output):
        logger.error("FFmpeg finished but HLS output file missing for {}", hls_url)
        return False

    os.replace(tmp_output, output_path)
    return True


def generate_thumbnail(file_path: str, output_path: str, seek_seconds: Optional[float] = None) -> bool:
    """Grabs a single frame from the video via ffmpeg as a local fallback
    thumbnail, used when PMVHaven's own thumbnail is missing or itself
    broken. Defaults to a frame 10% into the video (skips a likely-black
    opening frame at 0:00) unless a specific timestamp is given.
    BLOCKING -- call via asyncio.to_thread."""
    if seek_seconds is None:
        total_duration, _fps = get_video_duration(file_path)
        seek_seconds = max(1.0, total_duration * 0.1) if total_duration > 0 else 1.0

    tmp_output = f"{output_path}.part.jpg"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", str(seek_seconds),
        "-i", file_path,
        "-frames:v", "1",
        "-q:v", "3",
        tmp_output,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        logger.error("Thumbnail generation timed out for {}", file_path)
        if os.path.exists(tmp_output):
            os.remove(tmp_output)
        return False

    if result.returncode != 0 or not os.path.exists(tmp_output):
        logger.error("Thumbnail generation failed for {}: {}", file_path, result.stderr.decode(errors="replace")[-1000:])
        if os.path.exists(tmp_output):
            os.remove(tmp_output)
        return False

    os.replace(tmp_output, output_path)
    return True


def default_preview_timestamps(file_path: str, num_clips: int = 5, clip_duration: float = 2.0) -> dict:
    """The start times generate_preview_clip() would pick, for the Upload
    page to show (and let you move) before the preview is made."""
    total, _fps = get_video_duration(file_path)
    if total <= 0:
        try:
            total = get_video_duration_ffprobe(file_path) or 0
        except Exception:  # noqa: BLE001
            total = 0
    if total <= 0:
        return {"duration": 0, "timestamps": [], "clip_seconds": clip_duration}
    # The page keeps clips from overlapping, so start from times that don't.
    num_clips = max(1, min(num_clips, int(total // clip_duration)))
    times = sorted(_pick_preview_timestamps(total, num_clips, clip_duration))
    last = max(0.0, total - clip_duration)
    if any(b - a < clip_duration for a, b in zip(times, times[1:])) or (times and times[-1] > last):
        lo, hi = total * 0.10, min(total * 0.95, total) - clip_duration  # same window as the random pick
        if hi - lo < (num_clips - 1) * clip_duration:
            lo, hi = 0.0, last
        times = [lo + i * (hi - lo) / (num_clips - 1) for i in range(num_clips)] if num_clips > 1 else [last / 2]
    return {"duration": round(total, 3), "clip_seconds": clip_duration, "timestamps": [round(t, 2) for t in times]}


def grab_frame_jpeg(file_path: str, seconds: float, width: int = 480) -> Optional[bytes]:
    """One frame at `seconds` as JPEG bytes, `width` pixels wide -- works for
    any codec FFmpeg reads, including ones the browser can't play.
    BLOCKING -- call via asyncio.to_thread."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, seconds):.3f}", "-i", file_path,
           "-map", "0:V:0", "-frames:v", "1", "-vf", f"scale={int(width)}:-2", "-q:v", "4", "-f", "image2pipe", "-c:v", "mjpeg", "-"]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not grab a frame at {}s of {}: {}", seconds, file_path, exc)
        return None
    if result.returncode != 0 or not result.stdout:
        logger.warning("Could not grab a frame at {}s of {}: {}", seconds, file_path, result.stderr.decode(errors="replace")[-500:])
        return None
    return result.stdout


# What an MP4 may hold for browsers to play it: video codecs (H.264 only in
# 8-bit 4:2:0) and audio codecs.
MP4_BROWSER_VIDEO = {"h264", "hevc", "av1", "vp9"}
MP4_BROWSER_AUDIO = {"aac", "mp3", "opus", "flac"}


def probe_streams(file_path: str) -> dict:
    """{"video": codec, "pix_fmt", "audio": codec or None, "width", "height",
    "duration"} of the first real video track and first audio track, via
    ffprobe. {} when the file can't be read."""
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", file_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        data = json.loads(result.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        logger.warning("ffprobe failed for {}: {}", file_path, exc)
        return {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        return {}
    try:
        duration = float((data.get("format") or {}).get("duration") or video.get("duration") or 0)
    except ValueError:
        duration = 0.0
    return {
        "video": video.get("codec_name"), "pix_fmt": video.get("pix_fmt"),
        "width": video.get("width"), "height": video.get("height"),
        "audio": audio.get("codec_name") if audio else None, "duration": duration,
        "audio_channels": int(audio.get("channels") or 0) if audio else 0,
        "audio_codecs": [s.get("codec_name") for s in streams if s.get("codec_type") == "audio"],
        "format": (data.get("format") or {}).get("format_name"),
    }


def browser_video_ok(info: dict) -> bool:
    """Can a browser show this video track inside an MP4 as it is?"""
    codec = info.get("video")
    if codec not in MP4_BROWSER_VIDEO:
        return False
    if codec == "h264":
        return info.get("pix_fmt") in ("yuv420p", "yuvj420p")
    return True


def remux_to_mp4(src: str, dst: str, audio_codec: Optional[str], video_codec: Optional[str],
                 timeout: int = 6 * 3600) -> bool:
    """Re-packs `src` into an MP4 without re-encoding the picture (audio is
    converted to AAC when MP4/browsers can't carry it). Fast and lossless
    for the video. BLOCKING."""
    tmp = f"{dst}.part.mp4"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
           "-map", "0:V:0", "-map", "0:a:0?", "-c:v", "copy"]
    if video_codec == "hevc":
        cmd += ["-tag:v", "hvc1"]  # what Safari and most players expect for HEVC in MP4
    if audio_codec is None or audio_codec in MP4_BROWSER_AUDIO:
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-map_metadata", "0", "-movflags", "+faststart", "-dn", "-sn", tmp]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Re-packing {} into MP4 failed: {}", src, exc)
        result = None
    if result is None or result.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        if result is not None:
            logger.warning("Re-packing {} into MP4 failed: {}", src, result.stderr.decode(errors="replace")[-1500:])
        if os.path.exists(tmp):
            os.remove(tmp)
        return False
    os.replace(tmp, dst)
    return True


def _pick_preview_timestamps(total_duration: float, num_clips: int, clip_duration: float) -> list[float]:
    """Picks `num_clips` timestamps within the 10%-95% window of the video,
    each at least min_gap apart (min_gap scales with duration -- longer
    videos get spread further apart, as a percentage of total length)."""
    usable_start = total_duration * 0.10
    usable_end = min(total_duration * 0.95, total_duration) - clip_duration

    if usable_end <= usable_start:
        # Video too short for the sampling window -- fall back to a
        # single clip taken from the middle.
        mid = max(0.0, (total_duration - clip_duration) / 2)
        return [mid]

    span = usable_end - usable_start
    if total_duration < 300:  # < 5 min
        gap_pct = 0.05
    elif total_duration < 1200:  # < 20 min
        gap_pct = 0.07
    else:
        gap_pct = 0.10
    min_gap = total_duration * gap_pct

    # If the usable window can't fit `num_clips` points at min_gap apart,
    # relax the gap rather than fail outright.
    max_feasible_gap = span / (num_clips - 1) if num_clips > 1 else span
    if min_gap > max_feasible_gap:
        min_gap = max_feasible_gap

    for _attempt in range(200):
        candidates = sorted(random.uniform(usable_start, usable_end) for _ in range(num_clips))
        if all(candidates[i + 1] - candidates[i] >= min_gap - 1e-6 for i in range(len(candidates) - 1)):
            return candidates

    # Fallback if random sampling can't converge (very tight windows):
    # evenly space across the usable range instead.
    if num_clips == 1:
        return [usable_start + span / 2]
    step = span / (num_clips - 1)
    return [usable_start + i * step for i in range(num_clips)]


def generate_preview_clip(
        file_path: str,
        output_path: str,
        num_clips: int = 5,
        clip_duration_seconds: float = 2.0,
        max_height: int = 480,
        target_fps: int = 24,
        timestamps: Optional[list[float]] = None,
) -> bool:
    """Generates a scrub-through preview clip from the full video via
    ffmpeg -- used as a local fallback when PMVHaven's own preview is
    missing or itself broken. Samples `num_clips` random timestamps
    (10%-95% of the video, spread apart based on duration), cuts
    `clip_duration_seconds` from each, and concatenates them into one
    output clip (default: 5 x 2s = 10s total). `timestamps` uses those
    start times instead (picked by hand on the Upload page). BLOCKING --
    call via asyncio.to_thread."""
    total_duration, _fps = get_video_duration(file_path)
    if total_duration <= 0:
        logger.error("Could not read duration for {} while generating a preview", file_path)
        return False

    if timestamps:
        last = max(0.0, total_duration - clip_duration_seconds)
        timestamps = sorted(min(max(0.0, float(t)), last) for t in timestamps)
    else:
        timestamps = _pick_preview_timestamps(total_duration, num_clips, clip_duration_seconds)
    # Patches corrupted/out-of-range VUI colour metadata some source files
    # carry, which otherwise crashes filtergraph input negotiation ("Invalid
    # color space") before any filter runs. The bitstream filter must match
    # the source codec (h264_metadata refuses HEVC input outright), and there
    # is none for AV1 etc.; if the patched run still fails, try once without it.
    # Bitstream filters on an input need FFmpeg 7+.
    bsf = _COLOUR_FIX_BSF.get(video_codec(file_path) or "") if ffmpeg_version() >= (7, 0) else None
    if bsf and _run_preview_ffmpeg(file_path, output_path, timestamps, clip_duration_seconds, max_height, target_fps, bsf):
        return True
    return _run_preview_ffmpeg(file_path, output_path, timestamps, clip_duration_seconds, max_height, target_fps, None)


_COLOUR_FIX = "colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1"
_COLOUR_FIX_BSF = {"h264": f"h264_metadata={_COLOUR_FIX}", "hevc": f"hevc_metadata={_COLOUR_FIX}"}


def _run_preview_ffmpeg(file_path: str, output_path: str, timestamps: list, clip_duration_seconds: float,
                        max_height: int, target_fps: int, bsf: Optional[str]) -> bool:
    tmp_output = f"{output_path}.part.mp4"
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for ts in timestamps:
        cmd += [
            # "V" = real video streams only: some files also carry cover art as an
            # MJPEG "video" stream, which the colour fix (and the filters) must skip.
            *(["-bsf:V", bsf] if bsf else []),
            "-ss", f"{ts:.3f}",
            "-t", f"{clip_duration_seconds:.3f}",
            "-i", file_path,
        ]

    # Normalize each segment (fps/scale/pixel format/timestamps) before
    # concatenating, then concat filter joins them and re-encodes once.
    filters = []
    concat_labels = []
    for idx in range(len(timestamps)):
        filters.append(
            f"[{idx}:V:0]fps={target_fps},scale=-2:'min({max_height},ih)',"
            f"format=yuv420p,setpts=PTS-STARTPTS[v{idx}]"
        )
        concat_labels.append(f"[v{idx}]")
    filters.append(f"{''.join(concat_labels)}concat=n={len(timestamps)}:v=1:a=0[outv]")

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[outv]",
        "-an",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        tmp_output,
    ]
    logger.debug("FFmpeg preview-generation command: {}", " ".join(cmd))

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=180)
    except subprocess.TimeoutExpired:
        logger.error("Preview generation timed out for {}", file_path)
        if os.path.exists(tmp_output):
            os.remove(tmp_output)
        return False

    if result.returncode != 0 or not os.path.exists(tmp_output):
        log = logger.warning if bsf else logger.error  # with a bsf, a retry without it follows
        log("Preview generation failed for {}{}: {}", file_path, " (retrying without the colour fix)" if bsf else "",
            result.stderr.decode(errors="replace")[-2000:])
        if os.path.exists(tmp_output):
            os.remove(tmp_output)
        return False

    os.replace(tmp_output, output_path)
    return True


def compute_file_sha256(file_path: str, block_size: int = 1024 * 1024) -> Optional[str]:
    """SHA-256 of a file's bytes -- used by repair's phash-skip
    optimization (see repair_worker.py) to detect whether a file has
    genuinely changed since its local_phash was last computed, without
    needing to regenerate the (expensive) perceptual hash just to find
    out. Returns None (never raises) if the file can't be read."""
    try:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(block_size), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        logger.warning("Could not compute SHA-256 for {}", file_path)
        return None


# ------------------------------------------------------ duplicate detection
# Frame-sequence perceptual hashing + sliding-window sequence alignment,
# ported from a standalone script (video_similarity.py, based on the
# matching approach used by VideoDuplicateFinder -- see
# https://github.com/0x90d/videoduplicatefinder) into this module so it
# can reuse the same OpenCV/numpy imports already in use here. Used by
# backend/library_mixins/duplicate_worker.py to power score-based
# duplicate detection -- unlike generate_stash_phash() above (a single
# 64-bit hash per file, compared with a flat Hamming-distance cutoff),
# this samples MANY frames across each video and aligns the two
# sequences before scoring, which tolerates a trimmed intro/outro or a
# different starting offset between two encodes of "the same" content.

def _phash_from_gray(gray: np.ndarray, hash_size: int = 8, highfreq_factor: int = 4) -> int:
    """Standard DCT-based perceptual hash of a single grayscale frame
    (same algorithm the popular `imagehash` library uses), packed into a
    single Python int (hash_size**2 bits -- 64 bits for the default
    hash_size=8)."""
    img_size = hash_size * highfreq_factor
    resized = cv2.resize(gray, (img_size, img_size), interpolation=cv2.INTER_AREA)
    dct = cv2.dct(np.float32(resized))
    dct_low = dct[:hash_size, :hash_size]
    med = np.median(dct_low)
    bits = dct_low > med
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return value


def sample_frame_hashes(
        file_path: str,
        num_samples: int = 48,
        start_pct: float = 0.02,
        end_pct: float = 0.98,
        hash_size: int = 8,
) -> list[str]:
    """Seeks to `num_samples` evenly-spaced timestamps between start_pct
    and end_pct of the video's duration (avoiding intro/outro frames
    right at the very edges) and returns one pHash per sampled frame, as
    a list of hex strings, in chronological order. This is the expensive
    part of duplicate detection (real frame decodes) -- callers should
    cache the result (see LibraryItem.dup_phash_sequence) rather than
    calling this more than once per file-version.

    BLOCKING (OpenCV frame seeks/reads) -- call via asyncio.to_thread,
    not directly from async code. Raises RuntimeError if the file can't
    be opened, its duration can't be determined, or literally no frame
    could be read at any sampled timestamp. Wrapped in
    _suppress_native_av_output() -- see that function's docstring for
    why a decode-error-heavy file (e.g. AV1 content encoded at a profile
    level newer than this OpenCV build's bundled decoder recognizes)
    would otherwise spam the console directly, unattributably, bypassing
    every Python-level log control."""
    with _suppress_native_av_output():
        cap = cv2.VideoCapture(file_path)
        if not cap.isOpened():
            raise RuntimeError(f"Could not open video file: {file_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        duration_ms = (frame_count / fps) * 1000.0 if fps > 0 and frame_count > 0 else None

        if not duration_ms or duration_ms <= 0:
            cap.set(cv2.CAP_PROP_POS_AVI_RATIO, 1.0)
            duration_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
            cap.set(cv2.CAP_PROP_POS_AVI_RATIO, 0.0)

        if not duration_ms or duration_ms <= 0:
            cap.release()
            raise RuntimeError(f"Could not determine duration for: {file_path}")

        hex_len = hash_size * hash_size // 4
        hashes: list[str] = []
        for i in range(num_samples):
            pct = start_pct + (end_pct - start_pct) * (i / max(num_samples - 1, 1))
            cap.set(cv2.CAP_PROP_POS_MSEC, pct * duration_ms)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            value = _phash_from_gray(gray, hash_size=hash_size)
            hashes.append(f"{value:0{hex_len}x}")

        cap.release()

    if not hashes:
        raise RuntimeError(f"Could not extract any frames from: {file_path}")
    return hashes


def _grid_dims_for(n: int) -> tuple[int, int]:
    """Finds a (columns, rows) pair whose product == n, as close to a
    square as possible -- used to lay out exactly `n` sampled frames into
    one tiled sprite image for sample_frame_hashes_ffmpeg_fallback()
    below. Falls back to n columns x 1 row for a prime n (e.g. 47),
    which still works, just as a single wide strip instead of a grid."""
    for cols in range(int(n ** 0.5), 0, -1):
        if n % cols == 0:
            return cols, n // cols
    return n, 1


def get_video_duration_ffprobe(file_path: str) -> float:
    """ffprobe-based duration lookup (seconds) -- a fallback for exactly
    the same class of file get_video_duration() above can't handle: that
    function reads duration via cv2.VideoCapture's own frame-count/fps
    properties, which come from the same OpenCV bindings that fail
    outright to even open some real-world files (certain HEVC encodes,
    pixel formats, or containers) that a full ffmpeg install reads fine.
    Used by sample_frame_hashes_ffmpeg_fallback() below. Raises
    RuntimeError if ffprobe itself can't determine a usable duration."""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", file_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffprobe timed out reading duration for {file_path}") from exc
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {file_path}: {result.stderr.strip()}")
    try:
        duration = float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not parse a duration from ffprobe output for {file_path}") from exc
        if duration <= 0:
            raise RuntimeError(f"ffprobe reported a non-positive duration for {file_path}")
        return duration


def get_fps_ffprobe(file_path: str) -> float:
    """ffprobe-based frame-rate lookup -- same rationale as
    get_video_duration_ffprobe() above, used by reencode_video()'s
    tolerant=True recovery mode when cv2.VideoCapture can't open the file
    at all. Raises RuntimeError if ffprobe can't determine a usable fps."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate", "-of", "json", file_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffprobe timed out reading fps for {file_path}") from exc
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {file_path}: {result.stderr.strip()}")
    try:
        rate_str = json.loads(result.stdout)["streams"][0]["r_frame_rate"]
        num, den = rate_str.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except (KeyError, IndexError, ValueError, ZeroDivisionError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not parse an fps from ffprobe output for {file_path}") from exc
    if fps <= 0:
        raise RuntimeError(f"ffprobe reported a non-positive fps for {file_path}")
    return fps


def probe_duration_tolerant(file_path: str) -> Optional[float]:
    """Last-resort duration probe for a file whose container index is
    damaged badly enough that even ffprobe can't read a duration from it
    at all (the real case this was built for: an MP4 with an internally
    inconsistent sample-to-chunk/chunk-offset table -- ffprobe itself
    reports 'contradictionary STSC and STCO' / 'error reading header' and
    refuses to parse anything). Runs a full tolerant DECODE pass
    (`-err_detect ignore_err -fflags +genpts+igndts`, discarding output)
    and takes the last "time=" progress value ffmpeg reports as the
    effective duration -- this works because decoding sequentially never
    needs to trust the broken index the way opening/seeking via
    cv2.VideoCapture or reading metadata via ffprobe does.

    SLOW -- decodes the entire file start to finish. Only ever reached
    from reencode_video()'s tolerant=True recovery mode, as the last of
    three progressively more tolerant duration-probe attempts. Returns
    None (never raises) if nothing could be parsed at all."""
    cmd = [
        "ffmpeg", "-err_detect", "ignore_err", "-fflags", "+genpts+igndts",
        "-i", file_path, "-map", "0:v:0", "-f", "null", "-",
    ]
    try:
        proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, errors="replace")
    except Exception:
        logger.warning("Could not start the tolerant decode-based duration probe for {}", file_path)
        return None

    last_seconds = 0.0
    for line in proc.stderr:
        match = _TIME_RE.search(line)
        if match:
            last_seconds = max(last_seconds, _parse_ffmpeg_time(match.group(1)))
    proc.wait()

    return last_seconds if last_seconds > 0 else None


def sample_frame_hashes_ffmpeg_fallback(file_path: str, num_samples: int = 48, hash_size: int = 8) -> list[str]:
    """Fallback frame-sequence hasher for files sample_frame_hashes()
    above can't open at all via OpenCV's VideoCapture -- see this
    module's own notes on that being a real, if uncommon, category of
    file (not necessarily an actually-corrupt one). Uses the real
    `ffmpeg` CLI binary via subprocess instead of OpenCV's bindings --
    same tool generate_stash_phash() already relies on successfully for
    these same problem files (that function has never hit this issue,
    since it never goes through cv2.VideoCapture at all).

    Builds ONE tiled sprite image covering the whole video (mirrors
    generate_stash_phash()'s own approach: an ffmpeg `select` filter
    picks `num_samples` evenly-spaced frames, `tile` combines them into a
    single image, so this costs exactly one ffmpeg subprocess call
    regardless of num_samples), then slices that sprite back into
    `num_samples` individual cells and hashes each cell separately --
    this is what turns one cheap ffmpeg call into a full per-frame hash
    SEQUENCE (not a single combined hash), which is what duplicate-
    sequence alignment actually needs.

    NOTE: unlike the primary sample_frame_hashes(), this does not trim
    the first/last 2% of the video (start_pct/end_pct) -- kept simple
    deliberately, since -ss-based input seeking combined with the
    select filter's own internal timestamp math gets timing-fragile
    across ffmpeg versions, and this is only ever reached as a fallback
    for a small number of otherwise-unreadable files where exact edge
    trimming matters far less than successfully getting a sequence at
    all. Raises RuntimeError on failure, same contract as
    sample_frame_hashes()."""
    total_duration = get_video_duration_ffprobe(file_path)
    columns, rows = _grid_dims_for(num_samples)
    scale_width = 160
    interval = total_duration / num_samples

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", file_path,
        "-vf", (
            f"select=isnan(prev_selected_t)+gte(t-prev_selected_t\\,{interval:.6f}),"
            f"scale={scale_width}:-2,tile={columns}x{rows}"
        ),
        *_vfr_args(),
        "-frames:v", "1",
        "-an",
        "-f", "image2pipe",
        "-vcodec", "png",
        "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=300)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffmpeg fallback frame sampling timed out for {file_path}") from exc

    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(
            f"ffmpeg fallback frame sampling failed for {file_path}: "
            f"{result.stderr.decode(errors='replace')[-1000:]}"
        )

    sprite = cv2.imdecode(np.frombuffer(result.stdout, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if sprite is None:
        raise RuntimeError(f"Could not decode the fallback sprite image for {file_path}")

    cell_h = sprite.shape[0] // rows
    cell_w = sprite.shape[1] // columns
    if cell_h <= 0 or cell_w <= 0:
        raise RuntimeError(f"Fallback sprite for {file_path} had unexpected dimensions {sprite.shape}")

    hex_len = hash_size * hash_size // 4
    hashes: list[str] = []
    # ffmpeg's tile filter fills left-to-right, top-to-bottom, in the
    # SAME order frames were selected -- slicing row-major here preserves
    # chronological order, matching sample_frame_hashes()'s own return
    # order exactly.
    for row in range(rows):
        for col in range(columns):
            cell = sprite[row * cell_h:(row + 1) * cell_h, col * cell_w:(col + 1) * cell_w]
            value = _phash_from_gray(cell, hash_size=hash_size)
            hashes.append(f"{value:0{hex_len}x}")

    if not hashes:
        raise RuntimeError(f"Fallback sprite produced no frame hashes for {file_path}")
    return hashes


def best_alignment_similarity(
        hashes_a: list[int],
        hashes_b: list[int],
        hash_bits: int,
        max_offset_fraction: float = 0.25,
) -> dict:
    """Compares two chronological pHash-sequence int lists (already
    decoded from hex -- see sample_frame_hashes()'s return shape) and
    finds the index offset that minimizes average Hamming distance over
    the overlapping region -- tolerates one video having a longer/shorter
    intro or outro than the other. Cheap: pure integer XOR+popcount over
    already-computed hashes, no file access -- safe to call for every
    candidate pair on every scan.

    Returns {"offset": int, "avg_distance": float, "similarity_pct":
    float (0-100), "overlap_frames": int}."""
    n = min(len(hashes_a), len(hashes_b))
    max_offset = max(1, int(n * max_offset_fraction))

    best = {"offset": 0, "avg_distance": float("inf"), "overlap_frames": 0}

    for offset in range(-max_offset, max_offset + 1):
        if offset >= 0:
            seq_a, seq_b = hashes_a[offset:], hashes_b
        else:
            seq_a, seq_b = hashes_a, hashes_b[-offset:]

        overlap = min(len(seq_a), len(seq_b))
        if overlap < max(4, n // 4):
            continue

        total = sum(bin(seq_a[i] ^ seq_b[i]).count("1") for i in range(overlap))
        avg = total / overlap
        if avg < best["avg_distance"]:
            best = {"offset": offset, "avg_distance": avg, "overlap_frames": overlap}

    if best["avg_distance"] == float("inf"):
        overlap = n
        total = sum(bin(hashes_a[i] ^ hashes_b[i]).count("1") for i in range(overlap))
        best = {"offset": 0, "avg_distance": total / overlap if overlap else hash_bits, "overlap_frames": overlap}

    similarity_pct = max(0.0, 100.0 * (1 - best["avg_distance"] / hash_bits))
    best["similarity_pct"] = round(similarity_pct, 2)
    return best


# --------------------------------------------------------------------------
# Audio browsers can play (3.0.1)
# --------------------------------------------------------------------------
# Browsers play AAC, MP3, Opus and FLAC (and Vorbis in WebM / MKV). Some videos
# carry audio they can't -- uncompressed PCM ("pcm_s16le", "pcm_s24le", ...),
# AC-3 / E-AC-3, DTS, TrueHD, ALAC, MP2 -- and play silent. Such audio is
# converted to AAC; the picture is copied as it is.
PLAYABLE_AUDIO = MP4_BROWSER_AUDIO | {"vorbis"}


def unplayable_audio(info: dict) -> list[str]:
    """The audio codecs in `info` (probe_streams) a browser can't play."""
    return sorted({c for c in info.get("audio_codecs") or [] if c and c not in PLAYABLE_AUDIO})


def convert_audio_to_aac(path: str, info: dict, timeout: Optional[int] = None) -> Optional[Path]:
    """Writes a copy of `path` with its audio as AAC next to it (the video,
    chapters and details are copied as they are) and returns it; the caller
    puts it in place of the original. None when it didn't work. BLOCKING."""
    src = Path(path)
    tmp = src.with_name(f"{src.stem}.audio.part{src.suffix}")
    channels = int(info.get("audio_channels") or 2)
    bitrate = "192k" if channels <= 2 else f"{min(channels, 8) * 64}k"
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
           "-map", "0:V:0", "-map", "0:a?", "-c:v", "copy", "-c:a", "aac", "-b:a", bitrate,
           "-map_metadata", "0", "-map_chapters", "0", "-dn", "-sn"]
    if src.suffix.lower() in (".mp4", ".m4v", ".mov"):
        if info.get("video") == "hevc":
            cmd += ["-tag:v", "hvc1"]  # what Safari and most players expect for HEVC in MP4
        cmd += ["-movflags", "+faststart"]
    cmd.append(str(tmp))
    if timeout is None:
        timeout = 600 + src.stat().st_size // (2 * 1024 * 1024)  # 10 min + 1 s per 2 MB
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Converting the audio of {} failed: {}", src, exc)
        result = None
    if result is None or result.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        if result is not None:
            logger.warning("Converting the audio of {} failed: {}", src, result.stderr.decode(errors="replace")[-1500:])
        tmp.unlink(missing_ok=True)
        return None
    check = probe_streams(str(tmp))
    if not check.get("video") or unplayable_audio(check) or abs(float(check.get("duration") or 0) - float(info.get("duration") or 0)) > 2:
        logger.warning("Converting the audio of {} made an unusable file; the original is kept", src)
        tmp.unlink(missing_ok=True)
        return None
    return tmp


# Browser-playable preview clips
# --------------------------------------------------------------------------
# Browsers only decode 8-bit 4:2:0 video. Some previews served by the site are
# H.264 "High 10" (10-bit) -- they play in desktop players but stay blank in a
# browser. Such files are converted once, on first request (see
# ItemExtrasMixin.ensure_playable_preview).
_BROWSER_CODECS = {"h264", "vp8", "vp9", "av1"}
_BROWSER_PIX_FMTS = {"yuv420p", "yuvj420p"}


def is_browser_playable(file_path: str) -> Optional[bool]:
    """True/False from ffprobe, or None when the file can't be probed."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,pix_fmt", "-of", "json", file_path],
            capture_output=True, timeout=30,
        )
        streams = json.loads(result.stdout or b"{}").get("streams") or []
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None
    if not streams:
        return None
    s = streams[0]
    return s.get("codec_name") in _BROWSER_CODECS and s.get("pix_fmt") in _BROWSER_PIX_FMTS


def convert_preview_for_browser(src_path: str, dst_path: str) -> bool:
    """Re-encodes a preview clip to 8-bit H.264 (no audio: previews play muted).
    BLOCKING -- call via asyncio.to_thread."""
    tmp_output = f"{dst_path}.part.mp4"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src_path,
        "-map", "0:v:0", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", tmp_output,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=180)
    except subprocess.TimeoutExpired:
        logger.error("Preview conversion timed out for {}", src_path)
        result = None
    if result is None or result.returncode != 0 or not os.path.exists(tmp_output):
        if result is not None:
            logger.error("Preview conversion failed for {}: {}", src_path, result.stderr.decode(errors="replace")[-1000:])
        if os.path.exists(tmp_output):
            os.remove(tmp_output)
        return False
    os.replace(tmp_output, dst_path)
    return True
