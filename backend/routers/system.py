"""App-level routes: version/update check, docs (guide, changelog, license),
tool status and backups. See backend/app_info.py and backend/backup.py."""
from __future__ import annotations

import asyncio

import json
import re
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel

from ..i18n import L
from .. import app_info, backup
from ..config import get_settings
from ..library_manager import library_manager
from ..security import AuthContext, get_current_user, require_permission

router = APIRouter()


class BackupRequest(BaseModel):
    include: list[str] | None = None   # (3.7.0) backup.PARTS keys; else the two switches below
    include_cache: bool = False
    include_secrets: bool = False
    confirm_videos: bool = False       # the videos only after the page asked a second time
    dir: str | None = None             # (3.7.0) another folder for this backup; None: the backups folder


class BackupSettings(BaseModel):
    split_gb: float = 0  # (3.7.0) 0 = one file, else 1.9..50 (checked by BackupConfig)


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
_YANDEX_HOST = re.compile(r"^(?:[\w-]+\.)*yandex\.[a-z.]{2,10}$")
_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://yandex.com", "Referer": "https://yandex.com/images/",
}


def _yandex_results(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and bool(_YANDEX_HOST.match(parts.hostname or "")) and "cbir_id=" in parts.query


@router.post("/api/system/frame-search/yandex")
async def frame_search_yandex(request: Request, user: AuthContext = Depends(get_current_user)):
    """Search frame -> Yandex: uploads the frame (a JPEG from the player) to Yandex and
    returns the address of its results page, which the tab opens. (Yandex refuses the
    same upload straight from a browser.)
    Like Yandex's own page: the upload form with prg=1 answers with a redirect to the
    results; older answer (JSON with the address) tried second. What Yandex answered is
    logged when neither works."""
    data = await request.body()
    if not data or not data.startswith(b"\xff\xd8"):
        raise HTTPException(status_code=400, detail=L('Not a frame from the player'))
    tried = []
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=_BROWSER_HEADERS) as client:
            resp = await client.post(YANDEX_UPLOAD, params={"rpt": "imageview", "cbir_page": "sites"}, data={"prg": "1"},
                                     files={"upfile": ("frame.jpg", data, "image/jpeg")})
            final = str(resp.url)
            if _yandex_results(final):
                return {"url": final}
            tried.append(f"upload: {resp.status_code} {urlsplit(final).hostname}{urlsplit(final).path} "
                         f"({resp.headers.get('content-type', '?').split(';')[0]})")
            if "captcha" in final or "showcaptcha" in resp.text[:20000]:
                raise HTTPException(status_code=502, detail=L("Yandex asked to prove you're not a robot — try again later"))
            resp = await client.post(YANDEX_UPLOAD, params={"rpt": "imageview", "format": "json", "request": _YANDEX_BLOCKS},
                                     files={"upfile": ("frame.jpg", data, "image/jpeg")})
            try:
                url = f"{YANDEX_UPLOAD}?{resp.json()['blocks'][0]['params']['url']}"
                if _yandex_results(url):
                    return {"url": url}
            except (ValueError, KeyError, IndexError, TypeError):
                pass
            tried.append(f"json: {resp.status_code} ({resp.headers.get('content-type', '?').split(';')[0]}) "
                         f"{resp.text[:120]!r}")
    except httpx.HTTPError as exc:
        tried.append(exc.__class__.__name__)
    logger.warning("Search frame on Yandex: no results address — {}", " | ".join(tried))
    raise HTTPException(status_code=502, detail=L("Yandex didn't answer as expected — try again later (details in the log)"))


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
        raise HTTPException(status_code=404, detail=L('Not found'))
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


@router.get("/api/system/docs/{key}", response_class=PlainTextResponse)
def get_doc(key: str, user: AuthContext = Depends(get_current_user)):
    text = app_info.read_doc(key)
    if text is None:
        raise HTTPException(status_code=404, detail=L('Document not found'))
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
        "split_gb": settings.backup.split_gb,
        "locations": [str(d) for d in backup.backup_dirs(settings)],
        "pending_restore": backup.get_pending_restore(),
        "password_set": bool(backup.saved_password()),
    }


@router.get("/api/backups/offsite")
async def backups_offsite(live: bool = False, user: AuthContext = Depends(require_permission("settings.manage"))):
    """Where scheduled backup jobs copy and upload their backups (3.8.0): each job's copy folders
    and upload, what was uploaded to Filester, and (live) the Filester account's space."""
    from .. import backup_jobs, cloud_filester
    jobs = [j for j in library_manager.list_scheduled_jobs() if j.get("job_type") == "backup"]
    out = {"jobs": [{"id": j["id"], "name": j.get("name"), "enabled": j.get("enabled"),
                     "copy_to": (j.get("job_config") or {}).get("copy_to") or [],
                     "upload": (j.get("job_config") or {}).get("upload") or "",
                     "keep": (j.get("job_config") or {}).get("keep") or backup_jobs.DEFAULT_KEEP} for j in jobs]}
    try:
        out["filester"] = await asyncio.to_thread(cloud_filester.status, live)
    except cloud_filester.CloudError as exc:
        out["filester"] = {**await asyncio.to_thread(cloud_filester.status, False), "error": str(exc)}
    names = {j["id"]: j.get("name") for j in jobs}
    for u in out["filester"]["uploads"]:
        u["job_name"] = names.get(u.get("job"))
    return out


