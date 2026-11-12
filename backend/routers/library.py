"""
Everything under /api/library/* (library items: add/remove/retry, issues
merge/ignore, repair, reencode, share, rename, metadata, thumbnail,
stream, preview) plus /api/external/* (the API-key-authenticated routes
for external tools like a browser userscript). Grouped together because
they're all fundamentally "operate on a LibraryItem" -- the permission
mix within this file is intentionally NOT uniform: read routes need only
library.view (granted to regular users), everything that mutates an item
needs library.manage (admin-only), and the two /api/external/* routes use
a completely different auth mechanism (a static API key, see
verify_api_key in security.py) unrelated to user accounts at all.
"""
from __future__ import annotations

import mimetypes
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from starlette.requests import ClientDisconnect

from .. import activity, storyboard
from ..library_manager import library_manager
from ..hardening import scrub_item
from ..library_mixins.local_upload import UploadError
from ..models import (
    AddRequest, RenameRequest, ShareRequest, SetThumbnailRequest,
    ResolveRepairRequest, MergeItemsRequest, IgnoreIssueRequest, ReencodeMoveRequest, ReencodeRequest, ItemDetailsRequest, DeleteFilesRequest,
    UploadConvertRequest, PreviewFromTimestampsRequest,
)
from ..scraper_v1 import extract_video_id
from ..spellings import spellings
from ..security import require_permission, require_any, verify_api_key, AuthContext
from ..config import HEVC_PRESETS, get_settings
from ..media_tools import REENCODE_ENCODERS, available_encoders
from ..transcode import live_transcoder, TranscodeError
from ._common import stream_file

router = APIRouter()


