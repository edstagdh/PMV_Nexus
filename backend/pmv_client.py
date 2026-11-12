"""
Handles the PMVHaven-platform side of things: logging in (via a real
headless browser, since these sites are JS SPAs and may use CSRF tokens /
dynamic forms that are awkward to replicate with raw HTTP), persisting
that session to disk, and reusing it for plain HTTP scraping + downloads
via httpx (much cheaper than running a browser for every request).

One PMVClient instance = one site (PMVHaven, porntubeai.com, ...) -- each
site is a different domain with its own cookie jar and saved session file,
even though (per the user) they share the same login email/password. Use
get_client(site_key) to get/create the instance for a given site rather
than constructing PMVClient() directly; see the module-level singleton
cache at the bottom of this file.
"""
from __future__ import annotations

import asyncio
import json
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

# Best-effort selectors for the login form. Confirmed identical across
# every site on this platform (PMVHaven, porntubeai.com) as of writing --
# if login starts failing for one specific site, this is the first place
# to look (the site's markup may have diverged).
EMAIL_SELECTORS = [
    'input[type="email"]',
    'input[name="email"]',
    'input[autocomplete="email"]',
]
PASSWORD_SELECTORS = [
    'input[type="password"]',
    'input[name="password"]',
]
SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'button:has-text("Log in")',
    'button:has-text("Sign in")',
    'button:has-text("Login")',
]


class PMVLoginError(RuntimeError):
    pass


