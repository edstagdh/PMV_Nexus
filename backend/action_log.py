"""
Logs every change made through the API (any POST/PUT/PATCH/DELETE under
/api/) as an "action" in Admin -> Logs: who did it, what (the route's
function name, made readable), on which video, the result and how long it
took. Request bodies are never logged (they can hold passwords and keys).

A plain ASGI middleware rather than @app.middleware("http"): that one wraps
every response, including the never-ending live-update stream, which it
doesn't handle well. GET requests pass straight through untouched.
"""
from __future__ import annotations

import json
import re
import time

from loguru import logger

from . import ip_privacy
from .library_manager import library_manager
from .security import COOKIE_NAME, _read_session_payload

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
_ITEM_PATH = re.compile(r"^/api/(?:library|external)/([^/]+)(?:/|$)")
_PLAYLIST_PATH = re.compile(r"^/api/playlists/([0-9a-f]{32})(?:/|$)")
_MAX_PEEK = 64 * 1024

# Routes that log themselves in more detail (sign-in success/failure).
_SELF_LOGGED = {"login"}
# Frequent, routine changes: recorded at DEBUG, so hidden at the default level.
_QUIET = {"update_my_settings", "report_watch", "report_playing", "save_position", "mark_read", "end_live", "receive_local_upload"}

