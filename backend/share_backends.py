"""
Backend implementations for the "Share To" feature. Each `share_via_*`
function uploads a local file to one destination and returns a public URL
on success, or raises ShareError with a message that's safe to show
directly in the UI. All are async and accept an optional progress_cb(pct)
called with a 0-100 float as the upload proceeds.

Gofile/Pixeldrain are adapted from the standalone upload_gofile_api.py /
upload_put_pixeldrain_api.py scripts -- same endpoints and request shape,
rewritten around httpx so they can report progress and run inside the
FastAPI event loop instead of a blocking `requests` call.

Telegram is adapted from tg_upload_XXX_CH.py -- same Pyrogram user-session
approach (not a bot, so the 4GB Telegram Premium upload limit applies
instead of the 50MB bot API limit), same FloodWait retry loop, same
message-URL construction for channels vs. private groups.
"""
from __future__ import annotations

import asyncio
import base64
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

import httpx
from loguru import logger
from pymediainfo import MediaInfo

from . import proxy
from .config import Settings

ProgressCallback = Optional[Callable[[float], None]]

_CHUNK_SIZE = 1024 * 1024  # 1 MB, matches the original scripts' read size
_UPLOAD_TIMEOUT = httpx.Timeout(30.0, read=None, write=None)  # no read/write timeout for large streamed uploads
_TELEGRAM_THUMB_MAX_SIDE = 320  # Telegram's own limit for video thumbnails
_TELEGRAM_THUMB_MAX_BYTES = 200 * 1024


class ShareError(RuntimeError):
    """Raised when a share upload fails, with a message safe to show in the UI."""


def _make_telegram_thumbnail_sync(thumbnail_path: Path) -> Optional[Path]:
    """Telegram requires video thumbnails to be JPEG, <=320px on the long
    side, and under 200KB -- our downloaded thumbnail (webp/png/jpg, full
    size) usually doesn't qualify as-is. Re-encodes it via ffmpeg into a
    temp file that does. Returns None (falling back to Telegram generating
    its own thumbnail from the video) if anything about that fails, rather
    than blocking the whole upload over a cosmetic detail."""
    try:
        out_path = Path(tempfile.gettempdir()) / f"{thumbnail_path.stem}_tg_thumb.jpg"
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(thumbnail_path),
            "-vf", f"scale='min({_TELEGRAM_THUMB_MAX_SIDE},iw)':'min({_TELEGRAM_THUMB_MAX_SIDE},ih)':force_original_aspect_ratio=decrease",
            "-q:v", "4",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0 or not out_path.exists():
            return None
        if out_path.stat().st_size > _TELEGRAM_THUMB_MAX_BYTES:
            out_path.unlink(missing_ok=True)
            return None
        return out_path
    except Exception:
        logger.debug("Could not prepare a Telegram thumbnail from {}", thumbnail_path)
        return None


def _probe_video_dimensions(file_path: Path) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """Returns (duration_seconds, width, height) so Telegram can show the
    player immediately instead of probing the file itself first."""
    try:
        mi = MediaInfo.parse(str(file_path))
        video = next((t for t in mi.tracks if t.track_type == "Video"), None)
        if not video:
            return None, None, None
        duration = int(float(video.duration) / 1000) if video.duration else None
        return duration, video.width, video.height
    except Exception:
        return None, None, None


def _chunked_reader(file_path: Path, file_size: int, progress_cb: ProgressCallback):
    """Shared helper: yields the file in 1MB chunks, reporting % as it goes."""
    sent = 0
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            sent += len(chunk)
            if progress_cb and file_size:
                progress_cb(round(sent / file_size * 100, 1))
            yield chunk


async def _share_proxy(destination: str) -> Optional[str]:
    """The proxy URL when Server settings -> Proxy sends uploads to
    `destination` through it; refuses (rather than going direct) while the
    proxy fails its check."""
    url = proxy.for_share(destination)
    if url and not await proxy.usable():
        raise ShareError("The proxy isn't working, so nothing was uploaded (Server settings → Proxy → Test)")
    return url


TELEGRAM_RELOGIN_HINT = "The Telegram login has expired — sign in again in Server settings → Connections → Telegram."