# --------------------------------------------------------------- external
@router.post("/api/external/library")
async def add_to_library_external(payload: AddRequest, _=Depends(verify_api_key)):
    try:
        item = await library_manager.add(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item.model_dump()


@router.get("/api/external/index")
async def external_download_index(_=Depends(verify_api_key)):
    """Everything the userscript needs to mark videos without asking per
    video: the ids (the 24-character id at the end of a PMVHaven video URL)
    of every downloaded video, and of every removed duplicate with the video
    it duplicates. The userscript stores this and refreshes it daily."""
    done, merged = [], {}
    for item in list(library_manager._items.values()):
        if item.status == "done":
            done.append(item.id)
        elif item.status == "merged":
            kept = library_manager.get(item.merged_into) if item.merged_into else None
            merged[item.id] = {"of": kept.id if kept else None, "title": kept.title if kept else ""}
    return {"generated_at": time.time(), "done": done, "merged": merged}


@router.get("/api/external/exists")
async def check_exists_external(url: str, _=Depends(verify_api_key)):
    try:
        video_id = extract_video_id(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    item = library_manager.get(video_id)
    # "failed" items are treated as not-existing, since add() itself
    # allows failed items to be re-queued -- keep this endpoint consistent
    # with that behavior so the button doesn't lie about it being addable.
    if item is None or item.status == "failed":
        return {"exists": False}
    if item.status == "merged":
        # Removed as a duplicate of another downloaded video ("Keep this one"
        # on the Duplicates page). Stays this way until that video is removed.
        kept = library_manager.get(item.merged_into) if item.merged_into else None
        return {
            # status stays "merged" so existing userscripts keep working; label/duplicate_of are new.
            "exists": True, "status": "merged", "title": kept.title if kept else "",
            "label": f"Duplicate of “{kept.title}”" if kept else "Duplicate (already downloaded)",
            "duplicate_of": {"id": kept.id, "title": kept.title, "url": kept.url} if kept else None,
        }
    label = "Already downloaded" if item.status == "done" else f"In the library ({item.status})"
    return {"exists": True, "status": item.status, "title": item.title, "label": label}


# ------------------------------------------------------------------ library
@router.get("/api/library")
def get_library(
        page: int = 1,
        page_size: int = 50,
        search: str = "",
        tags: list[str] = Query(default=[]),
        performers: list[str] = Query(default=[]),
        codecs: list[str] = Query(default=[]),
        statuses: list[str] = Query(default=[]),
        sort_key: str = "created_at",
        sort_dir: str = "desc",
        creators: list[str] = Query(default=[]),
        match_all: bool = False,
        min_duration: Optional[int] = None,
        max_duration: Optional[int] = None,
        min_rating: Optional[float] = None,
        gone: Optional[bool] = None,
        qualities: list[str] = Query(default=[]),
        min_quality: Optional[str] = None,
        orientation: Optional[str] = Query(default=None, pattern="^(landscape|portrait|square)$"),
        p_cups: list[str] = Query(default=[]),
        p_hair: list[str] = Query(default=[]),
        p_eyes: list[str] = Query(default=[]),
        p_age_min: Optional[int] = Query(default=None, ge=0, le=150),
        p_age_max: Optional[int] = Query(default=None, ge=0, le=150),
        p_enhanced: Optional[bool] = None,
        user: AuthContext = Depends(require_permission("library.view")),
):
    result = library_manager.query_items(
        p_cups=p_cups, p_hair=p_hair, p_eyes=p_eyes, p_age_min=p_age_min, p_age_max=p_age_max, p_enhanced=p_enhanced,
        qualities=qualities,
        min_quality=min_quality,
        orientation=orientation,
        creators=creators,
        match_all=match_all,
        min_duration=min_duration,
        max_duration=max_duration,
        min_rating=min_rating,
        gone=gone,
        page=page,
        page_size=page_size,
        search=search,
        tags=tags,
        performers=performers,
        codecs=codecs,
        statuses=statuses,
        sort_key=sort_key,
        sort_dir=sort_dir,
    )
    result["items"] = library_manager.with_watch([scrub_item(i, user) for i in result.get("items", [])], user)  # regular users: no server paths; my place in each
    return result


@router.get("/api/library/performer-filters")
def get_performer_filter_options(user: AuthContext = Depends(require_permission("library.view"))):
    """Library -> Filters -> Performer details (2.19.0): the cup sizes, hair / eye colours and
    age range of performers in the library (from their ThePornDB profiles)."""
    return library_manager.performer_filter_options()


@router.get("/api/library/summary")
def get_library_summary(user: AuthContext = Depends(require_permission("library.view"))):
    """Facet option lists (for the filter dropdowns) and live task
    counts (for the header status indicator), both computed across the
    *entire* library regardless of what page is currently loaded --
    deliberately separate from query_items() so switching pages never
    makes a filter dropdown or the "2 downloading" indicator flicker
    down to just what's on the current page."""
    return library_manager.get_summary()


@router.get("/api/library/issues")
def get_library_issues(min_percentage: float = 75.0, user: AuthContext = Depends(require_permission("duplicates.manage"))):
    """Every currently-open maintenance issue (currently just duplicate
    groups, matched by persisted score-based comparisons -- see
    library_manager.find_issues()) for the Admin Portal's Issues page.
    `min_percentage` (0-100, higher = stricter) filters which persisted
    pairwise matches count toward a group -- see backend/library_mixins/
    duplicate_worker.py for how those scores are computed and stored.
    Read-only -- resolving an issue (merge/ignore, below) is an
    admin-only mutation."""
    return {"issues": library_manager.find_issues(min_percentage)}


@router.post("/api/library/issues/scan")
async def trigger_duplicate_scan(user: AuthContext = Depends(require_permission("duplicates.manage"))):
    """Manually triggers the background duplicate-detection sweep right
    now instead of waiting for the next daily run -- see
    library_manager.request_duplicate_scan_now(). Fire-and-forget: this
    route returns immediately, the scan itself continues in the
    background (poll GET /api/library/issues/scan-status for progress)."""
    library_manager.request_duplicate_scan_now()
    return {"triggered": True}


@router.get("/api/library/suggest")
async def suggest_values(
        field: str = Query(pattern="^(performers|tags|music|flags|creators)$"),
        q: str = "",
        limit: int = 15,
        user: AuthContext = Depends(require_permission("library.view")),
):
    """Autocomplete for Edit details: existing values matching what's typed."""
    return {"items": library_manager.suggest_values(field, q, limit)}


# ---- Seekbar previews (2.21.0, backend/storyboard.py) ----
@router.get("/api/library/{video_id}/storyboard.vtt")
async def get_storyboard_vtt(video_id: str, user: AuthContext = Depends(require_permission("library.stream"))):
    """The player's thumbnail track. Not made yet: it's queued (next in line) and this is a 404 --
    the preview is there the next time the video is played."""
    info = library_manager.storyboard_info(video_id)
    if not info:
        library_manager.request_storyboard(video_id, first=True)
        raise HTTPException(status_code=404, detail="No seekbar previews yet")
    v = int(info.get("made_at") or 0)
    body = storyboard.vtt(info, f"/api/library/{video_id}/storyboard/{{n}}.webp?v={v}")
    return Response(body, media_type="text/vtt; charset=utf-8", headers={"Cache-Control": "no-cache"})


@router.get("/api/library/{video_id}/storyboard/{n}.webp")
def get_storyboard_sheet(video_id: str, n: int, user: AuthContext = Depends(require_permission("library.stream"))):
    path = library_manager.storyboard_sheet(video_id, n)
    if not path:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "private, max-age=2592000"})


