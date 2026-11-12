from __future__ import annotations

import asyncio
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from .library_manager import library_manager
from .logging_setup import setup_logging
from .security import AuthContext, ensure_bootstrap_admin, get_current_user, require_permission
from .app_info import APP_VERSION
from .hardening import SecurityMiddleware
from .routers import health, auth, me, users, pmv, settings, library, scheduler, stats, system, credentials, site_sync, events, logs, playlists, telegram, performers, notifications, songs, bulk, now_playing
from .action_log import ActionLogMiddleware


def _quiet_proactor_reset_errors(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Windows' ProactorEventLoop logs a scary traceback whenever a client
    aborts an in-flight connection -- e.g. the video player seeking, which
    cancels the current range request mid-stream. Calling _sock.shutdown()
    on an already-reset socket then raises ConnectionResetError from inside
    asyncio's own connection-lost callback, which can't be caught with a
    try/except anywhere in our own request handlers. It's harmless and
    expected with HTTP range streaming, so downgrade just this specific
    case to a debug log instead of surfacing it as an ERROR."""
    exc = context.get("exception")
    message = context.get("message", "")
    if isinstance(exc, ConnectionResetError) and "_call_connection_lost" in message:
        logger.debug("Ignored benign ConnectionResetError from a client aborting a stream: {}", exc)
        return
    loop.default_exception_handler(context)


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.get_running_loop().set_exception_handler(_quiet_proactor_reset_errors)
    # Creates the first admin account from INITIAL_ADMIN_USERNAME/
    # INITIAL_ADMIN_PASSWORD in .env if (and only if) the `users` table is
    # still completely empty -- raises RuntimeError (crashing startup with
    # a clear message) if it's empty and those aren't set. See
    # security.py's ensure_bootstrap_admin() docstring.
    ensure_bootstrap_admin()
    # Resumes any downloads/re-encodes _load() found still in-flight from
    # before the last restart. Must happen here, now that the event loop is
    # actually running -- library_manager itself was constructed at import
    # time (see the `library_manager = LibraryManager()` singleton at the
    # bottom of library_manager.py), before any event loop existed for a
    # worker task to attach to. Without this call, recovered items just sit
    # at "pending"/"queued" forever, doing nothing, until a user happens to
    # add or retry some other item and incidentally wakes the worker up.
    library_manager.start()
    yield
    await library_manager.shutdown()


# docs_url/redoc_url/openapi_url are all disabled here and re-implemented
# below (see the "admin-only API docs" section further down), gated
# behind require_permission("settings.manage"). By default FastAPI serves
# /docs, /redoc, and /openapi.json completely unauthenticated -- anyone
# who knows (or guesses) the URL could browse the full API schema,
# including every admin-only route's exact shape/parameters, and even
# fire requests at them directly from Swagger's "Try it out" UI using
# whatever session cookie their browser already holds. Every route still
# independently enforces its own require_permission/require_admin
# dependency regardless of this change (that was never the gap) -- this
# closes off exposing the schema itself to anyone below admin.
app = FastAPI(
    title="PMV Nexus",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
setup_logging()

# ----------------------------------------------------------------- routers
# main.py is now purely app assembly -- every actual route lives in its
# own focused module under backend/routers/, grouped by domain (see each
# router module's own docstring for what it covers and its permission
# model). This is a modular-monolith split for maintainability: still one
# process, one FastAPI app, one shared LibraryDB connection -- NOT a
# microservices split, which would mean separate deployable processes and
# would be the wrong fit for this single-user, single-SQLite-file app.
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(me.router)
app.include_router(users.router)
app.include_router(pmv.router)
app.include_router(settings.router)
app.include_router(library.router)
app.include_router(scheduler.router)
app.include_router(stats.router)
app.include_router(system.router)
app.include_router(credentials.router)
app.include_router(site_sync.router)
app.include_router(events.router)
app.include_router(logs.router)
app.include_router(playlists.router)
app.include_router(telegram.router)
app.include_router(performers.router)
app.include_router(notifications.router)
app.include_router(songs.router)
app.include_router(bulk.router)
app.include_router(now_playing.router)

# Every change made through the API is recorded in Admin -> Logs.
app.add_middleware(ActionLogMiddleware)
# Security headers, cross-site request refusal, body size cap (backend/hardening.py).
# Added last = outermost: it also covers what the other middleware returns.
app.add_middleware(SecurityMiddleware)


# ------------------------------------------------------- admin-only API docs
# Re-implements the three routes FastAPI would otherwise auto-register for
# free (disabled above via docs_url=None/redoc_url=None/openapi_url=None),
# each behind the same settings.manage gate every other admin-only config
# route in this app already uses -- so a regular user hitting /docs, by
# mistake or otherwise, gets a normal 401 (not logged in) or 403 (logged
# in, but not admin) instead of the full API schema. MUST be registered
# before the SPA catch-all route at the very bottom of this file --
# Starlette matches routes in registration order, not by path
# specificity, so a route defined after `/{full_path:path}` would never
# actually be reached.
# 2.12.0: admin only ("*"), and anyone else -- signed in or not -- gets a page
# saying so that moves on to the library after 5 seconds (a meta refresh: no
# script needed under the app's Content-Security-Policy). The docs pages load
# Swagger UI / ReDoc from jsDelivr, so they carry their own, wider policy.
_DOCS_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
             "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
             "font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data: https://fastapi.tiangolo.com https://cdn.redoc.ly; "
             "worker-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'")

_NO_ACCESS_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="refresh" content="5;url=/">
<title>No permission - PMV Nexus</title><style>
:root{color-scheme:dark light}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0f0f12;color:#e8e8ee;
font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}@media (prefers-color-scheme:light){body{background:#f5f5f7;color:#1b1b20}}
.box{max-width:26rem;padding:2rem;text-align:center}h1{font-size:1.4rem;margin:.5rem 0}p{opacity:.8;margin:.25rem 0 1.25rem}
.icon{font-size:2.5rem}.bar{height:6px;border-radius:3px;background:rgba(127,127,127,.25);overflow:hidden}
.bar span{display:block;height:100%;background:#f97316;width:0;animation:go 5s linear forwards}@keyframes go{to{width:100%}}
a{color:#f97316}</style></head><body><main class="box"><div class="icon">&#128274;</div>
<h1>You don't have permission to view this page</h1><p>Taking you to the library in 5 seconds&hellip;</p>
<div class="bar"><span></span></div><p style="margin-top:1rem"><a href="/">Go now</a></p></main></body></html>"""


async def _docs_admin(request: Request):
    """The signed-in admin, or None (-> the no-permission page)."""
    try:
        user = await get_current_user(request)
    except HTTPException:
        return None
    return user if user.has_permission("*") else None


def _no_access() -> HTMLResponse:
    return HTMLResponse(_NO_ACCESS_PAGE, status_code=403, headers={"Cache-Control": "no-store"})


@app.get("/openapi.json", include_in_schema=False)
async def get_openapi_schema(request: Request):
    if not await _docs_admin(request):
        return _no_access()
    return get_openapi(title=app.title, version=app.version, routes=app.routes)


@app.get("/docs", include_in_schema=False)
async def get_swagger_docs(request: Request):
    if not await _docs_admin(request):
        return _no_access()
    page = get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} - Swagger UI")
    page.headers["Content-Security-Policy"] = _DOCS_CSP
    return page


