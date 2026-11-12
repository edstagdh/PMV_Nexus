"""
JSON-API-based metadata parser for PMVHaven, v2.

PMVHaven exposes a JSON API for each video's metadata at:
    https://pmvhaven.com/api/videos/<video_id>
This returns richer and more reliable data in a single request than
scraping the rendered HTML page (scraper_v1.py) -- notably a CORRECT
bayesian rating (scraper_v1's HTML-scraped "rating badge" was pulling the
wrong video's rating), exact timeline-thumbnail capture times (rather than
inferring seconds from a rendered "m:ss" label), and direct CDN URLs for
the thumbnail/preview/timeline images with no HTML parsing needed at all.

2.27.0: the download comes from the API too -- /api/videos/<_id>/download, with
`_id` from this JSON (the old notes below about needing the page are kept for history).

What this does NOT give us (before 2.27.0): the actual full-quality downloadable video
file. `data["videoUrl"]` in the API response is present but is NOT the
same file PMVHaven's own "Download Video" button serves (that lives
behind /api/videos/<embed_id>/download, whose <embed_id> is only exposed
in the rendered page's JSON-LD block) -- so video_url/is_hls are left
unset here and must still be resolved via scraper_v1's HTML-based helpers
(via a full parse_metadata() call) whenever an actual download is about to
happen. For repair and metadata-only refreshes that don't need to
redownload the video, this JSON alone is enough -- no HTML page fetch
required at all.

Orchestration (try v2, fall back to v1 on failure/incomplete data) lives
in library_manager.py, not here -- this module is pure parsing, exactly
like scraper_v1.py, and does no network I/O itself.

NOTE: only one real example response has been seen so far (a video with
empty `music`/`starsTags`/`userFunScripts`). The extraction helpers for
those three fields are best-effort/defensive rather than confirmed against
a populated example -- if a video that actually has music/models/a
funscript comes through with unexpected shapes, is_metadata_usable() won't
catch that on its own (it only checks title/id), so watch the logs for
downstream repair/metadata oddities on those fields specifically and send
an example JSON if one turns up.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from .scraper_v1 import VideoMetadata

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
    """69.2 -> "69%" -- matches the "NN%" format the frontend/scraper_v1
    already expect for rating_percent. bayesianRating is PMVHaven's own
    vote-weighted rating for THIS video specifically -- unlike the old
    HTML-scraped "Rating badge", which was liable to pick up whatever
    rating happened to render in that DOM slot, sometimes a different
    video's entirely (the actual bug that started this rewrite)."""
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
    seconds -- no guessing from a rendered "m:ss" label needed, unlike
    scraper_v1's HTML-based version) into the {"url", "seconds"} shape the
    rest of the app already expects."""
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
    it turns out to be. Falls back to an empty list, same as scraper_v1
    would if the page had no model chips."""
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
    """scraper_v1 read these off visible "Contains ..." chip tooltips in
    the HTML; the API instead exposes them as plain booleans. Reconstructs
    the same {"label", "description"} shape so downstream code (metadata
    JSON files, the video details page) doesn't need to care which parser
    produced them."""
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
    Returns None (the caller then falls back to scraper_v1's HTML-based
    funscript detection) if nothing usable is found here."""
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
    outer envelope -- unwrap that before calling this). video_url/is_hls
    are deliberately left unset (None/False) -- see the module docstring
    for why the actual downloadable file still needs scraper_v1's
    HTML-based resolution."""
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
    # PMVHaven's own phash for this file -- see VideoMetadata.phash's
    # docstring in scraper_v1.py for how library_manager.py uses this to
    # avoid computing our own hash on every download.
    meta.phash = data.get("phash")
    meta.phash_generated_at = data.get("phashGeneratedAt")

    return meta


def is_metadata_usable(meta: VideoMetadata) -> bool:
    """Sanity check used by the orchestrator in library_manager.py to
    decide whether the v2 JSON response was actually usable, or whether to
    fall back to scraper_v1's HTML scrape. Deliberately minimal -- just
    enough to catch "the API returned something but it was empty/garbage"
    (e.g. a moderation-hidden video, a malformed response) rather than
    trying to validate every field."""
    return bool(meta.title and meta.id)