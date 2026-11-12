"""
HTML -> metadata for a single PMVHaven video page.

Notes / fragility:
PMVHaven is a Vue/Nuxt SPA. This parser targets the server-rendered HTML
(the same raw markup the original userscript fetched with `fetch()`), using
a mix of regex (for the CDN video/funscript links, which live in an embedded
JSON payload rather than a plain <a href>) and BeautifulSoup (for the visible
metadata: title, creator, tags, description, video details table, etc).

If PMVHaven changes its markup, the selectors below are the place to fix it.
Only video-level metadata is extracted here -- related/recommended videos,
"more from this creator", playlists and comments are deliberately ignored.
"""
from __future__ import annotations

import re
import json
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup


ID_RE = re.compile(r"/video/(?:.*_)?([a-f0-9]{24})", re.IGNORECASE)
MP4_ABS_RE = re.compile(r'(https?://[^"\'\s<>]+?\.mp4)(?=["\'])', re.IGNORECASE)
MP4_REL_RE = re.compile(r'["\'](videos/[^"\'\s<>]+?\.mp4)["\']', re.IGNORECASE)
FUNSCRIPT_RE = re.compile(r'["\'](funscripts/[^"\'\s<>]+?\.funscript)["\']', re.IGNORECASE)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DURATION_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
FILESIZE_RE = re.compile(r"([\d.]+)\s*(MB|GB)", re.IGNORECASE)
THUMB_IMG_RE = re.compile(r'(https?://[^"\'\s<>]+?\.(?:jpg|jpeg|webp|png))(?=["\'])', re.IGNORECASE)

PREVIEW_ABS_RE = re.compile(
    r'(https?://[^"\'\s<>]+?(?:/videoPreview/|/previews/)[^"\'\s<>]+?\.mp4)',
    re.IGNORECASE,
)
PREVIEW_REL_RE = re.compile(
    r'["\']([a-f0-9]{24}/videoPreview/[^"\'\s<>]+?\.mp4|previews/[^"\'\s<>]+?\.mp4)["\']',
    re.IGNORECASE,
)
CDN_ID_RE = re.compile(r"([a-f0-9]{24})", re.IGNORECASE)

# Some videos are served only as HLS (segmented .m3u8 streaming), with no
# plain downloadable .mp4 URL anywhere on the page at all -- find_video_url
# below correctly returns None for these, and parse_metadata falls back to
# these instead. Quality-variant playlists are named directly by resolution
# (e.g. "..._1080p.m3u8"), which is a much more reliable signal to key off
# than trying to correlate a numeric quality label with a nearby URL in the
# embedded JSON payload (bitrate/width/height field ordering isn't consistent
# between qualities).
HLS_VARIANT_RE = re.compile(r'(https?://[^"\'\s<>]+?_(\d{3,4})p\.m3u8)(?=["\'])', re.IGNORECASE)
HLS_MASTER_RE = re.compile(r'(https?://[^"\'\s<>]+?/master\.m3u8)(?=["\'])', re.IGNORECASE)

# PMVHaven renders a row of ~10 "Video Timeline" scrubber thumbnails per
# video page -- small frame-grab images sampled at roughly even intervals
# across the runtime, under a /timeline-thumbnails/ path. These are useful
# as alternate thumbnail candidates (an actual frame from partway into the
# video, instead of PMVHaven's single curated og:image) -- see
# find_timeline_thumbnail_urls() below.
#
# NOTE: PMVHaven's filename convention for these is NOT stable across
# videos -- older videos use ".../timeline-thumbnails/<video_id>/
# timeline_<index>_<seconds>s.jpg" (seconds embedded directly in the
# filename), while newer videos use ".../timeline-thumbnails/<epoch_ms>_
# <random_id>_timeline_<index>_<epoch_ms>.webp" (no seconds value in the
# filename at all -- those numbers are just capture timestamps). Because
# of this, seconds are parsed from the page's own visible "m:ss" label
# next to each frame (see find_timeline_thumbnail_urls below), not from
# the filename. This regex only finds candidate URLs; it has exactly ONE
# capture group (the whole URL) so TIMELINE_THUMB_RE.findall(...) returns
# a flat list of URL strings.
TIMELINE_THUMB_RE = re.compile(
    r'(https?://[^"\'\s<>]+?/timeline-thumbnails/[^"\'\s<>]+?\.(?:jpg|jpeg|webp|png))',
    re.IGNORECASE,
)
# Only matches the OLDER filename convention (seconds embedded directly);
# used as a best-effort bonus when a URL happens to fit that shape, never
# required. Newer-scheme URLs simply won't match this and that's fine --
# their seconds come from the visible label instead.
TIMELINE_THUMB_META_RE = re.compile(r'timeline_(\d+)_([\d.]+)s\.', re.IGNORECASE)


