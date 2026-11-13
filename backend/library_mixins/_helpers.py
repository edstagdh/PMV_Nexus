"""
Plain module-level helper functions used across library_manager.py's
mixins -- no shared instance state, just pure(ish) utility functions
(string/number parsing, filesystem cleanup helpers). Split out first
(before any of the actual mixin classes) since every other mixin depends
on at least one of these.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from .. import open_files

from ..pmv_details import extract_tags_from_votes


def safe_name(text: str, max_len: int = 120) -> str:
    text = re.sub(r"[^\w\s\-\[\]]", "", text or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:max_len] or "untitled"


def parse_duration_seconds(duration_str: Optional[str]) -> Optional[int]:
    """'4:05' / '1:02:03' -> total seconds, so HLS download progress can be
    reported as a percentage (ffmpeg gives elapsed stream time, not bytes,
    for -c copy remuxing -- there's no Content-Length to divide by)."""
    if not duration_str:
        return None
    try:
        parts = [int(p) for p in duration_str.split(":")]
    except ValueError:
        return None
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds or None


_FILESIZE_RE = re.compile(r"^([\d.]+)\s*(KB|MB|GB|TB)?$", re.IGNORECASE)
_FILESIZE_MULT = {"KB": 1 / 1024, "MB": 1, "GB": 1024, "TB": 1024 * 1024}


def parse_filesize_mb(size_str: Optional[str]) -> Optional[float]:
    """'850 MB' / '1.2 GB' -> a comparable number of MB, for numeric size
    sorting (matches the same parser the frontend used to do this with,
    before sorting moved server-side)."""
    if not size_str:
        return None
    m = _FILESIZE_RE.match(size_str.strip())
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "MB").upper()
    return num * _FILESIZE_MULT.get(unit, 1)


_RATING_RE = re.compile(r"([\d.]+)")


def parse_rating_percent(rating_str: Optional[str]) -> Optional[float]:
    """'69%' -> 69.0 -- numeric rating for sorting, same pattern as
    parse_duration_seconds/parse_filesize_mb above."""
    if not rating_str:
        return None
    m = _RATING_RE.search(rating_str)
    return float(m.group(1)) if m else None


def parse_iso_to_epoch(iso_str: Optional[str]) -> Optional[float]:
    """"2026-01-21T15:47:54.260Z" -> epoch seconds -- for PMVHaven's own
    "phashGeneratedAt" timestamp (see VideoMetadata.phash_generated_at).
    Returns None if the string is missing or unparseable."""
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


SEARCH_FIELD_ALIASES = {
    "title": "title",
    "creator": "creator",
    "tags": "tags",
    "tag": "tags",
    "performers": "performers",
    "performer": "performers",
    # The video detail page's UI labels this data "Models" (see
    # pages/video.js's renderDetails()), even though it's stored,
    # filtered, and searched everywhere else in the app as "performers" --
    # both aliases are accepted here so either matches what the person
    # remembers seeing.
    "models": "performers",
    "model": "performers",
    "description": "description",
    "desc": "description",
    "music": "music",
    "codec": "codec",   # 2.17.1: "codec:hevc", "codec:avc*"
    "codecs": "codec",
}


def codec_family(codec: str) -> str:
    """"AV1 (Main@L4.0)" -> "AV1": the codec without its profile / level (2.17.1)."""
    return (codec or "").split(" (", 1)[0].strip()


def parse_search_query(raw: str) -> tuple[Optional[str], str]:
    """Splits a search string on a leading 'field:value' prefix -- e.g.
    'title:sunset' -> ('title', 'sunset') -- so query_items() can search
    just that one field instead of every field at once. Recognized field
    names (including aliases like 'model'/'models' -> 'performers') are
    listed in SEARCH_FIELD_ALIASES. Returns (None, raw) if the string has
    no colon, or the text before the colon isn't a recognized field name --
    callers should then fall back to searching every field, matching the
    plain (un-prefixed) search behavior from before this feature existed.
    The returned value is NOT lowercased here -- callers lowercase it
    themselves, consistent with how the un-prefixed path already
    lowercases the whole search string."""
    if ":" in raw:
        prefix, _, rest = raw.partition(":")
        field = SEARCH_FIELD_ALIASES.get(prefix.strip().lower())
        if field:
            return field, rest.strip()
    return None, raw


def is_broken_file(path_str: Optional[str]) -> bool:
    """Missing, or present but 0 bytes -- the two ways a downloaded asset
    ends up unusable."""
    if not path_str:
        return True
    p = Path(path_str)
    try:
        return not p.exists() or p.stat().st_size == 0
    except OSError:
        return True