@router.get("/api/maintenance/storyboards")
def storyboards_overview(user: AuthContext = Depends(require_permission("maintenance.run"))):
    return library_manager.storyboards_overview()


@router.post("/api/maintenance/storyboards")
async def make_missing_storyboards(user: AuthContext = Depends(require_permission("maintenance.run"))):
    """Queues every finished video without seekbar previews (one job on Tasks)."""
    return {"queued": library_manager.queue_missing_storyboards()}


@router.get("/api/maintenance/untracked-files")
async def list_untracked_files(user: AuthContext = Depends(require_permission("maintenance.run"))):
    """Files in the download/cache folders that no library item points to."""
    return await library_manager.find_untracked_files()


@router.post("/api/maintenance/untracked-files/delete")
async def delete_untracked_files(payload: DeleteFilesRequest, user: AuthContext = Depends(require_permission("maintenance.run"))):
    return await library_manager.delete_untracked_files(payload.paths[:5000])


@router.get("/api/library/issues/scan-plan")
async def get_duplicate_scan_plan(user: AuthContext = Depends(require_permission("duplicates.manage"))):
    """What the next duplicate scan will read (new/changed videos)."""
    return await library_manager.get_duplicate_scan_plan()


@router.get("/api/library/issues/scan-status")
def get_duplicate_scan_status(user: AuthContext = Depends(require_permission("duplicates.manage"))):
    """Whether a duplicate scan is running right now, when the last one
    finished, and how many completed items are still waiting on their
    first hash -- see library_manager.get_duplicate_scan_status()."""
    return library_manager.get_duplicate_scan_status()


