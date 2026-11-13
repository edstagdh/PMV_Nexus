"""
The PMVHaven-platform API (pmvhaven.com, porntubeai.com, hmvhaven.com) -> VideoMetadata.

Each video's details: GET <site>/api/videos/<video id> -> {"success": true, "data": {...}}.
The file: <site>/api/videos/<data._id>/download (2.27.0). Pure parsing, no network I/O
(sources/pmv_platform.py does the requests).

NOTE: the music / starsTags / userFunScripts extraction is defensive -- if a video with
those comes through with unexpected shapes, is_metadata_usable() won't catch it (it only
checks title/id), so watch the logs for oddities on those fields.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from .video_meta import VideoMetadata

PMV_API_PATH_TEMPLATE = "/api/videos/{video_id}"


def build_api_url(video_id: str, base_url: str) -> str:
    """The v2 JSON metadata endpoint for a given 24-char video id (the same
    id extract_video_id() pulls from a normal /video/<slug>_<id> page URL).
    base_url comes from settings.site.base_url (config.yaml), same as
    every other PMVHaven URL built elsewhere in this app."""
    return base_url.rstrip("/") + PMV_API_PATH_TEMPLATE.format(video_id=video_id)


def _format_upload_date(iso_str: Optional[str]) -> Optional[str]:
    """"2026-08-19T02:01:04.442Z" -> "2026-08-19" -- matches the plain
    YYYY-MM-DD format the rest of the app already expects for upload_date."""
    if not iso_str:
        return None
    return iso_str.split("T", 1)[0]


def _format_rating(bayesian_rating) -> Optional[str]:
    """69.2 -> "69%" (the "NN%" format the app uses for rating_percent). bayesianRating
    is the site's vote-weighted rating for this video."""
    if bayesian_rating is None:
        return None
    try:
        return f"{round(float(bayesian_rating))}%"
    except (TypeError, ValueError):
        return None


def _format_views(views) -> Optional[str]:
    if views is None:
        return None
    try:
        return f"{int(views):,} views"
    except (TypeError, ValueError):
        return str(views)


def _best_thumbnail_url(data: dict) -> Optional[str]:
    """Prefers the largest entry in thumbnailSizes ("lg") over the flat
    thumbnailUrl field, in case they ever diverge -- falls back to
    thumbnailUrl if thumbnailSizes is missing/empty."""
    sizes = data.get("thumbnailSizes") or {}
    for key in ("lg", "md", "sm", "xs"):
        entry = sizes.get(key)
        if entry and entry.get("url"):
            return entry["url"]
    return data.get("thumbnailUrl")


def _extract_timeline_thumbnails(data: dict) -> list[dict]:
    """Maps the API's timelineThumbnails (each with a real captureTime in
    seconds) into the {"url", "seconds"} shape the rest of the app expects."""
    out = []
    for entry in data.get("timelineThumbnails") or []:
        url = entry.get("url")
        if not url:
            continue
        seconds = entry.get("captureTime")
        out.append({"url": url, "seconds": float(seconds) if seconds is not None else None})
    return out


def _extract_models(data: dict) -> list[dict]:
    """starsTags was empty on the one example seen so far, so its item
    shape is unconfirmed -- handles both a plain string entry and a dict
    with a name/title + url/slug, so this doesn't blow up whichever shape
    it turns out to be. Falls back to an empty list."""
    out = []
    for entry in data.get("starsTags") or []:
        if isinstance(entry, str):
            out.append({"name": entry, "url": None})
        elif isinstance(entry, dict):
            name = entry.get("name") or entry.get("title") or ""
            url = entry.get("url") or entry.get("slug")
            if name:
                out.append({"name": name, "url": url})
    return out


def _extract_music(data: dict) -> list[dict]:
    """Music credits from the v2 API -- each entry is
    {"artist": "...", "song": "..."}, PMVHaven's own field names for this
    endpoint (confirmed against a real response), distinct from the
    label/title/name shape used elsewhere in this app. Kept defensive
    (falls back to label/title/name) in case PMVHaven ever changes shape
    or a different endpoint variant is in play."""
    out = []
    for entry in data.get("music") or []:
        if isinstance(entry, str):
            out.append({"label": entry, "url": None})
        elif isinstance(entry, dict):
            artist = entry.get("artist")
            song = entry.get("song")
            if artist or song:
                label = " — ".join(filter(None, [song, artist]))
                out.append({"label": label, "url": None})
                continue
            label = entry.get("label") or entry.get("title") or entry.get("name") or ""
            url = entry.get("url") or entry.get("artistUrl")
            if label:
                out.append({"label": label, "url": url})
    return out


def _extract_content_flags(data: dict) -> list[dict]:
    """The API's content booleans as {"label", "description"} entries (the shape the
    metadata JSON files and the video details page use)."""
    flag_map = [
        ("hasExplicitContent", "Explicit content", "Contains explicit content"),
        ("hasExtremeContent", "Extreme content", "Contains extreme content"),
        ("hasGayContent", "Gay content", "Contains gay content"),
        ("hasTransContent", "Trans content", "Contains trans content"),
        ("hasVoiceOver", "Voice over", "Contains voice over"),
        ("seizureWarning", "Seizure warning", "May contain flashing/strobing content"),
    ]
    flags = []
    for key, label, description in flag_map:
        if data.get(key):
            flags.append({"label": label, "description": description})
    return flags