# What each change is called in Management -> Logs, by the route's function
# name. Every changing route has an entry (Tools/check_action_labels.py checks
# it); a missing one falls back to the function name made readable.
_LABELS = {
    # account
    "logout": "Signed out",
    "first_run_setup": "Created the first admin account",
    "change_own_password": "Changed own password",
    "update_my_settings": "Changed My settings",
    "delete_own_account": "Deleted own account",
    # users & access (admin)
    "create_user": "Created user",
    "update_user": "Changed user",
    "delete_user": "Deleted user",
    "admin_reset_password": "Set a new password for user",
    "set_user_access": "Changed user's role / groups / permissions",
    "create_role": "Created role",
    "update_role": "Changed role permissions",
    "delete_role": "Deleted role",
    "create_group": "Created group",
    "update_group": "Changed group",
    "delete_group": "Deleted group",
    # adding videos
    "add_to_library": "Add to library",
    "add_to_library_external": "Add to library (userscript)",
    "retry_item": "Retry download",
    "move_download": "Move in download queue",
    "start_local_upload": "Upload started",
    "receive_local_upload": "Upload received",
    "cancel_local_upload": "Upload cancelled",
    "convert_local_upload": "Convert upload",
    "discover_random_video": "Discover (random video)",
    # one video
    "remove_from_library": "Remove from library",
    "edit_item_details": "Edit details",
    "rename_item": "Rename video",
    "set_thumbnail": "Change thumbnail",
    "upload_thumbnail_image": "Upload thumbnail image",
    "make_preview": "Make preview clip",
    "reencode_item": "Re-encode",
    "cancel_reencode": "Cancel re-encode",
    "move_reencode": "Move in re-encode queue",
    "repair_item": "Repair / update",
    "cancel_repair_item": "Cancel repair",
    "resolve_repair": "Resolve repair conflict",
    "report_watch": "Watched",
    "report_playing": "Playing",
    "save_position": "Save my place in a video",
    "clear_position": "Start a video over",
    "make_missing_storyboards": "Make seekbar previews for every video",
    "start_live": "Play converted (720p)",
    "end_live": "Stop converted playback",
    "scan_video": "Find songs",
    # songs
    "add": "Add song to Music",
    "dismiss": "Dismiss suggested song",
    "scan_now": "Find songs in videos without music",
    "bulk_action": "Picked videos",  # + the action, see _bulk_label
    # several videos / library care
    "bulk_edit": "Bulk edit",
    "rename_name": "Rename / merge name",
    "delete_name_rule": "Forget remembered name fix",
    "choose_spelling": "Choose spelling",
    "merge_library_items": "Merge duplicates",
    "ignore_library_issue": "Not duplicates (ignore)",
    "trigger_duplicate_scan": "Start duplicate scan",
    "start_site_sync": "Sync with PMVHaven",
    "delete_untracked_files": "Delete untracked files",
    "repair_all": "Repair everything",
    "sync_metadata": "Sync from metadata files",
    # performers
    "performers_link": "Link performer to ThePornDB",
    "performers_skip": "Skip performer match",
    "performers_split": "Split performer names",
    "performers_match_now": "Match performers now",
    "performers_refresh": "Refresh performer profile",
    "performers_convert_photos": "Convert performer photos to WebP",
    "performers_add_photo": "Add photo to performer",
    "performers_remove_photo": "Remove performer photo",
    "performers_main_photo": "Change performer's main photo",
    "performers_set_gender": "Set performer placeholder (female / male)",
    # playlists
    "create_playlist": "Create playlist",
    "update_playlist": "Change playlist",
    "delete_playlist": "Delete playlist",
    "add_items": "Add to playlist",
    "remove_items": "Remove from playlist",
    "reorder": "Reorder playlist",
    "set_playlist_cover": "Change playlist cover",
    "upload_playlist_cover": "Upload playlist cover image",
    "set_favorite": "Favorites",  # replaced by Add to / Remove from Favorites below
    # tasks & scheduler
    "stop_library_activity": "Stop job",
    "create_job": "Create scheduled job",
    "update_job": "Change scheduled job",
    "delete_job": "Delete scheduled job",
    "run_job_now": "Run scheduled job now",
    # backups & system
    "create_backup": "Create backup",
    "run_extension_action": "Extension action",
    "run_extension_panel": "Extension settings",
    "delete_backup": "Delete backup",
    "stage_restore": "Restore backup (at next start)",
    "cancel_restore": "Cancel restore",
    "test_backup": "Restore test of backup",
    "set_backup_password": "Change the backup password",
    "check_for_update": "Check for updates",
    "capture_snapshot_now": "Stats snapshot",
    "clear": "Clear notifications",
    "mark_read": "Mark notifications read",
    # server settings
    "set_download_settings": "Server settings: Download",
    "set_download_dir": "Change download folder",
    "set_cache_dir": "Change cache folder",
    "browse_dirs_mkdir": "Create folder",
    "set_reencode_settings": "Server settings: Re-encode",
    "set_features": "Server settings: switch re-encoding / sharing on or off",
    "set_playback_settings": "Server settings: Converting while watching",
    "set_upload_settings": "Server settings: Uploads",
    "test_upload_scanner": "Test the virus scanner",
    "set_duplicate_scan_settings": "Server settings: Duplicate scan",
    "set_logging_settings": "Server settings: Logging",
    "set_proxy_settings": "Server settings: Proxy",
    "proxy_test": "Proxy speed test",
    "performer_settings": "Server settings: Performers",
    "set_tray_settings": "Server settings: Opening the app",
    "save_settings": "Songs settings",
    # credentials & connections
    "update_credentials": "Change credentials",
    "generate_credential": "Generate credential",
    "test_pmvhaven_api_key": "Test PMVHaven API key",
    "test_virustotal_key": "Test VirusTotal key",
    "test_gemini": "Test Gemini API key",
    "local_stop": "App closed by Setup (update)",
}


def _cookie(scope, name: str) -> str | None:
    for key, value in scope.get("headers") or []:
        if key == b"cookie":
            for part in value.decode("latin-1").split(";"):
                k, _, v = part.strip().partition("=")
                if k == name:
                    return v
    return None


def _username(scope) -> str | None:
    token = _cookie(scope, COOKIE_NAME)
    payload = _read_session_payload(token) if token else None
    if payload:
        return payload.get("username")
    for key, _value in scope.get("headers") or []:
        if key == b"x-api-key":
            return "api-key"
    return None


def _category(path: str) -> str:
    """'/api/library/issues/merge' -> 'duplicates', '/api/library/x/rename' -> 'library', '/api/me/...' -> 'account'."""
    parts = path.strip("/").split("/")
    seg = parts[1] if len(parts) > 1 else "api"
    if seg == "library" and len(parts) > 2 and parts[2] == "issues":
        return "duplicates"
    return {"me": "account"}.get(seg, seg)


def _label(endpoint_name: str) -> str:
    if endpoint_name in _LABELS:
        return _LABELS[endpoint_name]
    words = endpoint_name.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def _item_title(item_id: str) -> str | None:
    item = library_manager.get_item_dict(item_id)
    if not item:
        return None
    return item.get("title") or item.get("url")


