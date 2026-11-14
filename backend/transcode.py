"""
Converting while watching (2.13.0): a video played as "720p" -- picked in the
player, or used automatically when the browser can't play the file -- is
streamed as HLS made on the fly, the way Plex / Jellyfin do it:

- The player gets a complete playlist straight away (VOD: every SEG-second
  segment of the whole video), so it shows the full length and can seek.
- ONE FFmpeg process per playback makes the segments in order into a temp
  folder (<cache>/.transcode/<session>/<n>.ts), H.264 + AAC in MPEG-TS, at
  most `playback.max_height` lines, with a keyframe exactly every SEG
  seconds so segment n is always [n*SEG, (n+1)*SEG).
- A request for a segment that's made (or about to be) waits for it. A jump
  further away (a seek) stops FFmpeg and starts it again at that segment,
  with timestamps offset to match (-output_ts_offset) so the player sees one
  continuous video.
- CPU: FFmpeg gets `playback.cpu_percent` of the cores (thread count + core
  affinity) at low priority, and is paused when it's more than AHEAD_MAX
  segments in front of the player (resumed when the player catches up), so a
  paused video costs nothing.
- At most `playback.max_sessions` at once (one per person: starting another
  ends their previous one). Sessions nobody asked anything of for IDLE
  seconds are ended; their folder is deleted. The folder is emptied at start.

Sessions are registered with open_files, so deleting / renaming / re-encoding
a video first ends its conversions (Windows can't touch an open file).
"""
from __future__ import annotations

import asyncio
import math
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from . import activity, media_tools, open_files

SEG = 6                 # seconds per segment
AHEAD_MAX = 12          # pause FFmpeg when this many segments are ready ahead of the player (72 s)
AHEAD_RESUME = 6        # ... and resume it below this
NEAR = 3                # a request this close after what's being made waits instead of restarting
WAIT = 45               # longest wait for one segment (s)
IDLE = 20 * 60          # end a session after this long without requests (a long pause)


class TranscodeError(Exception):
    pass


class Session:
    def __init__(self, sid: str, item_id: str, user_id: str, source: str, duration: float, folder: Path, height: int):
        self.sid, self.item_id, self.user_id, self.source = sid, item_id, user_id, source
        self.duration, self.folder, self.height = duration, folder, height
        self.proc: Optional[subprocess.Popen] = None
        self.start_seg = 0           # first segment of the running FFmpeg
        self.wanted = 0              # last segment the player asked for
        self.paused = False
        self.last_seen = time.time()
        self.lock = asyncio.Lock()
        self.closed = False
        self.has_audio = True
        self.activity = None  # its entry on Tasks (2.18.0)

    @property
    def segments(self) -> int:
        return max(1, math.ceil(self.duration / SEG))

    def seg_file(self, n: int) -> Path:
        return self.folder / f"{n}.ts"

    def made_up_to(self) -> int:
        """The highest segment ready in an unbroken run from start_seg (start_seg - 1 if none)."""
        n = self.start_seg
        while self.seg_file(n).exists():
            n += 1
        return n - 1

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop_ffmpeg(self) -> None:
        proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            try:
                if self.paused:
                    _resume(proc)
                proc.kill()
                proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                pass
        self.paused = False

    def close(self) -> None:
        """Ends the session (also called by open_files.release before a delete/rename)."""
        self.closed = True
        self.stop_ffmpeg()
        open_files.unregister(self.source, self)
        shutil.rmtree(self.folder, ignore_errors=True)


def _suspend(proc: subprocess.Popen) -> None:
    import psutil
    psutil.Process(proc.pid).suspend()


def _resume(proc: subprocess.Popen) -> None:
    import psutil
    psutil.Process(proc.pid).resume()


def _lower_priority(pid: int) -> None:
    try:
        import psutil
        p = psutil.Process(pid)
        p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if sys.platform == "win32" else 10)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not lower the priority of FFmpeg {}: {}", pid, exc)


