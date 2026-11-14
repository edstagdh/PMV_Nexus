"""
Songs (2.14.0): find the songs in a video and suggest them for its Music.

- scan from a video's page ("Find songs", start_song_scan) or the Scheduler
  job "Find songs in videos without music" (run_song_scan_job: videos with no
  music, never scanned or not for `songs.rescan_days`, newest first, at most
  `songs.per_run` per run);
- the samples and lookups are in backend/song_recognition.py;
- what's found waits as suggestions (Management -> Songs, and the video's
  "Find songs" dialog): Add puts it in the video's Music as a hand edit (it
  stays through syncs), Dismiss hides it for good for that video. Songs the
  video already lists aren't suggested.

One scan at a time (they share Shazam's patience); a scan asked for while
another runs waits its turn.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Optional

from loguru import logger

from .. import activity, song_recognition as rec
from ..notifications import notify, title_of, video_link
from ..spellings import fold, spellings


class SongsMixin:
    _song_lock: Optional[asyncio.Lock] = None

    # ------------------------------------------------------------ helpers
    def _songs_lock(self) -> asyncio.Lock:
        if self._song_lock is None:
            self._song_lock = asyncio.Lock()
            self._song_state: dict = {}  # item id -> {"state": "waiting"|"scanning", "done", "total", "found"}
        return self._song_lock

    def _song_scannable(self, item) -> bool:
        return bool(item and item.status == "done" and item.video_path and os.path.isfile(item.video_path))

    def _set_song_state(self, item_id: str, **state) -> None:
        if "state" in state and state["state"] is None:  # finished
            self._song_state.pop(item_id, None)
        else:
            self._song_state[item_id] = {**self._song_state.get(item_id, {}), **state}
        self.db._note_change(["songs"])

    # ------------------------------------------------------------ one video
    async def _scan_video(self, item, on_sample=None, method: Optional[str] = None) -> dict:
        """Samples and looks up one video; stores the suggestions. Raises
        RecognitionError when a service stops answering (limit, bad key).
        `method` (2.34.0): see config.SongsConfig.method; None = the setting."""
        cfg = self.settings.songs
        method = method or cfg.method
        key = self.settings.gemini_api_key if method != "shazam" else None
        if method == "gemini" and not key:
            raise rec.RecognitionError("Gemini needs its API key (Server Settings → Credentials → Google Gemini)")
        use_shazam = method != "gemini"
        from ..media_tools import probe_streams
        info = await asyncio.to_thread(probe_streams, item.video_path)
        duration = float(info.get("duration") or 0)
        if not info.get("audio"):
            self.db.save_song_scan(item.id, 0, 0, "done", "No sound in this video")
            return {"samples": 0, "found": 0, "detail": "No sound in this video"}
        times = rec.sample_times(duration, cfg.interval, cfg.max_samples)
        songs: dict[str, dict] = {}
        unmatched: list[float] = []
        shazam_error: Optional[str] = None
        taken = 0
        retries = 0

        def add(at: float, song: dict) -> None:
            lbl = rec.label(song)
            k = fold(lbl)
            entry = songs.setdefault(k, {**song, "label": lbl, "hits": 0, "times": []})
            if song["source"] not in entry["source"].split(" + "):
                entry["source"] = f"{entry['source']} + {song['source']}"  # e.g. "Shazam + Gemini (AI)"
            entry["hits"] += 1
            entry["times"].append(round(at))

        if not use_shazam:  # Gemini only: no Shazam requests at all
            unmatched = list(times)
            taken = len(times)
        async with rec.ShazamClient() as shazam:
            for i, at in enumerate(times if use_shazam else []):
                wav = await asyncio.to_thread(rec.cut_sample, item.video_path, at)
                taken += 1
                if on_sample:
                    on_sample(i + 1, len(times), len(songs))
                if not wav:
                    continue
                song = None
                if shazam_error is None:
                    try:
                        song = await shazam.lookup(wav)
                        # 2.18.0: missed? Try once more a few seconds on -- often past a
                        # transition or sound effect that hid the song.
                        later = at + rec.RETRY_SHIFT
                        if song is None and cfg.shazam_retry and later + rec.SAMPLE <= duration:
                            retry_wav = await asyncio.to_thread(rec.cut_sample, item.video_path, later)
                            if retry_wav:
                                await rec.polite_pause()
                                retries += 1
                                song = await shazam.lookup(retry_wav)
                                if song:
                                    at = later
                    except rec.RecognitionError as exc:
                        shazam_error = str(exc)
                        logger.warning("Song scan of '{}': {}", item.title, exc)
                        if not key:
                            raise
                    if i < len(times) - 1:
                        await rec.polite_pause()
                if song:
                    add(at, song)
                else:
                    unmatched.append(at)
        gemini_used, gemini_note = 0, None
        # Where Gemini listens: the spots Shazam missed, or (both / Gemini only) any spot.
        spots = unmatched if method == "shazam_gemini" else list(times)
        if method != "shazam" and not key:
            gemini_note = "Gemini skipped: no API key saved"
        elif key and spots and rec.gemini_paused():
            gemini_note = "Gemini skipped: its free models were out of quota a moment ago"
            if method == "gemini":
                raise rec.RecognitionError(gemini_note)
        elif key and spots:
            # Spread Gemini's few tries over the spots; it gets a longer clip.
            limit = cfg.gemini_max_samples
            step = max(1, len(spots) // max(1, limit))
            picked = spots[::step][:limit]
            for n, at in enumerate(picked):
                if not use_shazam and on_sample:
                    on_sample(n + 1, len(picked), len(songs))
                start = max(0.0, min(at, duration - rec.GEMINI_SAMPLE))
                clip = await asyncio.to_thread(rec.cut_sample, item.video_path, start, rec.GEMINI_SAMPLE)
                if not clip:
                    continue
                try:
                    song = await rec.gemini_lookup(clip, key)
                except rec.RecognitionError as exc:
                    gemini_note = f"Gemini: {exc}"
                    logger.warning("Song scan of '{}': {}", item.title, gemini_note)
                    if method == "gemini" and not gemini_used:
                        raise
                    break
                gemini_used += 1
                if song:
                    add(at, song)
        if not use_shazam:
            taken = gemini_used  # Gemini only: its clips are the samples
        have = {fold(v) for v in item.music} | {fold(spellings.canonical("music", v)) for v in item.music}
        new = [{**s, "samples": taken} for k, s in songs.items() if k not in have]
        pending = self.db.store_song_suggestions(item.id, sorted(new, key=lambda s: -s["hits"]))
        how = {"shazam": "Shazam", "shazam_gemini": "Shazam, then Gemini", "both": "Shazam and Gemini",
               "gemini": "Gemini"}[method]
        heard = f"{gemini_used} Gemini clips" if not use_shazam else f"{taken} samples"
        detail = (f"{how}: {len(songs)} song(s) heard in {heard}"
                  + (f", {len(songs) - len(new)} already listed" if len(songs) != len(new) else "")
                  + (f" ({retries} retried a bit later)" if retries else "")
                  + (f" (Gemini: {gemini_used} clips)" if gemini_used and use_shazam else "")
                  + (f"; {shazam_error}" if shazam_error else "")
                  + (f"; {gemini_note}" if gemini_note else ""))
        self.db.save_song_scan(item.id, taken, len(songs), "done", detail)
        logger.info("Song scan of '{}' [{}]: {}", item.title, item.id, detail)
        return {"samples": taken, "found": len(songs), "new": pending, "detail": detail}

    # ------------------------------------------------------------ from a video's page
    def start_song_scan(self, item_id: str, user: str, method: Optional[str] = None) -> dict:
        item = self._items.get(item_id)
        if not self._song_scannable(item):
            raise ValueError("This video's file isn't available")
        lock = self._songs_lock()
        if item_id in self._song_state:
            return self._song_state[item_id]
        self._set_song_state(item_id, state="waiting" if lock.locked() else "scanning", done=0, total=None, found=0)
        asyncio.get_running_loop().create_task(self._manual_song_scan(item, user, method))
        return self._song_state[item_id]

    async def _manual_song_scan(self, item, user: str, method: Optional[str] = None) -> None:
        lock = self._songs_lock()

        def progress() -> dict:
            st = self._song_state.get(item.id) or {}
            if st.get("state") == "waiting":
                # 2.18.0: on Tasks while it waits for the other song scan (Scheduler / Find songs now).
                return {"stage": "Waiting for the song scan that's running"}
            return {"stage": "Listening", "done": st.get("done"), "total": st.get("total"),
                    "detail": f"{st.get('found', 0)} song(s) so far"}

        def on_sample(done, total, found):
            self._set_song_state(item.id, done=done, total=total, found=found)

        try:
            async with activity.track("song_scan", f"Finding songs: {title_of(item)}", stop=True, progress=progress) as act:
                async with lock:
                    self._set_song_state(item.id, state="scanning")
                    result = await self._scan_video(item, on_sample, method)
                    act.summary = result["detail"]
            if result.get("new"):
                notify("job", "info", f"{result['new']} song(s) found in {title_of(item)}", link=video_link(item))
        except rec.RecognitionError as exc:
            self.db.save_song_scan(item.id, 0, 0, "failed", str(exc))
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.exception("Song scan of {} failed", item.id)
            self.db.save_song_scan(item.id, 0, 0, "failed", f"{exc.__class__.__name__}: {exc}")
        finally:
            self._set_song_state(item.id, state=None)

    def song_status(self, item_id: str) -> dict:
        self._songs_lock()
        item = self._items.get(item_id)
        if not item:
            raise KeyError(item_id)
        return {"running": self._song_state.get(item_id), "last_scan": self.db.get_song_scan(item_id),
                "suggestions": self.db.list_song_suggestions("pending", item_id),
                "music": list(item.music), "scannable": self._song_scannable(item)}

    # ------------------------------------------------------------ Scheduler job
    def _song_candidates(self) -> list:
        cfg = self.settings.songs
        scanned = self.db.song_scan_times()
        cutoff = time.time() - cfg.rescan_days * 86400 if cfg.rescan_days else None
        out = [it for it in self._items.values()
               if not it.music and self._song_scannable(it)
               and (it.id not in scanned or (cutoff is not None and scanned[it.id] < cutoff))]
        out.sort(key=lambda it: it.download_datetime or "", reverse=True)
        return out

    async def run_song_scan_job(self) -> dict:
        """Scheduler: videos without music (see the module docstring)."""
        cfg = self.settings.songs
        result = await self._scan_many(self._song_candidates()[:cfg.per_run], "Finding songs in videos without music")
        return {**result, "left": max(0, len(self._song_candidates()))}

    def start_song_scan_many(self, ids: list[str], user: str, method: Optional[str] = None) -> dict:
        """Library -> Select -> Find songs (2.18.0): the picked videos, one after the
        other, as one task on Tasks."""
        self._songs_lock()
        items = [self._items[i] for i in dict.fromkeys(ids) if self._song_scannable(self._items.get(i))]
        if items:
            asyncio.get_running_loop().create_task(
                self._scan_many_quietly(items, f"Finding songs in {len(items)} picked video(s)", method))
        return {"queued": len(items), "skipped": len(set(ids)) - len(items)}

    async def _scan_many_quietly(self, items: list, title: str, method: Optional[str] = None) -> None:
        try:
            await self._scan_many(items, title, method)
        except (rec.RecognitionError, asyncio.CancelledError):
            pass  # on Tasks and in the log
        except Exception:  # noqa: BLE001
            logger.exception("Finding songs in several videos failed")

    async def _scan_many(self, todo: list, title: str, method: Optional[str] = None) -> dict:
        state = {"done": 0, "total": len(todo), "found": 0, "title": "", "waiting": True}
        found_in = 0
        lock = self._songs_lock()

        def progress() -> dict:
            if state["waiting"] and lock.locked():
                return {"stage": "Waiting for the song scan that's running", "done": 0, "total": state["total"]}
            return {"stage": f"Listening to {state['title']}" if state["title"] else "Starting",
                    "done": state["done"], "total": state["total"], "detail": f"songs found in {found_in} so far"}

        async with activity.track("song_scan", title, stop=True, progress=progress) as act:
            async with lock:
                state["waiting"] = False
                for item in todo:
                    state["title"] = title_of(item)
                    self._set_song_state(item.id, state="scanning", done=0, total=None, found=0)
                    try:
                        result = await self._scan_video(
                            item, lambda d, t, f, i=item.id: self._set_song_state(i, done=d, total=t, found=f), method)
                    except rec.RecognitionError as exc:
                        self.db.save_song_scan(item.id, 0, 0, "failed", str(exc))
                        act.summary = f"Stopped after {state['done']} video(s): {exc}"
                        raise
                    finally:
                        self._set_song_state(item.id, state=None)
                    state["done"] += 1
                    if result.get("new"):
                        found_in += 1
            act.summary = f"{state['done']} video(s) listened to, songs found in {found_in}"
        if found_in:
            notify("job", "info", f"Songs found in {found_in} video(s) — waiting for you", link="/admin/portal/songs")
        return {"videos": state["done"], "with_songs": found_in}

    # ------------------------------------------------------------ suggestions
    def songs_overview(self) -> dict:
        self._songs_lock()
        groups: dict[str, dict] = {}
        for s in self.db.list_song_suggestions("pending"):
            item = self._items.get(s["item_id"])
            if not item:
                continue
            g = groups.setdefault(item.id, {"item_id": item.id, "title": item.title or item.url, "creator": item.creator,
                                            "music": list(item.music), "suggestions": []})
            g["suggestions"].append(s)
        scanning = [{"item_id": k, **v} for k, v in self._song_state.items()]
        return {"videos": list(groups.values()), "counts": {**self.db.song_counts(), "without_music": len(
            [1 for it in self._items.values() if not it.music and it.status == "done"]),
            "to_scan": len(self._song_candidates())}, "scanning": scanning}

    async def add_song_suggestion(self, suggestion_id: int, user: str, label: Optional[str] = None) -> dict:
        s = self.db.get_song_suggestion(suggestion_id)
        if not s or s["status"] != "pending":
            raise KeyError(suggestion_id)
        value = (label or s["label"]).strip()[:200]
        if not value:
            raise ValueError("The song needs a name")
        item = self._items.get(s["item_id"])
        if not item:
            raise KeyError(suggestion_id)
        if fold(value) not in {fold(v) for v in item.music}:
            await self.bulk_edit([item.id], {"music": {"add": [value]}})
        self.db.decide_song_suggestion(suggestion_id, "added", user, value)
        logger.debug("Song '{}' added to '{}' [{}] by {}", value, item.title, item.id, user)
        return {"ok": True, "music": list(item.music)}

    def dismiss_song_suggestion(self, suggestion_id: int, user: str) -> dict:
        s = self.db.get_song_suggestion(suggestion_id)
        if not s or s["status"] != "pending":
            raise KeyError(suggestion_id)
        self.db.decide_song_suggestion(suggestion_id, "dismissed", user)
        return {"ok": True}
