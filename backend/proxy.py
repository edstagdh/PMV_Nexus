"""
Using the optional proxy (Admin -> Server settings -> Proxy).

- Where it's used comes from Settings.proxy (see config.ProxyConfig):
  site traffic (off / split / all) and each share destination.
- `via_proxy` (a ContextVar) marks the work running on a proxy download
  connection: the download worker sets it for each download it starts on a
  proxy lane, and PMVClient.stream_download() / the HLS download read it.
- check() tests the proxy (cached CHECK_TTL seconds) so a dead proxy isn't
  used; speed_test() compares the direct connection with the proxy.
"""
from __future__ import annotations

import asyncio
import contextvars
import time
from typing import Optional
from urllib.parse import urlsplit

import httpx
from loguru import logger

from .config import get_settings

via_proxy: contextvars.ContextVar[bool] = contextvars.ContextVar("via_proxy", default=False)

CHECK_TTL = 300  # seconds a check result is trusted
CHECK_TIMEOUT = 12.0
IP_URL = "https://api.ipify.org?format=json"
SPEED_URL = "https://speed.cloudflare.com/__down?bytes={bytes}"
SPEED_BYTES = 10 * 1024 * 1024
SPEED_SECONDS = 15.0  # stop measuring after this long

_last_check: dict = {}
_last_test: dict = {}
_check_lock = asyncio.Lock()


def address() -> Optional[str]:
    return get_settings().proxy.address()


def scheme() -> Optional[str]:
    url = address()
    return urlsplit(url).scheme.lower() if url else None


def display_address() -> Optional[str]:
    """The proxy without its password, for the interface and logs."""
    url = address()
    if not url:
        return None
    p = urlsplit(url)
    return f"{p.scheme}://{p.hostname}{':' + str(p.port) if p.port else ''}"


def for_sites() -> str:
    """off | split | all -- "off" also when no proxy address is set."""
    cfg = get_settings().proxy
    return cfg.site_mode if cfg.address() else "off"


def for_share(destination: str) -> Optional[str]:
    """The proxy URL when uploads to `destination` should use it."""
    cfg = get_settings().proxy
    return cfg.address() if cfg.address() and getattr(cfg, destination, False) else None


def ffmpeg_args(url: Optional[str]) -> Optional[list[str]]:
    """FFmpeg's input option for the proxy, or None when FFmpeg can't use it
    (FFmpeg only speaks to HTTP proxies, not SOCKS)."""
    if not url:
        return []
    if urlsplit(url).scheme.lower() in ("http", "https"):
        return ["-http_proxy", url]
    return None


def pyrogram_proxy(url: Optional[str]) -> Optional[dict]:
    """The proxy in Pyrogram's form (socks4, socks5 or http)."""
    if not url:
        return None
    p = urlsplit(url)
    sch = {"socks5h": "socks5", "socks4a": "socks4", "https": "http"}.get(p.scheme.lower(), p.scheme.lower())
    out = {"scheme": sch, "hostname": p.hostname, "port": p.port or (1080 if sch.startswith("socks") else 8080)}
    if p.username:
        from urllib.parse import unquote
        out["username"] = unquote(p.username)
        out["password"] = unquote(p.password or "")
    return out


