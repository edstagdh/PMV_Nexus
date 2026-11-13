"""
Google Gemini, optional (2.33.0): the app's own small client for the Gemini REST API
(https://ai.google.dev/api) -- httpx, no Google SDK. Used only when an admin saves a
Gemini API key (Server settings -> Credentials) and turns on a feature that uses it
(so far: Management -> Songs -> "Ask Gemini (AI) about samples Shazam missed").

Models: the free tier's models, best first. The first request of a run asks Google
which models the key can use (GET /models, free) and refreshes the list: models
Google no longer offers are dropped, newer stable flash / flash-lite models are put
in front (never preview or experimental ones, never paid "pro" ones); the result is
kept for 6 hours. When a model is overloaded (503), rate limited or out of its free
quota (429), not offered (404) or failing (500/504), the next one is tried. When
every model is out, generate() raises GeminiBusy and the caller stops asking for a
while.

The key goes in the x-goog-api-key header, never in a URL, so it can't end up in a log.
Adapted from the Gemini provider of the Incident Bridge project (same author).
"""
from __future__ import annotations

import re
import time
from typing import Iterable, Optional, Sequence

import httpx
from loguru import logger

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT = 90              # a request with a sound clip can take a while
LIST_TIMEOUT = 15
MODEL_LIST_TTL = 6 * 3600

# Free of charge on the free tier, best first (Google's models page, autumn 2026).
# Refreshed from Google's own list on use (see refresh_model_list).
FREE_TIER_MODELS: tuple[str, ...] = (
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
)
FAMILIES = ("flash", "flash-lite")      # the families the free tier may use; also the tie-break order
SKIP_TO_NEXT = {404, 429, 500, 503, 504}

_STABLE_NAME = re.compile(r"^gemini-(\d+(?:\.\d+)*)-(flash-lite|flash|pro)$")
_ANY_NAME = re.compile(r"^gemini-(\d+(?:\.\d+)*)-(flash-lite|flash|pro)(?:-.*)?$")

_models: list[str] = list(FREE_TIER_MODELS)
_models_checked = 0.0


class GeminiError(Exception):
    """Gemini can't be used with this key (refused, not valid, bad request)."""


class GeminiBusy(GeminiError):
    """Every model is overloaded or out of its free quota right now."""


def _headers(key: str) -> dict:
    return {"x-goog-api-key": key, "Content-Type": "application/json"}


def _message(resp: httpx.Response) -> str:
    try:
        return str(((resp.json() or {}).get("error") or {}).get("message") or "")[:300]
    except ValueError:
        return ""


def _refusal(resp: httpx.Response) -> GeminiError:
    msg = _message(resp)
    if resp.status_code == 400 and "API key not valid" in msg:
        return GeminiError("The Gemini API key isn't valid (Server settings → Credentials)")
    if resp.status_code in (401, 403):
        return GeminiError(f"Gemini refused the key ({resp.status_code}){': ' + msg if msg else ''}")
    return GeminiError(f"Gemini answered {resp.status_code}{': ' + msg if msg else ''}")


async def available_models(key: str) -> list[str]:
    """The models this key can generate content with, in Google's order."""
    names: list[str] = []
    token = None
    async with httpx.AsyncClient(timeout=LIST_TIMEOUT) as client:
        for _ in range(10):
            params = {"pageSize": 100, **({"pageToken": token} if token else {})}
            resp = await client.get(f"{BASE_URL}/models", params=params, headers=_headers(key))
            if resp.status_code != 200:
                raise _refusal(resp)
            page = resp.json()
            for entry in page.get("models") or []:
                if "generateContent" in (entry.get("supportedGenerationMethods") or []):
                    names.append(str(entry.get("name", "")).split("/")[-1])
            token = page.get("nextPageToken")
            if not token:
                break
    return names


def _parse(model: str, pattern) -> Optional[tuple[tuple[int, ...], str]]:
    match = pattern.match(model)
    if not match:
        return None
    return tuple(int(p) for p in match.group(1).split(".")), match.group(2)


def refresh_model_list(available: Iterable[str], configured: Sequence[str]) -> list[str]:
    """Drops configured models Google no longer offers and puts newer stable flash /
    flash-lite models in front, newest first. Never empty: if nothing would be left,
    the configured list stays (a gone model is skipped like a busy one anyway)."""
    available = set(available)
    kept = [m for m in configured if m in available]
    newest: dict[str, tuple[int, ...]] = {}
    for m in kept:
        parsed = _parse(m, _ANY_NAME)
        if parsed and parsed[1] in FAMILIES:
            newest[parsed[1]] = max(newest.get(parsed[1], ()), parsed[0])
    candidates = []
    for m in available:
        parsed = _parse(m, _STABLE_NAME)
        if parsed is None or parsed[1] not in FAMILIES or m in kept:
            continue
        version, family = parsed
        if family in newest and version <= newest[family]:
            continue
        candidates.append((version, -FAMILIES.index(family), m))
    models = [m for _, _, m in sorted(candidates, reverse=True)] + kept
    return models or list(configured)


async def models_for(key: str) -> list[str]:
    """The model list, refreshed from Google at most every 6 hours."""
    global _models, _models_checked
    if time.time() - _models_checked > MODEL_LIST_TTL:
        try:
            refreshed = refresh_model_list(await available_models(key), FREE_TIER_MODELS)
            if refreshed != _models:
                logger.info("Gemini models: {}", " > ".join(refreshed))
            _models = refreshed
        except GeminiError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Gemini: couldn't list the models ({}); using the built-in list", exc.__class__.__name__)
        _models_checked = time.time()
    return list(_models)


async def generate(key: str, parts: list[dict], *, system: str = "", config: Optional[dict] = None) -> tuple[str, str]:
    """One generateContent request, trying the models best first. Returns (text, model).
    Raises GeminiBusy when every model is out, GeminiError when the key is refused."""
    body: dict = {"contents": [{"role": "user", "parts": parts}]}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if config:
        body["generationConfig"] = config
    tried = []
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for model in await models_for(key):
            try:
                resp = await client.post(f"{BASE_URL}/models/{model}:generateContent", json=body, headers=_headers(key))
            except httpx.HTTPError as exc:
                tried.append(f"{model}: {exc.__class__.__name__}")
                continue
            if resp.status_code in SKIP_TO_NEXT:
                tried.append(f"{model}: {resp.status_code}")
                continue
            if resp.status_code != 200:
                raise _refusal(resp)
            return _answer_text(resp.json()), model
    logger.info("Gemini: no model could answer ({})", "; ".join(tried))
    raise GeminiBusy("Gemini's free models are busy or out of today's quota — try again later")


def _answer_text(reply: dict) -> str:
    """The text of the first candidate (thought summaries skipped); "" when blocked."""
    candidates = (reply or {}).get("candidates") or []
    content = candidates[0].get("content") if candidates and isinstance(candidates[0], dict) else None
    parts = (content or {}).get("parts") or []
    return "".join(p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str) and not p.get("thought"))


async def test_key(key: str) -> dict:
    """Credentials -> Test key: lists the models (free, uses no quota)."""
    try:
        names = await available_models(key)
    except GeminiError as exc:
        return {"ok": False, "message": str(exc)}
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "message": f"Gemini didn't answer ({exc.__class__.__name__})"}
    use = refresh_model_list(names, FREE_TIER_MODELS)
    return {"ok": True, "message": f"Key works. Models to use, best first: {', '.join(use[:4])}"}
