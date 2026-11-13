"""PMVHaven and its sister sites (porntubeai.com, hmvhaven.com) as a source (2.30.0):
same platform, one source per `sites:` entry in config.yaml.

Only the sites' API is used (2.32.0: no web pages, no site login): the details from
/api/videos/<id> (pmv_details.py), the file from the site's own download endpoint
/api/videos/<_id>/download, with the user's API key.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from loguru import logger

from . import ProgressCallback, Source
from ..config import get_settings
from ..pmv_details import build_api_url, is_metadata_usable, parse_metadata_from_json
from ..video_meta import VideoMetadata, extract_video_id


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
        if found is None:
            raise RuntimeError("The site's API didn't return this video (removed, private, a link the API doesn't "
                               "know, or the site is unreachable).")
        if need_file and not found[0].video_url:
            raise RuntimeError("The site's API gave no download for this video.")
        return found

    async def download(self, url: str, dest: Path, progress: ProgressCallback = None,
                       headers: Optional[dict] = None) -> None:
        await self._client().stream_download(url, dest, progress, extra_headers=headers)