def _parse_label_seconds(label: str) -> Optional[float]:
    """'0:05' -> 5.0, '1:31' -> 91.0, '1:02:03' -> 3723.0 -- parses the
    visible "m:ss" (or "h:mm:ss") timestamp label PMVHaven renders next to
    each Video Timeline frame. This is the reliable source of truth for a
    frame's position in the video -- see the naming-convention note above
    TIMELINE_THUMB_RE for why the URL/filename itself isn't."""
    if not label:
        return None
    try:
        parts = [int(p) for p in label.strip().split(":")]
    except ValueError:
        return None
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + p
    return float(seconds)


def find_timeline_thumbnail_urls(html: str, decoded: str, soup: BeautifulSoup, cdn_base: str) -> list[dict]:
    """Returns [{"url": str, "seconds": float | None}, ...] for every
    timeline scrubber frame found on the page, in the same order PMVHaven
    itself renders them (already chronological, so no re-sorting needed).
    Deduplicated by URL. Returns an empty list if the page has no timeline
    section at all.

    Two independent extraction strategies are tried, in order:
    1. The actual rendered markup -- each frame is a
       <button class="... timeline-thumbnail-item"> wrapping an
       <img src="..."> and a sibling <div> showing the visible "m:ss"
       label (e.g. "0:05"). class_ is matched via membership in the
       parsed class list (not an exact string match), since the real
       class attribute has several space-separated classes
       (e.g. "group relative timeline-thumbnail-item"). Seconds come from
       that visible label (or, failing that, the image's
       "Thumbnail at m:ss" alt text) -- NOT from the URL/filename, whose
       naming convention PMVHaven has changed at least once (see the note
       above TIMELINE_THUMB_RE).
    2. A bare regex scan of the decoded payload, in case the element
       markup above doesn't match at all (page structure changed). Seconds
       are only available here for URLs that happen to fit the older
       filename-embedded-seconds convention; otherwise `seconds` is None.
    """
    entries: list[dict] = []
    seen_urls: set[str] = set()

    for item in soup.find_all(
        ["button", "a", "div"],
        class_=lambda c: c and "timeline-thumbnail-item" in c,
    ):
        img = item.find("img")
        if not img or not img.get("src"):
            continue
        src = img["src"].strip()
        url = src if src.startswith("http") else cdn_base + src.lstrip("/")
        if url in seen_urls:
            continue
        seen_urls.add(url)

        label = None
        for div in item.find_all("div"):
            text = div.get_text(strip=True)
            if DURATION_RE.match(text):
                label = text
                break
        if label is None:
            alt = (img.get("alt") or "").strip()
            m = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?)\s*$", alt)
            if m:
                label = m.group(1)

        entries.append({"url": url, "seconds": _parse_label_seconds(label) if label else None})

    if entries:
        return entries

    for url in TIMELINE_THUMB_RE.findall(decoded):
        if url in seen_urls:
            continue
        seen_urls.add(url)
        m = TIMELINE_THUMB_META_RE.search(url)
        entries.append({"url": url, "seconds": float(m.group(2)) if m else None})

    return entries


def find_hls_url(decoded: str) -> tuple[Optional[str], bool]:
    """Returns (url, is_hls). Prefers the highest-quality named variant
    playlist over the master playlist -- ffmpeg *can* consume a master
    playlist directly, but then picks whichever variant it likes on its own;
    an explicit quality-specific URL keeps download quality predictable."""
    variants = HLS_VARIANT_RE.findall(decoded)  # list of (url, quality_str)
    if variants:
        best_url, _ = max(variants, key=lambda v: int(v[1]))
        return best_url, True
    master_match = HLS_MASTER_RE.search(decoded)
    if master_match:
        return master_match.group(1), True
    return None, False


