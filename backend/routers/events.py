"""Live updates for the web UI (Server-Sent Events) -- replaces polling.

GET /api/events streams:
  event: hello    {"version"}                      once, on connect
  event: library  {"revision", "changed", "summary"}  when library items change
                  `changed` is the list of item ids written since the last
                  event, or null when too many changed (refresh everything).
A comment line is sent every HEARTBEAT seconds so proxies keep the stream open.
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ..app_info import APP_VERSION
from ..library_manager import library_manager
from ..now_playing import now_playing
from ..security import AuthContext, require_permission

router = APIRouter()

# Set by run.py as soon as the server is asked to stop, so open streams end by
# themselves (instead of keeping the shutdown waiting).
_stopping = False


def stop_streams() -> None:
    global _stopping
    _stopping = True


TICK_SECONDS = 1.0
HEARTBEAT_SECONDS = 15.0
MAX_IDS_PER_EVENT = 500


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


@router.get("/api/events")
async def stream_events(request: Request, user: AuthContext = Depends(require_permission("library.view"))):
    async def generator():
        db = library_manager.db
        revision = db.revision
        yield "retry: 3000\n\n"
        yield _sse("hello", {"version": APP_VERSION, "revision": revision, "summary": library_manager.get_summary()})
        last_beat = time.monotonic()
        while True:
            await asyncio.sleep(TICK_SECONDS)
            if _stopping or await request.is_disconnected():
                return
            now_playing.sweep()  # players that went quiet drop off Currently Playing
            current, changed = db.changes_since(revision)
            if current != revision:
                revision = current
                ids = None if changed is None or len(changed) > MAX_IDS_PER_EVENT else sorted(changed)
                yield _sse("library", {"revision": revision, "changed": ids, "summary": library_manager.get_summary()})
                last_beat = time.monotonic()
            elif time.monotonic() - last_beat >= HEARTBEAT_SECONDS:
                yield ": keep-alive\n\n"
                last_beat = time.monotonic()

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
