"""Performer profiles from ThePornDB (2.7.0).

Every performer name in the library is matched once to a ThePornDB
profile, by its id (uuid), which is kept from then on -- so the right
person's data is always fetched, whatever the name looks like later:

  - a search on ThePornDB; a single exact hit on the name or one of the
    aliases links automatically; several possible people, or only near
    misses, wait in the review list (Maintenance -> Performers) for a pick;
  - when a profile is linked, other names in the library that ThePornDB
    lists as her aliases point to the same profile ("Luna P" -> Luna
    Prism). With Server Settings -> Performers -> merge aliases on, the app
    then shows them as one name everywhere (see spellings.py); a wrong
    merge is split on her profile page, and that name is never merged into
    her again;
  - the profile (bio, details, aliases, similar performers) and photos are
    fetched once and refreshed every `refresh_days`.

Matching new names and refreshing old profiles is the Scheduler system job
"performer_sync" (run_performer_sync; daily by default, editable there);
Maintenance -> Performers -> "Match now" runs that job.

Names are keyed by spellings.fold() (case and spacing don't matter).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from datetime import date
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Optional

from loguru import logger

from .. import tpdb
from .. import activity
from .core import JobBlockedError
from ..spellings import fold, spellings

JOB_KEY = "performer_sync"  # Scheduler system job (system_worker.SYSTEM_JOBS)
MAX_CANDIDATES = 6
MAX_PHOTOS = 10  # from ThePornDB per performer (2.18.0: was 8), plus 1 of your own (MAX_CUSTOM_PHOTOS)



# ---- free-text facts from ThePornDB -> filterable values (Performers page, 2.18.0)
_NONE_WORDS = {"", "none", "no", "n/a", "na", "unknown", "-", "0", "false"}


def _clean(value) -> Optional[str]:
    text = str(value or "").strip()
    return None if text.lower() in _NONE_WORDS else text[:1].upper() + text[1:]


def _yes_no(value) -> Optional[bool]:
    """True / False / None (not known): booleans, "Yes"/"No", or a description ("Left arm") = yes."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("yes", "true", "1", "y"):
        return True
    return False if text in _NONE_WORDS else True


def _to_cm(value) -> Optional[int]:
    text = str(value or "").lower().replace(" ", "")
    if not text:
        return None
    m = re.match(r"^(\d)'(\d{1,2})?", text)  # 5'4" / 5'
    if m:
        return round(int(m.group(1)) * 30.48 + int(m.group(2) or 0) * 2.54)
    m = re.match(r"^(\d+(?:\.\d+)?)(cm|m)?", text)
    if not m:
        return None
    n = float(m.group(1))
    n = n * 100 if (m.group(2) == "m" or n < 3) else n
    return round(n) if 100 <= n <= 230 else None


def _to_kg(value) -> Optional[int]:
    text = str(value or "").lower().replace(" ", "")
    m = re.match(r"^(\d+(?:\.\d+)?)(kg|lbs?|pounds?)?", text)
    if not m:
        return None
    n = float(m.group(1)) * (0.4536 if m.group(2) and m.group(2).startswith(("lb", "pound")) else 1)
    return round(n) if 30 <= n <= 200 else None


def _cup(cupsize, measurements) -> Optional[str]:
    for text in (cupsize, measurements):
        m = re.match(r"^\s*\d*\s*([A-Ka-k]{1,4})\b", str(text or ""))
        if m:
            return m.group(1).upper()
    return None