# PMVHaven's own "Download Video" button hits this endpoint for the true
# original-quality file (full resolution/bitrate) -- it isn't the same file
# as the HLS streaming ladder, which is visibly lower quality/filesize
# (that's what the in-browser player uses, not what gets served here). The
# embed id it needs isn't the same id as either the /video/ URL slug or the
# CDN asset folder -- it's only exposed via the page's own JSON-LD block, as
# "embedUrl": "https://pmvhaven.com/videos/<embed_id>".
EMBED_ID_RE = re.compile(r'"embedUrl"\s*:\s*"https?://[^"]+?/videos/([a-f0-9]{24})"', re.IGNORECASE)


def find_download_api_url(decoded: str, page_url: str) -> Optional[str]:
    m = EMBED_ID_RE.search(decoded)
    if not m:
        return None
    origin = urlparse(page_url)
    return f"{origin.scheme}://{origin.netloc}/api/videos/{m.group(1)}/download"


def find_preview_url(decoded: str, cdn_base: str, thumbnail_url: Optional[str]) -> Optional[str]:
    candidates = [m.group(1) for m in PREVIEW_ABS_RE.finditer(decoded)]
    candidates += [cdn_base + m.group(1) for m in PREVIEW_REL_RE.finditer(decoded)]
    if not candidates:
        return None

    # Multiple previews can appear on one page (e.g. sidebar "related videos"
    # each have their own). Disambiguate by matching the internal CDN asset id
    # that the thumbnail URL uses, since thumbnail + preview share that id.
    if thumbnail_url:
        id_match = CDN_ID_RE.search(thumbnail_url)
        if id_match:
            target_id = id_match.group(1)
            matching = [u for u in candidates if target_id in u]
            if matching:
                return matching[0]

    # Fallback: best-effort first match (may be wrong on pages with sidebars
    # if the id-matching above didn't find anything).
    return candidates[0]


@dataclass
class VideoMetadata:
    id: str
    page_url: str
    title: str = ""
    creator_name: str = ""
    creator_url: Optional[str] = None
    subscribers: Optional[str] = None
    upload_date: Optional[str] = None
    duration: Optional[str] = None
    views: Optional[str] = None
    rating_percent: Optional[str] = None
    description: str = ""
    tags: list[str] = field(default_factory=list)
    music: list[dict] = field(default_factory=list)
    models: list[dict] = field(default_factory=list)
    content_flags: list[dict] = field(default_factory=list)
    video_details: dict = field(default_factory=dict)  # resolution/file size/type/uploaded/phash
    video_url: Optional[str] = None
    is_hls: bool = False  # True if video_url is an HLS playlist (.m3u8) rather than a direct progressive .mp4
    funscript_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    preview_url: Optional[str] = None
    # Alternate-thumbnail candidates from PMVHaven's own "Video Timeline"
    # scrubber row -- see find_timeline_thumbnail_urls() below. Each entry
    # is {"url": str, "seconds": float}, sorted chronologically.
    timeline_thumbnails: list = field(default_factory=list)
    # PMVHaven's own perceptual hash for the file this metadata describes,
    # and when it was computed -- only ever populated by the v2 JSON API
    # (see scraper_v2.parse_metadata_from_json); the v1 HTML scrape has no
    # access to this data at all, so these stay None on the v1-fallback
    # path. library_manager.py's _download() reuses this instead of
    # computing our own phash on every single download -- see that
    # method's comment for why, and _reencode_item()/_repair_item() for
    # when we DO compute our own instead.
    phash: Optional[str] = None
    phash_generated_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "page_url": self.page_url,
            "title": self.title,
            "creator": {
                "name": self.creator_name,
                "url": self.creator_url,
                "subscribers": self.subscribers,
            },
            "upload_date": self.upload_date,
            "duration": self.duration,
            "views": self.views,
            "rating_percent": self.rating_percent,
            "description": self.description,
            "tags": self.tags,
            "music": self.music,
            "models": self.models,
            "content_flags": self.content_flags,
            "video_details": self.video_details,
            "video_url": self.video_url,
            "is_hls": self.is_hls,
            "funscript_url": self.funscript_url,
            "thumbnail_url": self.thumbnail_url,
            "preview_url": self.preview_url,
            "timeline_thumbnails": self.timeline_thumbnails,
            "phash": self.phash,
            "phash_generated_at": self.phash_generated_at,
        }


