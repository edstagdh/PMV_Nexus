"""Languages (3.7.0): which are installed, their texts, and getting more.

Language files live in the app's GitHub repository (languages/*.json, listed with their size and
SHA-256 in languages/manifest.json). The server downloads them -- only from that fixed address --
checks size and hash against the manifest, then checks every entry (backend/i18n.py validate)
before saving it in data/languages. A file can also be installed by uploading it (no internet
needed, or to try a translation before publishing it). Installing and removing is for admins;
everyone picks from the installed ones in My Settings -> Language.
"""
from __future__ import annotations

import json
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from loguru import logger
from pydantic import BaseModel, Field

from .. import i18n
from ..app_info import REPO_NAME, REPO_OWNER
from ..config import get_settings, save_overrides
from ..i18n import L
from ..library_manager import library_manager
from ..security import AuthContext, require_permission

router = APIRouter()

RAW_BASE = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/HEAD/languages/"
MANIFEST_URL = RAW_BASE + "manifest.json"
FILE_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?\.json$")
TIMEOUT = 20.0


def _summary(lang: dict) -> dict:
    info, installed = lang["language"], lang.get("installed") or {}
    report = installed.get("report") or {}
    return {**info, "translated": report.get("translated", len(lang["messages"])), "total": report.get("total", 0),
            "source": installed.get("source"), "installed_at": installed.get("at"), "sha256": installed.get("sha256")}


def _default() -> str:
    code = get_settings().language.default
    return code if code == "en" or code in i18n.installed() else "en"


def _changed() -> None:
    library_manager.db._note_change(["languages"])


@router.get("/api/i18n/boot")
def boot():
    """Installed languages and the default -- no sign-in needed: the sign-in page uses it."""
    return {"default": _default(), "languages": [_summary(lang) for lang in i18n.installed().values()]}


@router.get("/api/i18n/lang/{code}.json")
def language_file(code: str, request: Request):
    lang = i18n.installed().get(code)
    if not lang:
        raise HTTPException(status_code=404, detail=L("That language isn't installed"))
    body = json.dumps({"language": lang["language"], "messages": lang["messages"]}, ensure_ascii=False).encode("utf-8")
    etag = '"' + i18n.sha256(body)[:32] + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return Response(body, media_type="application/json", headers={"ETag": etag, "Cache-Control": "no-cache"})


async def _get(url: str, limit: int) -> bytes:
    """GET with a size limit (the manifest says how big a file should be; never trust more)."""
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        async with client.stream("GET", url, headers={"Cache-Control": "no-cache"}) as resp:
            if resp.status_code == 404:
                raise HTTPException(status_code=502, detail=L("GitHub doesn't have that file (yet)"))
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=L("GitHub answered {status}", status=resp.status_code))
            data = bytearray()
            async for chunk in resp.aiter_bytes():
                data += chunk
                if len(data) > limit:
                    raise HTTPException(status_code=502, detail=L("The file from GitHub is bigger than expected"))
            return bytes(data)


async def _manifest() -> list[dict]:
    try:
        raw = await _get(MANIFEST_URL, 256_000)
    except HTTPException as exc:
        if "(yet)" in str(exc.detail):
            raise HTTPException(status_code=502, detail=L("No languages are published on GitHub yet")) from exc
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=L("Couldn't reach GitHub ({error})", error=type(exc).__name__)) from exc
    try:
        entries = json.loads(raw.decode("utf-8")).get("languages") or []
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=502, detail=L("GitHub's list of languages couldn't be read")) from exc
    return [e for e in entries if isinstance(e, dict) and FILE_RE.match(str(e.get("file") or ""))
            and i18n.CODE_RE.match(str(e.get("code") or "")) and isinstance(e.get("sha256"), str)
            and isinstance(e.get("size"), int) and 0 < e["size"] <= i18n.MAX_FILE_BYTES]


@router.get("/api/i18n/available")
async def available(user: AuthContext = Depends(require_permission("settings.manage"))):
    """The languages on GitHub, and which are installed / have an update."""
    entries = await _manifest()
    installed = i18n.installed()
    out = []
    for e in entries:
        have = installed.get(e["code"])
        have_sha = (have or {}).get("installed", {}).get("sha256")
        out.append({
            "code": e["code"], "name": str(e.get("name") or e["code"])[:60], "native": str(e.get("native") or "")[:60],
            "direction": "rtl" if e.get("direction") == "rtl" else "ltr", "version": str(e.get("version") or "")[:20],
            "size": e["size"], "translated": e.get("translated"), "total": e.get("total"),
            "status": "new" if not have else "installed" if have_sha == e["sha256"] else "update",
        })
    return {"languages": out}


class InstallRequest(BaseModel):
    code: str = Field(..., pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


def _store(raw: bytes, source: str) -> dict:
    try:
        clean, report = i18n.validate(raw)
    except i18n.LanguageError as exc:
        raise HTTPException(status_code=400, detail=exc.args[0]) from exc
    stored = i18n.save(clean, report, source, i18n.sha256(raw))
    _changed()
    return {"language": _summary(stored), "report": report}


@router.post("/api/i18n/install")
async def install_language(payload: InstallRequest, user: AuthContext = Depends(require_permission("settings.manage"))):
    """Downloads one language from GitHub, checks it against the manifest, then installs it."""
    entry = next((e for e in await _manifest() if e["code"] == payload.code), None)
    if not entry:
        raise HTTPException(status_code=404, detail=L("That language isn't on GitHub"))
    try:
        raw = await _get(RAW_BASE + entry["file"], entry["size"])
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=L("Couldn't reach GitHub ({error})", error=type(exc).__name__)) from exc
    if len(raw) != entry["size"] or i18n.sha256(raw) != entry["sha256"].lower():
        logger.warning("Translations: {} from GitHub doesn't match its manifest (size {} vs {}) -- not installed",
                       entry["file"], len(raw), entry["size"])
        raise HTTPException(status_code=502, detail=L("The downloaded file doesn't match GitHub's list (size or SHA-256) — not installed. Try again later."))
    result = _store(raw, "github")
    if result["language"]["code"] != payload.code:
        i18n.remove(result["language"]["code"])
        raise HTTPException(status_code=502, detail=L("The file from GitHub is for another language — not installed"))
    return result


@router.put("/api/i18n/upload")
async def upload_language(request: Request, user: AuthContext = Depends(require_permission("settings.manage"))):
    """Installs a language file sent from the browser (JSON, at most 1 MB)."""
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > i18n.MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail=L("The language file is too big (over {mb} MB)", mb=1))
    return _store(bytes(raw), "file")


@router.delete("/api/i18n/{code}")
def remove_language(code: str, user: AuthContext = Depends(require_permission("settings.manage"))):
    if not i18n.CODE_RE.match(code) or not i18n.remove(code):
        raise HTTPException(status_code=404, detail=L("That language isn't installed"))
    if get_settings().language.default == code:
        save_overrides(language={"default": "en"})
    _changed()
    logger.info("Translations: removed {} by {}", code, user.username)
    return {"ok": True}


class DefaultLanguageRequest(BaseModel):
    default: str = Field(..., pattern=r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


@router.post("/api/settings/language")
def set_default_language(payload: DefaultLanguageRequest, user: AuthContext = Depends(require_permission("settings.manage"))):
    """The language of the sign-in page and of accounts that didn't choose one."""
    if payload.default != "en" and payload.default not in i18n.installed():
        raise HTTPException(status_code=400, detail=L("Install that language first"))
    save_overrides(language={"default": payload.default})
    _changed()
    return {"ok": True, "default": payload.default}