def _destination(folder: str | None):
    try:
        return backup.check_destination(get_settings(), folder)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/backups/plan")
async def backup_plan(include: str = "", dir: str = "", user: AuthContext = Depends(require_permission("settings.manage"))):
    """What a backup would hold (3.7.0): every part with its files and size, the estimated
    size and the free space where it goes (`dir`, else the backups folder).
    `include`: comma-separated backup.PARTS keys."""
    parts = [k for k in include.split(",") if k]
    return await asyncio.to_thread(backup.plan_backup, get_settings(), parts, None, _destination(dir))


@router.get("/api/backups/space")
def backup_space(dir: str = "", user: AuthContext = Depends(require_permission("settings.manage"))):
    """Free space where a backup goes (`dir`, else the backups folder), asked every few seconds (3.7.0)."""
    return backup.free_space(get_settings(), _destination(dir))


@router.post("/api/backups")
async def create_backup(payload: BackupRequest, user: AuthContext = Depends(require_permission("settings.manage"))):
    """Starts a backup (3.7.0: in the background -- followed on Tasks) after checking it fits."""
    parts = backup.parts_from(payload.include, payload.include_cache, payload.include_secrets)
    if "videos" in parts and not payload.confirm_videos:
        raise HTTPException(status_code=400, detail=L("Including the videos has to be confirmed"))
    dest = _destination(payload.dir)
    plan = await asyncio.to_thread(backup.plan_backup, get_settings(), parts, None, dest)
    problem = backup.space_problem(plan)
    if problem:
        raise HTTPException(status_code=507, detail=problem)
    try:
        library_manager.start_backup(sorted(parts), dest=str(dest))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"started": True, "plan": plan}


@router.post("/api/backups/settings")
def save_backup_settings(payload: BackupSettings, user: AuthContext = Depends(require_permission("settings.manage"))):
    """Split size for new backups (3.7.0); scheduled backups use it too."""
    from ..config import BackupConfig, save_overrides
    try:
        value = BackupConfig(split_gb=payload.split_gb).split_gb
    except ValueError:
        raise HTTPException(status_code=400, detail=L("Parts can be 1.9 to 50 GB (or 0: one file)"))
    save_overrides(backup={"split_gb": value})
    return {"split_gb": value}


@router.post("/api/backups/{name}/test")
async def test_backup(name: str, payload: BackupPassword | None = None,
                      user: AuthContext = Depends(require_permission("settings.manage"))):
    """Restore test: opens the backup like a restore would (zip, database, settings) without changing anything."""
    try:
        return await library_manager.run_backup_test(name, password=payload.password if payload else None)
    except backup.PasswordNeeded as exc:
        raise _locked(exc) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=L('Backup not found')) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/backups/{name}/download")
def download_backup(name: str, part: int = 0, user: AuthContext = Depends(require_permission("settings.manage"))):
    """The backup file, or (a split backup, 3.7.0) its part number `part` (1, 2, ...)."""
    try:
        files = backup.files_of(backup.resolve_backup(get_settings(), name))
        path = files[part - 1] if part else files[0]
        if not part and len(files) > 1:
            raise ValueError("a split backup is downloaded part by part")
    except (ValueError, FileNotFoundError, IndexError):
        raise HTTPException(status_code=404, detail=L('Backup not found'))
    return FileResponse(str(path), media_type="application/zip" if path.suffix == ".zip" else "application/octet-stream",
                        filename=path.name)


@router.delete("/api/backups/{name}")
def delete_backup(name: str, user: AuthContext = Depends(require_permission("settings.manage"))):
    try:
        backup.delete_backup(get_settings(), name)
    except (ValueError, FileNotFoundError):
        raise HTTPException(status_code=404, detail=L('Backup not found'))
    return {"ok": True}


@router.post("/api/backups/{name}/restore")
def stage_restore(name: str, payload: BackupPassword | None = None,
                  user: AuthContext = Depends(require_permission("settings.manage"))):
    try:
        summary = backup.stage_restore(get_settings(), name, payload.password if payload else None)
    except backup.PasswordNeeded as exc:
        raise _locked(exc) from exc
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=L('Backup not found'))
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
