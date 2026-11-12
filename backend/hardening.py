"""
Hardening (2.11.0) for an app reached over a LAN / VPN, usually plain http.

SecurityMiddleware (pure ASGI, wraps every request):
  - Security headers on every response: a Content-Security-Policy (scripts,
    styles and connections only from this app; no plugins, no framing),
    nosniff (a file is never run as something other than its declared type --
    an uploaded video can't be turned into a page), no framing, no referrer
    to other sites, no camera/mic/location, same-origin window/resource policy.
  - API responses are never cached by the browser unless the route says so
    (videos, thumbnails and previews set their own caching).
  - Cross-site requests are refused for anything that changes data: a
    POST/PUT/PATCH/DELETE whose Origin (or Referer) isn't this app, or that the
    browser marks Sec-Fetch-Site: cross-site, gets 403. On a LAN, another web
    app on the same machine counts as "same site" for cookies, so the
    SameSite cookie alone wouldn't stop it. /api/external/* (API key, called
    by the browser extension from PMVHaven pages) is exempt.
  - Request bodies are capped (MAX_BODY) except the file upload routes, which
    stream to disk with their own limits.

LoginThrottle: after FAILS_BEFORE_LOCK failed sign-ins for a username (or
FAILS_BEFORE_LOCK_IP from one address) within WINDOW seconds, that username/address must wait (doubling
up to MAX_LOCK). Kept in memory (a restart clears it).

scrub_item(): what people who don't manage videos (no videos.edit / videos.maintain) get for a video --
server file paths become just the file name, and who added it / the site's
original values are left out.
"""
from __future__ import annotations

import json
import time
from typing import Optional
from urllib.parse import urlsplit

MAX_BODY = 2 * 1024 * 1024  # bytes, for JSON and form requests
_UNLIMITED_BODY = ("/api/library/local/", "/api/backups/upload")
_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
_CROSS_SITE_OK = ("/api/external/",)

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",      # Vue style bindings and the player's inline styles
    "img-src 'self' data: blob: https:",      # ThePornDB candidate pictures are shown from their site
    "media-src 'self' blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
_HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
]


def _host_of(value: str) -> Optional[str]:
    try:
        parts = urlsplit(value)
        return parts.netloc.lower() if parts.netloc else None
    except ValueError:
        return None


async def _send_json(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), *_HEADERS]})
    await send({"type": "http.response.body", "body": body})


class SecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        method = scope["method"]
        path = scope.get("path", "")
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}

        if method in _UNSAFE and not path.startswith(_CROSS_SITE_OK):
            host = (headers.get("host") or "").lower()
            origin = headers.get("origin")
            source = _host_of(origin) if origin and origin != "null" else _host_of(headers.get("referer") or "")
            if headers.get("sec-fetch-site") == "cross-site" or origin == "null" or (source and source != host):
                return await _send_json(send, 403, "Request from another site refused")

        limited = method in _UNSAFE and not path.startswith(_UNLIMITED_BODY) \
            and not path.endswith(("/thumbnail/image", "/photos/upload", "/cover/image"))  # stream with their own limits
        if limited:
            try:
                if int(headers.get("content-length") or 0) > MAX_BODY:
                    return await _send_json(send, 413, "Request too large")
            except ValueError:
                return await _send_json(send, 400, "Bad Content-Length")
            seen = 0

            async def receive_limited():
                nonlocal seen
                message = await receive()
                if message["type"] == "http.request":
                    seen += len(message.get("body") or b"")
                    if seen > MAX_BODY:
                        raise ValueError("Request body too large")
                return message
            inner_receive = receive_limited
        else:
            inner_receive = receive

        is_api = path.startswith("/api/")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                hdrs = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"server"]
                names = {k.lower() for k, _ in hdrs}
                hdrs += [h for h in _HEADERS if h[0] not in names]
                if is_api and b"cache-control" not in names:
                    hdrs.append((b"cache-control", b"no-store"))
                message = {**message, "headers": hdrs}
            await send(message)

        return await self.app(scope, inner_receive, send_with_headers)


# ------------------------------------------------------------------ sign-in throttle
FAILS_BEFORE_LOCK = 5          # per username
FAILS_BEFORE_LOCK_IP = 20      # per address (several people can share one, e.g. behind a router)
WINDOW = 15 * 60
FIRST_LOCK = 60
MAX_LOCK = 30 * 60


class LoginThrottle:
    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = {}
        self._locked: dict[str, tuple[float, int]] = {}  # key -> (until, how many locks so far)

    def wait_seconds(self, *keys: str) -> int:
        now = time.time()
        return max([int(until - now) + 1 for k in keys if (until := self._locked.get(k, (0, 0))[0]) > now] or [0])

    def failed(self, *keys: str) -> None:
        now = time.time()
        for k in keys:
            recent = [t for t in self._fails.get(k, []) if now - t < WINDOW] + [now]
            self._fails[k] = recent
            if len(recent) >= (FAILS_BEFORE_LOCK_IP if k.startswith("ip:") else FAILS_BEFORE_LOCK):
                _, n = self._locked.get(k, (0, 0))
                self._locked[k] = (now + min(MAX_LOCK, FIRST_LOCK * 2 ** n), n + 1)
                self._fails[k] = []
        if len(self._fails) > 10000:  # don't grow forever under a flood
            self._fails = {k: v for k, v in self._fails.items() if v and now - v[-1] < WINDOW}

    def succeeded(self, *keys: str) -> None:
        for k in keys:
            self._fails.pop(k, None)
            self._locked.pop(k, None)


login_throttle = LoginThrottle()


# ------------------------------------------------------------------ data for regular users
_PATH_FIELDS = ("video_path", "funscript_path", "metadata_path", "thumbnail_path", "preview_path")
_PRIVATE_FIELDS = ("added_by", "site_values", "error_debug", "_meta", "_meta_raw")


def scrub_item(item: Optional[dict], user) -> Optional[dict]:
    """A video as a regular user may see it: file names, not server paths;
    nothing about who added it or the site's original values."""
    if not item or user is None or any(user.has_permission(p) for p in ("videos.edit", "videos.maintain", "settings.manage")):
        return item
    out = dict(item)
    for f in _PATH_FIELDS:
        if out.get(f):
            out[f] = str(out[f]).replace("\\", "/").rsplit("/", 1)[-1]
    for f in _PRIVATE_FIELDS:
        out.pop(f, None)
    return out
