"""
ThePornDB API (https://api.theporndb.net) -- performers only: search,
profile, similar performers, photos. The API key is a credential
(Server settings -> Credentials -> ThePornDB) sent as a Bearer token.
Requests are spaced out (MIN_GAP seconds) so a library-wide match doesn't
hammer the API.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from . import images
from .config import get_settings

BASE_URL = "https://api.theporndb.net"
SITE_URL = "https://theporndb.net/performers/"
MIN_GAP = 1.0      # at least this long between two requests, app-wide
MAX_GAP = 5.0      # ...raised step by step (to this at most) after "too many requests"
RETRIES_429 = 3    # a 429 is waited out (Retry-After, capped) and retried this many times
MAX_WAIT_429 = 120.0
_gap = MIN_GAP

_lock = asyncio.Lock()
_last = 0.0


class TPDBError(RuntimeError):
    pass


def configured() -> bool:
    return bool(get_settings().tpdb_api_key)


async def _get(path: str, params: Optional[dict] = None) -> dict:
    global _last
    key = get_settings().tpdb_api_key
    if not key:
        raise TPDBError("No ThePornDB API key (Server settings → Credentials → ThePornDB)")
    global _gap
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json",
               "User-Agent": "PMV-Nexus"}
    for attempt in range(RETRIES_429 + 1):
        async with _lock:
            wait = _gap - (time.monotonic() - _last)
            if wait > 0:
                await asyncio.sleep(wait)
            _last = time.monotonic()
        try:
            async with httpx.AsyncClient(base_url=BASE_URL, headers=headers, timeout=httpx.Timeout(30.0),
                                         follow_redirects=True) as client:
                r = await client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise TPDBError(f"ThePornDB didn't answer: {exc or exc.__class__.__name__}") from exc
        if r.status_code != 429 or attempt == RETRIES_429:
            break
        # Too many requests: slow down for good (until restart) and wait as long as it asks.
        _gap = min(MAX_GAP, _gap * 2)
        try:
            pause = float(r.headers.get("Retry-After", ""))
        except ValueError:
            pause = 0.0
        pause = min(MAX_WAIT_429, max(pause, 10.0 * (attempt + 1)))
        logger.warning("ThePornDB: too many requests -- waiting {:.0f}s, then 1 request per {:.0f}s", pause, _gap)
        async with _lock:  # every other ThePornDB request waits too
            await asyncio.sleep(pause)
            _last = time.monotonic()
    if r.status_code in (401, 403):
        raise TPDBError("ThePornDB refused the API key (check it in Server settings → Credentials)")
    if r.status_code == 404:
        raise TPDBError("Not found on ThePornDB")
    if r.status_code == 429:
        raise TPDBError("ThePornDB says too many requests — try again in a while")
    if r.status_code >= 400:
        raise TPDBError(f"ThePornDB error {r.status_code}: {r.text[:200]}")
    return r.json()


def aliases(p: dict) -> list[str]:
    out = []
    for a in p.get("aliases") or []:
        name = a.get("name") if isinstance(a, dict) else a
        if name and isinstance(name, str):
            out.append(name.strip())
    return out


def slim(p: dict) -> dict:
    """The parts of a performer this app uses (search results, candidates)."""
    extras = p.get("extras") or {}
    return {
        "tpdb_id": p.get("id"), "name": p.get("name"), "disambiguation": p.get("disambiguation"),
        "image": p.get("face") or p.get("thumbnail") or p.get("image"), "aliases": aliases(p)[:12],
        "gender": extras.get("gender"), "birthplace": extras.get("birthplace"), "birthday": extras.get("birthday"),
        "url": SITE_URL + (p.get("slug") or p.get("id") or ""),
    }


async def search(name: str, limit: int = 10) -> list[dict]:
    data = await _get("/performers", {"q": name, "per_page": limit})
    return [p for p in (data.get("data") or []) if isinstance(p, dict) and p.get("id")]


async def performer(tpdb_id: str) -> dict:
    data = await _get(f"/performers/{tpdb_id}")
    p = data.get("data")
    if not isinstance(p, dict) or not p.get("id"):
        raise TPDBError("ThePornDB returned no profile")
    return p


async def similar(tpdb_id: str) -> list[dict]:
    try:
        data = await _get(f"/performers/{tpdb_id}/similar")
    except TPDBError:
        return []
    raw = data.get("data")
    items = raw if isinstance(raw, list) else [raw] if isinstance(raw, dict) else []
    return [{"tpdb_id": p.get("id"), "name": p.get("name")} for p in items if isinstance(p, dict) and p.get("id")]


async def download_photo(url: str, dest_base: Path) -> Optional[Path]:
    """Saves the photo as dest_base.webp (2.15.0, see images.py; a file that
    can't be converted keeps its own format); None if it fails."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
    except httpx.HTTPError:
        return None
    ctype = r.headers.get("content-type", "").split(";")[0].strip()
    content = r.content
    try:
        content = await asyncio.to_thread(images.to_webp, content)
        ext = ".webp"
    except ValueError:
        ext = {"image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(ctype, ".jpg")
    dest = dest_base.with_suffix(ext)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(content)
    tmp.replace(dest)
    for old in dest.parent.glob(dest_base.name + ".*"):
        if old != dest and not old.name.endswith(".part"):
            old.unlink(missing_ok=True)
    return dest
