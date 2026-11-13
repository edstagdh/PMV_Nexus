"""
Requests to a PMVHaven-platform site (pmvhaven.com, porntubeai.com, hmvhaven.com):
its JSON API (video details) and file downloads (the video from
/api/videos/<_id>/download, thumbnails, previews, funscripts) -- with the user's own
API key, never a site login or web pages (2.32.0).

One PMVClient instance = one site; use get_client(site_key) (cached at the bottom).
"""
from __future__ import annotations

import asyncio
import random
import time
from loguru import logger
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from . import proxy
from .config import get_settings
from .sites import get_site


class PMVClient:
    # -------------------------------------------------- metadata rate limiting
    # Enforced per-site (each PMVClient instance is one site) -- PMVHaven's
    # own per-account rate limit on metadata lookups ("429 Too many video
    # requests. Please wait a moment.") applies independently to each site
    # you use. These constants ONLY apply to fetch_json() (see
    # _get_with_metadata_rate_limit's docstring below) --
    # stream_download() (actual video/thumbnail/preview/funscript file
    # downloads) is unaffected, since normal day-to-day usage doesn't
    # generate enough of those to hit the limit.
    _METADATA_MIN_DELAY = 0.5   # seconds -- minimum gap between metadata requests
    _METADATA_MAX_DELAY = 3.0   # seconds -- maximum gap (actual gap is randomized in this range)
    _METADATA_RETRY_BACKOFF_MIN = 3.0   # seconds -- minimum backoff after a 429
    _METADATA_RETRY_BACKOFF_MAX = 8.0   # seconds -- maximum backoff after a 429
    _METADATA_MAX_RETRIES = 3           # total attempts (1 initial + up to 2 retries) before giving up

    # Always the current settings: get_settings() is cached and the cache is
    # cleared whenever settings change, so changes apply without a restart.
    # (Assigning is accepted and ignored, for older call sites.)
    @property
    def settings(self):
        return get_settings()

    @settings.setter
    def settings(self, _value) -> None:
        pass

    def __init__(self, site_key: str) -> None:
        self.site_key = site_key
        self.site = get_site(site_key)  # raises KeyError if site_key isn't configured
        self._client: Optional[httpx.AsyncClient] = None
        # The same client through the proxy (Server settings -> Proxy), made on first use.
        self._proxy_client: Optional[httpx.AsyncClient] = None
        self._proxy_client_url: Optional[str] = None
        self._lock = asyncio.Lock()

        # Shared across every caller of fetch_json() for THIS
        # site (repair workers, the metadata-fetch worker pool, etc.) -- a
        # single lock + timestamp gate, NOT a per-worker sleep, so it
        # correctly spaces out requests even when several workers try to
        # fire at the exact same moment. See _throttle_metadata_request().
        self._metadata_rate_lock = asyncio.Lock()
        self._last_metadata_request_at = 0.0

    def _api_headers(self, url: str) -> dict:
        """The user's own API key (Server settings → Credentials → PMVHaven API) on
        requests to the site's API, so the site knows who is calling (2.27.0)."""
        key = self.settings.pmvhaven_api_key
        if key and urlparse(url).path.startswith("/api/"):
            return {"Authorization": f"Bearer {key}"}
        return {}

    # --------------------------------------------------------- httpx client
    def _new_client(self, proxy_url: Optional[str] = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 (compatible; PMVNexus/1.0)"},
            timeout=httpx.Timeout(30.0, read=60.0),
            follow_redirects=True,
            proxy=proxy_url,
        )

    async def _get_client(self, use_proxy: Optional[bool] = None) -> httpx.AsyncClient:
        """The client: direct, or through the proxy when
        `use_proxy` (default: when Server settings -> Proxy sends all site
        traffic through it)."""
        if use_proxy is None:
            use_proxy = proxy.for_sites() == "all"
        async with self._lock:
            if use_proxy:
                url = proxy.address()
                if not url:
                    raise RuntimeError("The proxy is turned on but has no address (Server settings → Proxy)")
                if self._proxy_client is None or self._proxy_client_url != url:
                    if self._proxy_client is not None:
                        await self._proxy_client.aclose()
                    self._proxy_client, self._proxy_client_url = self._new_client(url), url
                return self._proxy_client
            if self._client is None:
                self._client = self._new_client()
            return self._client

    def _download_via_proxy(self) -> bool:
        mode = proxy.for_sites()
        return mode == "all" or (mode == "split" and proxy.via_proxy.get())

    async def close(self) -> None:
        async with self._lock:
            for name in ("_client", "_proxy_client"):
                client = getattr(self, name)
                if client is not None:
                    await client.aclose()
                    setattr(self, name, None)

    # ---------------------------------------------- metadata rate limiting
    async def _throttle_metadata_request(self) -> None:
        """Blocks the caller until it's safe to send the next metadata
        request TO THIS SITE. Shared across every concurrent caller
        (repair workers, the metadata-fetch worker pool) via a single lock
        + "last request went out at time T" timestamp -- NOT a per-caller
        sleep, which would fail to actually space out N workers all trying
        to fire in the same instant. Each call waits for a random 0.5-3s
        gap since the LAST request actually went out (by any caller for
        this site), then records itself as the new "last request" before
        releasing the lock and letting the actual HTTP GET happen outside
        the lock (so the request itself, which can take a while, doesn't
        block other callers from timing their own wait)."""
        async with self._metadata_rate_lock:
            now = time.monotonic()
            elapsed = now - self._last_metadata_request_at
            delay = random.uniform(self._METADATA_MIN_DELAY, self._METADATA_MAX_DELAY)
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
            self._last_metadata_request_at = time.monotonic()

    async def _get_with_metadata_rate_limit(self, url: str) -> httpx.Response:
        """GET plumbing for fetch_json() -- this site's JSON API, i.e. every
        metadata/repair lookup this app makes for this site. Deliberately NOT used by
        stream_download() (actual video/thumbnail/preview/funscript file
        downloads), since normal day-to-day usage doesn't generate enough
        of those requests to hit the platform's rate limit -- only bursty
        metadata lookups (e.g. many repair-queue items firing at once) do.

        Two layers of protection against the platform's own
        "429 Too many video requests. Please wait a moment." response:

        1. Prevention: _throttle_metadata_request() above spaces every
           outgoing metadata request (across ALL concurrent callers for
           this site) by a random 0.5-3s gap, so a burst of repairs/
           metadata fetches can't slam the API all at once in the first
           place.
        2. Reactive: if a 429 still comes back anyway, backs off a longer
           random 3-8s pause and retries, up to _METADATA_MAX_RETRIES
           attempts total, before giving up and raising HTTPStatusError
           like normal -- callers (e.g. _fetch_metadata_v2_or_none in
           library_manager.py) already catch and log/fall back on that
           exception, so no caller-side changes were needed for this."""
        client = await self._get_client()
        attempt = 0
        while True:
            attempt += 1
            await self._throttle_metadata_request()
            resp = await client.get(url, headers=self._api_headers(url))
            if resp.status_code == 429:
                if attempt >= self._METADATA_MAX_RETRIES:
                    logger.warning(
                        "{} still rate-limiting {} after {} attempt(s) -- giving up",
                        self.site_key, url, attempt,
                    )
                    resp.raise_for_status()  # raises HTTPStatusError, same as any other failed request
                backoff = random.uniform(self._METADATA_RETRY_BACKOFF_MIN, self._METADATA_RETRY_BACKOFF_MAX)
                logger.warning(
                    "{} rate-limited a metadata request to {} (attempt {}/{}) -- "
                    "backing off {:.1f}s before retrying",
                    self.site_key, url, attempt, self._METADATA_MAX_RETRIES, backoff,
                )
                await asyncio.sleep(backoff)
                continue
            resp.raise_for_status()
            return resp

    # ------------------------------------------------------------ requests
    async def fetch_json(self, url: str) -> dict:
        """One of this platform's JSON API endpoints (e.g. /api/videos/<id>, parsed by
        pmv_details.py), through the shared rate limit + 429 retry -- see
        _get_with_metadata_rate_limit's docstring."""
        resp = await self._get_with_metadata_rate_limit(url)
        return resp.json()


    async def stream_download(self, url: str, dest_path: Path, progress_cb=None, extra_headers: dict | None = None) -> None:
        """Streams `url` to `dest_path`, calling progress_cb(bytes_done, total_bytes).
        extra_headers lets a caller set e.g. Referer for endpoints that check
        it (some "generate the real download link" endpoints do, to prevent
        the link being hotlinked/shared outside the site's own player).

        Deliberately NOT routed through the metadata rate limiter/retry
        above -- this is for actual file downloads (video/thumbnail/
        preview/funscript), which don't hit the platform's per-account
        metadata rate limit under normal usage, only bursty metadata
        lookups do."""
        client = await self._get_client(self._download_via_proxy())
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")

        headers = {**(extra_headers or {}), **self._api_headers(url)}
        async with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code in (401, 403, 404) and "/api/videos/" in url and url.endswith("/download"):
                raise RuntimeError(
                    f"The site didn't give the file (HTTP {resp.status_code}): it may not be offered for download, "
                    "be private or removed, or need your API key (Server settings → Credentials).")
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length", 0)) or None
            done = 0
            with open(tmp_path, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=self.settings.download.chunk_size_kb * 1024):
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb:
                        progress_cb(done, total)
        tmp_path.replace(dest_path)


# --------------------------------------------------------------- singletons
# One PMVClient instance per configured site, created lazily on first use
# and reused across requests (same lifetime pattern as the old single
# `pmv_client` module-level singleton). get_client() is the only supported
# way to obtain a client -- do not construct PMVClient() directly outside
# this module, or you'll bypass the cache and end up with two separate
# httpx clients for the same site.
_clients: dict[str, PMVClient] = {}


def get_client(site_key: str) -> PMVClient:
    if site_key not in _clients:
        _clients[site_key] = PMVClient(site_key)
    return _clients[site_key]
