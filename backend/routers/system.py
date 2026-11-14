"""App-level routes: version/update check, docs (guide, changelog, license),
tool status and backups. See backend/app_info.py and backend/backup.py."""
from __future__ import annotations

import json
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel

from .. import app_info, backup
from ..config import get_settings
from ..library_manager import library_manager
from ..security import AuthContext, get_current_user, require_permission

router = APIRouter()


class BackupRequest(BaseModel):
    include_cache: bool = False
    include_secrets: bool = False


class BackupPassword(BaseModel):
    password: str | None = None  # opening a protected backup; never logged or stored


# 423 Locked: "this backup needs its password" (the page asks for it and tries again).
# Not 401, which the UI reads as "signed out".
def _locked(exc: backup.PasswordNeeded) -> HTTPException:
    return HTTPException(status_code=423, detail=str(exc))


@router.get("/api/system/info")
def get_system_info(user: AuthContext = Depends(get_current_user)):
    info = {**app_info.system_info(), "update": library_manager.get_update_status()}
    if not user.has_permission("settings.manage"):
        # Exact Python / OS / FFmpeg versions are for the admin only (they help an attacker pick an exploit).
        for key in ("python", "platform", "ffmpeg"):
            info.pop(key, None)
    return info


@router.post("/api/system/check-update")
async def check_for_update(user: AuthContext = Depends(get_current_user)):
    """Manual check (header version label). Any signed-in user may click it;
    repeated clicks within a minute return the cached result instead of
    hitting GitHub again."""
    return await library_manager.run_update_check(force=False)


# ---- Search this frame (3.2.0, ui/src/lib/frameSearch.js) ----
# The app's own pages may only send forms to the app (CSP form-action 'self'). This
# tiny page, opened in a new tab by the player, is the one place that may send a form
# to another (https) site: it gets the frame from the app (postMessage, same origin
# only) and uploads it to the reverse image search site picked, in that same tab.
_FRAME_SEARCH_CSP = ("default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; img-src 'self'; connect-src 'self'; "
                     "form-action https:; base-uri 'none'; frame-ancestors 'none'")
_FRAME_SEARCH_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Searching this frame…</title><meta name="referrer" content="no-referrer">
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0b0d;color:#e7e7ea;
font:15px/1.5 system-ui,sans-serif}p{margin:0;padding:1rem;text-align:center}</style></head>
<body><p id="msg">Sending the frame…</p><script src="/frame-search.js"></script></body></html>"""
_FRAME_SEARCH_JS = r"""(function () {
  var msg = document.getElementById("msg");
  var done = false;
  window.addEventListener("message", function (e) {
    if (done || e.origin !== location.origin || !e.data || e.data.type !== "pmv-frame-search") return;
    var d = e.data;
    if (!(d.file instanceof File)) return;
    if (d.mode === "yandex") {
      // Yandex answers an upload with the address of its results, not with them: the app's
      // server uploads the frame (a browser can't read Yandex's answer) and this tab opens it.
      done = true;
      msg.textContent = "Sending the frame to Yandex…";
      fetch("/api/system/frame-search/yandex", { method: "POST", body: d.file, credentials: "same-origin",
        headers: { "Content-Type": "image/jpeg", "X-Requested-With": "XMLHttpRequest" } })
        .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.detail || r.status); return j; }); })
        .then(function (j) { location.replace(j.url); })
        .catch(function (err) { msg.textContent = "Yandex didn't take the frame: " + err.message; });
      return;
    }
    if (!/^https:\/\/\S+$/i.test(d.url) || !/^[\w.\[\]-]+$/.test(d.field)) return;
    done = true;
    msg.textContent = "Sending the frame to " + String(d.name || "the search site") + "…";
    var form = document.createElement("form");
    form.method = "POST"; form.action = d.url; form.enctype = "multipart/form-data";
    var input = document.createElement("input");
    input.type = "file"; input.name = d.field;
    var dt = new DataTransfer(); dt.items.add(d.file); input.files = dt.files;
    form.appendChild(input); document.body.appendChild(form); form.submit();
  });
  if (window.opener) window.opener.postMessage({ type: "pmv-frame-search-ready" }, location.origin);
  else msg.textContent = "Open this from the player: ⚙ → Search frame.";
})();"""


YANDEX_UPLOAD = "https://yandex.com/images/search"
_YANDEX_BLOCKS = json.dumps({"blocks": [{"block": "b-page_type_search-by-image__link"}]})
_MAX_FRAME = 8 * 1024 * 1024


@router.post("/api/system/frame-search/yandex")
async def frame_search_yandex(request: Request, user: AuthContext = Depends(get_current_user)):
    """Search frame on Yandex: uploads the frame (a JPEG from the player) to Yandex
    and returns the address of its results page. Yandex's upload answers with that
    address rather than the results, which a browser form can't follow -- so this
    one step goes through the app's server, to Yandex only."""
    data = await request.body()
    if not data or len(data) > _MAX_FRAME or not data.startswith(b"\xff\xd8"):
        raise HTTPException(status_code=400, detail="Not a frame from the player")
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}) as client:
            resp = await client.post(YANDEX_UPLOAD, params={"rpt": "imageview", "format": "json", "request": _YANDEX_BLOCKS},
                                     files={"upfile": ("frame.jpg", data, "image/jpeg")})
        query = resp.json()["blocks"][0]["params"]["url"]
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        logger.warning("Search frame on Yandex failed: {}", exc.__class__.__name__)
        raise HTTPException(status_code=502, detail="Yandex didn't answer as expected — try again later") from exc
    url = f"{YANDEX_UPLOAD}?{query}"
    if urlsplit(url).netloc != "yandex.com":
        raise HTTPException(status_code=502, detail="Yandex answered with an unexpected address")
    return {"url": url}


