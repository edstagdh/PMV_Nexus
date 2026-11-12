"""
Song recognition (2.14.0): which songs play in a video.

A video is sampled every `songs.interval` seconds: FFmpeg cuts SAMPLE seconds
of its sound (mono 16 kHz WAV, in memory) and each sample is looked up:
  1. Shazam, through the shazamio package (free, no key; a reverse-engineered
     client of Shazam's app API, so Shazam may limit or change it),
  2. AudD (https://audd.io, API key in Credentials) for samples Shazam didn't
     recognise, when the key is saved and `songs.audd_fallback` is on --
     at most `songs.audd_max_samples` per video, since AudD counts requests.
Only these short sound samples are sent; never the video or its details.

The lookups return {"title", "artist", "url", "source"} or None; the caller
(library_mixins/songs.py) adds up the samples per song.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from typing import Optional

from loguru import logger

SAMPLE = 12          # seconds per sample (what Shazam's own app sends)
SHAZAM_GAP = 2.0     # seconds between two Shazam requests, to stay polite
RETRY_SHIFT = 7      # a missed sample is tried again this many seconds later (songs.shazam_retry)


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


def cut_sample(path: str, at: float) -> Optional[bytes]:
    """SAMPLE seconds of the video's sound from `at` as WAV bytes. BLOCKING.
    FFmpeg gives raw PCM and the WAV header is written here: a WAV FFmpeg writes
    into a pipe has no lengths in its header (it can't go back to fill them in),
    which Shazam's signature maker rejects ("unexpected end of file")."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{at}", "-t", str(SAMPLE),
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


# ------------------------------------------------------------------ AudD
AUDD_URL = "https://api.audd.io/"


async def audd_lookup(wav: bytes, api_key: str) -> Optional[dict]:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(AUDD_URL, data={"api_token": api_key}, files={"file": ("sample.wav", wav, "audio/wav")})
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RecognitionError(f"AudD didn't answer ({exc.__class__.__name__})") from exc
    return parse_audd(data)


def parse_audd(data: dict) -> Optional[dict]:
    if (data or {}).get("status") == "error":
        err = data.get("error") or {}
        # 900/901: bad or missing token; 902: request limit reached.
        raise RecognitionError(f"AudD: {err.get('error_message') or 'error'} (code {err.get('error_code')})")
    result = (data or {}).get("result") or {}
    title = (result.get("title") or "").strip()
    if not title:
        return None
    return {"title": title, "artist": (result.get("artist") or "").strip(), "url": result.get("song_link"), "source": "AudD"}


async def audd_key_test(api_key: str) -> dict:
    """Recognises AudD's own example clip (costs one request)."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(AUDD_URL, data={"api_token": api_key, "url": "https://audd.tech/example.mp3"})
        found = parse_audd(r.json())
    except RecognitionError as exc:
        return {"ok": False, "message": str(exc)}
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "message": f"AudD didn't answer ({exc.__class__.__name__})"}
    return {"ok": True, "message": f"Key accepted: AudD recognised its example as “{label(found)}”" if found
            else "Key accepted"}


def label(song: dict) -> str:
    """The library's music format: "Song — Artist" (as PMVHaven's credits)."""
    return " — ".join(x for x in (song.get("title"), song.get("artist")) if x)


async def polite_pause() -> None:
    await asyncio.sleep(SHAZAM_GAP)