class LiveTranscoder:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self.stopped: dict[str, float] = {}  # sessions stopped on Tasks -> when (the player shouldn't restart them)
        self._reaper: Optional[asyncio.Task] = None
        self._root: Optional[Path] = None
        self._mutex = threading.Lock()

    # ------------------------------------------------------------------ setup
    def root(self, settings) -> Path:
        root = Path(settings.cache_dir) / ".transcode"
        if self._root != root:
            self._root = root
        return root

    def clean(self, settings) -> None:
        """Startup: whatever a previous run left behind."""
        shutil.rmtree(self.root(settings), ignore_errors=True)

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.get_running_loop().create_task(self._reap_loop())

    async def _reap_loop(self) -> None:
        while self._sessions:
            await asyncio.sleep(5)
            now = time.time()
            for s in list(self._sessions.values()):
                if s.closed or now - s.last_seen > IDLE:
                    self.end(s.sid, reason="idle" if not s.closed else "closed")
                    continue
                await asyncio.to_thread(self._throttle, s)

    def _throttle(self, s: Session) -> None:
        if not s.running():
            return
        ahead = s.made_up_to() - s.wanted
        try:
            if not s.paused and ahead >= AHEAD_MAX:
                _suspend(s.proc)
                s.paused = True
            elif s.paused and ahead < AHEAD_RESUME:
                _resume(s.proc)
                s.paused = False
        except Exception as exc:  # noqa: BLE001
            logger.debug("Couldn't pause/resume FFmpeg: {}", exc)

    # ------------------------------------------------------------------ sessions
    def active(self) -> list[dict]:
        return [{"sid": s.sid, "item_id": s.item_id, "user_id": s.user_id, "segment": s.wanted, "paused": s.paused,
                 "running": s.running()} for s in self._sessions.values()]

    async def start(self, settings, item_id: str, user_id: str, source: str, title: str = "", username: str = "") -> Session:
        cfg = settings.playback
        if not cfg.live_convert:
            raise TranscodeError("Converting while watching is turned off (Server Settings → Re-encode)")
        if not os.path.isfile(source):
            raise TranscodeError("The video file is missing")
        # One per person: their previous playback ends.
        for s in [s for s in self._sessions.values() if s.user_id == user_id]:
            self.end(s.sid, reason="replaced")
        if len(self._sessions) >= cfg.max_sessions:
            n = len(self._sessions)
            raise TranscodeError(f"{n} {'video is' if n == 1 else 'videos are'} already being converted for watching -- "
                                 "try again when one stops, or play the original")
        info = await asyncio.to_thread(media_tools.probe_streams, source)
        duration = float(info.get("duration") or 0)
        if not info.get("video") or duration <= 0:
            raise TranscodeError("FFmpeg can't read this video")
        sid = secrets.token_urlsafe(12)
        folder = self.root(settings) / sid
        folder.mkdir(parents=True, exist_ok=True)
        height = min(cfg.max_height, int(info.get("height") or cfg.max_height))
        s = Session(sid, item_id, user_id, source, duration, folder, height)
        s.has_audio = bool(info.get("audio"))
        self._sessions[sid] = s
        open_files.register(source, s)
        self._ensure_reaper()
        # On Tasks while it runs (2.18.0); Stop there ends it (the player falls back to the original).
        s.activity = activity.start("live_convert", f"Converting for watching ({height}p): {title or item_id}",
                                    source=username or "manual", progress=lambda s=s: self._progress(s))
        s.activity.stopper = lambda sid=sid: self.end(sid, reason="stopped on Tasks")
        logger.info("Converting while watching: item {} at {}p for user {} (session {})", item_id, height, user_id, sid[:6])
        return s

    def get(self, sid: str, user_id: str) -> Session:
        s = self._sessions.get(sid)
        if not s or s.closed or s.user_id != user_id:
            raise KeyError(sid)
        s.last_seen = time.time()
        return s

    @staticmethod
    def _progress(s: Session) -> dict:
        at = s.wanted * SEG
        made = max(s.made_up_to(), s.wanted)
        return {"stage": "Paused — far enough ahead of the viewer" if s.paused else "Converting",
                "done": min(made + 1, s.segments), "total": s.segments,
                "detail": f"watching at {int(at // 60)}:{int(at % 60):02d} of {int(s.duration // 60)}:{int(s.duration % 60):02d}"}

    def end(self, sid: str, reason: str = "stopped") -> None:
        s = self._sessions.pop(sid, None)
        if s:
            s.close()
            if "Tasks" in reason:
                now = time.time()
                self.stopped = {k: t for k, t in self.stopped.items() if now - t < 3600}
                self.stopped[sid] = now
            if s.activity is not None:
                s.activity.finish("stopped" if "Tasks" in reason else "done", f"Ended ({reason})")
            logger.info("Converting while watching ended ({}): item {} (session {})", reason, s.item_id, sid[:6])

    def end_all(self) -> None:
        for sid in list(self._sessions):
            self.end(sid, reason="shutdown")

    # ------------------------------------------------------------------ playlist + segments
    @staticmethod
    def playlist(s: Session) -> str:
        lines = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{SEG}", "#EXT-X-MEDIA-SEQUENCE:0",
                 "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-INDEPENDENT-SEGMENTS"]
        for n in range(s.segments):
            length = min(SEG, s.duration - n * SEG)
            lines += [f"#EXTINF:{length:.3f},", f"{n}.ts"]
        lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines) + "\n"

    async def segment(self, settings, s: Session, n: int) -> Path:
        if n < 0 or n >= s.segments:
            raise TranscodeError("No such segment")
        s.wanted = n
        f = s.seg_file(n)
        async with s.lock:
            if not f.exists():
                made = s.made_up_to()
                in_reach = s.running() and s.start_seg <= n <= made + NEAR
                if not in_reach:
                    await asyncio.to_thread(self._launch, settings, s, n)
            if s.paused:
                await asyncio.to_thread(self._throttle, s)
        deadline = time.time() + WAIT
        while not f.exists():
            if s.closed:
                raise TranscodeError("The conversion was stopped")
            if not s.running() and not f.exists():
                if s.proc is not None and s.proc.returncode not in (0, None):
                    raise TranscodeError(f"FFmpeg stopped (exit code {s.proc.returncode})")
                if s.proc is None:
                    raise TranscodeError("The conversion was stopped")
            if time.time() > deadline:
                raise TranscodeError("The conversion is too slow for this video on this server")
            await asyncio.sleep(0.2)
        s.last_seen = time.time()
        return f

    def _launch(self, settings, s: Session, n: int) -> None:
        """(Re)starts FFmpeg at segment n. BLOCKING (briefly)."""
        s.stop_ffmpeg()
        for old in s.folder.glob("*.ts*"):  # segments from before the jump would break the "unbroken run" check
            try:
                if int(old.name.split(".")[0]) >= n:
                    old.unlink()
            except (ValueError, OSError):
                pass
        cfg = settings.playback
        total = os.cpu_count() or 4
        cores = max(1, min(total, round(total * cfg.cpu_percent / 100)))
        start = n * SEG
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
               "-ss", f"{start}", "-i", s.source,
               "-map", "0:v:0", *(["-map", "0:a:0"] if s.has_audio else []),
               "-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1",
               "-vf", f"scale=-2:'min({s.height},ih)':flags=bicubic,format=yuv420p",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-maxrate", "5M", "-bufsize", "10M",
               "-profile:v", "high", "-threads", str(cores),
               "-force_key_frames", f"expr:gte(t,n_forced*{SEG})", "-sc_threshold", "0",
               *(["-c:a", "aac", "-ac", "2", "-b:a", "160k"] if s.has_audio else []),
               "-output_ts_offset", f"{start}", "-muxdelay", "0", "-muxpreload", "0",
               "-f", "hls", "-hls_time", str(SEG), "-hls_list_size", "0", "-hls_segment_type", "mpegts",
               "-hls_flags", "temp_file+independent_segments", "-start_number", str(n),
               "-hls_segment_filename", str(s.folder / "%d.ts"), str(s.folder / "ffmpeg.m3u8")]
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        s.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=flags)
        s.start_seg = n
        s.paused = False
        media_tools.limit_process_cores(s.proc.pid, cores if cores < total else None)
        _lower_priority(s.proc.pid)
        threading.Thread(target=self._watch_stderr, args=(s, s.proc), daemon=True).start()

    @staticmethod
    def _watch_stderr(s: Session, proc: subprocess.Popen) -> None:
        err = (proc.stderr.read() or b"").decode(errors="replace").strip() if proc.stderr else ""
        code = proc.wait()
        if code not in (0, None) and not s.closed and s.proc is proc:
            logger.warning("Converting while watching: FFmpeg stopped with code {} for item {}: {}", code, s.item_id, err[-500:])


live_transcoder = LiveTranscoder()
