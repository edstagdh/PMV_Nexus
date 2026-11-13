"""A video's details as a source returns them (VideoMetadata), and the video id in
a PMVHaven-platform link. Parsed from the sites' API by pmv_details.py."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

ID_RE = re.compile(r"/video/(?:.*_)?([a-f0-9]{24})", re.IGNORECASE)


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
    # Alternate-thumbnail candidates from the site's "Video Timeline" row.
    # Each entry is {"url": str, "seconds": float}, sorted chronologically.
    timeline_thumbnails: list = field(default_factory=list)
    # The site's own perceptual hash for the file, and when it was computed
    # (from the API). The download reuses it instead of computing one;
    # re-encode and repair compute their own.
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