def extract_video_id(url: str) -> str:
    m = ID_RE.search(url)
    if not m:
        raise ValueError(f"Could not find a video id in url: {url}")
    return m.group(1)


def _decode(html: str) -> str:
    # Nuxt sometimes ships escaped payloads inline; undo the common escapes
    # the same way the userscript did before regex-matching links out of it.
    return html.replace("\\u002F", "/").replace('\\"', '"')


def find_video_url(html: str, decoded: str, cdn_base: str) -> Optional[str]:
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".mp4") and "preview" not in href.lower():
            return href if href.startswith("http") else cdn_base + href.lstrip("/")

    abs_urls = [u for u in MP4_ABS_RE.findall(decoded) if "preview" not in u.lower()]
    if abs_urls:
        pmv = next((u for u in abs_urls if "pmvhavencloud" in u), None)
        return pmv or abs_urls[0]

    rel_urls = [u for u in MP4_REL_RE.findall(decoded) if "preview" not in u.lower()]
    if rel_urls:
        return cdn_base + rel_urls[0]
    return None


def find_funscript_url(decoded: str, cdn_base: str) -> Optional[str]:
    m = FUNSCRIPT_RE.search(decoded)
    return cdn_base + m.group(1) if m else None


def _clean_text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()


def _find_stat_row(soup: BeautifulSoup) -> dict:
    """The small icon+text row with views / upload date / duration."""
    out = {"views": None, "upload_date": None, "duration": None}
    for div in soup.find_all("div"):
        classes = set((div.get("class") or []))
        if classes != {"flex", "items-center", "gap-1.5", "whitespace-nowrap"}:
            continue
        text = _clean_text(div)
        if not text:
            continue
        if DATE_RE.match(text):
            out["upload_date"] = text
        elif DURATION_RE.match(text):
            out["duration"] = text
        elif "views" in text.lower():
            out["views"] = text
        if all(out.values()):
            break
    return out


def _find_creator(soup: BeautifulSoup) -> tuple[str, Optional[str], Optional[str]]:
    for a in soup.find_all("a", href=re.compile(r"^/users/")):
        h3 = a.find("h3")
        if not h3:
            continue
        h3_copy = BeautifulSoup(str(h3), "lxml")
        for span in h3_copy.find_all("span"):
            span.decompose()
        name = _clean_text(h3_copy)
        if not name:
            continue
        sub_text = None
        # walk up a few ancestors looking for the "N subscribers" text near
        # the creator block (exact nesting can shift with markup tweaks)
        node = a
        for _ in range(4):
            node = node.find_parent("div")
            if not node:
                break
            sub_span = node.find(string=re.compile(r"subscribers?$"))
            if sub_span:
                sub_text = sub_span.strip()
                break
        return name, a["href"], sub_text
    return "", None, None


def _find_description(soup: BeautifulSoup) -> str:
    header = soup.find(lambda t: t.name == "h3" and t.get_text(strip=True) == "Description")
    if not header:
        return ""
    box = header.find_parent("div")
    p = box.find("p") if box else None
    return _clean_text(p) if p else ""


def _find_video_details(soup: BeautifulSoup) -> dict:
    header = soup.find(lambda t: t.name == "h3" and t.get_text(strip=True) == "Video Details")
    if not header:
        return {}
    box = header.find_parent("div")
    details = {}
    if not box:
        return details
    for row in box.find_all("div", class_=re.compile(r"flex.*justify-between")):
        spans = row.find_all("span")
        if len(spans) >= 2:
            label = _clean_text(spans[0]).rstrip(":")
            value = _clean_text(spans[-1])
            if label and label.lower() not in ("uploaded", "upload date"):
                details[label] = value
    return details


