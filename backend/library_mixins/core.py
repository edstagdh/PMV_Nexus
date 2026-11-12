from __future__ import annotations

from .. import activity, notifications, stats_store
import asyncio
import contextlib
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from ..config import get_settings
from ..spellings import fold, spellings
from ..db import LibraryDB
from ..models import LibraryItem
from .. import sources
from ._helpers import (
    codec_family,
    delete_with_retry,
    parse_duration_seconds,
    parse_filesize_mb,
    parse_rating_percent,
    parse_search_query,
)



_JOB_RESULT = {"success": ("success", "Finished"), "failed": ("error", "Failed"), "blocked": ("warning", "Didn't start"),
               "interrupted": ("warning", "Interrupted"), "cancelled": ("info", "Stopped")}


def _announce_job(act) -> None:
    """A finished job (backend/activity.py) -> a notification for the admins.
    Routine successes (automatic runs, snapshots, upload dates) stay quiet;
    extensions' work (source "extension") announces its own results."""
    if act.source == "extension":
        return
    if act.status == "success" and (act.source == "auto" or act.kind in ("stats_snapshot", "upload_dates", "update_check")):
        return
    level, verb = _JOB_RESULT.get(act.status, ("info", act.status))
    notifications.notify("job", level, f"{verb}: {act.title}", body=act.summary, link="/tasks")


def _wildcard(term: str) -> "re.Pattern[str]":
    """A search word with * (any characters, within one value) as a regex.
    The ends without * stick to the start / end of a word."""
    body = ".*".join(re.escape(part) for part in term.split("*"))
    if not term.startswith("*") and term[:1].isalnum():
        body = r"(?<!\w)" + body
    if not term.endswith("*") and term[-1:].isalnum():
        body += r"(?!\w)"
    return re.compile(body, re.IGNORECASE)


def _search_lines(item, field: Optional[str]) -> str:
    """The searched values one per line, so a wildcard can't run from one tag into the next."""
    values = {
        "title": [item.title], "creator": [item.creator], "tags": item.tags, "music": item.music,
        "description": [item.description], "codec": [item.tech_codec],
        "performers": [*item.performers, *spellings.canon_list("performers", item.performers)],
    }.get(field) or [item.title, item.creator, item.url, item.description, *item.tags, *item.performers,
                     *spellings.canon_list("performers", item.performers), *item.music]
    return "\n".join(v or "" for v in values)

class JobBlockedError(RuntimeError):
    """Raised when an INVASIVE operation (one that writes/modifies data
    -- currently duplicate scan and bulk library repair, with any future
    similarly-scoped job expected to follow the same pattern) can't
    start because a different invasive operation is already in progress
    somewhere in the app. See CoreMixin._try_acquire_invasive_lock()'s
    docstring for the actual mutual-exclusion mechanism this backs."""