@router.post("/api/library/issues/merge")
async def merge_library_items(payload: MergeItemsRequest, user: AuthContext = Depends(require_permission("duplicates.manage"))):
    try:
        result = await library_manager.merge_items(payload.keep_id, payload.merge_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/api/library/issues/ignore")
def ignore_library_issue(payload: IgnoreIssueRequest, user: AuthContext = Depends(require_permission("duplicates.manage"))):
    library_manager.ignore_issue(payload.issue_type, payload.item_ids)
    return {"ok": True}


@router.get("/api/library/activity")
def get_library_activity(user: AuthContext = Depends(require_permission("tasks.view"))):
    """Tasks page: library-wide jobs (Scheduler runs, scans, syncs, backups,
    performer matching, uploads...) running now, finished in the last day,
    and the next scheduled runs."""
    return library_manager.activity_overview()


@router.post("/api/library/activity/{activity_id}/stop")
async def stop_library_activity(activity_id: str, user: AuthContext = Depends(require_permission("tasks.manage"))):
    """The Stop button (Tasks / Scheduler page) for a running job that can be stopped."""
    act = activity.get(activity_id)
    if act is None:
        raise HTTPException(status_code=404, detail="That job has already finished")
    try:
        act.request_stop(user.username)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return act.to_dict()


@router.get("/api/library/tasks")
def get_library_tasks(user: AuthContext = Depends(require_permission("tasks.view"))):
    """Every currently-active item (not yet downloaded/failed, or done but
    still being re-encoded/shared) with no page_size cap -- what the Tasks
    tab actually needs is "all of them", not a page. Left readable by
    regular users (library.view) -- it's harmless read-only status, even
    though they can't be the one who queued anything into it. (The Tasks
    tab itself is hidden from regular users client-side since Step 6, but
    the route stays permissive at library.view for consistency with every
    other read route.)"""
    return {"items": library_manager.list_active_tasks()}


@router.post("/api/library")
async def add_to_library(payload: AddRequest, user: AuthContext = Depends(require_permission("videos.add"))):
    try:
        item = await library_manager.add(payload.url, added_by=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item.model_dump()


@router.delete("/api/library/{video_id}")
async def remove_from_library(
        video_id: str,
        delete_files: bool = False,
        user: AuthContext = Depends(require_permission("videos.delete")),
):
    result = await library_manager.remove(video_id, delete_files=delete_files)
    if not result["existed"]:
        raise HTTPException(status_code=404, detail="Not found")
    return result


@router.post("/api/library/{video_id}/retry")
async def retry_item(video_id: str, user: AuthContext = Depends(require_permission("videos.add"))):
    ok = await library_manager.retry(video_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}


# ------------------------------------------------------------- repair
@router.post("/api/library/{video_id}/repair")
async def repair_item(video_id: str, user: AuthContext = Depends(require_permission("videos.maintain"))):
    try:
        item = library_manager.request_repair(video_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item


@router.post("/api/library/{video_id}/repair/cancel")
async def cancel_repair_item(video_id: str, user: AuthContext = Depends(require_permission("videos.maintain"))):
    try:
        library_manager.cancel_repair(video_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"cancelled": True}


@router.post("/api/library/{video_id}/repair/resolve")
async def resolve_repair(video_id: str, payload: ResolveRepairRequest, user: AuthContext = Depends(require_any("videos.maintain", "videos.edit"))):
    try:
        item = await library_manager.resolve_repair_thumbnail(video_id, payload.choice)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item


@router.get("/api/library/{video_id}/thumbnail-options")
async def get_thumbnail_options(video_id: str, user: AuthContext = Depends(require_any("videos.edit", "videos.upload"))):
    try:
        return await library_manager.get_thumbnail_options(video_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/library/{video_id}/thumbnail")
async def set_thumbnail(video_id: str, payload: SetThumbnailRequest, user: AuthContext = Depends(require_any("videos.edit", "videos.upload"))):
    try:
        item = await library_manager.set_thumbnail(video_id, payload.source, payload.url, payload.seconds)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item


@router.patch("/api/library/{video_id}/details")
async def edit_item_details(video_id: str, payload: ItemDetailsRequest, user: AuthContext = Depends(require_permission("videos.edit"))):
    """Hand-edited tags/performers/music/flags; kept through syncs and repairs."""
    changes = {k: getattr(payload, k) for k in payload.model_fields_set}
    try:
        return await library_manager.set_item_details(video_id, changes)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ------------------------------------------------------------- reencode
@router.post("/api/library/{video_id}/reencode")
async def reencode_item(video_id: str, body: Optional[ReencodeRequest] = None,
                        user: AuthContext = Depends(require_permission("videos.maintain"))):
    try:
        item = await library_manager.request_reencode(
            video_id, codec=body.codec if body else None,
            options=body.options.model_dump() if body and body.options else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item.model_dump()


@router.post("/api/library/{video_id}/reencode/cancel")
async def cancel_reencode(video_id: str, user: AuthContext = Depends(require_permission("videos.maintain"))):
    try:
        item = await library_manager.cancel_reencode(video_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item.model_dump()


@router.post("/api/library/{video_id}/download/move")
async def move_download(video_id: str, body: ReencodeMoveRequest, user: AuthContext = Depends(require_permission("tasks.manage"))):
    """Reorders the download queue: direction "up" / "down" / "top" / "bottom"."""
    try:
        await library_manager.reorder_download(video_id, body.direction)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "order": library_manager.download_queue_order()}


@router.post("/api/library/{video_id}/reencode/move")
async def move_reencode(video_id: str, body: ReencodeMoveRequest, user: AuthContext = Depends(require_permission("tasks.manage"))):
    try:
        await library_manager.reorder_reencode(video_id, body.direction)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.get("/api/reencode/defaults")
def get_reencode_defaults(user: AuthContext = Depends(require_permission("videos.maintain"))):
    """The Re-encode dialog (2.22.0): Server settings -> Re-encode per codec, which the
    person can change for this one re-encode, and which encoders FFmpeg has."""
    s = get_settings()
    r = s.reencode
    return {
        "enabled": s.features.reencode,
        "ask": r.ask_each_time,
        "codec": r.codec,
        "hevc": {"crf": r.target_crf, "preset": r.preset},
        "av1": {"crf": r.av1_crf, "preset": str(r.av1_preset)},
        "copy_audio": r.copy_audio, "downscale": r.downscale,
        "remove_chapters": r.remove_chapters, "keep_original_file": r.keep_original_file,
        "hevc_presets": list(HEVC_PRESETS),
        "encoders": {c: enc in available_encoders() for c, enc in REENCODE_ENCODERS.items()},
    }


# ---------------------------------------------------------------- share to
@router.get("/api/share/config")
def get_share_config(user: AuthContext = Depends(require_permission("videos.share"))):
    """Tells the frontend which destinations are actually configured, so
    the Share To modal can grey out options that have no credentials
    instead of letting the user pick them and fail. Sharing itself is
    admin-only, so this is gated the same way rather than exposed to
    regular users who can't reach the Share modal anyway."""
    cfg = get_settings().share
    return {
        "enabled": get_settings().features.share,
        "telegram": bool(cfg.telegram_api_id and cfg.telegram_api_hash),
        "telegram_channel": bool(cfg.telegram_channel_id),
        "telegram_group": bool(cfg.telegram_group_id),
        "gofile": bool(cfg.gofile_api_key),
        "pixeldrain": bool(cfg.pixeldrain_api_key),
    }


@router.post("/api/library/{video_id}/share")
async def share_item(video_id: str, payload: ShareRequest, user: AuthContext = Depends(require_permission("videos.share"))):
    try:
        item = await library_manager.request_share(
            video_id, payload.destinations, payload.telegram_target, payload.caption
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item.model_dump()


# ------------------------------------------------------------- item extras
@router.post("/api/library/{video_id}/rename")
async def rename_item(video_id: str, payload: RenameRequest, user: AuthContext = Depends(require_permission("videos.edit"))):
    try:
        item = await library_manager.rename(video_id, payload.new_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item.model_dump()


@router.post("/api/library/discover")
def discover_random_video(user: AuthContext = Depends(require_permission("library.view"))):
    """Random downloaded video for this user, not repeated until the whole
    library has been shown (see library_mixins/discover.py)."""
    pick = library_manager.pick_discover_item(user.id)
    if pick is None:
        raise HTTPException(status_code=404, detail="The library has no downloaded videos yet")
    return pick


@router.get("/api/library/{video_id}/item")
def get_library_item(video_id: str, user: AuthContext = Depends(require_permission("library.view"))):
    """One library item -- the video page uses it for videos that are not on
    the library page currently loaded (e.g. opened from Issues or a link)."""
    item = library_manager.get_item_dict(video_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Not found")
    return library_manager.with_watch([scrub_item(item, user)], user)[0]


class PositionReport(BaseModel):
    position: float = Field(..., ge=0, le=7 * 24 * 3600)
    duration: float = Field(..., gt=0, le=7 * 24 * 3600)


@router.put("/api/library/{video_id}/position")
def save_position(video_id: str, body: PositionReport, user: AuthContext = Depends(require_permission("library.stream"))):
    """Where I am in this video (2.24.0): the player, every few seconds while playing and when it stops."""
    try:
        return library_manager.save_watch_position(str(user.id), video_id, body.position, body.duration)
    except KeyError:
        raise HTTPException(status_code=404, detail="Not found") from None


@router.delete("/api/library/{video_id}/position")
def clear_position(video_id: str, user: AuthContext = Depends(require_permission("library.stream"))):
    """Start over: forget where I was in this video."""
    library_manager.clear_watch_position(str(user.id), video_id)
    return {"ok": True}


class WatchReport(BaseModel):
    seconds: float = Field(0, ge=0, le=600)
    started: bool = False


@router.post("/api/library/{video_id}/watch")
def report_watch(video_id: str, body: WatchReport, user: AuthContext = Depends(require_permission("library.view"))):
    """The player's watched time (Stats -> watching): sent every ~30 s while playing and when it stops."""
    try:
        library_manager.record_watch(user.id, video_id, body.seconds, body.started)
    except KeyError:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}


@router.get("/api/library/{video_id}/related")
def get_related_items(video_id: str, limit: int = 12, user: AuthContext = Depends(require_permission("library.view"))):
    try:
        return {"items": library_manager.with_watch(
            [scrub_item(i, user) for i in library_manager.related_items(video_id, max(1, min(limit, 50)))], user)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Not found")


@router.get("/api/library/{video_id}/metadata")
async def get_item_metadata(video_id: str, user: AuthContext = Depends(require_permission("library.view"))):
    meta = await library_manager.get_metadata(video_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="No metadata available")
    item = library_manager.get_item_dict(video_id) or {}
    # App-side state for the video page (not part of the sidecar file).
    meta["_site_sync"] = {"status": item.get("site_status"), "synced_at": item.get("site_synced_at")}
    return meta


@router.get("/api/library/{video_id}/thumbnail")
async def get_thumbnail(video_id: str, user: AuthContext = Depends(require_permission("library.view"))):
    item = library_manager.get_item_dict(video_id)
    if not item or not item.get("thumbnail_path"):
        raise HTTPException(status_code=404, detail="No thumbnail available")
    path = Path(item["thumbnail_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail file missing on disk")
    media_type = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    return FileResponse(str(path), media_type=media_type)


@router.get("/api/library/{video_id}/stream")
async def stream_video(video_id: str, request: Request, user: AuthContext = Depends(require_permission("library.stream"))):
    item = library_manager.get_item_dict(video_id)
    if not item or not item.get("video_path"):
        raise HTTPException(status_code=404, detail="Video not found")
    path = Path(item["video_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk")
    return stream_file(path, request)


# ---- Converting while watching (2.13.0, backend/transcode.py) ----
@router.post("/api/library/{video_id}/live")
async def start_live(video_id: str, user: AuthContext = Depends(require_permission("library.stream"))):
    item = library_manager.get_item_dict(video_id)
    if not item or not item.get("video_path"):
        raise HTTPException(status_code=404, detail="Video not found")
    try:
        s = await live_transcoder.start(get_settings(), video_id, user.id, item["video_path"],
                                        title=item.get("title") or "", username=user.username)
    except TranscodeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"session": s.sid, "playlist": f"/api/live/{s.sid}/index.m3u8", "height": s.height, "duration": s.duration}


def _live_session(sid: str, user: AuthContext):
    try:
        return live_transcoder.get(sid, user.id)
    except KeyError:
        if sid in live_transcoder.stopped:  # stopped on Tasks: the player plays the original instead
            raise HTTPException(status_code=410, detail="Converting was stopped on Tasks") from None
        raise HTTPException(status_code=404, detail="This conversion has ended -- start the video again") from None


@router.get("/api/live/{sid}/index.m3u8")
async def live_playlist(sid: str, user: AuthContext = Depends(require_permission("library.stream"))):
    s = _live_session(sid, user)
    return Response(live_transcoder.playlist(s), media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-store"})


@router.get("/api/live/{sid}/{n}.ts")
async def live_segment(sid: str, n: int, user: AuthContext = Depends(require_permission("library.stream"))):
    s = _live_session(sid, user)
    try:
        path = await live_transcoder.segment(get_settings(), s, n)
    except TranscodeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileResponse(str(path), media_type="video/mp2t", headers={"Cache-Control": "no-store"})


@router.delete("/api/live/{sid}")
async def end_live(sid: str, user: AuthContext = Depends(require_permission("library.stream"))):
    try:
        live_transcoder.get(sid, user.id)
    except KeyError:
        return {"ok": True}
    live_transcoder.end(sid)
    return {"ok": True}


@router.get("/api/library/{video_id}/preview")
async def stream_preview(video_id: str, request: Request, user: AuthContext = Depends(require_permission("library.view"))):
    # Converts previews browsers can't decode (10-bit) the first time they're asked for.
    preview_path = await library_manager.ensure_playable_preview(video_id)
    if not preview_path:
        raise HTTPException(status_code=404, detail="No preview available")
    path = Path(preview_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Preview file missing on disk")
    return stream_file(path, request)


# ------------------------------------------------------- upload from computer
class LocalUploadStart(BaseModel):
    filename: str
    size: int
    title: str
    creator: str = ""
    upload_date: Optional[str] = None
    description: str = ""
    tags: list[str] = []
    performers: list[str] = []
    music: list[str] = []
    flags: list[str] = []


@router.post("/api/library/local")
def start_local_upload(body: LocalUploadStart, user: AuthContext = Depends(require_permission("videos.upload"))):
    """Step 1 of an upload: checks the details and the free space, returns an
    upload id. Step 2 sends the file itself to PUT /api/library/local/{id}."""
    try:
        return library_manager.start_local_upload(body.model_dump(), user.username, user.id)
    except UploadError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.put("/api/library/local/{upload_id}")
async def receive_local_upload(upload_id: str, request: Request, user: AuthContext = Depends(require_permission("videos.upload"))):
    """Step 2: the raw file as the request body (streamed to disk, never held
    in memory). Returns the new library item."""
    try:
        return await library_manager.receive_local_upload(upload_id, request.stream(), user)
    except UploadError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except ClientDisconnect as exc:
        raise HTTPException(status_code=400, detail="The upload was cancelled") from exc


@router.get("/api/library/local/{upload_id}")
def local_upload_status(upload_id: str, user: AuthContext = Depends(require_permission("videos.upload"))):
    """Where an upload is: checking, remuxing, needs_conversion (waiting for
    the codec choice), converting (with progress), finishing, done (with the
    new item) or failed."""
    try:
        return library_manager.local_upload_status(upload_id, user)
    except UploadError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.post("/api/library/local/{upload_id}/convert")
async def convert_local_upload(upload_id: str, body: UploadConvertRequest, user: AuthContext = Depends(require_permission("videos.upload"))):
    try:
        return await library_manager.convert_local_upload(upload_id, body.codec, body.crf, body.preset, user)
    except UploadError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


class NameRename(BaseModel):
    field: str = Field(pattern="^(tags|performers|music)$")
    old: str = Field(min_length=1, max_length=200)
    new: str = Field(min_length=1, max_length=200)
    remember: bool = True


class NameRuleDelete(BaseModel):
    field: str = Field(pattern="^(tags|performers|music)$")
    old: str = Field(min_length=1, max_length=200)


class BulkEdit(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=5000)
    changes: dict[str, dict[str, list[str]]]


@router.get("/api/names")
def list_names(field: str = Query("tags", pattern="^(tags|performers|music)$"),
               user: AuthContext = Depends(require_permission("names.manage"))):
    """Management -> Names: every value with its video count, and the remembered fixes."""
    return library_manager.name_list(field)


@router.get("/api/names/typos")
def name_typos(field: str = Query("tags", pattern="^(tags|performers|music)$"),
               user: AuthContext = Depends(require_permission("names.manage"))):
    """Names one or two letters apart that may be the same thing misspelled."""
    return {"pairs": library_manager.possible_typos(field)}


@router.post("/api/names/rename")
async def rename_name(body: NameRename, user: AuthContext = Depends(require_permission("names.manage"))):
    """Renames (or merges) a value in every video; remember=True keeps it as a rule for new videos."""
    try:
        return await library_manager.rename_name(body.field, body.old, body.new, body.remember)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/names/rules/delete")
def delete_name_rule(body: NameRuleDelete, user: AuthContext = Depends(require_permission("names.manage"))):
    try:
        library_manager.remove_name_rule(body.field, body.old)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/api/library/bulk-edit")
async def bulk_edit(body: BulkEdit, user: AuthContext = Depends(require_permission("videos.edit"))):
    """Adds / removes tags, performers or music on many videos at once (as hand edits)."""
    try:
        return await library_manager.bulk_edit(body.ids, body.changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class SpellingChoice(BaseModel):
    field: str
    value: str
    spelling: Optional[str] = None  # None = automatic (the most used)


@router.get("/api/library/spellings")
def list_spellings(field: str = Query("tags", pattern="^(tags|performers|music)$"),
                   user: AuthContext = Depends(require_permission("names.manage"))):
    """Values written in more than one way (case only), with the shown spelling (see spellings.py)."""
    return {"groups": spellings.groups(field)}


@router.post("/api/library/spellings")
def choose_spelling(body: SpellingChoice, user: AuthContext = Depends(require_permission("names.manage"))):
    try:
        spellings.pin(body.field, body.value, body.spelling)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    library_manager.db._note_change(["spellings"])  # pages showing tags refresh
    return {"ok": True}


@router.get("/api/library/{video_id}/frame")
async def video_frame(video_id: str, t: float = Query(0, ge=0), w: int = Query(480, ge=64, le=1920),
                      user: AuthContext = Depends(require_permission("library.view"))):
    """One frame of the video as a JPEG (any codec FFmpeg reads)."""
    try:
        data = await library_manager.grab_frame(video_id, t, w)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


_IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
_MAX_THUMB_BYTES = 15 * 1024 * 1024


@router.put("/api/library/{video_id}/thumbnail/image")
async def upload_thumbnail_image(video_id: str, request: Request, user: AuthContext = Depends(require_any("videos.edit", "videos.upload"))):
    """Your own picture as the thumbnail: the raw image as the body."""
    ext = _IMAGE_TYPES.get((request.headers.get("content-type") or "").split(";")[0].strip().lower())
    if not ext:
        raise HTTPException(status_code=415, detail="Send a JPEG, PNG or WebP image")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > _MAX_THUMB_BYTES:
            raise HTTPException(status_code=413, detail="That image is too big (15 MB at most)")
    try:
        return await library_manager.set_thumbnail(video_id, "upload", image=bytes(data), image_ext=ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/library/{video_id}/preview-plan")
async def preview_plan(video_id: str, user: AuthContext = Depends(require_any("videos.edit", "videos.upload"))):
    try:
        return await library_manager.preview_plan(video_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/library/{video_id}/preview")
async def make_preview(video_id: str, body: PreviewFromTimestampsRequest, user: AuthContext = Depends(require_any("videos.edit", "videos.upload"))):
    """Makes the hover preview from the given clip start times."""
    try:
        return await library_manager.make_preview(video_id, body.timestamps, body.clip_seconds)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/library/local/{upload_id}")
def cancel_local_upload(upload_id: str, user: AuthContext = Depends(require_permission("videos.upload"))):
    try:
        library_manager.cancel_local_upload(upload_id, user)
    except UploadError:
        pass  # already gone
    return {"ok": True}
