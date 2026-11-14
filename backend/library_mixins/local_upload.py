"""Uploading a video from your own computer (Upload page).

  1. start_local_upload(details) checks the form, the file's extension and
     size and the free space, and returns an upload id (only its owner -- or
     the admin -- can use it).
  2. receive_local_upload(id, chunks) streams the request body to
     "<download folder>/.incoming/<id>.<ext>.part" (never into memory; it
     stops the moment more arrives than was announced). Several people can
     send files at the same time.
  3. Then the upload joins ONE first-in-first-out line (2.11.0): a single
     worker takes the uploads in the order they arrived and, for each:
       - checks the first bytes match the extension (upload_safety.sniff),
       - scans it with the server's antivirus if there is one (upload_safety.scan),
       - asks FFmpeg what's inside; it must be a known video container with
         a real video track,
       - re-writes it: re-packed into a clean MP4 (first video + audio track
         only) when the picture can stay as it is, else re-encoded -- right
         away with Server Settings -> Uploads -> automatic, otherwise after
         the uploader picks a codec (the upload then leaves the line and
         joins its end again once they've picked),
       - moves it into "<download folder>/<creator>/" and adds it to the
         library; thumbnail, preview and details are made from the file.
     One upload failing (or getting stuck until its time limit) never stops
     the ones after it, and its files are always removed.
  Leftovers from a crash or restart (the .incoming folder) are deleted when
  the app starts. The Upload page follows along with local_upload_status().
Such videos have site "local": never synced with or repaired from PMVHaven.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Optional

from loguru import logger

from .. import media_tools, stats_store, upload_safety
from ..models import LibraryItem
from ..notifications import notify, title_of, video_link
from ._helpers import safe_name

VIDEO_EXTENSIONS = set(upload_safety.VIDEO_EXTENSIONS)
UPLOAD_TTL_SECONDS = 6 * 3600
SPACE_MARGIN = 200 * 1024 * 1024  # keep this much free besides the file
INCOMING = ".incoming"  # inside the download folder: files still being received or checked
ACTIVE_STATES = ("receiving", "queued", "checking", "scanning", "remuxing", "converting", "finishing")


class UploadError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _clean_list(values) -> list[str]:
    out: list[str] = []
    for v in values or []:
        v = str(v).strip()[:200]
        if v and v.casefold() not in {x.casefold() for x in out}:
            out.append(v)
    return out[:200]


class LocalUploadMixin:
    _uploads: dict = {}
    _upload_line: list = []            # upload ids waiting for the worker, oldest first
    _upload_current: Optional[str] = None
    _upload_wake: Optional[asyncio.Event] = None
    _upload_worker: Optional[asyncio.Task] = None

    # ------------------------------------------------------------ helpers
    def _incoming_dir(self) -> Path:
        d = self.settings.output_dir / INCOMING
        d.mkdir(parents=True, exist_ok=True)
        return d

    def clean_incoming_uploads(self) -> None:
        """At startup: nothing is being uploaded yet, so whatever is in the
        .incoming folder was left by a crash or restart."""
        d = self.settings.output_dir / INCOMING
        if not d.is_dir():
            return
        removed = 0
        for f in d.iterdir():
            if f.is_file():
                try:
                    f.unlink()
                    removed += 1
                except OSError as exc:
                    logger.warning("Could not delete the unfinished upload {}: {}", f, exc)
        if removed:
            logger.info("Deleted {} unfinished upload file(s) left from before the restart", removed)

    def _owned(self, upload_id: str, user) -> dict:
        up = self._uploads.get(upload_id)
        if not up or (user is not None and up.get("user_id") != user.id and not user.has_permission("*")):
            raise UploadError("This upload expired or doesn't exist -- start it again", 404)
        return up

    def _reserved_bytes(self) -> int:
        return sum(u["size"] * (2 if u.get("state") in ACTIVE_STATES or u.get("receiving") else 1)
                   for u in self._uploads.values() if u.get("state") not in ("done", "failed"))

    def _line_message(self, upload_id: str) -> str:
        try:
            n = self._upload_line.index(upload_id) + 1
        except ValueError:
            return "Waiting…"
        ahead = n - 1 + (1 if self._upload_current else 0)
        return "Next in line…" if not ahead else f"Waiting in line: {ahead} upload{'s' if ahead > 1 else ''} ahead"

    # ------------------------------------------------------------ step 1
    def start_local_upload(self, details: dict, username: str, user_id: Optional[str] = None) -> dict:
        cfg = self.settings.upload
        title = (details.get("title") or "").strip()[:300]
        if not title:
            raise UploadError("Give the video a title")
        filename = (details.get("filename") or "").strip()
        ext = Path(filename).suffix.lower()
        if ext not in VIDEO_EXTENSIONS:
            raise UploadError(f"Only these video files can be uploaded: {', '.join(sorted(VIDEO_EXTENSIONS))}", 415)
        size = int(details.get("size") or 0)
        if size <= 0:
            raise UploadError("The file is empty")
        if size > cfg.max_size_gb * 1024 ** 3:
            raise UploadError(f"The file is bigger than the {cfg.max_size_gb} GB limit (Server Settings → Uploads)", 413)
        if cfg.virus_scan == "required" and not upload_safety.scanner():
            raise UploadError("Uploads need a virus scanner on the server (Microsoft Defender or ClamAV), and none was "
                              "found — see Server Settings → Uploads", 503)
        if self._disk.get("low"):
            raise UploadError("The server is low on disk space — uploads wait until there's room again", 507)
        mine = [u for u in self._uploads.values() if u.get("user_id") == user_id and u.get("state") not in ("done", "failed")]
        if len(mine) >= cfg.max_waiting:
            raise UploadError(f"You already have {len(mine)} unfinished uploads — wait for them first", 429)
        upload_date = (details.get("upload_date") or "").strip() or None
        if upload_date:
            try:
                datetime.strptime(upload_date, "%Y-%m-%d")
            except ValueError as exc:
                raise UploadError("Upload date must look like 2024-05-31") from exc
        creator = (details.get("creator") or "").strip()[:200] or "Unknown"
        incoming = self._incoming_dir()
        free = shutil.disk_usage(incoming).free
        # Room for this file twice (the original and its re-written copy) plus
        # everything else still being uploaded or processed.
        if free < size * 2 + SPACE_MARGIN + self._reserved_bytes():
            raise UploadError(f"Not enough free space for this upload ({free // 2**20:,} MB free)", 507)

        now = time.time()
        for key in [k for k, v in self._uploads.items()
                    if now - v["created"] > UPLOAD_TTL_SECONDS and v.get("state") not in ACTIVE_STATES]:
            self._drop_upload(key)
        upload_id = secrets.token_hex(16)
        self._uploads[upload_id] = {
            "created": now, "user": username, "user_id": user_id, "ext": ext, "size": size, "title": title, "creator": creator,
            "upload_date": upload_date, "description": (details.get("description") or "").strip()[:10000],
            "tags": _clean_list(details.get("tags")), "performers": _clean_list(details.get("performers")),
            "music": _clean_list(details.get("music")), "flags": _clean_list(details.get("flags")),
            "receiving": False, "state": "waiting_file", "files": [],
        }
        return {"upload_id": upload_id}

    # ------------------------------------------------------------ step 2
    async def receive_local_upload(self, upload_id: str, chunks: AsyncIterator[bytes], user=None) -> dict:
        """Stores the file, then puts the upload in line (see _upload_worker_loop)."""
        up = self._owned(upload_id, user)
        if up["receiving"] or up.get("staged") or up.get("state") != "waiting_file":
            raise UploadError("This file is already being uploaded", 409)
        up["receiving"] = True
        up["state"] = "receiving"
        item_id = secrets.token_hex(12)  # 24 hex characters, like the site's ids
        staged = self._incoming_dir() / f"{item_id}{up['ext']}.part"
        up["files"].append(str(staged))
        written = 0
        try:
            with open(staged, "xb") as fh:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > up["size"]:
                        raise UploadError("More data arrived than the file's size — upload refused", 413)
                    await asyncio.to_thread(fh.write, chunk)
            if written != up["size"]:
                raise UploadError(f"The upload was cut off ({written:,} of {up['size']:,} bytes) -- try again")
        except BaseException:
            staged.unlink(missing_ok=True)
            up.update(receiving=False, state="waiting_file")
            raise
        base_name = safe_name(f"{up['creator']} - {up['upload_date'] or 'unknown-date'} - {up['title']} [{item_id}]")
        up.update(receiving=False, staged=str(staged), item_id=item_id, base_name=base_name, written=written,
                  state="queued")
        logger.info("Received upload '{}' from {} ({:,} bytes); in line to be checked", up["title"], up["user"], written)
        self._enqueue_upload(upload_id)
        return self.local_upload_status(upload_id)

    # ------------------------------------------------------------ the line (FIFO)
    def _enqueue_upload(self, upload_id: str) -> None:
        if upload_id not in self._upload_line:
            self._upload_line.append(upload_id)
        up = self._uploads[upload_id]
        up.update(state="queued", progress=None, message=self._line_message(upload_id))
        if self._upload_wake is None:
            self._upload_wake = asyncio.Event()
        self._upload_wake.set()
        if self._upload_worker is None or self._upload_worker.done():
            self._upload_worker = asyncio.create_task(self._upload_worker_loop())

    async def _upload_worker_loop(self) -> None:
        """One upload at a time, oldest first. Every step is guarded: a failure
        (or a step hitting its time limit) fails that upload only."""
        while not self._shutting_down:
            if not self._upload_line:
                self._upload_wake.clear()
                try:
                    await asyncio.wait_for(self._upload_wake.wait(), timeout=60)
                except asyncio.TimeoutError:
                    if not self._upload_line:
                        return  # idle: the next upload starts a new worker
                continue
            upload_id = self._upload_line.pop(0)
            if upload_id not in self._uploads:
                continue  # cancelled while waiting
            self._upload_current = upload_id
            for other in self._upload_line:
                if other in self._uploads:
                    self._uploads[other]["message"] = self._line_message(other)
            up = self._uploads[upload_id]
            try:
                if up.get("convert"):
                    await self._convert_upload(upload_id, *up.pop("convert"))
                else:
                    await self._process_upload(upload_id)
            except Exception as exc:  # noqa: BLE001
                self._fail_upload(upload_id, exc)
            finally:
                self._upload_current = None
                if upload_id not in self._uploads:  # cancelled while this step ran
                    self._remove_upload_files(up)

    def upload_activities(self) -> list[dict]:
        """Uploads in line or being processed, for the Tasks page (same shape
        as backend/activity.py entries)."""
        labels = {"queued": "Waiting in line", "checking": "Checking the file", "scanning": "Virus scan",
                  "remuxing": "Re-packing into MP4", "converting": "Converting",
                  "finishing": "Making the thumbnail and preview", "needs_conversion": "Waiting for the uploader to pick a format"}
        out = []
        for upload_id, up in self._uploads.items():
            state = up.get("state")
            if state not in labels:
                continue
            out.append({"id": f"upload:{upload_id}", "kind": "upload", "title": f"Upload: {up.get('title') or 'video'}",
                        "source": "manual", "job_id": None,
                        "status": "waiting" if state in ("needs_conversion", "queued") else "running",
                        "stage": labels[state], "detail": up.get("message"), "summary": None,
                        "started_at": up.get("created"), "finished_at": None, "can_stop": False, "stopped_by": None,
                        "done": None, "total": None, "percent": up.get("progress") if state == "converting" else None})
        return out

    def local_upload_status(self, upload_id: str, user=None) -> dict:
        up = self._owned(upload_id, user)
        if up.get("state") == "queued":
            up["message"] = self._line_message(upload_id)
        out = {k: up.get(k) for k in ("state", "message", "reason", "progress", "item", "checks")}
        out["upload_id"] = upload_id
        out["probe"] = {k: (up.get("probe") or {}).get(k) for k in ("video", "audio", "width", "height", "pix_fmt", "duration")}
        if up.get("state") == "needs_conversion":
            codec, crf, preset = self.settings.upload.params(self.settings.reencode)
            encoders = media_tools.available_encoders()
            out["defaults"] = {"codec": codec, "crf": crf, "preset": preset}
            out["encoders"] = {c: (not encoders) or enc in encoders for c, enc in media_tools.UPLOAD_ENCODERS.items()}
            out["codec_defaults"] = {c: dict(zip(("codec", "crf", "preset"), self.settings.upload.params(self.settings.reencode, c)))
                                     for c in media_tools.UPLOAD_ENCODERS}
        return out

    # ------------------------------------------------------------ step 3 (in line)
    async def _process_upload(self, upload_id: str) -> None:
        up = self._uploads.get(upload_id)
        if not up:
            return
        staged = Path(up["staged"])
        up.update(state="checking", message="Checking the file…")
        why = await asyncio.to_thread(upload_safety.sniff, str(staged), up["ext"])
        if why:
            raise UploadError(why)
        mode = self.settings.upload.virus_scan
        if mode != "off":
            up.update(state="scanning", message="Scanning it for viruses…")
            verdict, detail = await asyncio.to_thread(upload_safety.scan, str(staged))
            if upload_id not in self._uploads:
                return
            if verdict == "infected":
                logger.warning("Upload '{}' from {} refused: {}", up["title"], up["user"], detail)
                raise UploadError(f"The virus scan refused this file: {detail}. It was deleted.")
            if verdict != "clean" and mode == "required":
                raise UploadError(f"The file couldn't be virus-scanned ({detail}), so it wasn't accepted.")
            up["scan"] = detail if verdict == "clean" else None
            up.setdefault("checks", []).append({"name": "Virus scan", "ok": verdict == "clean",
                                                "result": detail if verdict != "none" else "skipped -- no antivirus on the server"})
        hash_mode = self.settings.upload.hash_check
        if hash_mode != "off":
            up.update(state="scanning", message="Looking up its fingerprint on VirusTotal…")
            key = self.settings.virustotal_api_key
            if key:
                verdict, detail = await asyncio.to_thread(upload_safety.hash_lookup, str(staged), key)
            else:
                verdict, detail = "error", "No VirusTotal API key saved (Server Settings → Credentials)"
            if upload_id not in self._uploads:
                return
            if verdict == "malicious":
                logger.warning("Upload '{}' from {} refused: {}", up["title"], up["user"], detail)
                raise UploadError(f"Refused: {detail}. It was deleted.")
            if verdict == "error" and hash_mode == "required":
                raise UploadError(f"The fingerprint couldn't be checked ({detail}), so it wasn't accepted.")
            up["hash_check"] = detail
            up.setdefault("checks", []).append({"name": "VirusTotal fingerprint", "ok": verdict in ("clean", "unknown"), "result": detail})
        up.update(state="checking", message="Checking what's inside…")
        info = await asyncio.to_thread(media_tools.probe_streams, str(staged))
        if upload_id not in self._uploads:
            return
        if not info or not info.get("video"):
            raise UploadError("FFmpeg can't find a video in this file")
        if info.get("format") not in upload_safety.ALLOWED_FORMATS:
            raise UploadError(f"This isn't a supported video file (it's {info.get('format') or 'unknown'})")
        up["probe"] = info
        video_ok = media_tools.browser_video_ok(info)
        if video_ok:
            # Always re-written, also when it's already a fine MP4: only the
            # first video and audio track are kept (nothing else rides along).
            up.update(state="remuxing", message="Re-packing it into a clean MP4 (the picture isn't touched)…")
            remuxed = staged.with_name(f"{up['item_id']}.remux.mp4")
            up["files"].append(str(remuxed))
            limit = 600 + up["size"] // (2 * 1024 * 1024)  # 10 min + 1 s per 2 MB
            ok = await asyncio.to_thread(media_tools.remux_to_mp4, str(staged), str(remuxed), info.get("audio"),
                                         info.get("video"), limit)
            if upload_id not in self._uploads:
                Path(remuxed).unlink(missing_ok=True)
                return
            if ok:
                staged.unlink(missing_ok=True)
                await self._finalize_upload(upload_id, remuxed, "re-packed into MP4")
                return
            reason = "Re-packing it into MP4 didn't work, so it has to be re-encoded."
        else:
            what = (info.get("video") or "this").upper()
            if info.get("video") == "h264":
                what = f"H.264 in {info.get('pix_fmt')}"
            reason = f"Browsers can't play {what} video, so it has to be re-encoded."
        up["reason"] = reason
        if self.settings.upload.auto_convert:
            codec, crf, preset = self.settings.upload.params(self.settings.reencode)
            await self._convert_upload(upload_id, codec, crf, preset)
        else:
            up.update(state="needs_conversion", message=reason)  # out of line until the uploader picks

    async def convert_local_upload(self, upload_id: str, codec: str, crf: int, preset: str, user=None) -> dict:
        """The uploader's choice for a file that has to be re-encoded: back in line (at the end)."""
        up = self._owned(upload_id, user)
        if up.get("state") != "needs_conversion":
            raise UploadError("This upload isn't waiting for a conversion", 409)
        if codec not in media_tools.UPLOAD_ENCODERS:
            raise UploadError("Unknown codec")
        encoders = await asyncio.to_thread(media_tools.available_encoders)
        if encoders and media_tools.UPLOAD_ENCODERS[codec] not in encoders:
            raise UploadError(f"Your FFmpeg has no {media_tools.UPLOAD_ENCODERS[codec]} encoder -- pick another codec")
        hi = 63 if codec == "av1" else 51
        if not 0 <= crf <= hi:
            raise UploadError(f"Quality (CRF) must be 0-{hi}")
        if codec == "av1":
            if not str(preset).isdigit() or not 0 <= int(preset) <= 13:
                raise UploadError("AV1 preset must be 0-13")
        elif preset not in ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"):
            raise UploadError("Unknown preset")
        up["convert"] = (codec, crf, str(preset))
        self._enqueue_upload(upload_id)
        return self.local_upload_status(upload_id)

    async def _convert_upload(self, upload_id: str, codec: str, crf: int, preset: str) -> None:
        up = self._uploads.get(upload_id)
        if not up:
            return
        label = {"h264": "AVC (H.264)", "hevc": "HEVC", "av1": "AV1"}.get(codec, codec)
        up.update(state="converting", progress=0, message=f"Re-encoding to {label}…", codec=codec)
        holder: dict = {}
        up["proc_holder"] = holder

        def on_progress(p: dict) -> None:
            up["progress"] = p.get("percent", 0)
            up["message"] = f"Re-encoding to {label}… {p.get('percent', 0)}% · {p.get('speed', '')} · about {p.get('eta_seconds', 0) // 60} min left"

        out = await asyncio.to_thread(
            media_tools.reencode_video, up["staged"], codec, crf, preset, False, self.settings.reencode.cpu_cores(),
            False, True, on_progress, holder, False, False)
        if out:
            up["files"].append(str(out))
        if upload_id not in self._uploads:  # cancelled meanwhile
            if out:
                Path(out).unlink(missing_ok=True)
            return
        if not out:
            up.update(state="needs_conversion", progress=None,
                      message=f"The re-encode to {label} failed (see the log) -- try other settings or another codec.")
            return
        Path(up["staged"]).unlink(missing_ok=True)
        logger.info("Converted upload '{}' to {} (CRF {}, preset {})", up["title"], label, crf, preset)
        await self._finalize_upload(upload_id, Path(out), f"re-encoded to {label}")

    def _fail_upload(self, upload_id: str, exc: BaseException) -> None:
        up = self._uploads.get(upload_id)
        if not up:
            return
        if not isinstance(exc, UploadError):
            logger.exception("Upload '{}' failed", up.get("title"))
        self._remove_upload_files(up)
        up.update(state="failed", message=str(exc) or "The upload failed", progress=None)
        notify("upload", "error", f"Upload failed: “{up.get('title') or 'video'}”", body=up["message"][:300],
               link="/upload", user_id=up.get("user_id"))

    @staticmethod
    def _remove_upload_files(up: dict) -> None:
        """Every temporary file this upload made (the final video is never in this list)."""
        for f in up.get("files") or []:
            try:
                Path(f).unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Could not delete {}: {}", f, exc)
        up["files"] = []
        up["staged"] = None

    async def _finalize_upload(self, upload_id: str, path: Path, how: str) -> None:
        """Moves the checked, re-written MP4 into place and adds the video to the library."""
        up = self._uploads.get(upload_id)
        if not up:
            Path(path).unlink(missing_ok=True)
            return
        item_id = up["item_id"]
        folder = self.settings.output_dir / safe_name(up["creator"])
        folder.mkdir(parents=True, exist_ok=True)
        final = folder / f"{up['base_name']}.mp4"
        if final.exists():  # never overwrite anything
            final = folder / f"{up['base_name']}-{secrets.token_hex(3)}.mp4"
        shutil.move(str(path), str(final))
        self._remove_upload_files(up)
        item = LibraryItem(
            id=item_id, url=f"local:{item_id}", site="local", status="done",
            title=up["title"], creator=up["creator"], description=up["description"],
            tags=up["tags"], performers=up["performers"], music=up["music"], upload_date=up["upload_date"],
            video_path=str(final), download_datetime=datetime.now(timezone.utc).isoformat(),
            progress_percent=100, progress_label="Uploaded", added_by=up.get("user_id"),
        )
        if up["flags"]:
            item.user_edits = {"flags": up["flags"]}
        async with self._lock:
            self._items[item.id] = item
            self.db.save_item(item)
        logger.info("Uploaded '{}' [{}] by {} -> {} ({}{}{})", item.title, item.id, up["user"], final, how,
                    f", scanned by {up['scan']}" if up.get("scan") else "",
                    f", VirusTotal: {up['hash_check']}" if up.get("hash_check") else "")
        up.update(state="finishing", progress=None, message="Added to the library -- making the thumbnail and preview…")
        await self._finish_local_upload(item, up, up["base_name"])
        up.update(state="done", progress=100, message=f"Added to the library ({how}).", item=item.model_dump())
        self.request_storyboard(item.id)  # seekbar previews (2.21.0)
        self.request_frames(item.id)  # video frames under the player (3.0.1)
        notify("upload", "success", f"Uploaded {title_of(item)}", link=video_link(item), user_id=item.added_by)
        try:
            stats_store.bump("upload_bytes", final.stat().st_size)
        except OSError:
            pass

    async def _finish_local_upload(self, item: LibraryItem, up: dict, base_name: str) -> None:
        """Technical details, thumbnail, preview clip and a metadata JSON, all
        made from the file itself."""
        try:
            video = item.video_path
            cache_dir = self.settings.cache_dir / safe_name(up["creator"])
            cache_dir.mkdir(parents=True, exist_ok=True)
            tech = await asyncio.to_thread(media_tools.get_technical_info, video)
            thumb = cache_dir / f"{base_name}.jpg"
            preview = cache_dir / f"{base_name}_preview.mp4"
            thumb_ok = await asyncio.to_thread(media_tools.generate_thumbnail, video, str(thumb))
            preview_ok = await asyncio.to_thread(media_tools.generate_preview_clip, video, str(preview))
            meta_path = cache_dir / f"{base_name}.json"
            meta = {
                "source": "local upload", "title": item.title, "creator": {"name": item.creator},
                "upload_date": item.upload_date, "description": item.description,
                "tags": item.tags, "models": item.performers, "music": item.music,
                "content_flags": up["flags"], "video_details": tech,
            }
            meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
            async with self._lock:
                if item.id not in self._items:
                    return
                item.tech_codec = tech.get("Video Codec")
                item.tech_resolution = tech.get("Resolution")
                item.tech_filesize = tech.get("File Size")
                item.tech_duration = tech.get("Duration")
                item.metadata_path = str(meta_path)
                if thumb_ok:
                    item.thumbnail_path = str(thumb)
                    item.thumbnail_updated_at = time.time()
                if preview_ok:
                    item.preview_path = str(preview)
                    item.preview_updated_at = time.time()
                item.touch()
                self.db.save_item(item)
        except Exception:  # noqa: BLE001
            logger.exception("Could not finish the details of uploaded video [{}]", item.id)
        self.request_duplicate_scan_after("download")

    async def repair_local_item(self, item: LibraryItem, thumb_broken: bool, preview_broken: bool) -> list[str]:
        """Repair for an uploaded video: there's no site to fetch from, so
        missing pieces are made from the file again."""
        fixed: list[str] = []
        video = item.video_path
        cache_dir = next((Path(p).parent for p in (item.thumbnail_path, item.preview_path, item.metadata_path) if p),
                         self.settings.cache_dir / safe_name(item.creator or "Unknown"))
        cache_dir.mkdir(parents=True, exist_ok=True)
        base = Path(video).stem
        if thumb_broken:
            thumb = Path(item.thumbnail_path) if item.thumbnail_path else cache_dir / f"{base}.jpg"
            if await asyncio.to_thread(media_tools.generate_thumbnail, video, str(thumb)):
                item.thumbnail_path, item.thumbnail_updated_at = str(thumb), time.time()
                fixed.append("thumbnail (generated locally)")
        if preview_broken:
            preview = Path(item.preview_path) if item.preview_path else cache_dir / f"{base}_preview.mp4"
            if await asyncio.to_thread(media_tools.generate_preview_clip, video, str(preview)):
                item.preview_path, item.preview_updated_at = str(preview), time.time()
                fixed.append("preview clip (generated locally)")
        tech = await asyncio.to_thread(media_tools.get_technical_info, video)
        if tech:
            item.tech_codec = tech.get("Video Codec")
            item.tech_resolution = tech.get("Resolution")
            item.tech_filesize = tech.get("File Size")
            item.tech_duration = tech.get("Duration")
        async with self._lock:
            if item.id in self._items:
                item.touch()
                self.db.save_item(item)
        return fixed

    def cancel_local_upload(self, upload_id: str, user=None) -> None:
        self._owned(upload_id, user)
        self._drop_upload(upload_id)

    def _drop_upload(self, upload_id: str) -> None:
        """Forgets an upload: out of the line, its FFmpeg stopped, its temporary files deleted.
        A step already running for it notices and cleans up after itself."""
        up = self._uploads.pop(upload_id, None)
        if upload_id in self._upload_line:
            self._upload_line.remove(upload_id)
        if not up:
            return
        proc = (up.get("proc_holder") or {}).get("proc")
        if proc and proc.poll() is None:
            proc.kill()
        if upload_id != self._upload_current:  # the running step removes its own files when it notices
            self._remove_upload_files(up)