@app.get("/redoc", include_in_schema=False)
async def get_redoc_docs(request: Request):
    if not await _docs_admin(request):
        return _no_access()
    page = get_redoc_html(openapi_url="/openapi.json", title=f"{app.title} - ReDoc")
    page.headers["Content-Security-Policy"] = _DOCS_CSP
    return page


@app.on_event("startup")
async def _install_quiet_exception_handler() -> None:
    asyncio.get_running_loop().set_exception_handler(_quiet_proactor_reset_errors)


class _NoCacheStaticFiles(StaticFiles):
    """Serves /assets/* exactly like the normal StaticFiles always has --
    including its built-in ETag/Last-Modified support, so an unchanged
    file still gets a cheap 304 instead of a full re-download -- but adds
    Cache-Control: no-cache, which forces the browser to always
    REVALIDATE with the server before using a cached copy, instead of
    trusting it blindly for some heuristic amount of time (the default
    behavior with no explicit Cache-Control header at all, which is what
    was causing restarted-server code changes to need a hard refresh /
    manual cache-clear to actually show up). This costs one small
    conditional request per asset load instead of zero -- a good trade
    for an actively-developed single-user tool."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


mimetypes.add_type("application/javascript", ".js")
# The interface (Vue, built from ui/ into frontend/ by `npm run build`):
# frontend/index.html plus frontend/assets/*.
(FRONTEND_DIR / "assets").mkdir(parents=True, exist_ok=True)
app.mount("/assets", _NoCacheStaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")

# index.html itself needs the same treatment -- it's served directly via
# FileResponse below, not through the StaticFiles mount above, so it
# wouldn't otherwise get this header. A stale cached index.html could
# keep referencing since-renamed or removed script files.
_INDEX_HTML_HEADERS = {"Cache-Control": "no-cache"}


def _index_response():
    index_file = FRONTEND_DIR / "index.html"
    if not index_file.is_file():
        raise HTTPException(status_code=404, detail="The interface has not been built (see ui/README.md)")
    return FileResponse(str(index_file), headers=_INDEX_HTML_HEADERS)


@app.get("/")
def index():
    return _index_response()


@app.get("/{full_path:path}")
def spa_fallback(full_path: str, request: Request):
    """Serves the app shell for every client-side route (/video/{id},
    /admin/..., and anything unknown -- the Vue router shows its own "not
    found" page). MUST be registered LAST so it only catches paths nothing
    more specific matched. /api/ and /assets/ paths get a real 404 instead
    of the HTML shell, so a missing endpoint or file is obvious.

    /next/... was the new interface's preview address (1.8-1.13); it now
    redirects to the same page at the root so old bookmarks keep working.
    Old classic addresses (/admin/download, ...) are redirected by the Vue
    router."""
    if full_path.startswith("api/") or full_path.startswith("assets/"):
        raise HTTPException(status_code=404, detail="Not Found")
    if full_path == "next" or full_path.startswith("next/"):
        target = "/" + full_path[len("next"):].lstrip("/")
        if request.url.query:
            target += "?" + request.url.query
        return RedirectResponse(target, status_code=308)
    return _index_response()