def telegram_client(settings: Settings, name: Optional[str] = None):
    """A Pyrogram client for the saved Telegram login (or a new one: `name`),
    through the proxy when Server settings -> Proxy says so."""
    from pyrogram import Client
    cfg = settings.share
    session_dir = settings.session_dir / "telegram"
    session_dir.mkdir(parents=True, exist_ok=True)
    kwargs = {}
    via = proxy.pyrogram_proxy(proxy.for_share("telegram"))
    if via:
        kwargs["proxy"] = via
    return Client(name or cfg.telegram_session_name, api_id=cfg.telegram_api_id, api_hash=cfg.telegram_api_hash,
                  workdir=str(session_dir), **kwargs)


def is_expired_login(exc: BaseException) -> bool:
    """Telegram no longer accepts the saved login (AUTH_KEY_UNREGISTERED and friends)."""
    name = type(exc).__name__
    return name in {"AuthKeyUnregistered", "AuthKeyInvalid", "SessionRevoked", "SessionExpired", "UserDeactivated",
                    "AuthKeyDuplicated", "Unauthorized"} or "AUTH_KEY_UNREGISTERED" in str(exc)


# --------------------------------------------------------------- Pixeldrain
async def share_via_pixeldrain(
    file_path: Path,
    settings: Settings,
    progress_cb: ProgressCallback = None,
) -> str:
    api_key = settings.share.pixeldrain_api_key
    if not api_key:
        raise ShareError("Pixeldrain API key is not configured (Admin \u2192 Credentials)")

    auth_header = {"Authorization": "Basic " + base64.b64encode(f":{api_key}".encode()).decode()}
    file_size = file_path.stat().st_size
    url = f"https://pixeldrain.com/api/file/{quote(file_path.name)}"

    async def body():
        for chunk in _chunked_reader(file_path, file_size, progress_cb):
            yield chunk
            await asyncio.sleep(0)  # yield control back to the event loop between chunks

    via = await _share_proxy("pixeldrain")
    try:
        async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT, proxy=via) as client:
            resp = await client.put(url, headers=auth_header, content=body())
    except httpx.HTTPError as exc:
        raise ShareError(f"Pixeldrain upload failed: {exc}") from exc

    if resp.status_code != 201:
        raise ShareError(f"Pixeldrain upload failed ({resp.status_code}): {resp.text[:300]}")

    try:
        file_id = resp.json()["id"]
    except Exception as exc:  # noqa: BLE001
        raise ShareError(f"Pixeldrain returned an unexpected response: {resp.text[:300]}") from exc

    return f"https://pixeldrain.com/u/{file_id}"


# ------------------------------------------------------------------ Gofile
async def share_via_gofile(
    file_path: Path,
    settings: Settings,
    progress_cb: ProgressCallback = None,
) -> str:
    cfg = settings.share
    if not cfg.gofile_api_key:
        raise ShareError("Gofile API key is not configured (Admin \u2192 Credentials)")

    base_url = "https://upload.gofile.io/uploadFile"
    if cfg.gofile_region:
        base_url = f"https://upload-{cfg.gofile_region}.gofile.io/uploadFile"

    boundary = uuid.uuid4().hex
    filename = file_path.name
    file_size = file_path.stat().st_size

    preamble = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()

    folder_field = b""
    if cfg.gofile_folder_id:
        folder_field = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="folderId"\r\n\r\n'
            f"{cfg.gofile_folder_id}\r\n"
        ).encode()

    epilogue = f"--{boundary}--\r\n".encode()

    async def body():
        yield preamble
        for chunk in _chunked_reader(file_path, file_size, progress_cb):
            yield chunk
            await asyncio.sleep(0)
        yield b"\r\n"
        if folder_field:
            yield folder_field
        yield epilogue

    headers = {
        "Authorization": f"Bearer {cfg.gofile_api_key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }

    via = await _share_proxy("gofile")
    try:
        async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT, proxy=via) as client:
            resp = await client.post(base_url, headers=headers, content=body())
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise ShareError(f"Gofile upload failed: {exc}") from exc

    if data.get("status") != "ok":
        raise ShareError(f"Gofile upload failed: {data}")

    result = data.get("data", {})
    link = result.get("downloadPage") or result.get("directLink")
    if not link:
        raise ShareError("Gofile upload succeeded but no download link was returned")
    return link