class PerformerError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class PerformersMixin:
    _perf_loaded = False
    _perf_by_id: dict = {}
    _perf_custom: dict = {}  # owner id -> photos added by hand (2.16.0)
    _perf_local: dict = {}   # name key -> performer_local row (2.17.0)
    _perf_by_tpdb: dict = {}
    _perf_names: dict = {}
    _perf_busy: Optional[str] = None
    _perf_running = False
    _perf_step: dict = {}
    _perf_last_error: Optional[str] = None

    # ---------------------------------------------------------------- state
    def _perf_load(self, force: bool = False) -> None:
        if self._perf_loaded and not force:
            return
        by_id = {}
        for row in self.db.performer_rows():
            row["data"] = json.loads(row["data"] or "{}")
            row["similar"] = json.loads(row["similar"] or "[]")
            row["photos"] = json.loads(row.get("photos") or "[]")
            by_id[row["id"]] = row
        names = {}
        for row in self.db.performer_name_rows():
            row["candidates"] = json.loads(row["candidates"] or "[]")
            row["blocked"] = json.loads(row["blocked"] or "[]")
            names[row["name_key"]] = row
        custom: dict[str, list[dict]] = {}
        for c in self.db.custom_photo_rows():
            custom.setdefault(c["performer_id"], []).append(c)
        self._perf_custom = custom
        self._perf_local = {r["name_key"]: r for r in self.db.performer_local_rows()}
        self._perf_by_id = by_id
        self._perf_by_tpdb = {r["tpdb_id"]: r for r in by_id.values() if r.get("tpdb_id")}
        self._perf_names = names
        self._perf_loaded = True
        spellings.set_resolver("performers", self._performer_display_name)
        spellings.invalidate()

    def invalidate_performer_names(self) -> None:
        """Settings changed (e.g. merge aliases): shown names must be worked out again."""
        spellings.invalidate()
        self.db._note_change(["performers"])

    def _performer_display_name(self, value: str) -> Optional[str]:
        """spellings.py hook: with merge_aliases on, a linked name shows as her main name."""
        if not self.settings.performers.merge_aliases:
            return None
        row = self._perf_names.get(fold(value))
        if row and row.get("status") == "linked" and row.get("performer_id"):
            p = self._perf_by_id.get(row["performer_id"])
            return p["name"] if p else None
        return None

    def _save_name(self, key: str, name: str, **fields) -> dict:
        row = {**self._perf_names.get(key, {"name_key": key, "name": name, "candidates": [], "blocked": []}), **fields}
        row["name"] = row.get("name") or name
        self.db.upsert_performer_name({
            "name_key": key, "name": row["name"], "performer_id": row.get("performer_id"), "status": row["status"],
            "source": row.get("source"), "candidates": json.dumps(row.get("candidates") or []),
            "blocked": json.dumps(row.get("blocked") or []), "checked_at": row.get("checked_at") or time.time(),
        })
        self._perf_names[key] = row
        spellings.invalidate()
        if row["status"] == "linked" and row.get("performer_id"):
            # Photos added while she wasn't on ThePornDB go with her profile now (2.17.0).
            local_id = self._local_id(key)
            if self._perf_custom.get(local_id) and self.db.move_custom_photos(local_id, row["performer_id"]):
                moved = self._perf_custom.pop(local_id)
                target = self._perf_custom.setdefault(row["performer_id"], [])
                if any(c["main"] for c in target):
                    for c in moved:
                        c["main"] = 0
                for c in moved:
                    c["performer_id"] = row["performer_id"]
                target.extend(moved)
        return row

    def _library_performer_names(self) -> dict[str, tuple[str, int]]:
        """folded name -> (a spelling seen, number of videos) for finished videos."""
        out: dict[str, list] = {}
        for item in self._items.values():
            if item.status != "done":
                continue
            for name in item.performers or []:
                key = fold(name)
                if key:
                    spot = out.setdefault(key, [name, 0])
                    spot[1] += 1
        return {k: (v[0], v[1]) for k, v in out.items()}

    # ---------------------------------------------------------------- job
    def performer_job(self) -> Optional[dict]:
        """The Scheduler system job that matches and refreshes performers."""
        return next((j for j in self.db.list_scheduled_jobs() if j.get("system_key") == JOB_KEY), None)

    async def run_performer_sync(self) -> dict:
        """Scheduler job: look up new performer names on ThePornDB, refresh
        profiles older than refresh_days, fetch missing photos."""
        if not tpdb.configured():
            raise PerformerError("No ThePornDB API key -- add it in Server Settings -> Credentials")
        if self._perf_running:
            raise JobBlockedError("Performer matching is already running")
        self._perf_running = True
        self._perf_last_error = None
        self._perf_step = {"stage": "Starting"}
        try:
            async with activity.track(JOB_KEY, "Matching performers", progress=lambda: dict(self._perf_step),
                                      stop=True) as act:
                self._perf_load()
                result = {"matched": await self.match_new_performers(), "refreshed": await self.refresh_stale_performers(),
                          "photos": await self._backfill_photos()}
                act.summary = act.summary or activity.summarize(result)
                return result
        except tpdb.TPDBError as exc:
            self._perf_last_error = str(exc)
            raise
        finally:
            self._perf_running = False
            self._perf_busy = None

    async def match_new_performers(self, retry_none: bool = False) -> int:
        """Looks up every library name not handled yet. Returns how many were looked up."""
        self._perf_load()
        names = self._library_performer_names()
        todo = [(k, v[0]) for k, v in sorted(names.items(), key=lambda kv: -kv[1][1])
                if k not in self._perf_names or (retry_none and self._perf_names[k]["status"] == "none")]
        for n, (key, name) in enumerate(todo, 1):
            if self._shutting_down:
                break
            self._perf_busy = f"Matching performers… {n}/{len(todo)} ({name})"
            self._perf_step = {"stage": "Looking up new names on ThePornDB", "done": n - 1, "total": len(todo), "detail": name}
            await self._match_name(key, name)
        if todo:
            logger.info("Matched {} performer name(s) with ThePornDB", len(todo))
        return len(todo)

    async def refresh_stale_performers(self, force: bool = False) -> int:
        self._perf_load()
        limit = time.time() - self.settings.performers.refresh_days * 86400
        stale = [p for p in self._perf_by_id.values() if p.get("tpdb_id") and (force or (p.get("fetched_at") or 0) < limit)]
        for n, p in enumerate(stale, 1):
            if self._shutting_down:
                break
            self._perf_busy = f"Refreshing performer profiles… {n}/{len(stale)} ({p['name']})"
            self._perf_step = {"stage": "Refreshing profiles", "done": n - 1, "total": len(stale), "detail": p["name"]}
            try:
                await self._fetch_profile(p["tpdb_id"], p["id"])
            except tpdb.TPDBError as exc:
                logger.warning("Could not refresh performer {}: {}", p["name"], exc)
        return len(stale)

    # ---------------------------------------------------------------- matching
    async def _match_name(self, key: str, name: str) -> dict:
        row = self._perf_names.get(key, {})
        blocked = set(row.get("blocked") or [])
        results = await tpdb.search(name)
        blocked_tpdb = {self._perf_by_id[b]["tpdb_id"] for b in blocked if b in self._perf_by_id}
        results = [r for r in results if r.get("id") not in blocked_tpdb]
        exact = [r for r in results if fold(r.get("name") or "") == key or key in {fold(a) for a in tpdb.aliases(r)}]
        by_name = [r for r in exact if fold(r.get("name") or "") == key]
        pick = by_name[0] if len(by_name) == 1 else exact[0] if len(exact) == 1 else None
        if pick is not None:
            return await self._link(key, name, pick["id"], "auto")
        candidates = [tpdb.slim(r) for r in (exact or results)[:MAX_CANDIDATES]]
        status = "review" if candidates else "none"
        return self._save_name(key, name, status=status, performer_id=None, source=None, candidates=candidates,
                               checked_at=time.time())

    async def _link(self, key: str, name: str, tpdb_id: str, source: str) -> dict:
        p = self._perf_by_tpdb.get(tpdb_id)
        if p is None:
            p = await self._fetch_profile(tpdb_id)
        row = self._save_name(key, name, status="linked", performer_id=p["id"], source=source, candidates=[],
                              checked_at=time.time())
        self._link_aliases(p)
        return row

    def _link_aliases(self, p: dict) -> int:
        """Other library names that are this performer's aliases point to her too
        (unless one was split off from her before, or was linked by hand)."""
        names = self._library_performer_names()
        linked = 0
        for alias in [p["name"], *tpdb.aliases(p["data"])]:
            key = fold(alias)
            if key not in names:
                continue
            row = self._perf_names.get(key)
            if row and (row["status"] == "linked" or row["status"] == "skipped" or p["id"] in (row.get("blocked") or [])):
                continue
            self._save_name(key, names[key][0], status="linked", performer_id=p["id"], source="alias", candidates=[],
                            checked_at=time.time())
            linked += 1
        return linked

    async def _fetch_profile(self, tpdb_id: str, performer_id: Optional[str] = None) -> dict:
        data = await tpdb.performer(tpdb_id)
        existing = self._perf_by_id.get(performer_id) if performer_id else self._perf_by_tpdb.get(tpdb_id)
        pid = existing["id"] if existing else uuid.uuid4().hex
        sim = await tpdb.similar(tpdb_id)
        photos = await self._fetch_photos(pid, data, existing)
        row = {"id": pid, "tpdb_id": tpdb_id, "name": data.get("name") or (existing or {}).get("name") or "?",
               "data": json.dumps(data, ensure_ascii=False), "similar": json.dumps(sim),
               "photo_path": photos[0] if photos else None, "photos": json.dumps(photos),
               "fetched_at": time.time(), "created_at": (existing or {}).get("created_at") or time.time()}
        self.db.upsert_performer(row)
        row = {**row, "data": data, "similar": sim, "photos": photos}
        self._perf_by_id[pid] = row
        self._perf_by_tpdb[tpdb_id] = row
        spellings.invalidate()
        return row

    async def _backfill_photos(self) -> int:
        """Profiles saved before 2.7.1 have one photo: fetch the rest from the
        data already stored (no ThePornDB API call). Returns how many got photos."""
        done = 0
        for p in list(self._perf_by_id.values()):
            if self._shutting_down:
                break
            if len(p.get("photos") or []) >= len(self._photo_urls(p.get("data") or {})):
                continue
            self._perf_busy = f"Fetching photos of {p['name']}…"
            self._perf_step = {"stage": "Fetching photos", "detail": p["name"]}
            photos = await self._fetch_photos(p["id"], p.get("data") or {}, {**p, "data": {}})
            if photos:
                p["photos"], p["photo_path"] = photos, photos[0]
                self.db.set_performer_photos(p["id"], photos)
                done += 1
        return done

    @staticmethod
    def _photo_urls(data: dict) -> list[str]:
        """Her main image, then ThePornDB's posters (in their order), at most MAX_PHOTOS."""
        urls = [data.get("image") or data.get("thumbnail") or data.get("face")]
        posters = [p for p in (data.get("posters") or []) if isinstance(p, dict) and p.get("url")]
        urls += [p["url"] for p in sorted(posters, key=lambda p: p.get("order") or 0)]
        out: list[str] = []
        for u in urls:
            if u and u not in out:
                out.append(u)
        return out[:MAX_PHOTOS]

    async def _fetch_photos(self, pid: str, data: dict, existing: Optional[dict]) -> list[str]:
        """Downloads her photos to cache/performers/<id>[_n].<ext> -- again only when
        ThePornDB's list changed or a file is missing. Returns the local paths."""
        from pathlib import Path
        urls = self._photo_urls(data)
        old_paths = list((existing or {}).get("photos") or ([existing["photo_path"]] if existing and existing.get("photo_path") else []))
        if existing and self._photo_urls(existing.get("data") or {}) == urls and len(old_paths) == len(urls) \
                and all(Path(p).exists() for p in old_paths):
            return old_paths
        folder = self.performer_photos_dir()
        paths: list[str] = []
        for n, url in enumerate(urls):
            saved = await tpdb.download_photo(url, folder / (pid if n == 0 else f"{pid}_{n}"))
            if saved:
                paths.append(str(saved))
        for stale in old_paths:  # photos ThePornDB no longer lists
            if stale not in paths:
                Path(stale).unlink(missing_ok=True)
        return paths or old_paths

    # ---------------------------------------------------------------- admin actions
    async def link_performer(self, name: str, tpdb_id: str) -> dict:
        """The admin picked this ThePornDB profile for `name`."""
        self._perf_load()
        key = fold(name)
        row = self._perf_names.get(key, {})
        blocked = [b for b in (row.get("blocked") or []) if self._perf_by_id.get(b, {}).get("tpdb_id") != tpdb_id]
        if blocked != (row.get("blocked") or []):
            self._save_name(key, name, status=row.get("status", "review"), blocked=blocked)
        result = await self._link(key, name, tpdb_id, "manual")
        logger.info("Performer '{}' linked to ThePornDB {} by hand", name, tpdb_id)
        return result

    def skip_performer(self, name: str) -> dict:
        """Leave this name alone (no ThePornDB profile)."""
        self._perf_load()
        return self._save_name(fold(name), name, status="skipped", performer_id=None, source="manual", candidates=[])

    async def split_performer_name(self, name: str) -> dict:
        """Undo a wrong merge: this name stops pointing to its current performer,
        is never merged into her again, and is looked up on its own."""
        self._perf_load()
        key = fold(name)
        row = self._perf_names.get(key)
        if not row or not row.get("performer_id"):
            raise PerformerError("That name isn't linked to anyone")
        blocked = sorted({*(row.get("blocked") or []), row["performer_id"]})
        self._save_name(key, row["name"], status="review", performer_id=None, source=None, candidates=[], blocked=blocked)
        logger.info("Performer name '{}' split off", row["name"])
        if tpdb.configured():
            try:
                return await self._match_name(key, row["name"])
            except tpdb.TPDBError as exc:
                self._perf_last_error = str(exc)
        return self._perf_names[key]

    async def search_tpdb(self, q: str) -> list[dict]:
        return [tpdb.slim(r) for r in await tpdb.search(q, limit=15)]

    async def refresh_performer(self, performer_id: str) -> dict:
        self._perf_load()
        p = self._perf_by_id.get(performer_id)
        if not p or not p.get("tpdb_id"):
            raise PerformerError("Unknown performer", 404)
        await self._fetch_profile(p["tpdb_id"], performer_id)
        self._link_aliases(self._perf_by_id[performer_id])
        return {"ok": True}

    # ---------------------------------------------------------------- reading
    def _age(self, birthday: Optional[str], deathday: Optional[str]) -> Optional[int]:
        try:
            b = date.fromisoformat((birthday or "")[:10])
        except ValueError:
            return None
        end = date.today()
        if deathday:
            try:
                end = date.fromisoformat(deathday[:10])
            except ValueError:
                pass
        return end.year - b.year - ((end.month, end.day) < (b.month, b.day))

    def performer_profile(self, name: str) -> dict:
        """Everything the profile page shows for a performer name."""
        self._perf_load()
        key = fold(name)
        lib = self._library_performer_names()
        row = self._perf_names.get(key)
        p = self._perf_by_id.get(row["performer_id"]) if row and row.get("performer_id") else None
        out: dict = {"name": name, "status": row["status"] if row else ("unchecked" if key in lib else "unknown"),
                     "configured": tpdb.configured(), "merge_aliases": self.settings.performers.merge_aliases,
                     "candidates": (row or {}).get("candidates") or [], "performer": None, "names": [], "similar": []}
        if not p:
            out["names"] = [{"name": lib[key][0] if key in lib else name, "videos": lib.get(key, (name, 0))[1],
                             "source": None, "main": True}]
            if key in lib:  # her own photos and placeholder (2.17.0)
                out["local"] = self._local_info(key, lib[key][0])
            return out
        d = p["data"]
        extras = d.get("extras") or {}
        out["name"] = p["name"]
        out["performer"] = {
            "id": p["id"], "tpdb_id": p["tpdb_id"], "name": p["name"], "disambiguation": d.get("disambiguation"),
            "gender": self._gender_of(p),
            "bio": d.get("bio"), "aliases": tpdb.aliases(d), "rating": d.get("rating"),
            "photo": f"/api/performers/{p['id']}/photo?v={self._photo_version(p)}" if self._all_photos(p) else None,
            "photos": [f"/api/performers/{p['id']}/photo?n={n}&v={self._photo_version(p)}" for n in range(len(self._all_photos(p)))],
            "custom_photos": [{"id": c["id"], "main": bool(c["main"]), "by": c.get("created_by"),
                               "url": f"/api/performers/{p['id']}/custom-photo/{c['id']}"}
                              for c in self._perf_custom.get(p["id"], [])],
            "url": tpdb.SITE_URL + (d.get("slug") or p["tpdb_id"]), "fetched_at": p.get("fetched_at"),
            "age": self._age(extras.get("birthday"), extras.get("deathday")),
            "extras": {k: extras.get(k) for k in (
                "gender", "birthday", "deathday", "birthplace", "nationality", "ethnicity", "astrology", "height", "weight",
                "measurements", "cupsize", "fake_boobs", "hair_colour", "eye_colour", "tattoos", "piercings",
                "career_start_year", "career_end_year")},
            "links": {k: v for k, v in (extras.get("links") or {}).items() if v} if isinstance(extras.get("links"), dict) else {},
        }
        names = [r for r in self._perf_names.values() if r.get("performer_id") == p["id"] and r["status"] == "linked"]
        out["names"] = sorted(
            [{"name": lib[r["name_key"]][0] if r["name_key"] in lib else r["name"],
              "videos": lib.get(r["name_key"], ("", 0))[1], "source": r.get("source"),
              "main": r["name_key"] == fold(p["name"])} for r in names],
            key=lambda n: (not n["main"], -n["videos"]))
        for s in p.get("similar") or []:
            other = self._perf_by_tpdb.get(s.get("tpdb_id"))
            if other and other["id"] != p["id"]:
                in_lib = any(r.get("performer_id") == other["id"] for r in self._perf_names.values())
                if in_lib:
                    out["similar"].append({"name": other["name"], "gender": self._gender_of(other),
                                           "photo": f"/api/performers/{other['id']}/photo?v={self._photo_version(other)}"
                                           if self._all_photos(other) else None})
        return out

    # ---------------------------------------------------------------- photos as WebP (2.15.0)
    def _photos_of(self, p: dict) -> list[str]:
        """Her ThePornDB photos (saved in data/performers; before 3.0.1 cache/performers)."""
        return list(p.get("photos") or ([p["photo_path"]] if p.get("photo_path") else []))

    def _all_photos(self, p: dict) -> list[str]:
        """What her profile shows, in order (2.16.0): a photo added by hand marked
        "show first", her ThePornDB photos, then the other photos added by hand."""
        custom = self._perf_custom.get(p["id"], [])
        return ([c["path"] for c in custom if c["main"]] + self._photos_of(p)
                + [c["path"] for c in custom if not c["main"]])

    def _photo_version(self, p: dict) -> int:
        """Changes whenever the list changes, so browsers don't show a cached photo for a moved index."""
        custom = self._perf_custom.get(p["id"], [])
        mark = sum(hash((c["id"], c["main"])) for c in custom) & 0xFFFF
        return int(p.get("fetched_at") or 0) * 100000 + len(custom) * 65536 + mark

    def performer_photo_files(self) -> list[str]:
        """Every saved ThePornDB performer photo (Maintenance -> Untracked files keeps them)."""
        self._perf_load()
        return [f for p in self._perf_by_id.values() for f in self._photos_of(p)]

    def performer_photos_to_convert(self) -> int:
        self._perf_load()
        return sum(1 for f in self.performer_photo_files() if not f.lower().endswith(".webp"))

    async def convert_performer_photos(self) -> dict:
        """Maintenance -> Performers -> Convert photos to WebP: every saved photo
        not in WebP yet (see backend/images.py). Several at once (half the
        cores), shown on Tasks, can be stopped; the database is updated per
        performer, so stopping halfway loses nothing."""
        import concurrent.futures
        from .. import images
        self._perf_load()
        todo = [p for p in self._perf_by_id.values() if any(not f.lower().endswith(".webp") for f in self._photos_of(p))]
        total = sum(1 for p in todo for f in self._photos_of(p) if not f.lower().endswith(".webp"))
        st = {"done": 0, "failed": 0, "before": 0, "after": 0}
        def mb(n: int) -> str:
            n = n / 1024 / 1024
            return f"{n / 1024:,.2f} GB" if n >= 1024 else f"{n:,.1f} MB" if n < 10 else f"{n:,.0f} MB"
        workers = max(1, (os.cpu_count() or 2) // 2)
        loop = asyncio.get_running_loop()

        def convert(path: str):
            try:
                return images.convert_file(path)
            except (OSError, ValueError) as exc:
                return exc

        async with activity.track("performer_photos", "Converting performer photos to WebP", stop=True,
                                  progress=lambda: {"stage": "Converting", "done": st["done"], "total": total,
                                                    "detail": f"{mb(st['before'])} → {mb(st['after'])}"}) as act:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                async def one(p: dict) -> None:
                    old = self._photos_of(p)
                    jobs = [loop.run_in_executor(pool, convert, f) if not f.lower().endswith(".webp") and os.path.isfile(f) else None
                            for f in old]
                    new = []
                    for f, job in zip(old, jobs):
                        if job is None:
                            if not f.lower().endswith(".webp"):
                                st["failed"] += 1  # file missing
                                st["done"] += 1
                            new.append(f)
                            continue
                        result = await job
                        st["done"] += 1
                        if isinstance(result, Exception):
                            st["failed"] += 1
                            logger.warning("Couldn't convert {} to WebP: {}", f, result)
                            new.append(f)
                        else:
                            path, before, after = result
                            st["before"] += before
                            st["after"] += after
                            new.append(path)
                    if new != old:
                        self.db.set_performer_photos(p["id"], new)
                        p["photos"], p["photo_path"] = new, new[0] if new else None

                for i in range(0, len(todo), workers):
                    # A few performers at once. Shielded: Stop waits for the ones being
                    # converted, so the database never points to a replaced original.
                    batch = asyncio.gather(*(one(p) for p in todo[i:i + workers]))
                    try:
                        await asyncio.shield(batch)
                    except asyncio.CancelledError:
                        await batch
                        raise
            saved = st["before"] - st["after"]
            act.summary = (f"{st['done'] - st['failed']} photos converted, {mb(st['before'])} → {mb(st['after'])} "
                           f"({mb(saved)} saved)" + (f"; {st['failed']} couldn't be converted (see the log)" if st["failed"] else ""))
        logger.info("Performer photos to WebP: {}", act.summary)
        return {**st, "saved": saved}

    def performer_photo_path(self, performer_id: str, n: int = 0) -> Optional[str]:
        self._perf_load()
        p = self._perf_by_id.get(performer_id)
        photos = self._all_photos(p) if p else self._local_photos(performer_id)
        return photos[n] if 0 <= n < len(photos) else None

    # ---------------------------------------------------------------- performers not on ThePornDB (2.17.0)
    # A name with no profile is known by a local id made from the name ("n" + 31 hex),
    # used wherever a performer id is (photos added by hand, photo URLs). Its
    # placeholder gender is kept in performer_local (default female); when the
    # name gets linked to ThePornDB its photos move to the profile (_save_name).
    @staticmethod
    def _local_id(key: str) -> str:
        return "n" + hashlib.sha1(key.encode()).hexdigest()[:31]

    def _local_name(self, local_id: str) -> Optional[tuple[str, str]]:
        """(name key, name) of a local id: a performer in the library without a profile."""
        if not (len(local_id) == 32 and local_id.startswith("n")):
            return None
        row = next((r for r in self._perf_local.values() if r["local_id"] == local_id), None)
        if row:
            return row["name_key"], row["name"]
        for key, (name, _videos) in self._library_performer_names().items():
            if self._local_id(key) == local_id:
                return key, name
        return None

    def _local_photos(self, local_id: str) -> list[str]:
        custom = self._perf_custom.get(local_id, [])
        return [c["path"] for c in custom if c["main"]] + [c["path"] for c in custom if not c["main"]]

    def _local_version(self, local_id: str) -> int:
        custom = self._perf_custom.get(local_id, [])
        return len(custom) * 65536 + (sum(hash((c["id"], c["main"])) for c in custom) & 0xFFFF)

    @staticmethod
    def _gender_of(p: dict) -> str:
        """Placeholder silhouette for a profile without photos: ThePornDB's gender."""
        g = str(((p.get("data") or {}).get("extras") or {}).get("gender") or "").lower()
        return "male" if g in ("male", "transgender male", "trans male") else "female"

    def _photo_owner_name(self, owner: str) -> Optional[str]:
        """The performer's name if `owner` is a profile id or a local id, else None."""
        self._perf_load()
        if owner in self._perf_by_id:
            return self._perf_by_id[owner]["name"]
        found = self._local_name(owner)
        return found[1] if found else None

    def _local_info(self, key: str, name: str) -> dict:
        local_id = self._local_id(key)
        photos = self._local_photos(local_id)
        v = self._local_version(local_id)
        return {"id": local_id, "gender": (self._perf_local.get(key) or {}).get("gender", "female"),
                "photo": f"/api/performers/{local_id}/photo?v={v}" if photos else None,
                "photos": [f"/api/performers/{local_id}/photo?n={n}&v={v}" for n in range(len(photos))],
                "custom_photos": [{"id": c["id"], "main": bool(c["main"]), "by": c.get("created_by"),
                                   "url": f"/api/performers/{local_id}/custom-photo/{c['id']}"}
                                  for c in self._perf_custom.get(local_id, [])]}

    def set_performer_gender(self, name: str, gender: str) -> dict:
        """The placeholder of a performer without a ThePornDB profile."""
        if gender not in ("female", "male"):
            raise PerformerError("Gender must be female or male")
        self._perf_load()
        key = fold(name)
        lib = self._library_performer_names()
        if key not in lib:
            raise PerformerError("No videos with this performer")
        row = self._perf_names.get(key)
        if row and row.get("status") == "linked" and row.get("performer_id") in self._perf_by_id:
            raise PerformerError("Her gender comes from her ThePornDB profile")
        self.db.upsert_performer_local(self._local_id(key), key, lib[key][0], gender)
        self._perf_local[key] = {"local_id": self._local_id(key), "name_key": key, "name": lib[key][0], "gender": gender}
        return {"ok": True, "gender": gender}

    def _browse_details(self, p: dict) -> dict:
        """ThePornDB's facts made filterable (Performers page, 2.18.0): free text like
        "5'4\"", "110lbs", "34B", "None" becomes cm, kg, cup letters, yes/no. None = not known."""
        e = (p.get("data") or {}).get("extras") or {}
        start, end = e.get("career_start_year"), e.get("career_end_year")
        return {
            "age": self._age(e.get("birthday"), e.get("deathday")),
            "active": (not end) if (start or end) else None,
            "gender_label": _clean(e.get("gender")),
            "nationality": _clean(e.get("nationality")), "ethnicity": _clean(e.get("ethnicity")),
            "hair": _clean(e.get("hair_colour")), "eyes": _clean(e.get("eye_colour")),
            "height": _to_cm(e.get("height")), "weight": _to_kg(e.get("weight")),
            "cup": _cup(e.get("cupsize"), e.get("measurements")),
            "enhanced": _yes_no(e.get("fake_boobs")), "tattoos": _yes_no(e.get("tattoos")),
            "piercings": _yes_no(e.get("piercings")),
        }

    # ---------------------------------------------------------------- library filters (2.19.0)
    def performer_facts_by_name(self) -> dict[str, dict]:
        """folded performer name -> _browse_details() of the ThePornDB profile it's linked to
        (names not on ThePornDB are left out: nothing is known about them)."""
        self._perf_load()
        facts: dict[str, dict] = {}
        by_profile: dict[str, dict] = {}
        for key, row in self._perf_names.items():
            if row.get("status") != "linked" or not row.get("performer_id"):
                continue
            p = self._perf_by_id.get(row["performer_id"])
            if p:
                if p["id"] not in by_profile:
                    by_profile[p["id"]] = self._browse_details(p)
                facts[key] = by_profile[p["id"]]
        return facts

    def performer_filter_options(self) -> dict:
        """Library -> Filters -> Performer details: the cup sizes, hair and eye colours
        that performers in finished videos have, and their age range."""
        facts = self.performer_facts_by_name()
        seen = [facts[k] for k in self._library_performer_names() if k in facts]
        def values(field: str) -> list[str]:
            return sorted({str(f[field]) for f in seen if f.get(field)}, key=str.casefold)
        ages = [f["age"] for f in seen if f.get("age")]
        cups = sorted({f["cup"] for f in seen if f.get("cup")}, key=lambda c: (c[0], len(c), c))
        return {"cups": cups, "hair": values("hair"), "eyes": values("eyes"),
                "age_min": min(ages) if ages else None, "age_max": max(ages) if ages else None,
                "enhanced": any(f.get("enhanced") is not None for f in seen), "known": len(seen)}

    def performers_browse(self) -> list[dict]:
        """Performers page: everyone in the finished videos, one entry per person
        (names linked to the same ThePornDB profile together). The page sorts them,
        profiles first."""
        self._perf_load()
        lib = self._library_performer_names()
        people: dict[str, dict] = {}
        for key, (name, videos) in lib.items():
            row = self._perf_names.get(key)
            p = self._perf_by_id.get(row["performer_id"]) if row and row.get("status") == "linked" and row.get("performer_id") else None
            if p:
                e = people.get(p["id"])
                if e is None:
                    photos = self._all_photos(p)
                    e = people[p["id"]] = {
                        "name": p["name"], "link": name, "linked": True, "videos": 0, "names": 0, "gender": self._gender_of(p),
                        "photo": f"/api/performers/{p['id']}/photo?v={self._photo_version(p)}" if photos else None, "_best": -1,
                        **self._browse_details(p)}
                e["videos"] += videos
                e["names"] += 1
                if videos > e["_best"]:  # link with the name most of her videos use
                    e["link"], e["_best"] = name, videos
            else:
                info = self._local_info(key, name)
                people[info["id"]] = {"name": name, "link": name, "linked": False, "videos": videos, "names": 1,
                                      "gender": info["gender"], "photo": info["photo"],
                                      "status": row["status"] if row else "unchecked",
                                      # only the placeholder gender is known (set on their page; female by default)
                                      "gender_label": info["gender"].title()}
        out = list(people.values())
        for e in out:
            e.pop("_best", None)
        return out

    # ---------------------------------------------------------------- photos added by hand (2.16.0)
    MAX_CUSTOM_PHOTOS = 1  # 2.18.0: one of your own per performer; a new one replaces it

    def performer_photos_dir(self) -> Path:
        """ThePornDB photos (3.0.1): their own folder in the app's data folder.
        Before, they were in the cache folder's "performers" folder, among a folder
        per creator."""
        return self.settings.library_db_file.parent / "performers"

    def relocate_performer_photos(self) -> None:
        """At every start (3.0.1), quick when there's nothing to do:
        - moves ThePornDB photos from <cache folder>/performers to data/performers;
        - points saved photo paths whose file is gone at the same file in today's
          folders -- e.g. after a backup was restored with other folders (the
          paths in the database are those of the computer it came from).
        Never raises: a photo that can't be found or moved stays as it was."""
        new_dir, old_dir = self.performer_photos_dir(), self.settings.cache_dir / "performers"
        custom_dir = self._custom_photo_dir()

        def name_of(value: str) -> str:  # a path of this or another computer (Windows / Linux)
            return PureWindowsPath(value).name if "\\" in value else PurePosixPath(value).name

        moved = repointed = 0
        try:
            for row in self.db.performer_rows():
                photos = json.loads(row.get("photos") or "[]") or ([row["photo_path"]] if row.get("photo_path") else [])
                new = []
                for value in photos:
                    name, cur = name_of(value), Path(value)
                    dest = new_dir / name
                    try:
                        if cur.is_file() and cur.parent != new_dir:
                            new_dir.mkdir(parents=True, exist_ok=True)
                            if not dest.exists():
                                shutil.move(str(cur), str(dest))
                                moved += 1
                            new.append(str(dest) if dest.is_file() else value)
                        elif not cur.is_file() and dest.is_file():
                            new.append(str(dest))
                            repointed += 1
                        elif not cur.is_file() and (old_dir / name).is_file():
                            new_dir.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(old_dir / name), str(dest))
                            new.append(str(dest))
                            moved += 1
                        else:
                            new.append(value)
                    except OSError as exc:
                        logger.warning("Performer photo {} couldn't be moved: {}", value, exc)
                        new.append(value)
                if new != photos:
                    self.db.set_performer_photos(row["id"], new)
            for c in self.db.custom_photo_rows():
                cand = custom_dir / name_of(c["path"])
                if not Path(c["path"]).is_file() and cand.is_file():
                    self.db.set_custom_photo_path(c["id"], str(cand))
                    repointed += 1
            if old_dir.is_dir() and not any(old_dir.iterdir()):
                old_dir.rmdir()
        except Exception:  # noqa: BLE001 -- never stop the start over photos
            logger.exception("Couldn't check the performer photos' folders")
        if moved or repointed:
            logger.info("Performer photos: {} moved to {}, {} found again after a folder change", moved, new_dir, repointed)

    def _custom_photo_dir(self):
        """In the app's data folder (not the cache): they can't be downloaded again,
        so they're in every backup (backup.py)."""
        return self.settings.library_db_file.parent / "performer_photos"

    def custom_photo_file(self, performer_id: str, photo_id: str) -> Optional[str]:
        self._perf_load()
        c = next((c for c in self._perf_custom.get(performer_id, []) if c["id"] == photo_id), None)
        return c["path"] if c else None

    async def add_custom_photo(self, performer_id: str, data: bytes, user: str) -> dict:
        """Her photo from your computer, converted to WebP (backend/images.py)."""
        from .. import images
        self._perf_load()
        owner_name = self._photo_owner_name(performer_id)
        if owner_name is None:
            raise PerformerError("Performer not found")
        try:
            webp = await asyncio.to_thread(images.to_webp, data)
        except ValueError:
            raise PerformerError("That file isn't a picture the app can read (JPEG, PNG, WebP, GIF, BMP, TIFF…)") from None
        photo_id = uuid.uuid4().hex
        folder = self._custom_photo_dir()
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{performer_id}_{photo_id}.webp"
        await asyncio.to_thread(dest.write_bytes, webp)
        # Her own photos are limited (MAX_CUSTOM_PHOTOS): the oldest go once the new one is saved.
        old = list(self._perf_custom.get(performer_id, []))
        keep_main = any(c["main"] for c in old)
        self.db.add_custom_photo(photo_id, performer_id, str(dest), user)
        for c in old[:max(0, len(old) + 1 - self.MAX_CUSTOM_PHOTOS)]:
            self.db.delete_custom_photo(c["id"])
            try:
                os.unlink(c["path"])
            except OSError as exc:
                logger.warning("Couldn't delete the replaced photo {}: {}", c["path"], exc)
        if keep_main:  # the replacement stays "shown first" like the one it replaced
            self.db.set_custom_photo_main(performer_id, photo_id)
        self._perf_load(force=True)
        logger.debug("Photo added to performer {} by {} ({} KB → {} KB WebP)", owner_name, user,
                    len(data) // 1024, len(webp) // 1024)
        return {"id": photo_id, "size": len(webp), "original_size": len(data)}

    def remove_custom_photo(self, performer_id: str, photo_id: str) -> None:
        path = self.custom_photo_file(performer_id, photo_id)
        if not path:
            raise PerformerError("Photo not found")
        self.db.delete_custom_photo(photo_id)
        try:
            os.unlink(path)
        except OSError as exc:
            logger.warning("Couldn't delete the removed photo {}: {}", path, exc)
        self._perf_load(force=True)

    def set_main_custom_photo(self, performer_id: str, photo_id: Optional[str]) -> None:
        if photo_id and not self.custom_photo_file(performer_id, photo_id):
            raise PerformerError("Photo not found")
        self.db.set_custom_photo_main(performer_id, photo_id)
        self._perf_load(force=True)

    def performers_overview(self) -> dict:
        """Maintenance -> Performers: counts, the review list, what's running."""
        self._perf_load()
        lib = self._library_performer_names()
        counts = {"linked": 0, "review": 0, "none": 0, "skipped": 0, "unchecked": 0}
        review = []
        for key, (name, videos) in lib.items():
            row = self._perf_names.get(key)
            status = row["status"] if row else "unchecked"
            counts[status] = counts.get(status, 0) + 1
            if status == "review":
                review.append({"name": name, "videos": videos, "candidates": row.get("candidates") or []})
        review.sort(key=lambda r: -r["videos"])
        return {"configured": tpdb.configured(), "counts": counts, "total": len(lib), "review": review,
                "photos_to_convert": self.performer_photos_to_convert(),
                "profiles": len(self._perf_by_id), "busy": self._perf_busy, "error": self._perf_last_error,
                "job": self._performer_job_info()}

    def _performer_job_info(self) -> Optional[dict]:
        job = self.performer_job()
        if not job:
            return None
        return {"id": job["id"], "enabled": bool(job.get("enabled")), "next_run_at": job.get("next_run_at"),
                "last_run_at": job.get("last_run_at"), "last_run_status": job.get("last_run_status"),
                "running": job["id"] in self._scheduler_active_jobs}
