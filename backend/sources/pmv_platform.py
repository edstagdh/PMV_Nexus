"""PMVHaven and its sister sites (porntubeai.com, hmvhaven.com) as a source (2.30.0):
same platform, one source per `sites:` entry in config.yaml.

Details come from the site's API (/api/videos/<id>, scraper_v2) and the file from the
site's own download endpoint (/api/videos/<_id>/download). Only when
download.website_fallback is on is the video's web page read too (scraper_v1) -- the
sites' terms forbid scraping, so it's off by default (2.27.0).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from loguru import logger

from . import ProgressCallback, Source
from ..config import get_settings
from ..scraper_v1 import VideoMetadata, extract_video_id, parse_metadata
from ..scraper_v2 import build_api_url, is_metadata_usable, parse_metadata_from_json


class PMVPlatformSource(Source):
    def __init__(self, site_key: str):
        self.key = site_key
        site = get_settings().sites.get(site_key)
        self.label = (getattr(site, "label", None) or site_key) if site else site_key

    @property
    def _site(self):
        return get_settings().sites[self.key]

    def _client(self):
        from ..pmv_client import get_client
        return get_client(self.key)

    def owns(self, url: str) -> bool:
        return urlparse(url).netloc.lower() == urlparse(self._site.base_url).netloc.lower()

    def item_id(self, url: str) -> str:
        return extract_video_id(url)

    async def api_details(self, video_id: str, page_url: str) -> Optional[tuple[VideoMetadata, dict]]:
        """The site's API JSON, or None when it isn't usable."""
        try:
            response = await self._client().fetch_json(build_api_url(video_id, self._site.base_url))
            data = response.get("data") if isinstance(response, dict) else None
            if not data:
                logger.warning("The {} API returned no data for {}", self.label, video_id)
                return None
            meta = parse_metadata_from_json(data, page_url)
            if not is_metadata_usable(meta):
                logger.warning("The {} API returned incomplete data for {}", self.label, video_id)
                return None
            return meta, data
        except Exception as exc:  # noqa: BLE001
            logger.warning("The {} API failed for {}: {}", self.label, video_id, exc)
            return None

    async def details(self, item, need_file: bool = True) -> tuple[VideoMetadata, Optional[dict]]:
        found = await self.api_details(item.id, item.url)
        if found is not None and (found[0].video_url or not need_file):
            return found
        if not get_settings().download.website_fallback:
            raise RuntimeError(
                "The site's API didn't return this video (removed, private, a link the API doesn't know, "
                "or the site is unreachable). Reading its web page instead is off (download.website_fallback).")
        html = await self._client().fetch_page(item.url)
        page_meta = parse_metadata(html, item.url, self._site.cdn_base)
        raw: Optional[dict] = None
        if found is not None:
            meta, raw = found
            meta.video_url = page_meta.video_url
            meta.is_hls = page_meta.is_hls
            if not meta.funscript_url:
                meta.funscript_url = page_meta.funscript_url
        else:
            meta = page_meta
        if need_file and not meta.video_url:
            from ..library_manager import library_manager
            debug_path = library_manager._dump_debug_html(item.id, html)
            raise RuntimeError("Could not find a downloadable video URL on the page"
                               + (f" (raw HTML saved to {debug_path} for debugging)" if debug_path else ""))
        return meta, raw

    async def download(self, url: str, dest: Path, progress: ProgressCallback = None,
                       headers: Optional[dict] = None) -> None:
        await self._client().stream_download(url, dest, progress, extra_headers=headers)