class CoreMixin:

    # Always the current settings: get_settings() is cached and the cache is
    # cleared whenever settings change, so changes apply without a restart.
    # (Assigning is accepted and ignored, for older call sites.)
    @property
    def settings(self):
        return get_settings()

    @settings.setter
    def settings(self, _value) -> None:
        pass

    def __init__(self) -> None:
        self._items: dict[str, LibraryItem] = {}
        self._lock = asyncio.Lock()
        self._worker_task: asyncio.Task | None = None  # unused since 2.6.0 (see _download_lanes)
        # Download "lanes" (2.6.0): lane name -> its loop task. "direct-N" use the
        # normal connection, "proxy-N" the proxy (Server settings -> Proxy).
        self._download_lanes: dict[str, asyncio.Task] = {}
        self._claimed_downloads: set[str] = set()
        # Metadata (title/creator/tags/performers/thumbnail) is fetched by a
        # small pool of workers, entirely independent of the download
        # worker/slot above -- see _ensure_metadata_worker().
        self._metadata_tasks: list[asyncio.Task] = []
        self._metadata_claimed: set[str] = set()
        # Per-item asyncio.Task handles for whatever's CURRENTLY actually
        # running for that item id (a download in progress, or a metadata
        # fetch in progress) -- tracked so remove()/_cancel_in_flight()
        # can actually cancel the right in-flight operation instead of
        # merely deleting the item's bookkeeping while the real operation
        # (and, for these two, the single worker loop processing it)
        # keeps running to completion in the background regardless. See
        # _cancel_in_flight()'s docstring for the full explanation.
        self._active_download_tasks: dict[str, asyncio.Task] = {}
        self._active_metadata_tasks: dict[str, asyncio.Task] = {}
        # Per-item ffmpeg Popen handle for an HLS download currently in
        # progress, so _cancel_in_flight() can terminate the actual OS
        # process -- a plain task.cancel() has no effect on this one
        # (see _download_hls()'s docstring for why).
        self._active_download_procs: dict[str, dict] = {}
        self._reencode_queue: list[str] = []
        self._reencode_task: asyncio.Task | None = None
        self._current_reencode_id: Optional[str] = None
        self._current_reencode_proc: dict = {}
        self._cancel_requested: bool = False
        self._repair_cancel_requested: set[str] = set()
        # Per-item repair (thumbnail/preview/metadata fix-up), entirely
        # independent of the download/reencode workers above -- see
        # request_repair()/_ensure_repair_worker().
        self._repair_queue: list[str] = []
        self._repair_tasks: list[asyncio.Task] = []
        self._repair_max_concurrent: int = 1
        # Same per-item tracking as _active_download_tasks/
        # _active_metadata_tasks above, but for repair runs.
        self._active_repair_tasks: dict[str, asyncio.Task] = {}
        # Score-based duplicate detection (see library_mixins/
        # duplicate_worker.py) -- no longer runs its own daily timer; a
        # recurring scan is now configured as a Scheduler job instead
        # (see library_mixins/scheduler_worker.py). This flag/dict below
        # are still used regardless of what triggers a run: manual "Run
        # scan now", the post-download/post-reencode auto-trigger, or a
        # Scheduler-triggered run.
        self._duplicate_scan_running: bool = False
        # Live progress for whichever scan phase is currently running --
        # see get_duplicate_scan_status() in duplicate_worker.py, which
        # is what the Admin Portal's Issues page polls to show real
        # numbers instead of a bare "running" spinner with no indication
        # of how far along it is or how much longer it might take.
        self._duplicate_scan_progress: dict = {
            "phase": None, "hashed": 0, "hash_total": 0, "compared": 0, "compare_total": 0,
        }
        # Generic background-job Scheduler (see library_mixins/
        # scheduler_worker.py) -- one recurring tick loop that dispatches
        # ANY registered job type at its configured time, plus a dict
        # tracking which specific job ids are actively running right now
        # so the tick loop never double-dispatches the same job and so
        # shutdown() knows exactly what to cancel and log as interrupted.
        self._scheduler_task: asyncio.Task | None = None
        self._scheduler_active_jobs: dict[str, asyncio.Task] = {}
        # Global mutual-exclusion flag for INVASIVE operations (ones that
        # write/modify data anywhere -- duplicate scan and bulk library
        # repair today, any future similarly-scoped job going forward).
        # Holds the name of whichever invasive operation currently has
        # the "lock" (None if nothing does) -- see
        # _try_acquire_invasive_lock()/_release_invasive_lock() below.
        # Deliberately a plain flag, not a real asyncio.Lock: callers
        # want an immediate "no, something else is running" rejection,
        # not to queue up and wait their turn.
        self._invasive_lock_holder: Optional[str] = None
        self._shutting_down: bool = False
        # When this process actually started -- backs the Stats page's
        # "uptime" figure (see stats_worker.py's compute_system_stats()).
        self._start_time: float = time.time()
        self.db = LibraryDB(self.settings.library_db_file)
        self._load()

    def _try_acquire_invasive_lock(self, operation_name: str) -> bool:
        """Non-blocking "try-lock" for invasive (data-modifying)
        operations -- returns True and marks `operation_name` as the
        current holder if nothing else invasive is running; returns
        False immediately if something already is. Callers treat False
        as "don't start this operation at all" -- see JobBlockedError,
        which every current caller raises when this returns False.

        This is what makes "duplicate scan cannot run while a bulk
        library repair is in progress, and vice versa" hold true
        regardless of HOW either one was triggered -- the manual Admin
        Portal button, the post-download/post-reencode auto-trigger, or
        a Scheduler job -- because every trigger path ultimately calls
        into the same underlying operation, and this check lives at that
        shared level, not duplicated per trigger path. A future invasive
        job type is automatically covered the same way, as long as its
        own runner calls this before doing any real work."""
        if self._invasive_lock_holder is not None:
            return False
        self._invasive_lock_holder = operation_name
        return True

    def _release_invasive_lock(self, operation_name: str) -> None:
        """Releases the invasive-operation lock -- a no-op if
        `operation_name` isn't actually the current holder (defensive;
        should never happen, but guards against one operation's cleanup
        accidentally clearing a DIFFERENT operation's lock)."""
        if self._invasive_lock_holder == operation_name:
            self._invasive_lock_holder = None

    # ------------------------------------------------------------- storage
    def _load(self) -> None:
        self._migrate_legacy_json_if_needed()
        self._items = self.db.load_all()
        spellings.bind(lambda: self._items.values(), self.db)
        self._perf_load()  # performer profiles + alias merging (performers.py)

        # anything that was mid-flight when we last shut down goes back to
        # pending so the worker retries it.
        #
        # Re-encodes get the same treatment as downloads: a stalled
        # re-encode is cheap and safe to just restart from scratch (ffmpeg
        # re-reads the source file, no partial-upload state to worry about),
        # so items are requeued rather than surfaced as failures the user
        # has to notice and manually retry. Without this, an item that was
        # "encoding" or still "queued" when the server stopped kept that
        # status in the DB forever -- reencode_status "queued"/"encoding" is
        # what both the Tasks tab and the Re-encode/Cancel button toggle on,
        # so an item stuck in that state showed as an active task with only
        # "Cancel" available, with nothing actually processing it.
        #
        # Repairs get the same treatment as re-encodes: a stalled repair is
        # cheap and safe to just restart from scratch (no partial-upload
        # state), so it's bounced back to "queued" rather than surfaced as a
        # failure.
        dirty: dict[str, LibraryItem] = {}
        for item in self._items.values():
            if item.status in ("downloading", "fetching"):
                item.status = "pending"
                dirty[item.id] = item

            if item.reencode_status == "encoding":
                # the ffmpeg process that was encoding this died along with
                # the old server process -- there's no partial result to
                # resume, so start the encode over from scratch.
                item.reencode_status = "queued"
                item.reencode_progress_percent = 0
                item.reencode_eta_seconds = None
                item.reencode_speed = ""
                dirty[item.id] = item
            if item.reencode_status == "queued":
                self._reencode_queue.append(item.id)

            if item.repair_status == "running":
                # the repair step running when we last shut down was
                # interrupted -- cheap and safe to just restart it, same
                # reasoning as the re-encode bounce-back above.
                item.repair_status = "queued"
                item.repair_progress_label = "Queued"
                dirty[item.id] = item
            if item.repair_status == "queued":
                self._repair_queue.append(item.id)

        if dirty:
            self.db.save_items(dirty.values())
        if self._repair_queue:
            logger.info(
                "Restart recovery: re-queued {} repair(s) that were interrupted: {}",
                len(self._repair_queue), self._repair_queue,
            )

    def _migrate_legacy_json_if_needed(self) -> None:
        """One-time import of the old library.json flat file into SQLite.
        Only runs when the SQLite store is still empty and a legacy file is
        present, so this is safe to leave in place permanently -- it's a
        no-op on every run after the first. Every migrated item gets
        `download_datetime` backfilled to today's date if it doesn't
        already have one, since the JSON store never tracked that field."""
        legacy_path = self.settings.library_file
        if not legacy_path.exists() or not self.db.is_empty():
            return

        try:
            raw = json.loads(legacy_path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Could not read legacy library file {} for migration", legacy_path)
            return

        if not raw:
            return

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        items: list[LibraryItem] = []
        for entry in raw:
            try:
                item = LibraryItem(**entry)
            except Exception:
                logger.warning("Skipping unreadable legacy library entry {} during migration", entry.get("id"))
                continue
            if not item.download_datetime:
                item.download_datetime = today
            items.append(item)

        if not items:
            return

        self.db.save_items(items)

        backup_path = legacy_path.with_suffix(".json.migrated")
        try:
            legacy_path.rename(backup_path)
        except OSError:
            pass  # non-fatal -- the migration into SQLite already succeeded

        logger.info(
            "Migrated {} library item(s) from {} into SQLite ({}); backfilled "
            "download_datetime={} for entries that didn't have one. Old file kept as {}.",
            len(items), legacy_path, self.settings.library_db_file, today, backup_path,
        )

    # ------------------------------------------------------------- public
    def list_items(self) -> list[dict]:
        return [i.model_dump() for i in sorted(self._items.values(), key=lambda i: i.created_at, reverse=True)]

    def get(self, video_id: str) -> Optional[LibraryItem]:
        return self._items.get(video_id)

    def get_item_dict(self, video_id: str) -> Optional[dict]:
        """Cheap single-item lookup -- self._items is already a dict keyed
        by id, so this is O(1) instead of list_items()'s "serialize every
        item, then linear-scan the result to find one" (which is what the
        thumbnail/stream/preview endpoints used to do, on every request,
        even with hundreds of items in the library)."""
        item = self._items.get(video_id)
        return item.model_dump() if item else None

    _SORT_KEYS = {"title", "creator", "status", "tech_duration", "ratings", "tech_resolution", "tech_codec",
                  "tech_filesize", "created_at", "download_datetime", "reencode_status", "views", "upload_date"}

    def query_items(
            self,
            page: int = 1,
            page_size: int = 50,
            search: str = "",
            tags: Optional[list[str]] = None,
            performers: Optional[list[str]] = None,
            codecs: Optional[list[str]] = None,
            statuses: Optional[list[str]] = None,
            sort_key: str = "created_at",
            sort_dir: str = "desc",
            creators: Optional[list[str]] = None,
            match_all: bool = False,
            min_duration: Optional[int] = None,
            max_duration: Optional[int] = None,
            min_rating: Optional[float] = None,
            gone: Optional[bool] = None,
            qualities: Optional[list[str]] = None,
            min_quality: Optional[str] = None,
            orientation: Optional[str] = None,
            p_cups: Optional[list[str]] = None,
            p_hair: Optional[list[str]] = None,
            p_eyes: Optional[list[str]] = None,
            p_age_min: Optional[int] = None,
            p_age_max: Optional[int] = None,
            p_enhanced: Optional[bool] = None,
    ) -> dict:
        """Filters + sorts + paginates entirely in memory -- self._items
        already holds every item as the in-run source of truth (see db.py's
        module docstring), so this is a handful of Python-level list
        operations over at most a few thousand rows, not a real query
        engine. The actual win from pagination isn't algorithmic, it's that
        the API stops serializing (and the frontend stops re-rendering) all
        N items on every 2s poll when only a page's worth is ever visible
        at once.

        `search` supports an optional 'field:value' prefix (e.g.
        'title:sunset') to scope the match to one field instead of
        searching everything -- see parse_search_query()/
        _SEARCH_FIELD_ALIASES above. With no recognized prefix, the
        un-prefixed behavior is unchanged: title/creator/url/description/
        tags/performers/music are all searched together."""
        tags_set = set(tags or [])
        performers_set = set(performers or [])
        codecs_set = set(codecs or [])
        statuses_set = set(statuses or [])
        creators_set = {c.lower() for c in (creators or [])}

        tags_set = {spellings.key("tags", v) for v in tags_set}
        performers_set = {spellings.key("performers", v) for v in performers_set}

        def has(wanted: set[str], values: list[str], field: str) -> bool:
            # match_all: every selected tag/performer must be present;
            # otherwise any one of them is enough. Upper/lower case doesn't
            # matter, and a performer's merged aliases count as her (spellings.py).
            have = {spellings.key(field, v) for v in values}
            return wanted.issubset(have) if match_all else bool(wanted & have)
        search_field, search_text = parse_search_query(search.strip())
        # Every word must appear somewhere (any order): "alyx star natasha nice"
        # finds "... with Natasha Nice and Alyx Star". "Quoted words" must
        # appear together, as written.
        search_terms = [(a or b).casefold() for a, b in re.findall(r'"([^"]+)"|(\S+)', search_text)]
        # 2.17.0: * is a wildcard inside one value (one tag, one name...): "avc*"
        # a word starting with avc, "*avc" ending with it, "h*vc" anything between.
        # A word without * still matches any part ("avc" = "*avc*").
        wild_terms = [_wildcard(t) for t in search_terms if "*" in t and t.strip("*")]
        search_terms = [t for t in search_terms if "*" not in t]

        quality_set = {q.upper() for q in (qualities or []) if q}

        min_quality_rank = QUALITY_RANK.get((min_quality or "").upper()) if min_quality else None

        # Performer details (2.19.0): a video matches when ONE of its performers has
        # all of them (cup D *and* 25-30 = the same person). Known only for
        # performers matched to ThePornDB.
        p_cups_set = {c.upper() for c in (p_cups or []) if c}
        p_hair_set = {h.casefold() for h in (p_hair or []) if h}
        p_eyes_set = {e.casefold() for e in (p_eyes or []) if e}
        performer_filter = bool(p_cups_set or p_hair_set or p_eyes_set or p_age_min is not None
                                or p_age_max is not None or p_enhanced is not None)
        facts = self.performer_facts_by_name() if performer_filter else {}

        def fits(f: Optional[dict]) -> bool:
            if not f:
                return False
            if p_cups_set and f.get("cup") not in p_cups_set:
                return False
            if p_hair_set and (f.get("hair") or "").casefold() not in p_hair_set:
                return False
            if p_eyes_set and (f.get("eyes") or "").casefold() not in p_eyes_set:
                return False
            age = f.get("age")
            if (p_age_min is not None or p_age_max is not None) and age is None:
                return False
            if p_age_min is not None and age < p_age_min:
                return False
            if p_age_max is not None and age > p_age_max:
                return False
            return p_enhanced is None or f.get("enhanced") is p_enhanced


        def matches(item: LibraryItem) -> bool:
            if statuses_set and item.status not in statuses_set:
                return False
            # A codec family ("AV1", from My settings -> Library -> Group codec versions)
            # matches all its versions ("AV1 (Main@L4.0)"...).
            if codecs_set and item.tech_codec not in codecs_set and codec_family(item.tech_codec) not in codecs_set:
                return False
            if tags_set and not has(tags_set, item.tags, "tags"):
                return False
            if performers_set and not has(performers_set, item.performers, "performers"):
                return False
            if creators_set and (item.creator or "").lower() not in creators_set:
                return False
            if gone is not None and (item.site_status == "removed") != gone:
                return False
            if min_duration is not None or max_duration is not None:
                secs = parse_duration_seconds(item.tech_duration)
                if secs is None:
                    return False
                if min_duration is not None and secs < min_duration:
                    return False
                if max_duration is not None and secs > max_duration:
                    return False
            if min_rating is not None and (parse_rating_percent(item.ratings) or 0) < min_rating:
                return False
            if quality_set or min_quality_rank is not None:
                bucket = quality_bucket(item.tech_resolution)
                if quality_set and bucket not in quality_set:
                    return False
                if min_quality_rank is not None and QUALITY_RANK.get(bucket, -1) < min_quality_rank:
                    return False
            if orientation and video_orientation(item.tech_resolution) != orientation:
                return False
            if performer_filter and not any(fits(facts.get(fold(n))) for n in item.performers or []):
                return False
            if search_terms or wild_terms:
                if search_field == "title":
                    haystack = item.title.lower()
                elif search_field == "creator":
                    haystack = item.creator.lower()
                elif search_field == "tags":
                    haystack = " ".join(item.tags).lower()
                elif search_field == "performers":
                    haystack = " ".join([*item.performers, *spellings.canon_list("performers", item.performers)]).lower()
                elif search_field == "description":
                    haystack = item.description.lower()
                elif search_field == "music":
                    haystack = " ".join(item.music).lower()
                elif search_field == "codec":
                    haystack = (item.tech_codec or "").lower()
                else:
                    haystack = " ".join(
                        [item.title, item.creator, item.url, item.description,
                         *item.tags, *item.performers, *spellings.canon_list("performers", item.performers), *item.music]
                    ).casefold()
                haystack = haystack.casefold()
                if not all(term in haystack for term in search_terms):
                    return False
                if wild_terms:
                    lines = _search_lines(item, search_field)
                    if not all(w.search(lines) for w in wild_terms):
                        return False
            return True

        filtered = [i for i in self._items.values() if matches(i)]

        sort_key = sort_key if sort_key in self._SORT_KEYS else "created_at"

        def sort_value(item: LibraryItem):
            if sort_key == "tech_duration":
                return parse_duration_seconds(item.tech_duration) or -1
            if sort_key == "tech_filesize":
                return parse_filesize_mb(item.tech_filesize) or -1
            if sort_key in ("created_at", "download_datetime"):
                return _acquired(item)
            if sort_key == "ratings":
                return parse_rating_percent(item.ratings) or -1
            if sort_key == "tech_resolution":
                return _resolution_height(item.tech_resolution)
            if sort_key == "views":
                return (item.site_stats or {}).get("views") or -1
            value = getattr(item, sort_key, "") or ""
            return value.lower() if isinstance(value, str) else value

        reverse = sort_dir == "desc"
        filtered.sort(key=sort_value, reverse=reverse)
        if sort_key == "upload_date":  # unknown dates last, whichever direction
            filtered = [i for i in filtered if i.upload_date] + [i for i in filtered if not i.upload_date]

        total = len(filtered)
        page_size = max(1, min(page_size, 200))
        total_pages = max(1, math.ceil(total / page_size))
        page = min(max(1, page), total_pages)
        start = (page - 1) * page_size
        page_items = filtered[start: start + page_size]

        return {
            "items": [i.model_dump() for i in page_items],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }

    # Content-flag labels as the interface shows them (ui/src/lib/metadata.js).
    _FLAG_LABELS = ("Explicit content", "Extreme content", "Gay content", "Trans content", "Voice over", "Seizure warning")

    def suggest_values(self, field: str, q: str = "", limit: int = 15) -> list[dict]:
        """Existing values of performers/tags/music/flags for autocomplete:
        ones that start with what's typed first, then ones with a word that
        does ("snow" -> "Skylar Snow"), then any other match; the most used
        first within each group."""
        counts: dict[str, int] = {}
        for item in self._items.values():
            if item.status == "merged":
                continue
            if field == "flags":
                values = item.user_edits.get("flags", [])
            elif field == "creators":
                values = [item.creator] if item.creator else []
            else:
                # One entry per value however it's capitalised, in its shown spelling.
                values = spellings.canon_list(field, getattr(item, field, None) or [])
            for v in values:
                counts[v] = counts.get(v, 0) + 1
        if field == "flags":
            for label in self._FLAG_LABELS:
                counts.setdefault(label, 0)
        needle = q.strip().casefold()

        def rank(value: str):
            v = value.casefold()
            if not needle:
                return 0
            if v.startswith(needle):
                return 0
            if any(word.startswith(needle) for word in re.split(r"[\s\-_/·—]+", v)):
                return 1
            return 2 if needle in v else None

        scored = [(r, -n, v.casefold(), v, n) for v, n in counts.items() if (r := rank(v)) is not None]
        scored.sort()
        return [{"value": v, "count": n} for *_, v, n in scored[:max(1, min(limit, 50))]]

    def related_items(self, item_id: str, limit: int = 12) -> list[dict]:
        """Downloaded videos most similar to `item_id`: shared performers
        weigh most, then same creator, then shared tags. Ties go to the more
        recently downloaded video."""
        base = self._items.get(item_id)
        if base is None:
            raise KeyError(item_id)
        performers = {spellings.key("performers", v) for v in base.performers}
        tags = {fold(v) for v in base.tags}
        creator = (base.creator or "").lower()
        scored = []
        for item in self._items.values():
            if item.id == item_id or item.status != "done":
                continue
            score = 3 * len(performers.intersection(spellings.key("performers", v) for v in item.performers)) \
                + len(tags.intersection(fold(v) for v in item.tags))
            if creator and (item.creator or "").lower() == creator:
                score += 2
            if score:
                scored.append((score, item.download_datetime or "", item))
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        return [s[2].model_dump() for s in scored[: max(1, min(limit, 50))]]

    def list_active_tasks(self) -> list[dict]:
        """Every item that isn't just sitting at rest in the library: not
        yet downloaded (or failed to), or done but still being re-encoded/
        repaired/shared. Deliberately NOT routed through query_items()/
        page_size -- that method's page_size is capped at 200 for the
        Library tab's own real pagination, which silently truncated the
        Tasks tab's "fetch everything active" request whenever the library
        (across every status, not just active tasks) exceeded 200 items
        total. This returns the full set with no cap, computed directly
        here since self._items already holds everything in memory --
        mirrors isActiveTask() in frontend/js/library.js exactly, so what's
        counted as an active task is identical on both sides."""

        def is_active(item: LibraryItem) -> bool:
            # A "merged" item was resolved via the Issues tab's merge action
            # (see merge_items()) -- it's a terminal, already-handled state,
            # not something waiting on any worker, so it should never show
            # up as an active task even though its status isn't "done".
            if item.status == "merged":
                return False
            if item.status != "done":
                return True
            if item.reencode_status in ("queued", "encoding"):
                return True
            return item.repair_status in ("queued", "running")

        items = [i for i in self._items.values() if is_active(i)]
        items.sort(key=lambda i: i.created_at, reverse=True)
        return [i.model_dump() for i in items]

    def get_summary(self) -> dict:
        """Facet option lists (for the filter dropdowns) and live task
        counts (for the header status indicator), both computed across the
        *entire* library regardless of what page is currently loaded --
        deliberately separate from query_items() so switching pages never
        makes a filter dropdown or the "2 downloading" indicator flicker
        down to just what's on the current page.

        "merged" items (see merge_items()) are excluded entirely here --
        they're a resolved, terminal bookkeeping record kept only so a
        re-added URL is recognized, not a real entry in the library
        anymore, so they shouldn't count toward total_items, appear as a
        filterable status, or contribute their (now-cleared) tags/
        performers/codec to the facet lists."""
        tags: set[str] = set()
        performers: set[str] = set()
        codecs: set[str] = set()
        statuses: set[str] = set()
        creators: set[str] = set()
        gone_count = 0
        done_count = 0

        total_items = 0
        downloading = pending = fetching = 0
        encoding = queued_encodes = 0
        uploading = queued_uploads = 0

        for item in self._items.values():
            if item.status == "merged":
                continue
            total_items += 1

            tags.update(item.tags)
            performers.update(item.performers)
            if item.creator:
                creators.add(item.creator)
            if item.site_status == "removed":
                gone_count += 1
            if item.status == "done":
                done_count += 1
            if item.tech_codec:
                codecs.add(item.tech_codec)
            statuses.add(item.status)

            if item.status == "downloading":
                downloading += 1
            elif item.status == "pending":
                pending += 1
            elif item.status == "fetching":
                fetching += 1

            if item.reencode_status == "encoding":
                encoding += 1
            elif item.reencode_status == "queued":
                queued_encodes += 1

        return {
            "total_items": total_items,
            "facets": {
                "tags": sorted(tags),
                "performers": sorted(performers),
                "codecs": sorted(codecs),
                "statuses": sorted(statuses),
                "creators": sorted(creators, key=str.lower),
            },
            "gone_count": gone_count,
            # Videos in the Library view (downloaded) -- lets live clients
            # notice a download finishing without re-polling the list.
            "done_count": done_count,
            # Waiting order for items with reencode_status == "queued" (the
            # currently-encoding item, if any, isn't in this list -- see
            # reorder_reencode's docstring). Lets the frontend show position
            # and enable/disable move-up/move-down without a second request.
            "reencode_queue_order": list(self._reencode_queue),
            "reencode_current_id": self._current_reencode_id,
            # Same idea as reencode_queue_order above, but for the repair
            # queue -- lets the Admin Portal's Tasks page (see
            # frontend/js/pages/admin/tasks.js) show "#N of M" for an item
            # still waiting on a repair slot, the same way it already can
            # for a queued re-encode. Unlike reencode_queue_order, repair
            # can run several items concurrently (see
            # _repair_max_concurrent) -- this list is still just the
            # items in "queued" repair_status, not the ones already
            # "running".
            "repair_queue_order": list(self._repair_queue),
            # Videos waiting to download, in the order they'll run (Tasks page).
            "download_queue_order": self.download_queue_order(),
            "reencode_codec": self.settings.reencode.codec,
            "reencode_ask": self.settings.reencode.ask_each_time,  # Re-encode… window, or straight away
            # Switched-off features are hidden everywhere (2.22.0).
            "features": self.settings.features.model_dump(),
            # Disk-space guard (disk_guard.py): the banner. No server paths here --
            # everyone gets the summary; admins see the folder in Server settings.
            "disk": {"low": bool(self._disk.get("low")), "free_percent": self._disk.get("free_percent"),
                     "free_gb": round((self._disk.get("free_bytes") or 0) / 1024 ** 3, 1),
                     "limit": self.settings.download.min_free_percent},
            "tasks": {
                "downloading": downloading,
                "pending_downloads": pending,
                "fetching": fetching,
                "encoding": encoding,
                "queued_encodes": queued_encodes,
                "uploading": uploading,
                "queued_uploads": queued_uploads,
                "jobs": activity.count(),  # library-wide jobs (backend/activity.py)
                "active_total": downloading + fetching + encoding + uploading + activity.count(),
                "queued_total": pending + queued_encodes + queued_uploads,
            },
        }

    async def add(self, url: str, added_by: Optional[str] = None) -> LibraryItem:
        # Raises ValueError (caught by main.py's route handlers -> 400) if
        # the URL doesn't match any configured site -- e.g. a typo'd
        # domain, or a site that isn't in config.yaml's `sites` block yet.
        source = sources.for_url(url)   # 2.30.0: backend/sources (built-in sites + extensions)
        site_key = source.key
        video_id = source.item_id(url)
        async with self._lock:
            existing = self._items.get(video_id)
            # Any existing item that isn't "failed" is returned as-is
            # instead of re-queued -- this also covers "merged" items (see
            # merge_items()): a video that was already identified as a
            # duplicate and merged into another item stays merged, it is
            # never silently re-downloaded just because its URL was pasted
            # in again. The frontend distinguishes this case (see add.js,
            # which checks status === "merged" on the response) so the
            # person sees a clear "already merged" message instead of it
            # looking like a normal successful add.
            if existing and existing.status not in ("failed",):
                return existing
            item = LibraryItem(id=video_id, url=url, site=site_key, status="fetching", title="Fetching details...",
                               added_by=added_by)
            self._items[video_id] = item
            self.db.save_item(item)
        # A brand-new item starts in "fetching", not "pending" -- it needs
        # the metadata worker, not the download worker, to pick it up.
        self._ensure_metadata_worker()
        return item

    async def _cancel_in_flight(self, video_id: str) -> None:
        """Actively stops whatever background operation is currently
        running for this item -- a download, a metadata fetch, an active
        re-encode, or a running repair -- and waits for it to fully stop
        before returning. Called by remove(), AFTER it has already popped
        the item from self._items (order matters -- see remove()'s own
        comment on why: the operation's own `finally` block decides
        whether to clean up its partial file by checking whether the
        item is still in self._items, and that check needs to already
        see "no" by the time this method runs).

        Why this is necessary: this app's download/metadata-fetch workers
        are simple loops that `await` one item's operation directly rather
        than spawning a separately-cancellable task per item -- without
        this, deleting an item's bookkeeping mid-operation did NOT stop
        the operation itself. The coroutine kept running against the
        now-detached item object until it finished naturally, and:
          - For a download or metadata fetch, the single worker loop
            processing it stayed blocked the entire time, which silently
            prevented the NEXT queued item from starting.
          - Whichever operation eventually finished still called
            self.db.save_item(item) as part of its own normal completion
            logic, silently resurrecting the row remove() had just deleted.
          - Whatever partial ".part" temp file that operation was mid-
            writing was left behind on disk forever, since stream_download()
            (and, for an HLS remux, media_tools.download_hls_to_mp4())
            only renames/replaces that temp file into the real destination
            on a SUCCESSFUL finish.

        This method cancels the actual asyncio.Task backing a download or
        metadata fetch (see _worker_loop/_metadata_worker_loop, which
        register each one in self._active_download_tasks/
        self._active_metadata_tasks keyed by item id) and awaits it so the
        cancellation has actually taken effect before returning. For a
        download that's an HLS remux specifically, a plain task.cancel()
        has no effect on its own (see _download_hls()'s docstring for why)
        -- the underlying ffmpeg process (tracked in
        self._active_download_procs) is terminated directly first, which
        makes the blocking call return promptly. _download()'s own
        `finally` block is what actually deletes the leftover partial
        file once the item is gone from self._items -- this method's job
        is only to stop the operation and wait for that to have happened.

        For an active re-encode, this reuses the same ffmpeg-process-
        termination path cancel_reencode() uses, then waits (bounded, so a
        stuck process can't hang removal forever) for the reencode worker
        to notice and stop. For a running repair, it cancels that item's
        tracked repair task the same way as downloads/metadata fetches.

        A no-op (returns immediately) for any operation that isn't
        currently running for this id."""
        download_task = self._active_download_tasks.get(video_id)
        if download_task and not download_task.done():
            proc_holder = self._active_download_procs.get(video_id)
            if proc_holder:
                proc = proc_holder.get("proc")
                if proc and proc.poll() is None:
                    proc.terminate()
            download_task.cancel()
            try:
                await download_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        metadata_task = self._active_metadata_tasks.get(video_id)
        if metadata_task and not metadata_task.done():
            metadata_task.cancel()
            try:
                await metadata_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        if self._current_reencode_id == video_id:
            self._cancel_requested = True
            proc = self._current_reencode_proc.get("proc")
            if proc and proc.poll() is None:
                proc.terminate()
            # Wait for the reencode worker to actually notice the
            # terminated process and finish its own cancellation
            # handling (see _reencode_item) -- bounded to ~5s so a
            # zombie/unresponsive ffmpeg process can't hang removal
            # forever. The process keeps being torn down in the
            # background even if this timeout is hit.
            for _ in range(50):
                if self._current_reencode_id != video_id:
                    break
                await asyncio.sleep(0.1)

        repair_task = self._active_repair_tasks.get(video_id)
        if repair_task and not repair_task.done():
            repair_task.cancel()
            try:
                await repair_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def remove(self, video_id: str, delete_files: bool = False) -> dict:
        async with self._lock:
            item = self._items.pop(video_id, None)
            self.db.delete_item(video_id)
            # Duplicates merged into this video only existed to say "already
            # downloaded as <this one>"; with it gone, they go too.
            linked = [i.id for i in self._items.values() if i.status == "merged" and i.merged_into == video_id]
            for linked_id in linked:
                self._items.pop(linked_id, None)
                self.db.delete_item(linked_id)
            if linked:
                logger.info("Removed {} duplicate record(s) linked to [{}]", len(linked), video_id)
            # Any persisted duplicate-match rows referencing this item are
            # now stale (see backend/library_mixins/duplicate_worker.py) --
            # clean them up so a removed item doesn't keep showing up
            # inside a duplicate group on the Issues page.
            self.db.delete_duplicate_matches_for_item(video_id)
            self.db.delete_dup_samples_for_item(video_id)

        if item is None:
            return {"existed": False, "deleted_files": 0}
        self.remove_storyboard(video_id)  # seekbar previews: only useful with the video in the library
        self.forget_watch_positions(video_id)  # everyone's place in it (2.24.0)

        # Stop whatever's actively running for this item, now that it's
        # already gone from self._items. Order matters here: popped THEN
        # cancelled, not the other way around -- the in-flight
        # download's/metadata-fetch's own `finally` block (see
        # _download()/_fetch_metadata()) decides whether to clean up its
        # partial ".part" temp file by checking whether its item id is
        # STILL in self._items; that check needs to already see "no" by
        # the moment cancellation actually happens, or the partial-file
        # cleanup would be silently skipped. See _cancel_in_flight()'s
        # docstring for the full explanation of why cancellation itself
        # is necessary in the first place (queue-stall prevention, not
        # resurrecting the just-deleted row).
        await self._cancel_in_flight(video_id)

        deleted_files = 0
        # A video that never finished downloading (queued, downloading or
        # failed) has only its fetched details in the cache: thumbnail,
        # preview clip and metadata JSON (plus maybe a funscript). Nothing
        # usable is left once it's removed, so those always go.
        if not delete_files and item.status not in ("done", "merged"):
            for attr in ("funscript_path", "metadata_path", "thumbnail_path", "preview_path"):
                file_path_str = getattr(item, attr, None)
                if file_path_str and await asyncio.to_thread(delete_with_retry, Path(file_path_str), video_id):
                    deleted_files += 1
            for p in (item.thumbnail_path, item.preview_path, item.metadata_path):
                if p:
                    with contextlib.suppress(OSError):
                        parent = Path(p).parent
                        if parent.exists() and not any(parent.iterdir()):
                            parent.rmdir()
                    break
        if delete_files:
            for attr in ("video_path", "funscript_path", "metadata_path", "thumbnail_path", "preview_path"):
                file_path_str = getattr(item, attr, None)
                if not file_path_str:
                    continue
                file_path = Path(file_path_str)
                if await asyncio.to_thread(delete_with_retry, file_path, video_id):
                    deleted_files += 1

            # clean up an empty creator folder left behind, but only if it's
            # actually empty -- never delete a folder that still has other
            # videos in it.
            if item.video_path:
                creator_dir = Path(item.video_path).parent
                try:
                    if creator_dir.exists() and not any(creator_dir.iterdir()):
                        creator_dir.rmdir()
                except Exception:
                    pass
            if item.thumbnail_path or item.preview_path or item.metadata_path:
                for p in (item.thumbnail_path, item.preview_path, item.metadata_path):
                    if p:
                        cache_creator_dir = Path(p).parent
                        try:
                            if cache_creator_dir.exists() and not any(cache_creator_dir.iterdir()):
                                cache_creator_dir.rmdir()
                        except Exception:
                            pass
                        break

        return {"existed": True, "deleted_files": deleted_files}

    async def retry(self, video_id: str) -> bool:
        async with self._lock:
            item = self._items.get(video_id)
            if not item:
                return False
            item.status = "pending"
            item.error = None
            item.touch()
            self.db.save_item(item)
        self._ensure_worker()
        return True

    def _ensure_worker(self) -> None:
        """Starts a loop for every download lane the settings ask for that
        isn't running yet (each takes one video at a time)."""
        for lane in self._wanted_download_lanes():
            task = self._download_lanes.get(lane)
            if task is None or task.done():
                self._download_lanes[lane] = asyncio.create_task(self._worker_loop(lane))

    def start(self) -> None:
        """Resumes any work _load() found still pending/queued from before a
        restart. Must be called once from the app's own startup (see
        lifespan() in main.py) rather than from __init__ -- __init__ runs at
        import time, when library_manager is constructed as a module-level
        singleton, well before uvicorn's event loop exists for
        asyncio.create_task() to attach a worker to. Cheap and safe to call
        even when there's nothing to do: _ensure_worker()/
        _ensure_metadata_worker()/_ensure_reencode_worker()/
        _ensure_repair_worker() are no-ops if their queue is empty, same as
        when a request handler calls them mid-run."""
        self._ensure_worker()
        self._ensure_metadata_worker()
        self._ensure_reencode_worker()
        self._ensure_repair_worker()
        self._ensure_scheduler_worker()
        # After a restore: find the files in the folders now set (2.25.1, backend/folders.py).
        self._relink_task = asyncio.create_task(self.run_pending_relink())
        self._ensure_disk_guard()
        self.clean_incoming_uploads()
        from ..transcode import live_transcoder
        live_transcoder.clean(self.settings)
        activity.set_notifier(lambda: self.db._note_change(["activity"]))
        activity.on_finish = _announce_job
        notifications.bind(self.db)
        stats_store.bind(self.db)
        if self.db.get_app_state("stats_daily_since", None) is None:
            self.db.set_app_state("stats_daily_since", stats_store.today())
        self.seed_default_system_jobs()
        asyncio.get_running_loop().create_task(self._backfill_upload_dates())

    async def _backfill_upload_dates(self) -> None:
        """One-off for videos downloaded before upload_date was stored: read it
        from the metadata file, else from the file name ("Creator - YYYY-MM-DD -
        Title [id]"). Runs in the background at startup; does nothing once filled."""
        missing = [i for i in self._items.values() if i.status == "done" and not i.upload_date]
        if not missing:
            return

        def read(item) -> Optional[str]:
            if item.metadata_path:
                try:
                    with open(item.metadata_path, encoding="utf-8") as fh:
                        data = json.load(fh)
                    # Two file formats: the curated one ("upload_date": "2024-09-05")
                    # and the raw PMVHaven API response ("uploadDate": ISO timestamp).
                    value = data.get("upload_date") or data.get("uploadDate")
                    if value:
                        return str(value)[:10]
                except (OSError, ValueError, AttributeError):
                    pass
            # "Creator - 2024-09-05 - Title [id]", or "- 2024-09-05 - Title [id]"
            # when the uploader's account was deleted (empty creator).
            m = re.search(r"(?:^|\s)-\s(\d{4}-\d{2}-\d{2})\s-\s", Path(item.video_path or "").name)
            return m.group(1) if m else None

        async with activity.track("upload_dates", "Reading upload dates", source="auto",
                                  stage=f"{len(missing)} videos without one"):
            found = await asyncio.to_thread(lambda: {i.id: read(i) for i in missing})
        filled = 0
        async with self._lock:
            for item_id, date in found.items():
                item = self._items.get(item_id)
                if date and item and not item.upload_date:
                    item.upload_date = date
                    filled += 1
            if filled:
                self.db.save_items([self._items[i] for i, d in found.items() if d and i in self._items])
        if filled:
            logger.info("Filled in the upload date of {} video(s) from their metadata files", filled)

    async def reorder_reencode(self, video_id: str, direction: str) -> None:
        """Moves video_id one slot earlier ('up') or later ('down') among
        items still *waiting* to be re-encoded. The item currently encoding
        (tracked separately in self._current_reencode_id, already popped off
        self._reencode_queue by the time it starts -- see
        _reencode_worker_loop) can't be reordered, since it's already
        running; only the FIFO order of what runs after it can change."""
        if direction not in ("up", "down"):
            raise ValueError("direction must be 'up' or 'down'")
        async with self._lock:
            if video_id not in self._reencode_queue:
                raise ValueError("This item isn't currently waiting in the re-encode queue")
            idx = self._reencode_queue.index(video_id)
            if direction == "up":
                if idx == 0:
                    raise ValueError("Already next in line")
                swap_with = idx - 1
            else:
                if idx == len(self._reencode_queue) - 1:
                    raise ValueError("Already last in the queue")
                swap_with = idx + 1
            self._reencode_queue[idx], self._reencode_queue[swap_with] = (
                self._reencode_queue[swap_with],
                self._reencode_queue[idx],
            )

    def download_queue_order(self) -> list[str]:
        """Ids of videos waiting to download, in the order they'll run."""
        pending = [i for i in self._items.values() if i.status == "pending"]
        return [i.id for i in sorted(pending, key=lambda i: i.download_rank)]

    async def reorder_download(self, video_id: str, direction: str) -> None:
        """Moves a waiting download one place earlier/later ('up'/'down') or
        to the front/back ('top'/'bottom'). Downloads already running are
        unaffected; the new order is saved, so it survives a restart."""
        if direction not in ("up", "down", "top", "bottom"):
            raise ValueError("direction must be 'up', 'down', 'top' or 'bottom'")
        async with self._lock:
            pending = sorted((i for i in self._items.values() if i.status == "pending"), key=lambda i: i.download_rank)
            idx = next((n for n, i in enumerate(pending) if i.id == video_id), None)
            if idx is None:
                raise ValueError("This video isn't waiting in the download queue")
            item = pending[idx]
            changed = [item]
            if direction == "top":
                if idx == 0:
                    raise ValueError("Already next in line")
                item.queue_rank = pending[0].download_rank - 1
            elif direction == "bottom":
                if idx == len(pending) - 1:
                    raise ValueError("Already last in the queue")
                item.queue_rank = pending[-1].download_rank + 1
            else:
                other_idx = idx - 1 if direction == "up" else idx + 1
                if not 0 <= other_idx < len(pending):
                    raise ValueError("Already next in line" if direction == "up" else "Already last in the queue")
                other = pending[other_idx]
                a, b = item.download_rank, other.download_rank
                if a == b:  # same timestamp: nudge apart so the swap is visible
                    b = a + (-0.001 if direction == "up" else 0.001)
                item.queue_rank, other.queue_rank = b, a
                changed.append(other)
            for i in changed:
                i.touch()
            self.db.save_items(changed)

    async def shutdown(self) -> None:
        """Called once from main.py's lifespan(), after `yield` (i.e. as
        the server is actually shutting down) -- stops every background
        worker from picking up further work, terminates every OS
        subprocess we hold a live handle to (ffmpeg re-encodes, HLS
        remuxes), cancels every in-flight asyncio task, and waits
        (bounded) for that to actually happen before returning.

        Without this, background workers simply kept running after
        uvicorn logged "Shutting down" -- the Python process doesn't
        actually exit until every thread in its default thread pool
        (asyncio.to_thread) finishes naturally, which meant a large batch
        operation (e.g. "Repair/Update all 2000+ items") could keep
        grinding through the queue well after the server appeared to
        have stopped.

        KNOWN LIMITATION: Python cannot forcibly kill a thread.
        Cancelling an asyncio Task wrapping asyncio.to_thread() only
        stops the event loop from waiting on/using its eventual result --
        if the underlying call has no OS subprocess we hold a handle to
        (e.g. media_tools.generate_stash_phash()'s internal
        subprocess.run() call, or a bcrypt hash), that call keeps running
        on its own thread until it finishes naturally. What this method
        DOES guarantee: no NEW item starts once shutdown begins, and
        every OS subprocess we track (ffmpeg re-encodes, HLS remuxes) is
        terminated immediately. See repair_worker.py's phash-skip
        optimization for what reduces how often that one untrackable call
        actually runs in the first place."""
        from ..transcode import live_transcoder
        live_transcoder.end_all()  # Converting while watching (2.13.0)
        self._shutting_down = True

        # Terminate OS subprocesses we hold a live handle to FIRST -- this
        # actually stops CPU-bound ffmpeg work immediately, unlike
        # cancelling the asyncio Task wrapping it (which only stops the
        # event loop from waiting on the result, not the OS process).
        for proc_holder in list(self._active_download_procs.values()):
            proc = proc_holder.get("proc")
            if proc and proc.poll() is None:
                proc.terminate()
        if self._current_reencode_proc:
            proc = self._current_reencode_proc.get("proc")
            if proc and proc.poll() is None:
                proc.terminate()

        # Cancel every worker-loop task -- stops each loop from pulling
        # its NEXT item. Cancelling a task that's currently awaiting an
        # inner per-item task (e.g. _worker_loop awaiting
        # self._download(item)) cascades the cancellation into that inner
        # task too (documented asyncio behavior), so this also interrupts
        # whatever's in-flight wherever an await point exists to land on.
        loop_tasks = [
            t for t in [self._worker_task, self._reencode_task]
            if t is not None
        ]
        loop_tasks += list(self._download_lanes.values())
        loop_tasks += list(self._metadata_tasks)
        loop_tasks += list(self._repair_tasks)
        for task in loop_tasks:
            if not task.done():
                task.cancel()

        # Belt-and-braces: also directly cancel every currently-tracked
        # per-item task, in case a loop task was between iterations (not
        # actively awaiting its inner task) at the moment of cancellation.
        per_item_tasks = (
                list(self._active_download_tasks.values())
                + list(self._active_metadata_tasks.values())
                + list(self._active_repair_tasks.values())
        )
        for task in per_item_tasks:
            if not task.done():
                task.cancel()

        all_tasks = loop_tasks + per_item_tasks
        if all_tasks:
            # Bounded wait -- a stuck/unresponsive thread (see the known
            # limitation above) can't be forced to finish, so this doesn't
            # wait forever; whatever hasn't wound down after this timeout
            # is simply left to finish in the background as the process
            # exits.
            try:
                await asyncio.wait_for(asyncio.gather(*all_tasks, return_exceptions=True), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Some background tasks did not stop within 10s of shutdown -- proceeding anyway")

        # The Scheduler gets its own dedicated shutdown step (rather than
        # being folded into the generic loop_tasks cancellation above)
        # because it needs to do more than just stop -- see
        # _shutdown_scheduler()'s own docstring in scheduler_worker.py.
        await self._shutdown_scheduler()

        logger.info("LibraryManager shutdown complete")


# Same buckets as the interface's quality badges (ui/src/lib/format.js),
# judged on the short side so vertical videos count like horizontal ones.
QUALITY_BUCKETS = ((3240, "8K"), (1800, "UHD"), (1260, "QHD"), (900, "FHD"), (600, "HD"), (420, "SD"), (0, "LD"))
QUALITY_RANK = {label: rank for rank, (_, label) in enumerate(reversed(QUALITY_BUCKETS))}


def quality_bucket(resolution: Optional[str]) -> Optional[str]:
    """'1920x1080' -> 'FHD'; None when the resolution is unknown."""
    m = re.search(r"(\d+)\s*[x×]\s*(\d+)", resolution or "", re.I)
    if not m:
        return None
    short = min(int(m.group(1)), int(m.group(2)))
    return next(label for floor, label in QUALITY_BUCKETS if short >= floor)


def video_orientation(resolution: Optional[str]) -> Optional[str]:
    """'landscape' / 'portrait' / 'square' -- the same rule as the interface's 🖥️/📱 badge."""
    m = re.search(r"(\d+)\s*[x×]\s*(\d+)", resolution or "", re.I)
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    return "landscape" if w > h else "portrait" if w < h else "square"


def _resolution_height(resolution: Optional[str]) -> int:
    """'1920x1080' / '1080p' -> 1080, for sorting by resolution numerically
    (string sorting put '720' above '1080')."""
    nums = [int(n) for n in re.findall(r"\d+", resolution or "")]
    if not nums:
        return -1
    return nums[1] if len(nums) >= 2 else nums[0]


def _acquired(item: LibraryItem) -> float:
    """"Date acquired": when the file arrived -- downloaded (download_datetime),
    or for items without one (uploads, not downloaded yet) when the item was
    added (created_at). A bare date counts as midnight UTC that day."""
    raw = item.download_datetime
    if raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            pass
    return item.created_at or 0.0