# ---------------------------------------------------------------- Telegram
async def share_via_telegram(
    file_path: Path,
    settings: Settings,
    target: str,
    caption: Optional[str],
    progress_cb: ProgressCallback = None,
    thumbnail_path: Optional[Path] = None,
    file_name: Optional[str] = None,
) -> str:
    """target is 'channel' or 'group', mapping to TELEGRAM_CHANNEL_ID /
    the Telegram group set in Admin -> Credentials. Uses a Pyrogram *user* session (not a bot),
    so the upload size limit is whatever your account's Telegram Premium
    status allows (4GB), not the 50MB bot API cap.

    thumbnail_path, if given, is converted to a Telegram-legal JPEG thumb;
    file_name overrides the filename Telegram displays (otherwise it's
    inferred from file_path's own basename, which is normally the same
    thing -- pass it explicitly so a rename right before sharing can't
    leave the two out of sync)."""
    cfg = settings.share
    if not cfg.telegram_api_id or not cfg.telegram_api_hash:
        raise ShareError("Telegram API ID / hash are not configured (Admin \u2192 Credentials)")

    chat_ref = cfg.telegram_channel_id if target == "channel" else cfg.telegram_group_id
    if not chat_ref:
        raise ShareError(f"No Telegram {target} configured (Admin \u2192 Credentials)")

    session_dir = settings.session_dir / "telegram"
    if not (session_dir / f"{cfg.telegram_session_name}.session").exists():
        raise ShareError("Not signed in to Telegram yet — sign in once in Server settings → Connections → Telegram.")

    from pyrogram.errors import FloodWait

    if proxy.for_share("telegram") and not await proxy.usable():
        raise ShareError("The proxy isn't working, so nothing was uploaded (Server settings → Proxy → Test)")
    app = telegram_client(settings)

    def on_progress(current: int, total: int) -> None:
        if progress_cb and total:
            progress_cb(round(current / total * 100, 1))

    tg_thumb = None
    if thumbnail_path and thumbnail_path.exists():
        tg_thumb = await asyncio.to_thread(_make_telegram_thumbnail_sync, thumbnail_path)

    duration, width, height = await asyncio.to_thread(_probe_video_dimensions, file_path)
    caption_text = (caption or file_path.stem)[:1024]  # Telegram's hard cap on media captions

    try:
        await app.start()
    except Exception as exc:  # noqa: BLE001
        if is_expired_login(exc):
            raise ShareError(TELEGRAM_RELOGIN_HINT) from exc
        raise ShareError(f"Could not connect to Telegram: {exc}") from exc
    try:
        try:
            chat = await app.get_chat(chat_ref)
        except Exception as exc:  # noqa: BLE001
            raise ShareError(f"Could not resolve Telegram {target} '{chat_ref}': {exc}") from exc

        message = None
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                message = await app.send_video(
                    chat_id=chat.id,
                    video=str(file_path),
                    caption=caption_text,
                    supports_streaming=True,
                    progress=on_progress,
                    thumb=str(tg_thumb) if tg_thumb else None,
                    file_name=file_name or file_path.name,
                    duration=duration or 0,
                    width=width or 0,
                    height=height or 0,
                )
                break
            except FloodWait as exc:
                logger.warning("Telegram FloodWait: sleeping {}s", exc.value + 1)
                await asyncio.sleep(exc.value + 1)
            except Exception as exc:  # noqa: BLE001
                if attempt == max_retries:
                    raise ShareError(f"Telegram upload failed: {exc}") from exc
                logger.warning("Telegram upload attempt {} failed, retrying: {}", attempt, exc)
                await asyncio.sleep(2)

        if message is None:
            raise ShareError("Telegram upload failed after retries")

        if chat.username:
            return f"https://t.me/{chat.username}/{message.id}"
        # private channels/groups: chat.id looks like -1001234567890
        return f"https://t.me/c/{str(chat.id)[4:]}/{message.id}"
    finally:
        await app.stop()
        if tg_thumb:
            tg_thumb.unlink(missing_ok=True)