@router.get("/frame-search", include_in_schema=False)
def frame_search_page():
    return HTMLResponse(_FRAME_SEARCH_HTML, headers={"Content-Security-Policy": _FRAME_SEARCH_CSP, "Cache-Control": "no-cache"})


@router.get("/frame-search.js", include_in_schema=False)
def frame_search_script():
    return Response(_FRAME_SEARCH_JS, media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@router.get("/api/system/docs/screenshots/{name}")
def get_doc_screenshot(name: str, user: AuthContext = Depends(get_current_user)):
    """The Guide's pictures (docs/screenshots/, 3.2.0) for the in-app Help page."""
    path = app_info.doc_screenshot(name)
    if path is None:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


@router.get("/api/system/docs/{key}", response_class=PlainTextResponse)
def get_doc(key: str, user: AuthContext = Depends(get_current_user)):
    text = app_info.read_doc(key)
    if text is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return PlainTextResponse(text)


@router.get("/api/system/tools")
def get_tool_status(user: AuthContext = Depends(require_permission("settings.manage"))):
    # Sync route -> FastAPI runs it in its threadpool, so the short
    # subprocess calls inside don't block the event loop.
    return app_info.tool_status()


# ------------------------------------------------------------------- backups
@router.get("/api/backups")
def list_backups(user: AuthContext = Depends(require_permission("settings.manage"))):
    settings = get_settings()
    return {
        "backups": backup.list_backups(settings),
        "dir": str(settings.backup_dir),
        "keep": settings.backup.keep,
        "pending_restore": backup.get_pending_restore(),
        "password_set": bool(backup.saved_password()),
    }


@router.post("/api/backups")
async def create_backup(payload: BackupRequest, user: AuthContext = Depends(require_permission("settings.manage"))):
    try:
        return await library_manager.run_backup(
            include_cache=payload.include_cache, include_secrets=payload.include_secrets, trigger="manual",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Backup failed: {exc}")


@router.post("/api/backups/{name}/test")
async def test_backup(name: str, payload: BackupPassword | None = None,
                      user: AuthContext = Depends(require_permission("settings.manage"))):
    """Restore test: opens the backup like a restore would (zip, database, settings) without changing anything."""
    try:
        return await library_manager.run_backup_test(name, password=payload.password if payload else None)
    except backup.PasswordNeeded as exc:
        raise _locked(exc) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Backup not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/backups/{name}/download")
def download_backup(name: str, user: AuthContext = Depends(require_permission("settings.manage"))):
    try:
        path = backup.resolve_backup(get_settings(), name)
    except (ValueError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="Backup not found")
    return FileResponse(str(path), media_type="application/zip", filename=path.name)


@router.delete("/api/backups/{name}")
def delete_backup(name: str, user: AuthContext = Depends(require_permission("settings.manage"))):
    try:
        backup.delete_backup(get_settings(), name)
    except (ValueError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="Backup not found")
    return {"ok": True}


@router.post("/api/backups/{name}/restore")
def stage_restore(name: str, payload: BackupPassword | None = None,
                  user: AuthContext = Depends(require_permission("settings.manage"))):
    try:
        summary = backup.stage_restore(get_settings(), name, payload.password if payload else None)
    except backup.PasswordNeeded as exc:
        raise _locked(exc) from exc
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "backup": summary}


@router.put("/api/backups-password")
def set_backup_password(payload: BackupPassword, user: AuthContext = Depends(require_permission("settings.manage"))):
    """Save the password new backups are protected with; empty removes it (2.25.0)."""
    try:
        backup.set_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "password_set": bool(payload.password)}


@router.delete("/api/backups-restore")
def cancel_restore(user: AuthContext = Depends(require_permission("settings.manage"))):
    backup.cancel_pending_restore()
    return {"ok": True}
