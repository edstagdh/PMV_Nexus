"""Client for the PMVHaven REST API (https://pmvhaven.com/api, spec in
openapi.json). Public endpoints work without a key; when an API key is saved
in Admin -> Credentials it is sent as `Authorization: Bearer <key>` (needed
for account endpoints such as favorites, and gives the account's own limits).

Every call is spaced at least MIN_GAP_SECONDS apart and 429 / 5xx responses
are retried with backoff (honouring Retry-After)."""
from __future__ import annotations

import asyncio
import time
from typing import Optional

import httpx

from .app_info import APP_VERSION

MIN_GAP_SECONDS = 1.0
MAX_ATTEMPTS = 4
BULK_LIMIT = 100  # /videos/bulk maximum per request


class PMVApiError(RuntimeError):
    pass


class PMVApiClient:
    def __init__(self, settings, site_key: str = "pmvhaven"):
        site = settings.sites.get(site_key)
        self.base = (site.base_url if site else "https://pmvhaven.com").rstrip("/") + "/api"
        self.api_key: Optional[str] = settings.pmvhaven_api_key
        self._client: Optional[httpx.AsyncClient] = None
        self._last_call = 0.0

    async def __aenter__(self) -> "PMVApiClient":
        headers = {"Accept": "application/json", "User-Agent": f"PMVNexus/{APP_VERSION}"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        self._client = httpx.AsyncClient(timeout=30, follow_redirects=True, headers=headers)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()

    async def _get(self, path: str, params: Optional[dict] = None) -> dict:
        assert self._client is not None, "use `async with PMVApiClient(...)`"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            wait = MIN_GAP_SECONDS - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()
            try:
                resp = await self._client.get(self.base + path, params=params)
            except httpx.HTTPError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise PMVApiError(f"Could not reach PMVHaven ({exc.__class__.__name__})") from exc
                await asyncio.sleep(2 * attempt)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == MAX_ATTEMPTS:
                    raise PMVApiError(f"PMVHaven returned HTTP {resp.status_code} (giving up after {attempt} tries)")
                try:
                    delay = float(resp.headers.get("Retry-After", ""))
                except ValueError:
                    delay = 5.0 * attempt
                await asyncio.sleep(min(max(delay, 1.0), 60.0))
                continue
            if resp.status_code in (401, 403):
                raise PMVApiError("PMVHaven rejected the API key -- check Server settings → Credentials")
            if resp.status_code != 200:
                raise PMVApiError(f"PMVHaven returned HTTP {resp.status_code} for {path}")
            try:
                body = resp.json()
            except ValueError as exc:
                raise PMVApiError(f"PMVHaven returned invalid JSON for {path}") from exc
            if isinstance(body, dict) and body.get("success") is False:
                raise PMVApiError(str(body.get("error") or body.get("message") or "request failed"))
            return body
        raise PMVApiError("Unreachable")  # pragma: no cover

    async def bulk_videos(self, ids: Optional[list[str]] = None, old_ids: Optional[list[str]] = None) -> list[dict]:
        """Up to BULK_LIMIT videos by _id or by legacy oldId. Missing ids are
        simply absent from the result."""
        values = ids if ids is not None else old_ids
        if not values:
            return []
        if len(values) > BULK_LIMIT:
            raise ValueError(f"bulk lookup is limited to {BULK_LIMIT} ids")
        key = "ids" if ids is not None else "oldIds"
        body = await self._get("/videos/bulk", {key: ",".join(values)})
        data = body.get("data") if isinstance(body, dict) else None
        return [v for v in (data or []) if isinstance(v, dict)]

    async def get_video(self, video_id: str) -> Optional[dict]:
        body = await self._get(f"/videos/{video_id}")
        return body.get("data") if isinstance(body, dict) else None