def _extract_funscript_url(data: dict) -> Optional[str]:
    """No confirmed example of a populated funscript yet (userFunScripts
    was empty on the one video checked) -- best-effort extraction from the
    first entry of userFunScripts if present, trying the common key names.
    Returns None if nothing usable is found."""
    entries = data.get("userFunScripts") or []
    if not entries:
        return None
    first = entries[0]
    if isinstance(first, str):
        return first
    if isinstance(first, dict):
        return first.get("url") or first.get("funscriptUrl") or first.get("path")
    return None


def extract_tags_from_votes(data: dict) -> list[str]:
    """Ranks ALL of this video's tags by net vote score (upvotes minus
    downvotes), most popular first -- unlike the flat "tags" field, which
    PMVHaven itself caps to its own top-5 picks. "upvotes"/"downvotes" in
    each tagVotes entry are lists of user-id strings, not raw counts, so
    the vote count is just their length.

    Ties (equal net score) keep tagVotes' own original order (stable sort)
    rather than being resorted alphabetically, since that ordering already
    reflects something about PMVHaven's own ranking for equally-voted tags.

    Falls back to the flat, deduplicated "tags" field if tagVotes is
    missing or empty (e.g. an older API response, or a video with no tag
    votes at all yet) -- same fallback the code this replaced used to do
    unconditionally.
    """
    entries = data.get("tagVotes") or []
    if not entries:
        return list(dict.fromkeys(data.get("tags") or []))

    scored: list[tuple[int, int, str]] = []
    for index, entry in enumerate(entries):
        tag = entry.get("tag")
        if not tag:
            continue
        upvotes = entry.get("upvotes") or []
        downvotes = entry.get("downvotes") or []
        net_score = len(upvotes) - len(downvotes)
        scored.append((net_score, index, tag))

    # Sort by score descending; original tagVotes order breaks ties
    # (ascending index, so the FIRST-listed of equally-scored tags comes
    # first, not the last).
    scored.sort(key=lambda item: (-item[0], item[1]))

    seen: set[str] = set()
    ordered: list[str] = []
    for _score, _index, tag in scored:
        if tag not in seen:
            seen.add(tag)
            ordered.append(tag)
    return ordered


def parse_metadata_from_json(data: dict, page_url: str) -> VideoMetadata:
    """Builds a VideoMetadata from one video's PMVHaven JSON-API response
    (the `data` object inside {"success": true, "data": {...}}, NOT the
    outer envelope -- unwrap that before calling this)."""
    video_id = data.get("_id") or ""

    meta = VideoMetadata(id=video_id, page_url=page_url)
    meta.title = data.get("title") or ""
    meta.creator_name = data.get("uploader") or data.get("uploaderUsername") or ""
    uploader_username = data.get("uploaderUsername")
    meta.creator_url = f"/users/{uploader_username}" if uploader_username else None
    meta.subscribers = None  # not exposed by this endpoint

    meta.upload_date = _format_upload_date(data.get("uploadDate"))
    meta.duration = data.get("duration")
    meta.views = _format_views(data.get("views"))
    meta.rating_percent = _format_rating(data.get("bayesianRating"))
    meta.description = data.get("description") or ""

    # Use the full tagVotes-ranked list (all tags, sorted by popularity)
    # instead of the flat "tags" field, which PMVHaven itself caps to its
    # own top-5 picks -- see extract_tags_from_votes()'s docstring.
    meta.tags = extract_tags_from_votes(data)
    meta.music = _extract_music(data)
    meta.models = _extract_models(data)
    meta.content_flags = _extract_content_flags(data)

    width = data.get("width")
    height = data.get("height")
    video_details: dict = {}
    if width and height:
        video_details["Resolution"] = f"{width}x{height}"
    if data.get("fileSize"):
        video_details["File Size"] = f"{data['fileSize'] / (1024 * 1024):.0f} MB"
    if data.get("duration"):
        video_details["Duration"] = data["duration"]
    if data.get("contentType"):
        video_details["Type"] = data["contentType"]
    meta.video_details = video_details

    # 2.27.0: the original-quality file is the site's own download endpoint for
    # this `_id` (what its Download button uses) -- no web page needed. If the site
    # doesn't offer a video for download, that endpoint refuses, and so does the app.
    origin = urlparse(page_url)
    meta.video_url = (f"{origin.scheme}://{origin.netloc}/api/videos/{video_id}/download"
                      if video_id and origin.scheme and origin.netloc else None)
    meta.is_hls = False

    meta.funscript_url = _extract_funscript_url(data)
    meta.thumbnail_url = _best_thumbnail_url(data)
    meta.preview_url = data.get("previewUrl")
    meta.timeline_thumbnails = _extract_timeline_thumbnails(data)
    # The site's own phash for this file (see VideoMetadata.phash).
    meta.phash = data.get("phash")
    meta.phash_generated_at = data.get("phashGeneratedAt")

    return meta


def is_metadata_usable(meta: VideoMetadata) -> bool:
    """Was the API response usable? Deliberately minimal -- catches "the API returned
    something but it was empty/garbage" (a moderation-hidden video, a malformed
    response) rather than validating every field."""
    return bool(meta.title and meta.id)