"""PMVHaven and its sister sites (porntubeai.com, hmvhaven.com) as a source (2.30.0):
same platform, one source per `sites:` entry in config.yaml.

Only the sites' API is used (2.32.0: no web pages, no site login): the details from
/api/videos/<id> (pmv_details.py), the file from the site's own download endpoint
/api/videos/<_id>/download, with the user's API key.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from loguru import logger

from ..i18n import L
from . import ProgressCallback, Source, VideoGone
from ..config import get_settings
from ..pmv_details import build_api_url, is_metadata_usable, parse_metadata_from_json
from ..video_meta import VideoMetadata, extract_video_id


_NOT_FOUND = re.compile(r"not\s*found|doesn'?t exist|does not exist|no such video|deleted|removed", re.I)


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
        return (await self._lookup(video_id, page_url))[0]

    async def _lookup(self, video_id: str, page_url: str) -> tuple[Optional[tuple[VideoMetadata, dict]], str]:
        """(details or None, why): why is "ok", "gone" (the API answered 404 / 410, or said the
        video wasn't found), "denied" (401 / 403: private, or needs the API key), "empty" (an
        answer without usable details) or "unreachable" (no answer, a server error, a rate limit)."""
        try:
            response = await self._client().fetch_json(build_api_url(video_id, self._site.base_url))
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            logger.warning("The {} API answered HTTP {} for {}", self.label, code, video_id)
            return None, "gone" if code in (404, 410) else "denied" if code in (401, 403) else "unreachable"
        except Exception as exc:  # noqa: BLE001
            logger.warning("The {} API failed for {}: {}", self.label, video_id, exc)
            return None, "unreachable"
        if isinstance(response, dict) and response.get("success") is False:
            said = str(response.get("error") or response.get("message") or "")
            logger.warning("The {} API said no for {}: {}", self.label, video_id, said[:200] or "(no message)")
            return None, "gone" if _NOT_FOUND.search(said) else "empty"
        data = response.get("data") if isinstance(response, dict) else None
        if not data:
            logger.warning("The {} API returned no data for {}", self.label, video_id)
            return None, "empty"
        try:
            meta = parse_metadata_from_json(data, page_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("The {} API returned details that couldn't be read for {}: {}", self.label, video_id, exc)
            return None, "empty"
        if not is_metadata_usable(meta):
            logger.warning("The {} API returned incomplete data for {}", self.label, video_id)
            return None, "empty"
        return (meta, data), "ok"

    async def details(self, item, need_file: bool = True) -> tuple[VideoMetadata, Optional[dict]]:
        found, why = await self._lookup(item.id, item.url)
        if why == "gone":
            # 3.12.1: asked once more before calling it gone (the client already spaces requests)
            found, why = await self._lookup(item.id, item.url)
            if why == "gone":
                logger.info("{}: {} is gone from the site (it answered \"not found\" twice)", self.label, item.id)
                raise VideoGone(L("{site} doesn't have this video any more (it answered \"not found\").", site=self.label))
        if found is None:
            raise RuntimeError({
                "denied": L("{site} refused this video (HTTP 401/403): it may be private, or need your API key (Server Settings → Credentials).", site=self.label),
                "empty": L("{site} answered without usable details for this video.", site=self.label),
            }.get(why, L("{site} couldn't be reached (no answer, a server error or too many requests) -- try again later.", site=self.label)))
        if need_file and not found[0].video_url:
            raise RuntimeError(L("The site's API gave no download for this video."))
        return found

    async def download(self, url: str, dest: Path, progress: ProgressCallback = None,
                       headers: Optional[dict] = None) -> None:
        await self._client().stream_download(url, dest, progress, extra_headers=headers)