class PMVClient:
    # -------------------------------------------------- metadata rate limiting
    # Enforced per-site (each PMVClient instance is one site) -- PMVHaven's
    # own per-account rate limit on metadata lookups ("429 Too many video
    # requests. Please wait a moment.") applies independently to each site
    # you're logged into. These constants ONLY apply to fetch_page()/
    # fetch_json() (see _get_with_metadata_rate_limit's docstring below) --
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
        # The same session through the proxy (Server settings -> Proxy), made on first use.
        self._proxy_client: Optional[httpx.AsyncClient] = None
        self._proxy_client_url: Optional[str] = None
        self._lock = asyncio.Lock()

        # Shared across every caller of fetch_page()/fetch_json() for THIS
        # site (repair workers, the metadata-fetch worker pool, etc.) -- a
        # single lock + timestamp gate, NOT a per-worker sleep, so it
        # correctly spaces out requests even when several workers try to
        # fire at the exact same moment. See _throttle_metadata_request().
        self._metadata_rate_lock = asyncio.Lock()
        self._last_metadata_request_at = 0.0

    @property
    def storage_state_file(self) -> Path:
        """Saved cookies/session for THIS site specifically, under its own
        subfolder of the shared session_dir -- e.g.
        data/pmv_session/pmvhaven/storage_state.json and
        data/pmv_session/porntubeai/storage_state.json side by side. Each
        site is a different domain, so its cookies must be kept separate
        even though the login email/password are shared."""
        d = self.settings.session_dir / self.site_key
        d.mkdir(parents=True, exist_ok=True)
        return d / "storage_state.json"

    # ---------------------------------------------------------------- login
    def has_saved_session(self) -> bool:
        return self.storage_state_file.exists()

    async def login(self) -> None:
        from playwright.async_api import async_playwright

        email, password = self.settings.login_for(self.site_key)
        if not email or not password:
            label = self.site.label or self.site_key
            raise PMVLoginError(
                f"The {label} email / password are not set -- add them in Server settings \u2192 Credentials"
            )

        base_url = self.site.base_url.rstrip("/")
        login_url = base_url + self.site.login_path

        launch_args = {}
        if proxy.for_sites() == "all":  # everything to the sites goes through the proxy, sign-in too
            from urllib.parse import unquote, urlsplit, urlparse
            parts = urlsplit(proxy.address())
            launch_args["proxy"] = {"server": f"{parts.scheme}://{parts.hostname}:{parts.port}",
                                    **({"username": unquote(parts.username), "password": unquote(parts.password or "")}
                                       if parts.username else {})}
        async with async_playwright() as p:
            browser = await p.firefox.launch(headless=True, **launch_args)
            context = await browser.new_context()
            page = await context.new_page()
            try:
                await page.goto(login_url, wait_until="networkidle")

                # Dismiss the age-verification overlay if it's present, since it
                # blocks clicks on the login form underneath. Same overlay
                # across every site on this platform.
                await self._dismiss_age_gate(page)

                email_field = await self._first_visible(page, EMAIL_SELECTORS)
                password_field = await self._first_visible(page, PASSWORD_SELECTORS)
                if not email_field or not password_field:
                    raise PMVLoginError(
                        f"Could not find the login form fields on {login_url}. "
                        f"This site's markup may have changed -- update the "
                        f"selectors in backend/pmv_client.py."
                    )

                await email_field.fill(email)
                await password_field.fill(password)

                submit = await self._first_visible(page, SUBMIT_SELECTORS)
                if submit:
                    await submit.click()
                else:
                    await password_field.press("Enter")

                await self._verify_login(page, base_url)

                self.storage_state_file.parent.mkdir(parents=True, exist_ok=True)
                await context.storage_state(path=str(self.storage_state_file))
            finally:
                await context.close()
                await browser.close()

        # force the httpx client to rebuild with fresh cookies
        await self.close()

    @staticmethod
    async def _first_visible(page, selectors: list[str]):
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    return el
            except Exception:
                continue
        return None

    @staticmethod
    async def _dismiss_age_gate(page, timeout_ms: int = 3000) -> None:
        """Clicks through the age-verification modal if it appears.
        Non-fatal if it's not present (e.g. already confirmed this session).
        Same overlay/selector across every site on this platform."""
        try:
            confirm_btn = await page.wait_for_selector(
                "button.btn-confirm", timeout=timeout_ms, state="visible"
            )
            await confirm_btn.click()
            # give the overlay time to animate/unmount before continuing
            await page.wait_for_selector(
                ".age-verification-overlay", state="hidden", timeout=timeout_ms
            )
        except Exception:
            logger.debug("Age-verification overlay not found or already dismissed.")

    async def _verify_login(self, page, base_url: str, timeout_ms: int = 15000) -> None:
        """Confirms login succeeded by waiting for the post-login redirect to
        the homepage. Takes a couple seconds to redirect after a successful
        login, so this actively waits rather than checking page.url once
        (which can race the redirect)."""
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        homepage_pattern = f"{base_url}/"

        try:
            await page.wait_for_url(homepage_pattern, timeout=timeout_ms)
            logger.info("Login confirmed for {}: redirected to homepage.", self.site_key)
            return
        except PlaywrightTimeoutError:
            pass  # fall through to failure handling below

        # Didn't redirect in time -- login likely failed. Try to surface why.
        error_text = None
        for sel in ['[role="alert"]', ".error-text", ".text-red-500", ".text-red-400"]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    error_text = (await el.inner_text()).strip()
                    if error_text:
                        break
            except Exception:
                continue

        debug_dir = self.settings.session_dir / self.site_key / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        shot_path = debug_dir / "login_fail.png"
        await page.screenshot(path=str(shot_path))

        raise PMVLoginError(
            f"Login to {self.site_key} did not redirect to the homepage within {timeout_ms}ms "
            f"(still on {page.url}). "
            f"{'Site said: ' + error_text if error_text else ''} "
            f"Screenshot saved to {shot_path} for debugging."
        )

    def _api_headers(self, url: str) -> dict:
        """The user's own API key (Server settings → Credentials → PMVHaven API) on
        requests to the site's API, so the site knows who is calling (2.27.0)."""
        key = self.settings.pmvhaven_api_key
        if key and urlparse(url).path.startswith("/api/"):
            return {"Authorization": f"Bearer {key}"}
        return {}

    # --------------------------------------------------------- httpx client
    def _new_client(self, proxy_url: Optional[str] = None) -> httpx.AsyncClient:
        cookies = httpx.Cookies()
        state_file = self.storage_state_file
        if state_file.exists():
            data = json.loads(state_file.read_text(encoding="utf-8"))
            for c in data.get("cookies", []):
                cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
        return httpx.AsyncClient(
            cookies=cookies,
            headers={"User-Agent": "Mozilla/5.0 (compatible; PMVNexus/1.0)"},
            timeout=httpx.Timeout(30.0, read=60.0),
            follow_redirects=True,
            proxy=proxy_url,
        )

    async def _get_client(self, use_proxy: Optional[bool] = None) -> httpx.AsyncClient:
        """The signed-in client: direct, or through the proxy when
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
        """Shared GET plumbing for fetch_page()/fetch_json() -- this site's
        page HTML and its v2 JSON metadata API, i.e. every metadata/repair
        lookup this app makes for this site. Deliberately NOT used by
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
    async def fetch_page(self, url: str) -> str:
        resp = await self._get_with_metadata_rate_limit(url)
        return resp.text


    async def fetch_json(self, url: str) -> dict:
        """Same as fetch_page(), but for this platform's JSON API endpoints
        (e.g. /api/videos/<id>) instead of HTML pages -- used by
        scraper_v2's metadata parsing. Reuses the same authenticated httpx
        client (cookies from this site's saved session), since the JSON
        API appears to honor the same session as the regular site. Goes
        through the same shared rate limit + 429 retry as fetch_page() --
        see _get_with_metadata_rate_limit's docstring."""
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
# httpx clients/cookie jars for the same site.
_clients: dict[str, PMVClient] = {}


def get_client(site_key: str) -> PMVClient:
    if site_key not in _clients:
        _clients[site_key] = PMVClient(site_key)
    return _clients[site_key]


# Back-compat alias: anything that still imports the old bare `pmv_client`
# singleton gets the PMVHaven client specifically, rather than crashing at
# import time. New code (library_manager.py, main.py) should use
# get_client(item.site) / get_client(site_key) instead so it works for
# every configured site, not just PMVHaven.
pmv_client = get_client("pmvhaven")