def _client(url: Optional[str], timeout: float = CHECK_TIMEOUT) -> httpx.AsyncClient:
    return httpx.AsyncClient(proxy=url, timeout=httpx.Timeout(timeout), follow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0 (compatible; pmv-download-manager/1.0)"})


async def _probe(url: Optional[str]) -> dict:
    """Reachability through `url` (None = direct): the address the world sees
    and how long a request to the main site takes."""
    out: dict = {"ok": False}
    try:
        async with _client(url) as client:
            started = time.monotonic()
            r = await client.get(IP_URL)
            r.raise_for_status()
            out["ip"] = r.json().get("ip")
            out["latency_ms"] = round((time.monotonic() - started) * 1000)
            out["ok"] = True
            site = get_settings().sites.get("pmvhaven")
            if site:
                try:
                    started = time.monotonic()
                    r = await client.head(site.base_url)
                    out["site_ms"] = round((time.monotonic() - started) * 1000)
                    out["site_status"] = r.status_code
                except Exception as exc:  # noqa: BLE001 -- the connection works; the site didn't answer
                    out["site_error"] = _describe(exc)
    except Exception as exc:  # noqa: BLE001
        out["error"] = _describe(exc)
    return out


def _describe(exc: Exception) -> str:
    text = str(exc)
    if not text:
        text = {"ConnectError": "Couldn't connect", "ConnectTimeout": "Timed out connecting", "ReadTimeout": "Timed out waiting for an answer",
                "RemoteProtocolError": "The connection was closed unexpectedly"}.get(exc.__class__.__name__, exc.__class__.__name__)
    if isinstance(exc, httpx.ProxyError) or "407" in text:
        return f"The proxy refused the connection ({text}) -- check its address and login"
    if "socksio" in text or "SOCKS support" in text:
        return "SOCKS proxies need the 'socksio' package (pip install -r requirements.txt)"
    return text[:300]


async def check(force: bool = False) -> dict:
    """{ok, ip, latency_ms, error, checked_at} for the proxy, cached for CHECK_TTL."""
    url = address()
    if not url:
        return {"ok": False, "error": "No proxy address set", "checked_at": time.time()}
    async with _check_lock:
        if not force and _last_check.get("url") == url and time.time() - _last_check.get("checked_at", 0) < CHECK_TTL:
            return _last_check
        result = await _probe(url)
        result.update(url=url, checked_at=time.time())
        if not result["ok"]:
            logger.warning("Proxy {} isn't working: {}", display_address(), result.get("error"))
        _last_check.clear()
        _last_check.update(result)
        return result


async def usable() -> bool:
    """May the proxy be used right now? Checks it first when the settings ask for that."""
    if not address():
        return False
    if not get_settings().proxy.check_before_use:
        return True
    return (await check()).get("ok", False)


async def _speed(url: Optional[str]) -> dict:
    out: dict = {}
    try:
        async with _client(url, timeout=SPEED_SECONDS + 5) as client:
            started = time.monotonic()
            got = 0
            async with client.stream("GET", SPEED_URL.format(bytes=SPEED_BYTES)) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(256 * 1024):
                    got += len(chunk)
                    if time.monotonic() - started > SPEED_SECONDS:
                        break
            secs = max(0.001, time.monotonic() - started)
            out = {"mbps": round(got * 8 / secs / 1_000_000, 1), "mb": round(got / 1_048_576, 1), "seconds": round(secs, 1)}
    except Exception as exc:  # noqa: BLE001
        out = {"error": _describe(exc)}
    return out


async def speed_test() -> dict:
    """Direct connection vs proxy: reachability, address seen, latency and
    download speed (about 10 MB each). Also refreshes the check cache."""
    url = address()
    if not url:
        return {"error": "No proxy address set"}
    direct, via = await asyncio.gather(_probe(None), _probe(url))
    direct_speed = await _speed(None) if direct["ok"] else {}
    proxy_speed = await _speed(url) if via["ok"] else {}
    direct.update(direct_speed)
    via.update(proxy_speed)
    verdict, level = _verdict(direct, via)
    result = {"direct": direct, "proxy": via, "verdict": verdict, "level": level, "tested_at": time.time(),
              "address": display_address()}
    async with _check_lock:
        _last_check.clear()
        _last_check.update({**{k: via.get(k) for k in ("ok", "ip", "latency_ms", "error")}, "url": url, "checked_at": time.time()})
    _last_test.clear()
    _last_test.update(result)
    logger.info("Proxy speed test: {}", verdict)
    return result


def _verdict(direct: dict, via: dict) -> tuple[str, str]:
    if not via.get("ok"):
        return f"The proxy doesn't work: {via.get('error', 'no answer')}", "bad"
    if direct.get("ip") and direct.get("ip") == via.get("ip"):
        return "The proxy answers, but the sites see the same address as without it — it isn't hiding or splitting anything.", "warn"
    if via.get("site_error") and not direct.get("site_error"):
        return f"The proxy works, but PMVHaven doesn't answer through it ({via['site_error']}).", "bad"
    if via.get("error"):
        return f"The proxy works, but the speed test failed: {via['error']}", "warn"
    d, p = direct.get("mbps"), via.get("mbps")
    if d and p is not None:
        share = p / d
        if share < 0.25:
            return f"The proxy works but is slow: {p} Mbit/s vs {d} direct. Downloads through it would hold things up.", "warn"
        if share < 0.6:
            return f"The proxy works at about {round(share * 100)}% of the direct speed ({p} vs {d} Mbit/s). Fine for a second download.", "ok"
        return f"The proxy works well: {p} Mbit/s (direct {d}).", "ok"
    return "The proxy works.", "ok"


def status() -> dict:
    cfg = get_settings().proxy
    return {
        "configured": bool(cfg.address()), "address": display_address(), "scheme": scheme(),
        "last_check": {k: v for k, v in _last_check.items() if k != "url"} or None,
        "last_test": dict(_last_test) or None,
        "ffmpeg_ok": ffmpeg_args(cfg.address()) is not None,
    }