def _playlist_name(playlist_id: str) -> str | None:
    row = library_manager.db.get_playlist(playlist_id)
    return row["name"] if row else None


# ---- what an action was about -----------------------------------------
# Besides the video / playlist in the address, the log entry names its target
# from (in this order): other ids in the address (user, scheduled job, backup,
# upload, performer), the response (e.g. the video Discover picked, the video
# just added) and the request body (title, name, username, url, video ids...).
# Only the harmless fields listed here are ever read from a body -- never
# passwords, keys, codes or phone numbers -- and bodies are never stored.

_PATH_TARGETS = [
    (re.compile(r"^/api/users/([^/]+)"), lambda m: _username_of(m.group(1)), "user"),
    (re.compile(r"^/api/scheduler/jobs/([^/]+)"), lambda m: _job_name(m.group(1)), "job"),
    (re.compile(r"^/api/backups/([^/]+)"), lambda m: m.group(1), "backup"),
    (re.compile(r"^/api/library/local/([0-9a-f]{32})"), lambda m: _upload_title(m.group(1)), "upload"),
    (re.compile(r"^/api/performers/([0-9a-f]{32}|n[0-9a-f]{31})/"), lambda m: _performer_name(m.group(1)), "performer"),
    (re.compile(r"^/api/credentials/([a-z0-9_]+)/"), lambda m: m.group(1), "credential"),
    (re.compile(r"^/api/roles/([^/]+)"), lambda m: m.group(1), "role"),
    (re.compile(r"^/api/groups/([^/]+)"), lambda m: _group_name(m.group(1)), "group"),
    (re.compile(r"^/api/library/activity/([^/]+)/stop"), lambda m: _activity_title(m.group(1)), "job"),
]
# Request-body fields that may appear in the log (scalars and short lists).
_SAFE_BODY_KEYS = (
    "url", "urls", "title", "name", "username", "role", "disabled", "filename", "creator", "upload_date",
    "item_id", "item_ids", "keep_id", "merge_ids", "codec", "crf", "preset",
    "direction", "source", "seconds", "visibility", "kind", "field", "value", "spelling", "tpdb_id", "on",
    "new_dir", "move_existing", "max_concurrent", "job_type", "cron_expression", "enabled", "level", "site_mode",
    "proxy_downloads", "auto_convert", "convert_codec", "merge_aliases", "auto_match", "timestamps",
    "label", "gender", "permissions", "groups", "max_height", "cpu_percent", "max_sessions", "live_convert", "virus_scan",
    "hash_check", "interval", "max_samples", "per_run", "rescan_days", "gemini", "old", "new", "remember",
)
_SECRET_HINT = re.compile(r"pass|secret|token|key|hash|code|phone|session", re.I)


def _username_of(user_id: str) -> str | None:
    user = library_manager.db.get_user_by_id(user_id)
    return user.username if user else None


def _job_name(job_id: str) -> str | None:
    row = library_manager.db.get_scheduled_job(job_id)
    return row.get("name") if row else None


def _group_name(group_id: str) -> str | None:
    g = next((g for g in library_manager.db.list_groups() if g.get("id") == group_id), None)
    return g.get("name") if g else None


def _activity_title(activity_id: str) -> str | None:
    from . import activity
    a = activity.get(activity_id)
    return a.title if a else None


_SONG_PATH = re.compile(r"^/api/songs/(\d+)/")
_LIVE_PATH = re.compile(r"^/api/live/([^/]+)$")


def _song_target(suggestion_id: int) -> tuple[str | None, str | None, str | None]:
    """(video id, video title, song) of a song suggestion."""
    s = library_manager.db.get_song_suggestion(suggestion_id)
    if not s:
        return None, None, None
    return s["item_id"], _item_title(s["item_id"]), s["label"]


def _live_target(sid: str) -> tuple[str | None, str | None]:
    from .transcode import live_transcoder
    s = live_transcoder._sessions.get(sid)
    return (s.item_id, _item_title(s.item_id)) if s else (None, None)


def _upload_title(upload_id: str) -> str | None:
    up = getattr(library_manager, "_uploads", {}).get(upload_id)
    return up.get("title") if up else None