def _find_tags_music_models_flags(soup: BeautifulSoup) -> tuple[list, list, list, list]:
    tags: list[str] = []
    music: list[dict] = []
    models: list[dict] = []
    flags: list[dict] = []

    for a in soup.find_all("a", href=re.compile(r"^/search\?tags=")):
        text = _clean_text(a).lstrip("#").strip()
        if text and text not in tags:
            tags.append(text)

    for a in soup.find_all("a", href=re.compile(r"^/search\?musicArtist=")):
        title_attr = a.get("title", "").strip()
        span = a.find("span")
        label = _clean_text(span) if span else _clean_text(a)
        entry = {"label": title_attr or label, "url": a["href"]}
        if entry not in music:
            music.append(entry)

    for a in soup.find_all("a", href=re.compile(r"^/model/")):
        span = a.find("span")
        name = _clean_text(span) if span else _clean_text(a)
        entry = {"name": name, "url": a["href"]}
        if entry not in models:
            models.append(entry)

    # Content flag chips carry a descriptive `title` attribute, e.g.
    # title="Contains explicit content (nudity)"
    for span in soup.find_all("span", title=re.compile(r"^Contains", re.IGNORECASE)):
        entry = {"label": _clean_text(span), "description": span["title"].strip()}
        if entry not in flags:
            flags.append(entry)

    return tags, music, models, flags


def _find_title(soup: BeautifulSoup) -> str:
    for h1 in soup.find_all("h1"):
        classes = " ".join(h1.get("class") or [])
        if "font-bold" in classes and "text-white" in classes:
            return _clean_text(h1)
    # fallback: previous behavior, in case markup changes again
    h1 = soup.find("h1")
    return _clean_text(h1) if h1 else ""


def _find_rating_percent(soup: BeautifulSoup) -> Optional[str]:
    badge = soup.find(attrs={"aria-label": "Rating badge"})
    if not badge:
        return None
    m = re.search(r"\d{1,3}%", _clean_text(badge))
    return m.group(0) if m else None


def parse_metadata(html: str, page_url: str, cdn_base: str) -> VideoMetadata:
    decoded = _decode(html)
    soup = BeautifulSoup(html, "lxml")

    meta = VideoMetadata(id=extract_video_id(page_url), page_url=page_url)

    meta.title = _find_title(soup)

    stats = _find_stat_row(soup)
    meta.views = stats["views"]
    meta.upload_date = stats["upload_date"]
    meta.duration = stats["duration"]

    meta.creator_name, meta.creator_url, meta.subscribers = _find_creator(soup)
    meta.description = _find_description(soup)
    meta.video_details = _find_video_details(soup)
    meta.rating_percent = _find_rating_percent(soup)
    meta.tags, meta.music, meta.models, meta.content_flags = _find_tags_music_models_flags(soup)

    # Try the "Download Video" button's own API endpoint FIRST -- it's
    # derived from this specific page's own JSON-LD embedUrl, so unlike the
    # regex scrape below it can't accidentally pick up a different video's
    # link that happens to also appear on the same page (e.g. a "related
    # videos" sidebar entry matching the same .mp4 URL pattern -- this
    # happened at least once and produced a 403 downloading someone else's
    # video file under this item's name). Only falls through to the
    # regex-based direct-link scrape, then HLS detection, if this page has
    # no embedUrl at all.
    download_api_url = find_download_api_url(decoded, page_url)
    if download_api_url:
        meta.video_url = download_api_url
    else:
        meta.video_url = find_video_url(html, decoded, cdn_base)
        if not meta.video_url:
            hls_url, is_hls = find_hls_url(decoded)
            if hls_url:
                meta.video_url = hls_url
                meta.is_hls = True
    meta.funscript_url = find_funscript_url(decoded, cdn_base)
    meta.thumbnail_url = find_thumbnail_url(html, decoded, soup, cdn_base)
    meta.preview_url = find_preview_url(decoded, cdn_base, meta.thumbnail_url)
    meta.timeline_thumbnails = find_timeline_thumbnail_urls(html, decoded, soup, cdn_base)

    return meta

def find_thumbnail_url(html: str, decoded: str, soup: BeautifulSoup, cdn_base: str) -> Optional[str]:
    # 1. Open Graph image -- the most reliable, stable single "thumbnail for
    # this page" source, since it's meant for social link previews.
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        content = og_image["content"].strip()
        return content if content.startswith("http") else cdn_base + content.lstrip("/")

    # 2. <video poster="..."> if the player sets one directly.
    video_tag = soup.find("video", poster=True)
    if video_tag and video_tag.get("poster"):
        poster = video_tag["poster"].strip()
        return poster if poster.startswith("http") else cdn_base + poster.lstrip("/")

    # 3. Fallback: a "preview" image URL near the video (thumbnails are
    # often named like the video but with "preview" + an image extension).
    preview_imgs = [u for u in THUMB_IMG_RE.findall(decoded) if "preview" in u.lower()]
    if preview_imgs:
        return preview_imgs[0]

    return None


