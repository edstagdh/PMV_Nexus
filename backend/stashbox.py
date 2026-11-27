"""FansDB (fansdb.cc) and StashDB (stashdb.org): performer search by hand (3.9.0).

Both run stash-box, so they share one GraphQL API; each has its own endpoint address and its own
API key (Server Settings -> Credentials -> FansDB / StashDB; the key goes in the "ApiKey" header, never in
the address or the log). ThePornDB stays the automatic match: these are only searched when
someone presses "Find on FansDB / StashDB" on a performer's page.

Requests are spaced out (MIN_GAP seconds per site) and wait at most the ThePornDB time
(Server Settings -> Performers -> Wait for ThePornDB). Older stash-box servers lack a few newer
fields (birth_date, cup_size...): the query falls back to the older field names once.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional

import httpx
from loguru import logger

from .config import get_settings
from .i18n import L

BOXES = {
    "fansdb": {"label": "FansDB", "url": "fansdb_url", "key": "fansdb_api_key", "default": "https://fansdb.cc/graphql"},
    "stashdb": {"label": "StashDB", "url": "stashdb_url", "key": "stashdb_api_key", "default": "https://stashdb.org/graphql"},
}
MIN_GAP = 1.0
_locks: dict[str, asyncio.Lock] = {}
_last: dict[str, float] = {}

_COMMON = "id name disambiguation aliases gender country ethnicity eye_color hair_color height breast_type " \
          "career_start_year career_end_year deleted images { url width height } "
FIELDS_NEW = _COMMON + "birth_date death_date cup_size band_size waist_size hip_size scene_count " \
                       "tattoos { location description } piercings { location description } urls { url site { name } }"
FIELDS_OLD = _COMMON + "birthdate { date } measurements { cup_size band_size waist hip } " \
                       "tattoos { location description } piercings { location description } urls { url type }"


class StashBoxError(RuntimeError):
    pass


def _creds() -> dict:
    from . import secrets_store
    return secrets_store.values()


def endpoint(box: str) -> str:
    url = (_creds().get(BOXES[box]["url"]) or BOXES[box]["default"]).strip()
    if not re.match(r"^https?://[^\s/]+(/[^\s]*)?$", url, re.I):
        raise StashBoxError(L("The {site} address isn't a web address (Server Settings → Credentials)", site=BOXES[box]["label"]))
    return url


def configured(box: str) -> bool:
    return bool(_creds().get(BOXES[box]["key"]))


def boxes() -> list[dict]:
    """What the performer page offers: every site, and whether it has a key."""
    creds = _creds()
    return [{"box": b, "label": d["label"], "configured": bool(creds.get(d["key"]))} for b, d in BOXES.items()]


def site_url(box: str, sb_id: str) -> str:
    base = re.sub(r"/graphql/?$", "", endpoint(box).rstrip("/"))
    return f"{base}/performers/{sb_id}"


async def _query(box: str, query: str, variables: dict) -> dict:
    if box not in BOXES:
        raise StashBoxError(L("Unknown site"))
    label = BOXES[box]["label"]
    key = _creds().get(BOXES[box]["key"])
    if not key:
        raise StashBoxError(L("No {site} API key (Server Settings → Credentials → {site})", site=label))
    url = endpoint(box)
    timeout = float(get_settings().performers.tpdb_timeout)
    lock = _locks.setdefault(box, asyncio.Lock())
    async with lock:
        wait = MIN_GAP - (time.monotonic() - _last.get(box, 0.0))
        if wait > 0:
            await asyncio.sleep(wait)
        _last[box] = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=False) as client:
            r = await client.post(url, json={"query": query, "variables": variables},
                                  headers={"ApiKey": key, "Accept": "application/json", "User-Agent": "PMV-Nexus"})
    except httpx.TimeoutException:
        logger.warning("{}: no answer within {}s", label, timeout)
        raise StashBoxError(L("{site} didn't answer within {s} seconds", site=label, s=int(timeout))) from None
    except httpx.HTTPError as exc:
        logger.warning("{}: request failed: {}", label, type(exc).__name__)
        raise StashBoxError(L("{site} couldn't be reached ({error})", site=label, error=type(exc).__name__)) from None
    if r.status_code in (401, 403) or (r.status_code == 422 and "api" in r.text.lower()):
        logger.warning("{}: the API key was refused ({})", label, r.status_code)
        raise StashBoxError(L("{site} refused the API key (check it in Server Settings → Credentials)", site=label))
    try:
        body = r.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        logger.warning("{}: answered {} without JSON: {}", label, r.status_code, r.text[:200])
        raise StashBoxError(L("{site} error {status}", site=label, status=r.status_code))
    errors = body.get("errors") or []
    if errors:
        message = "; ".join(str(e.get("message", e))[:200] for e in errors if isinstance(e, dict)) or str(errors)[:200]
        if re.search(r"not authori[sz]ed|unauthori[sz]ed|forbidden|api ?key", message, re.I):
            logger.warning("{}: the API key was refused: {}", label, message)
            raise StashBoxError(L("{site} refused the API key (check it in Server Settings → Credentials)", site=label))
        raise StashBoxError(message)
    return body.get("data") or {}


async def _with_fields(box: str, template: str, variables: dict) -> dict:
    """Newer field names first; an older server that doesn't know them gets the older ones."""
    try:
        return await _query(box, template.replace("FIELDS", FIELDS_NEW), variables)
    except StashBoxError as exc:
        if not re.search(r"cannot query field|unknown field|field .* (is not|does not) (defined|exist)", str(exc), re.I):
            raise
        logger.info("{}: older server, using the older field names", BOXES[box]["label"])
        return await _query(box, template.replace("FIELDS", FIELDS_OLD), variables)