def _performer_name(performer_id: str) -> str | None:
    """A ThePornDB profile id or a local id (performer without a profile, 2.17.0)."""
    try:
        return library_manager._photo_owner_name(performer_id)
    except Exception:  # noqa: BLE001 -- logging must never break a request
        return None


def _titles(ids) -> str | None:
    """“A”, “B” and 3 more -- for a list of video ids."""
    if not isinstance(ids, list) or not ids:
        return None
    names = [t for t in (_item_title(str(i)) for i in ids[:3]) if t]
    if not names:
        return f"{len(ids)} videos"
    text = ", ".join(f"“{n}”" for n in names)
    return text + (f" and {len(ids) - 3} more" if len(ids) > 3 else "")


def _short(value, limit: int = 160) -> str:
    if isinstance(value, list):
        text = ", ".join(str(v) for v in value[:5]) + (f" (+{len(value) - 5})" if len(value) > 5 else "")
    elif isinstance(value, dict):
        text = "{…}"
    else:
        text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _body_details(body: dict) -> dict:
    out = {}
    for key in _SAFE_BODY_KEYS:
        if key in body and not _SECRET_HINT.search(key) and body[key] not in (None, "", [], {}):
            out[key] = body[key]
    # Settings forms: which fields were sent (names only).
    if not out and body:
        names = [k for k in body if not _SECRET_HINT.search(k)]
        if names:
            out["fields"] = names[:20]
    return out


def _body_target(body: dict) -> str | None:
    for key in ("title", "name", "username", "filename"):
        if isinstance(body.get(key), str) and body[key].strip():
            return body[key].strip()
    for key in ("item_id", "keep_id"):
        if isinstance(body.get(key), str):
            t = _item_title(body[key])
            if t:
                return t
    for key in ("item_ids", "merge_ids", "ids"):
        t = _titles(body.get(key))
        if t:
            return t.strip("“”") if t.count("“") == 1 else t
    if isinstance(body.get("url"), str):
        return body["url"]
    if isinstance(body.get("urls"), list) and body["urls"]:
        urls = body["urls"]
        return urls[0] if len(urls) == 1 else f"{urls[0]} and {len(urls) - 1} more"
    return None


def _response_target(data) -> tuple[str | None, str | None]:
    """(item_id, title) of a video in a JSON response (one item, or the first of a list)."""
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        data = data["items"]
    if isinstance(data, list):
        data = next((d for d in data if isinstance(d, dict)), None)
        many = True
    else:
        many = False
    if isinstance(data, dict) and isinstance(data.get("id"), str):
        title = _item_title(data["id"])  # a library item (Discover answers with just its id)
        if title:
            return data["id"], title + (" …" if many else "")
    return None, None


def _json(raw: bytes):
    try:
        return json.loads(raw) if raw else None
    except (ValueError, UnicodeDecodeError):
        return None


class ActionLogMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in _MUTATING or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        path = scope["path"]
        user = _username(scope)
        match = _ITEM_PATH.match(path)
        item_id = match.group(1) if match else None
        # Looked up before the action runs, so "remove" still knows the title.
        title = _item_title(item_id) if item_id else None
        if not title:
            item_id = None  # e.g. /api/library/discover, or an unknown id
        pl = _PLAYLIST_PATH.match(path)
        if pl:
            title = _playlist_name(pl.group(1))
        song = None
        sm = _SONG_PATH.match(path)
        if sm:
            item_id, title, song = _song_target(int(sm.group(1)))
        lm = _LIVE_PATH.match(path)
        if lm:
            item_id, title = _live_target(lm.group(1))
        if not title:
            for pattern, lookup, _kind in _PATH_TARGETS:
                m = pattern.match(path)
                if m:
                    try:
                        title = lookup(m)
                    except Exception:  # noqa: BLE001
                        title = None
                    break

        # A small JSON body is read here (for its harmless fields) and then
        # handed on to the endpoint unchanged.
        body = None
        headers = dict(scope.get("headers") or [])
        ctype = headers.get(b"content-type", b"").decode("latin-1")
        length = int(headers.get(b"content-length", b"0") or 0)
        if "json" in ctype and 0 < length <= _MAX_PEEK:
            chunks, more = [], True
            while more:
                message = await receive()
                chunks.append(message)
                more = message.get("more_body", False)
            body = _json(b"".join(m.get("body", b"") for m in chunks))
            pending = list(chunks)

            async def receive():  # noqa: F811 -- replays the buffered body
                return pending.pop(0) if pending else {"type": "http.disconnect"}
        details = _body_details(body) if isinstance(body, dict) else {}
        if song:
            details = {"song": song, **details}
        if not title and isinstance(body, dict):
            title = _body_target(body)
            if not item_id and isinstance(body.get("item_id"), str) and _item_title(body["item_id"]):
                item_id = body["item_id"]
        scope.setdefault("state", {})["action_body_on"] = body.get("on") if isinstance(body, dict) else None

        status_code = 500
        response = {"json": False, "chunks": [], "size": 0}

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                rh = dict(message.get("headers") or [])
                response["json"] = b"json" in rh.get(b"content-type", b"")
            elif message["type"] == "http.response.body" and response["json"] and response["size"] <= _MAX_PEEK:
                part = message.get("body", b"")
                response["size"] += len(part)
                if response["size"] <= _MAX_PEEK:
                    response["chunks"].append(part)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            try:
                if not item_id and response["chunks"] and status_code < 400:
                    rid, rtitle = _response_target(_json(b"".join(response["chunks"])))
                    if rid:
                        item_id = rid
                        # A just-added video has no title yet ("Fetching details..."): keep its
                        # link; the entry gets the real title once it's known (log_store.retarget).
                        if not (title and (title.startswith("http") and rtitle.startswith("Fetching details"))):
                            title = title if title and not title.startswith("http") else rtitle
                if not title and response["chunks"] and status_code < 400 and path == "/api/backups":
                    made = _json(b"".join(response["chunks"]))  # "Create backup · pmvnexus_backup_….zip" (2.25.0)
                    title = made.get("name") if isinstance(made, dict) and isinstance(made.get("name"), str) else None
                self._log(scope, user, item_id, title, details, status_code, started)
            except Exception:  # logging must never break a request
                pass

    @staticmethod
    def _log(scope, user, item_id, title, details, status_code, started) -> None:
        endpoint = scope.get("endpoint")
        name = getattr(endpoint, "__name__", "") or ""
        if name in _SELF_LOGGED:
            return
        label = _label(name) if name else f"{scope['method']} {scope['path']}"
        if name == "bulk_action":  # /api/bulk/<action> (2.18.0)
            label = {"songs": "Find songs", "repair": "Repair / update", "reencode": "Re-encode"}.get(scope["path"].rsplit("/", 1)[-1], label) + " (picked videos)"
        if name in ("run_extension_action", "run_extension_panel"):  # 2.30.0: name the extension's action
            from . import extensions
            key = scope["path"].rsplit("/", 1)[-1]
            found = extensions.actions.get(key) or extensions.panels.get(key)
            if found:
                label = f"{found.label} ({found.extension})"
        if name == "set_favorite":
            label = "Add to Favorites" if (scope.get("state") or {}).get("action_body_on") else "Remove from Favorites"
        category = _category(scope["path"])
        client = scope.get("client")
        if title and not title.startswith("“"):
            title = f"“{title}”"
        message = f"{label} · {title}" if title else label
        song = details.get("song")
        if song:  # "Add song to Music · “Song — Artist” → “Video”" (added under a corrected name: label)
            added_as = details.get("label")
            shown = added_as if added_as and added_as != song else song
            message = f"{label} · “{shown}”" + (f" → {title}" if title else "")
        if status_code >= 400:
            message += f" — failed ({status_code})"
        level = "ERROR" if status_code >= 500 else "WARNING" if status_code >= 400 else "INFO"
        if name in _QUIET and status_code < 400:
            level = "DEBUG"
        extra = {f"req_{k}": (_short(v) if not isinstance(v, (bool, int, float)) else v) for k, v in details.items()}
        logger.bind(
            kind="action", user=user, source="api", category=category, event=f"{category}.{name or 'request'}",
            ip=ip_privacy.apply(client[0] if client else None),
            method=scope["method"], path=scope["path"], status=status_code,
            ms=round((time.perf_counter() - started) * 1000), item_id=item_id,
            target=title.strip("“”") if title else None, **extra,
        ).log(level, message)