def delete_with_retry(file_path: Path, video_id: str, attempts: int = 5, delay_seconds: float = 0.3) -> bool:
    """Deletes file_path, retrying briefly on failure. Exists mainly for
    Windows: a file that was open a moment ago (e.g. still being streamed to
    the video player when Remove-and-delete was clicked on it) can't be
    unlinked until the OS finishes releasing that handle, which isn't always
    instantaneous even once the client has closed its connection -- the
    frontend now closes the player before sending the delete request, but
    that only shrinks the race window, it doesn't guarantee it's gone by the
    time this runs. Returns False (without raising) if the file didn't exist
    to begin with, or if every attempt failed.

    BLOCKING (real time.sleep, not asyncio.sleep) -- call via
    asyncio.to_thread, not directly from async code."""
    if not file_path.exists():
        return False
    # The app's own open streams of this file would block the delete on Windows.
    open_files.release(file_path)
    last_exc: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            file_path.unlink()
            return True
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < attempts:
                time.sleep(delay_seconds)
    logger.warning(
        "Could not delete {} for {} after {} attempts: {}",
        file_path, video_id, attempts, last_exc,
    )
    return False


def cleanup_partial_download_file(dest_path: Optional[Path], *, is_hls_video: bool = False) -> None:
    """Deletes whichever '.part' (or, for an HLS remux, '.part.mp4') temp
    file PMVClient.stream_download() -- or, for HLS, media_tools.
    download_hls_to_mp4() -- left behind for `dest_path`, if a download
    for it was cancelled or the item was removed mid-download. A no-op
    if dest_path is None (this particular asset was never going to be
    downloaded this run) or nothing partial actually exists there.

    stream_download() names its temp file dest_path.with_suffix(
    dest_path.suffix + '.part') (e.g. 'foo.mp4' -> 'foo.mp4.part').
    download_hls_to_mp4() uses a different convention of its own:
    '<output_path>.part.mp4' (e.g. 'foo.mp4' -> 'foo.mp4.part.mp4'). Both
    are checked when is_hls_video=True, since which one was actually in
    use depends on exactly when the cancellation landed."""
    if dest_path is None:
        return
    candidates = [dest_path.with_suffix(dest_path.suffix + ".part")]
    if is_hls_video:
        candidates.append(Path(f"{dest_path}.part.mp4"))
    for part_path in candidates:
        try:
            if part_path.exists():
                part_path.unlink()
                logger.info("Cleaned up partial download file {} (download was cancelled/removed)", part_path)
        except OSError as exc:
            logger.warning("Could not clean up partial download file {}: {}", part_path, exc)


def extract_tags_performers_music_from_meta_dict(meta_dict: dict) -> tuple[list[str], list[str], list[str]]:
    """Pulls tags/performers/music back out of a metadata JSON dict already
    on disk -- shape-aware, since the file can be either the raw site API
    response or the older curated shape (VideoMetadata.to_dict(), files
    written before 2.27.0). Used by sync_metadata_from_disk()
    and _repair_item(), which have no corresponding in-memory VideoMetadata
    object to read from -- just whatever's already saved.

    - tags: ranked by popularity across ALL of this video's tags (not just
      PMVHaven's own flat top-5 "tags" field) -- see extract_tags_from_votes()
      in pmv_details.py. Works for both shapes: the raw API dict has
      "tagVotes" and this ranks off that; the older curated shape has
      no "tagVotes" at all, so this transparently falls back to the flat
      "tags" field.
    - performers: raw v2 shape lists them under "starsTags" (each entry a
      plain string or a dict with a name/title key); the curated v1
      fallback shape lists them under "models" (each a dict with a "name"
      key) -- same data, different key name/shape depending on which
      version wrote the file. Displayed on the video detail page under
      the heading "Models" (see pages/video.js's renderDetails()), but
      stored/filtered/searched everywhere else in the app -- this function
      included -- as "performers", for consistency with the rest of the UI.
    - music: raw v2 shape stores each credit as {"artist": ..., "song":
      ...}; the curated v1 fallback shape stores {"label": ...} (already
      assembled when the file was written). Rendered as
      "Song — Artist" when both are present, mirroring
      normalizeMetaForDisplay() in frontend/js/pages/video.js -- falls
      back to whichever one exists, then to label/title/name for any
      other shape this field might show up in."""
    tags = extract_tags_from_votes(meta_dict)

    if "starsTags" in meta_dict:  # raw v2 shape
        performers = []
        for entry in meta_dict.get("starsTags") or []:
            if isinstance(entry, str):
                performers.append(entry)
            elif isinstance(entry, dict):
                name = entry.get("name") or entry.get("title")
                if name:
                    performers.append(name)
    else:  # curated v1 fallback shape
        performers = [m.get("name") for m in (meta_dict.get("models", []) or []) if m.get("name")]

    music = []
    for entry in meta_dict.get("music") or []:
        if isinstance(entry, str):
            if entry:
                music.append(entry)
        elif isinstance(entry, dict):
            if entry.get("artist") or entry.get("song"):
                label = " \u2014 ".join(filter(None, [entry.get("song"), entry.get("artist")]))
            else:
                label = entry.get("label") or entry.get("title") or entry.get("name") or ""
            if label:
                music.append(label)

    return tags, performers, music