async def search(box: str, term: str, limit: int = 10) -> list[dict]:
    data = await _with_fields(box, "query($term: String!, $limit: Int) { searchPerformer(term: $term, limit: $limit) { FIELDS } }",
                              {"term": term.strip()[:100], "limit": max(1, min(limit, 25))})
    return [slim(box, p) for p in data.get("searchPerformer") or [] if isinstance(p, dict) and p.get("id") and not p.get("deleted")]


async def performer(box: str, sb_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", sb_id or ""):
        raise StashBoxError(L("Not found on {site}", site=BOXES.get(box, {}).get("label", "")))
    data = await _with_fields(box, "query($id: ID!) { findPerformer(id: $id) { FIELDS } }", {"id": sb_id})
    p = data.get("findPerformer")
    if not isinstance(p, dict) or not p.get("id"):
        raise StashBoxError(L("Not found on {site}", site=BOXES[box]["label"]))
    return p


# ------------------------------------------------------------------ turning an answer into the app's shape
_GENDER = {"FEMALE": "female", "MALE": "male", "TRANSGENDER_FEMALE": "trans", "TRANSGENDER_MALE": "trans"}


def _word(v) -> Optional[str]:
    """MIDDLE_EASTERN -> Middle eastern."""
    return str(v).replace("_", " ").capitalize() if v else None


def _mods(items) -> Optional[str]:
    out = []
    for m in items or []:
        if isinstance(m, dict) and m.get("location"):
            out.append(m["location"] + (f" ({m['description']})" if m.get("description") else ""))
    return "; ".join(out)[:100] or None


def gender(p: dict) -> Optional[str]:
    return _GENDER.get(str(p.get("gender") or ""))


def images(p: dict) -> list[str]:
    imgs = [i for i in p.get("images") or [] if isinstance(i, dict) and str(i.get("url", "")).startswith("https://")]
    return [i["url"] for i in imgs]


def links(p: dict) -> list[dict]:
    out = []
    for u in p.get("urls") or []:
        if not isinstance(u, dict) or not re.match(r"^https?://", str(u.get("url") or ""), re.I):
            continue
        label = ((u.get("site") or {}).get("name") if isinstance(u.get("site"), dict) else None) or u.get("type") or ""
        out.append({"label": str(label)[:40], "url": str(u["url"])[:500]})
    return out


def details(p: dict) -> dict:
    """The profile in the shape of the details typed in by hand (performers.clean_local_details)."""
    m = p.get("measurements") if isinstance(p.get("measurements"), dict) else {}
    cup = p.get("cup_size") or m.get("cup_size")
    band, waist, hip = p.get("band_size") or m.get("band_size"), p.get("waist_size") or m.get("waist"), p.get("hip_size") or m.get("hip")
    measurements = "-".join(str(x) for x in (f"{band or ''}{cup or ''}", waist, hip) if x) if (band or waist or hip) else None
    birthday = p.get("birth_date") or ((p.get("birthdate") or {}).get("date") if isinstance(p.get("birthdate"), dict) else None)
    out = {
        "disambiguation": p.get("disambiguation"), "aliases": [a for a in p.get("aliases") or [] if isinstance(a, str)],
        "birthday": birthday if birthday and re.fullmatch(r"\d{4}-\d{2}-\d{2}", birthday) else None,
        "deathday": p.get("death_date") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(p.get("death_date") or "")) else None,
        "nationality": p.get("country"), "ethnicity": _word(p.get("ethnicity")), "hair_colour": _word(p.get("hair_color")),
        "eye_colour": _word(p.get("eye_color")),
        "height": p.get("height") if isinstance(p.get("height"), int) and 100 <= p["height"] <= 230 else None,
        "measurements": measurements, "cupsize": cup,
        "fake_boobs": {"FAKE": True, "NATURAL": False}.get(str(p.get("breast_type") or "")),
        "tattoos": _mods(p.get("tattoos")), "piercings": _mods(p.get("piercings")),
        "career_start_year": p.get("career_start_year") if isinstance(p.get("career_start_year"), int) and 1950 <= p["career_start_year"] <= 2100 else None,
        "career_end_year": p.get("career_end_year") if isinstance(p.get("career_end_year"), int) and 1950 <= p["career_end_year"] <= 2100 else None,
        "links": links(p),
    }
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def slim(box: str, p: dict) -> dict:
    """A search result."""
    d = details(p)
    imgs = images(p)
    return {"box": box, "id": p["id"], "name": p.get("name"), "disambiguation": p.get("disambiguation"),
            "aliases": d.get("aliases", [])[:12], "gender": gender(p), "birthday": d.get("birthday"),
            "country": p.get("country"), "scenes": p.get("scene_count"), "image": imgs[0] if imgs else None,
            "url": site_url(box, p["id"])}
