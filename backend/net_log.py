"""Every HTTP request the app sends out, in the app's log (3.9.0; Admin -> Logs, category
"network").

install() wraps httpx's Client.send / AsyncClient.send once at start, so every caller is
covered -- ThePornDB, FansDB/StashDB, PMVHaven, Gemini, VirusTotal, Filester, the update check,
language downloads, performer photos, extensions -- without each one doing it:

  - an answer of 400 or more, a timeout, or a connection that failed: WARNING (404: INFO)
  - one that took longer than SLOW seconds: INFO
  - everything else: DEBUG (Logging level "Debug" shows every call)

Logged: method, host, path, the query's parameter names (values of anything that looks like a
key, token, password or signature are left out), status, time and size. Never headers or bodies.
The caller still gets the same answer or exception; logging can't fail a request.
"""
from __future__ import annotations

import re
import time

import httpx
from loguru import logger

SLOW = 20.0
_SECRET = re.compile(r"key|token|secret|pass|auth|sig|session|cookie|credential|code", re.I)
_log = logger.bind(category="network", event="network.out")
_installed = False


def describe(request: httpx.Request) -> str:
    url = request.url
    params = []
    for k, v in url.params.multi_items():
        params.append(f"{k}=…" if _SECRET.search(k) else f"{k}={v[:60]}")
    return f"{request.method} {url.host}{url.path}" + (("?" + "&".join(params)) if params else "")


def _size(response: httpx.Response) -> str:
    n = response.headers.get("content-length")
    if not n or not n.isdigit():
        return ""
    n = int(n)
    return f", {n / 1048576:.1f} MB" if n >= 1048576 else f", {n / 1024:.0f} KB" if n >= 1024 else f", {n} bytes"


def _done(request: httpx.Request, started: float, response=None, error: BaseException | None = None) -> None:
    try:
        took = time.monotonic() - started
        what = describe(request)
        if error is not None:
            kind = "no answer in time" if isinstance(error, httpx.TimeoutException) else type(error).__name__
            _log.warning("Out: {} failed after {:.1f}s: {} {}", what, took, kind, str(error)[:200])
            return
        code = response.status_code
        line = f"Out: {what} -> {code} in {took:.1f}s{_size(response)}"
        if code == 404:
            _log.info(line)
        elif code >= 400:
            _log.warning(line)
        elif took >= SLOW:
            _log.info(line + " (slow)")
        else:
            _log.debug(line)
    except Exception:  # noqa: BLE001 -- logging never breaks a request
        pass


def install() -> None:
    """Wraps httpx's send() once (idempotent)."""
    global _installed
    if _installed:
        return
    _installed = True
    sync_send = httpx.Client.send
    async_send = httpx.AsyncClient.send

    def send(self, request, *args, **kwargs):
        started = time.monotonic()
        try:
            response = sync_send(self, request, *args, **kwargs)
        except BaseException as exc:
            if not isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                _done(request, started, error=exc)
            raise
        _done(request, started, response)
        return response

    async def asend(self, request, *args, **kwargs):
        started = time.monotonic()
        try:
            response = await async_send(self, request, *args, **kwargs)
        except BaseException as exc:
            import asyncio
            if not isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit, GeneratorExit)):
                _done(request, started, error=exc)
            raise
        _done(request, started, response)
        return response

    httpx.Client.send = send
    httpx.AsyncClient.send = asend
