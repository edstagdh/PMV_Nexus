"""
Song recognition (2.14.0): which songs play in a video.

A video is sampled every `songs.interval` seconds: FFmpeg cuts SAMPLE seconds
of its sound (mono 16 kHz WAV, in memory) and each sample is looked up:
  1. Shazam, through the shazamio package (free, no key; a reverse-engineered
     client of Shazam's app API, so Shazam may limit or change it),
  2. optional (2.33.0): Google Gemini (backend/gemini.py) for samples Shazam
     didn't recognise, when a Gemini API key is saved and `songs.gemini` is on --
     at most `songs.gemini_max_samples` per video (the free tier counts requests).
     It gets a longer clip (GEMINI_SAMPLE seconds) of the same spot and is asked
     to name the song only when it's sure; what it names is marked "Gemini (AI)".
Only these short sound samples are sent; never the video or its details.

The lookups return {"title", "artist", "url", "source"} or None; the caller
(library_mixins/songs.py) adds up the samples per song.
"""
from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import time
import sys
from typing import Optional

from loguru import logger

SAMPLE = 12          # seconds per sample (what Shazam's own app sends)
SHAZAM_GAP = 2.0     # seconds between two Shazam requests, to stay polite
RETRY_SHIFT = 7      # a missed sample is tried again this many seconds later (songs.shazam_retry)
GEMINI_SAMPLE = 20   # seconds of sound Gemini gets
GEMINI_SOURCE = "Gemini (AI)"
GEMINI_PAUSE = 3600  # after "every model is out": don't ask Gemini again for this long


class RecognitionError(Exception):
    """A service refused or failed in a way that should stop the scan (limit reached, bad key)."""


def sample_times(duration: float, interval: int, max_samples: int) -> list[float]:
    """Where to take samples: every `interval` s from 5 s in, never past the end,
    spread evenly (at most max_samples)."""
    if duration <= 0:
        return []
    last = max(0.0, duration - SAMPLE)
    if last <= 5:
        return [0.0]
    count = min(max_samples, max(1, int((last - 5) // interval) + 1))
    if count == 1:
        return [min(5.0, last)]
    step = (last - 5) / (count - 1) if count > 1 else 0
    return [round(5 + i * step, 1) for i in range(count)]


RATE = 16000


def cut_sample(path: str, at: float, seconds: int = SAMPLE) -> Optional[bytes]:
    """`seconds` (default SAMPLE) of the video's sound from `at` as WAV bytes. BLOCKING.
    FFmpeg gives raw PCM and the WAV header is written here: a WAV FFmpeg writes
    into a pipe has no lengths in its header (it can't go back to fill them in),
    which Shazam's signature maker rejects ("unexpected end of file")."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{at}", "-t", str(seconds),
           "-i", path, "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "pipe:1"]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=flags)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Song scan: couldn't cut a sample at {}s of {}: {}", at, path, exc)
        return None
    if r.returncode != 0 or len(r.stdout) < RATE:  # under ~0.5 s of sound: no audio track
        return None
    return to_wav(r.stdout)


def to_wav(pcm: bytes) -> bytes:
    """Mono 16-bit RATE Hz PCM -> a complete WAV file."""
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


# ------------------------------------------------------------------ Shazam
class ShazamClient:
    """One shazamio client for a whole scan (async with)."""

    def __init__(self) -> None:
        self._shazam = None

    async def __aenter__(self) -> "ShazamClient":
        from shazamio import Shazam
        self._shazam = Shazam()
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            await self._shazam.close()
        except Exception:  # noqa: BLE001
            pass

    async def lookup(self, wav: bytes) -> Optional[dict]:
        from shazamio_core import SignatureError
        try:
            answer = await self._shazam.recognize(wav)
        except SignatureError as exc:  # made here, before anything is sent: this sample only
            logger.warning("Song scan: Shazam's signature couldn't be made from a sample: {}", exc)
            return None
        except Exception as exc:  # noqa: BLE001 -- network, 429, changed API...
            status = getattr(exc, "status", None)
            if status == 429:
                raise RecognitionError("Shazam is limiting requests right now -- try again later") from exc
            raise RecognitionError(f"Shazam didn't answer ({exc.__class__.__name__}{f' {status}' if status else ''})") from exc
        return parse_shazam(answer)


def parse_shazam(answer: dict) -> Optional[dict]:
    track = (answer or {}).get("track") or {}
    title, artist = (track.get("title") or "").strip(), (track.get("subtitle") or "").strip()
    if not title:
        return None
    return {"title": title, "artist": artist, "url": track.get("url"), "source": "Shazam"}


# ------------------------------------------------------------------ Gemini (optional AI)
GEMINI_SYSTEM = (
    "You identify music. You get a short audio clip from a music video: a song, possibly mixed with "
    "sound effects, voices or other songs. Name the song only if you really recognise it. Never guess "
    "or make one up: if you aren't sure, answer with an empty title and confidence \"low\".")
GEMINI_CONFIG = {
    "temperature": 0,
    "responseMimeType": "application/json",
    "responseSchema": {
        "type": "OBJECT",
        "properties": {"title": {"type": "STRING"}, "artist": {"type": "STRING"},
                       "confidence": {"type": "STRING", "enum": ["high", "medium", "low"]}},
        "required": ["title", "artist", "confidence"],
    },
}
_gemini_paused_until = 0.0


def gemini_paused() -> bool:
    return time.time() < _gemini_paused_until


async def gemini_lookup(wav: bytes, api_key: str) -> Optional[dict]:
    """Asks Gemini which song the clip is. Only a "high" confidence answer counts.
    Raises RecognitionError when Gemini can't be used (bad key, every model out --
    then it isn't asked again for GEMINI_PAUSE seconds)."""
    global _gemini_paused_until
    from . import gemini
    parts = [{"inline_data": {"mime_type": "audio/wav", "data": base64.b64encode(wav).decode()}},
             {"text": "Which song is playing in this clip? Answer as JSON."}]
    try:
        text, model = await gemini.generate(api_key, parts, system=GEMINI_SYSTEM, config=GEMINI_CONFIG)
    except gemini.GeminiBusy as exc:
        _gemini_paused_until = time.time() + GEMINI_PAUSE
        raise RecognitionError(str(exc)) from exc
    except gemini.GeminiError as exc:
        raise RecognitionError(str(exc)) from exc
    return parse_gemini(text, model)


def parse_gemini(text: str, model: str = "") -> Optional[dict]:
    try:
        data = json.loads(text or "{}")
    except ValueError:
        logger.debug("Gemini ({}) answered something that isn't JSON", model)
        return None
    title = str((data or {}).get("title") or "").strip()
    if not title or data.get("confidence") != "high":
        return None
    return {"title": title[:150], "artist": str(data.get("artist") or "").strip()[:150], "url": None,
            "source": GEMINI_SOURCE}


def label(song: dict) -> str:
    """The library's music format: "Song — Artist" (as PMVHaven's credits)."""
    return " — ".join(x for x in (song.get("title"), song.get("artist")) if x)


async def polite_pause() -> None:
    await asyncio.sleep(SHAZAM_GAP)