def _format_seconds_label(seconds: float) -> str:
    """91.28 -> "1:31" -- matches the "m:ss" label PMVHaven already renders
    next to each Video Timeline frame, for the fallback path below where
    we're computing it ourselves from the filename instead of reading it
    off the page."""
    total = int(round(seconds))
    m, s = divmod(total, 60)
    return f"{m}:{s:02d}"


def find_timeline_thumbnails(html: str, decoded: str, soup: BeautifulSoup, cdn_base: str) -> list[dict]:
    """The "Video Timeline" strip on a PMVHaven video page: a row of small
    preview frames sampled evenly through the video, each a candidate
    thumbnail for the thumbnail-picker modal (see parse_thumbnail_options
    below) beyond just the page's single default thumbnail_url. Returns a
    list of {"url": ..., "label": "m:ss" or None}, in on-page order.

    Soup-based lookup first (keyed off the timeline strip's own button
    markup, which conveniently also gives us the human-readable "m:ss"
    label the page already renders, instead of us having to compute one) --
    falls back to a bare regex scan of the decoded payload (label computed
    from the seconds encoded in the filename itself) if that markup ever
    changes, same fallback pattern used elsewhere in this file."""
    results: list[dict] = []
    seen_urls: set[str] = set()

    # PMVHaven's actual current markup for each timeline frame: a <button
    # class="... timeline-thumbnail-item"> wrapping an <img src="..."> and
    # a small "m:ss" label div -- e.g.:
    #   <button class="group relative timeline-thumbnail-item">
    #     <div class="w-full aspect-video ..."><img src="..." alt="Thumbnail at 0:12" .../></div>
    #     <div class="absolute bottom-2 right-2 ...">0:12</div>
    #   </button>
    # find_all(..., class_="timeline-thumbnail-item") only matches an EXACT
    # single-class attribute, so it never matches here (the real class
    # attribute has several space-separated classes) -- that's why this
    # soup-based path was silently falling through to the regex fallback
    # below in practice. Matched via a class_ filter function instead, which
    # checks membership in the parsed class list rather than requiring an
    # exact string match.
    for item in soup.find_all(
        ["button", "a", "div"],
        class_=lambda c: c and "timeline-thumbnail-item" in c,
    ):
        img = item.find("img")
        if not img or not img.get("src"):
            continue
        src = img["src"].strip()
        url = src if src.startswith("http") else cdn_base + src.lstrip("/")
        if url in seen_urls:
            continue

        # Prefer the label PMVHaven already rendered as text (e.g. "0:12")
        # over parsing it back out of the alt text -- but the alt text
        # ("Thumbnail at 0:12") is a reliable fallback if the visible label
        # div's structure changes.
        label = None
        for div in item.find_all("div"):
            text = div.get_text(strip=True)
            if DURATION_RE.match(text):
                label = text
                break
        if label is None:
            alt = (img.get("alt") or "").strip()
            m = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?)\s*$", alt)
            if m:
                label = m.group(1)

        results.append({"url": url, "label": label})
        seen_urls.add(url)

    if results:
        return results

    for url in TIMELINE_THUMB_RE.findall(decoded):
        if url in seen_urls:
            continue
        m = TIMELINE_THUMB_META_RE.search(url)
        label = None
        if m:
            try:
                label = _format_seconds_label(float(m.group(2)))
            except ValueError:
                label = None
        results.append({"url": url, "label": label})
        seen_urls.add(url)

    return results


def parse_thumbnail_options(html: str, cdn_base: str) -> dict:
    """Pulls just the thumbnail-related candidates off a video page -- the
    page's own default thumbnail plus every frame in its Video Timeline
    strip -- for the thumbnail-picker modal. Deliberately separate from
    parse_metadata() (which already computes thumbnail_url as part of a
    full VideoMetadata) since this runs on-demand from a fresh page fetch
    each time the picker opens, not bundled with a full metadata parse.
    Returns {"original_url": str | None, "timeline": [{"url", "label"}, ...]}."""
    decoded = _decode(html)
    soup = BeautifulSoup(html, "lxml")
    original_url = find_thumbnail_url(html, decoded, soup, cdn_base)
    timeline = find_timeline_thumbnails(html, decoded, soup, cdn_base)
    return {"original_url": original_url, "timeline": timeline}