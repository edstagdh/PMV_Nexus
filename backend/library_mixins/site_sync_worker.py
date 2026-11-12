"""Sync with PMVHaven: compares every downloaded pmvhaven.com video with the
site's live data (REST API /videos/bulk, 100 per call) and updates tags,
performers, music, rating, description and site stats. Videos the API no
longer returns are flagged site_status="removed". Video files and names are
never touched; title changes on the site are reported but not applied, since
titles are part of the file names.

The metadata JSON sidecar is updated too (merged the same way repair does),
otherwise "Sync database from files" would put the old values back."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from loguru import logger

from ..scraper_v2 import _format_upload_date

from ..config import get_settings
from .. import activity
from ..pmv_api import BULK_LIMIT, PMVApiClient, PMVApiError
from ..scraper_v2 import _format_rating
from ._helpers import extract_tags_performers_music_from_meta_dict
from .core import JobBlockedError

STATE_KEY = "site_sync_last_result"
REPORT_LIMIT = 300
# Sidecar keys owned by this app (download URL, local MediaInfo details,
# re-encode marker) -- never overwritten by site data.
PRESERVED_JSON_KEYS = ("video_details", "video_url", "is_hls", "funscript_url", "Re-encoded")


def _list_diff(old: list[str], new: list[str]) -> dict:
    diff = {"added": [x for x in new if x not in old], "removed": [x for x in old if x not in new]}
    if not diff["added"] and not diff["removed"]:
        diff["reordered"] = True  # same entries, new ranking (e.g. tag votes shifted)
    return diff


def _norm_text(text: str) -> str:
    """Whitespace-insensitive form, so line-ending/spacing differences between
    the old page scraper and the API are not reported as description changes."""
    return " ".join((text or "").split())


def compute_changes(item, video: dict) -> tuple[dict, dict]:
    """(changes to apply, report entry). Tags are only compared when the API
    returned tagVotes: without them the ranking falls back to the site's short
    flat tag list, which would wrongly drop tags."""
    apply: dict = {}
    report: dict = {}
    tags, performers, music = extract_tags_performers_music_from_meta_dict(video)
    # Compared with the site's own last value: a field the user edited by hand
    # keeps the edit (see LibraryItem.apply_user_edits) and isn't a "change".
    site_tags, site_performers, site_music = (item.site_value(f) for f in ("tags", "performers", "music"))
    if "tagVotes" in video and tags and tags != site_tags:
        apply["tags"] = tags
        report["tags"] = _list_diff(site_tags, tags)
    if "starsTags" in video and performers != site_performers:
        apply["performers"] = performers
        report["performers"] = _list_diff(site_performers, performers)
    if "music" in video and music != site_music:
        apply["music"] = music
        report["music"] = _list_diff(site_music, music)
    rating = _format_rating(video.get("bayesianRating"))
    if rating and rating != item.ratings:
        apply["ratings"] = rating
        report["ratings"] = {"old": item.ratings, "new": rating}
    desc = (video.get("description") or "").strip()
    if desc and _norm_text(desc) != _norm_text(item.description):
        apply["description"] = desc
        report["description"] = {"changed": True}
    title = (video.get("title") or "").strip()
    if title and title != (item.title or "").strip():
        report["title"] = {"old": item.title, "new": title, "applied": False}
    return apply, report


def _site_stats(video: dict) -> dict:
    return {k: video.get(k) for k in ("views", "likes", "dislikes", "favorites") if video.get(k) is not None}


def _merge_sidecar(path: Path, video: dict) -> None:
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return  # no readable sidecar: nothing to keep in step
    merged = {**existing, **video}
    for key in PRESERVED_JSON_KEYS:
        if key in existing:
            merged[key] = existing[key]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


class SiteSyncWorkerMixin:
    _site_sync_running: bool = False
    _site_sync_progress: dict = {}

    def get_site_sync_status(self) -> dict:
        return {
            "running": self._site_sync_running,
            "progress": self._site_sync_progress,
            "last_result": self.db.get_app_state(STATE_KEY, None),
            "api_key_set": bool(get_settings().pmvhaven_api_key),
        }

    def request_site_sync(self, dry_run: bool) -> None:
        if self._site_sync_running:
            raise JobBlockedError("A sync with PMVHaven is already running")
        asyncio.create_task(self._run_site_sync_safely(dry_run))

    async def _run_site_sync_safely(self, dry_run: bool) -> None:
        try:
            await self.run_site_sync(dry_run=dry_run)
        except JobBlockedError as exc:
            logger.info("Sync with PMVHaven not started: {}", exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Sync with PMVHaven failed")
            self.db.set_app_state(STATE_KEY, {"error": str(exc), "finished_at": time.time(), "dry_run": dry_run})

    async def run_site_sync(self, dry_run: bool = False) -> dict:
        """The sync (or its preview), shown on the Tasks page while it runs."""
        if self._site_sync_running:
            raise JobBlockedError("A sync with PMVHaven is already running")
        if not dry_run and self._invasive_lock_holder is not None:
            raise JobBlockedError(f"Blocked: '{self._invasive_lock_holder}' is already running")
        title = "Sync with PMVHaven (preview)" if dry_run else "Sync with PMVHaven"
        async with activity.track("site_sync", title, progress=self._site_sync_activity, stop=True,
                                  # a real sync can only stop while it's still reading (nothing changed yet)
                                  stop_when=lambda: dry_run or (self._site_sync_progress or {}).get("phase") != "applying") as act:
            result = await self._site_sync(dry_run)
            act.summary = act.summary or activity.summarize(result)
            return result

    def _site_sync_activity(self) -> dict:
        p = self._site_sync_progress or {}
        if p.get("phase") == "fetching":
            return {"stage": "Fetching video details from the sites", "done": p.get("done"), "total": p.get("total")}
        if p.get("phase") == "applying":
            return {"stage": "Comparing and applying changes"}
        return {"stage": "Starting"}

    async def _site_sync(self, dry_run: bool = False) -> dict:
        if self._site_sync_running:
            raise JobBlockedError("A sync with PMVHaven is already running")
        locked = False
        if not dry_run:
            if not self._try_acquire_invasive_lock("site_sync"):
                raise JobBlockedError(f"Blocked: '{self._invasive_lock_holder}' is already running")
            locked = True
        self._site_sync_running = True
        started = time.time()
        try:
            settings = get_settings()
            done = [i for i in self._items.values() if i.status == "done"]
            # Every site on the platform has the same API (and takes the same
            # API key); each site's videos are fetched from that site.
            by_site: dict[str, list] = {}
            for i in done:
                if (i.site or "pmvhaven") in settings.sites:
                    by_site.setdefault(i.site or "pmvhaven", []).append(i)
            total = sum(len(v) for v in by_site.values())
            self._site_sync_progress = {"phase": "fetching", "done": 0, "total": total}

            found: dict[str, dict] = {}
            api_fields: set[str] = set()
            site_errors: dict[str, str] = {}
            items = []
            fetched = 0
            for site_key, site_items in by_site.items():
                ids = [i.id for i in site_items]
                site_found: dict[str, dict] = {}
                try:
                    async with PMVApiClient(settings, site_key) as api:
                        for start in range(0, len(ids), BULK_LIMIT):
                            if self._shutting_down:
                                raise PMVApiError("Interrupted by shutdown")
                            batch = ids[start:start + BULK_LIMIT]
                            for v in await api.bulk_videos(ids=batch):
                                if v.get("_id") in batch:
                                    site_found[v["_id"]] = v
                            missing = [i for i in batch if i not in site_found]
                            if missing:  # items added by a legacy (old-site) id
                                for v in await api.bulk_videos(old_ids=missing):
                                    if v.get("oldId") in missing:
                                        site_found[v["oldId"]] = v
                            self._site_sync_progress["done"] = fetched + min(start + BULK_LIMIT, len(ids))
                    if site_key != "pmvhaven" and len(ids) >= 5 and not site_found:
                        # Found none at all: more likely the site's API doesn't
                        # serve these videos than that every one was removed.
                        raise PMVApiError(f"the site's API returned none of {len(ids)} videos")
                except PMVApiError as exc:
                    if site_key == "pmvhaven" or "shutdown" in str(exc):
                        raise
                    # A sister site whose API can't be reached: leave its
                    # videos alone (never mark them removed) and report it.
                    label = settings.sites[site_key].label or site_key
                    logger.warning("Sync: skipping {} videos -- {}", label, exc)
                    site_errors[label] = str(exc)
                    fetched += len(ids)
                    continue
                fetched += len(ids)
                found.update(site_found)
                items.extend(site_items)
            for v in list(found.values())[:20]:
                api_fields.update(v.keys())

            self._site_sync_progress["phase"] = "applying"
            now = time.time()
            changes, removed = [], []
            updated = newly_removed = applicable = title_diffs = 0
            field_counts: dict[str, int] = {}
            async with self._lock:
                for item in items:
                    if item.id not in self._items:
                        continue
                    video = found.get(item.id)
                    if video is None:
                        if item.site_status != "removed":
                            newly_removed += 1
                        removed.append({"id": item.id, "title": item.title})
                        if not dry_run:
                            item.site_status = "removed"
                            item.site_synced_at = now
                            self.db.save_item(item)
                        continue
                    apply, report = compute_changes(item, video)
                    if report:
                        changes.append({"id": item.id, "title": item.title, "changes": report})
                    applicable += bool(apply)
                    title_diffs += "title" in report
                    for field, info in report.items():
                        key = f"{field}_reordered" if isinstance(info, dict) and info.get("reordered") else field
                        field_counts[key] = field_counts.get(key, 0) + 1
                    if dry_run:
                        continue
                    for field, value in apply.items():
                        setattr(item, field, value)
                    item.site_stats = _site_stats(video)
                    if video.get("uploadDate"):  # kept current silently: not a "change" to report
                        item.upload_date = _format_upload_date(video["uploadDate"]) or item.upload_date
                    item.site_status = "available"
                    item.site_synced_at = now
                    if apply:
                        updated += 1
                        item.touch()  # only for visible changes: avoids re-fetching every thumbnail
                        if item.metadata_path and Path(item.metadata_path).is_file():
                            try:
                                _merge_sidecar(Path(item.metadata_path), video)
                            except OSError as exc:
                                logger.warning("Could not update metadata JSON for {}: {}", item.id, exc)
                    self.db.save_item(item)

            result = {
                "dry_run": dry_run, "finished_at": time.time(), "elapsed_seconds": round(time.time() - started, 1),
                "checked": len(items), "found": len(found),
                # with_changes: videos with something to apply (title-only
                # differences are reported but never applied).
                "with_changes": applicable, "title_differences": title_diffs, "updated": updated,
                "field_counts": field_counts,
                "removed": len(removed), "newly_removed": newly_removed,
                "skipped_other_sites": len(done) - len(items),
                "site_errors": site_errors,
                "tag_votes_available": "tagVotes" in api_fields,
                "api_fields": sorted(api_fields),
                "changes": changes[:REPORT_LIMIT], "removed_items": removed[:REPORT_LIMIT],
                "truncated": len(changes) > REPORT_LIMIT or len(removed) > REPORT_LIMIT,
            }
            self.db.set_app_state(STATE_KEY, result)
            logger.info(
                "Sync with PMVHaven{}: {} checked, {} with changes, {} updated, {} not on the site, {}s "
                "| changed fields: {} | tag votes from API: {}",
                " (preview)" if dry_run else "", len(items), applicable, updated, len(removed),
                result["elapsed_seconds"],
                ", ".join(f"{k}={v}" for k, v in sorted(field_counts.items())) or "none",
                "yes" if result["tag_votes_available"] else "no",
            )
            return {k: v for k, v in result.items() if k not in ("changes", "removed_items")}
        finally:
            self._site_sync_running = False
            self._site_sync_progress = {}
            if locked:
                self._release_invasive_lock("site_sync")
