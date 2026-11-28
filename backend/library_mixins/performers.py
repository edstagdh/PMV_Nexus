"""Performer profiles from ThePornDB (2.7.0).

Every performer name in the library is matched once to a ThePornDB
profile, by its id (uuid), which is kept from then on -- so the right
person's data is always fetched, whatever the name looks like later:

  - a search on ThePornDB; a single exact hit on the name or one of the
    aliases links automatically; several possible people, or only near
    misses, wait in the review list (Maintenance -> Performers) for a pick;
  - when a profile is linked, other names in the library that ThePornDB
    lists as their aliases point to the same profile ("Luna P" -> Luna
    Prism). With Server Settings -> Performers -> merge aliases on, the app
    then shows them as one name everywhere (see spellings.py); a wrong
    merge is split on the profile page, and that name is never merged into
    it again;
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

from ..i18n import L
from ..resources import JOB, resources
from .. import tpdb
from .. import stashbox
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


# 3.9.0: what can be typed in for a performer not on ThePornDB -- the same names ThePornDB uses,
# so the profile page, the Performers page filters and the library filters treat both alike.
LOCAL_TEXT = ("birthplace", "nationality", "ethnicity", "hair_colour", "eye_colour", "measurements", "cupsize",
              "tattoos", "piercings", "disambiguation")
MAX_LINKS = 20


def clean_local_details(raw: dict) -> dict:
    """The details form, checked (PerformerError for anything that can't be kept)."""
    if not isinstance(raw, dict):
        raise PerformerError(L("Details must be a form"))
    out: dict = {}
    for k in LOCAL_TEXT:
        v = str(raw.get(k) or "").strip()
        if len(v) > 100:
            raise PerformerError(L("{field} is too long (100 characters at most)", field=k))
        if v:
            out[k] = v
    for k, lo, hi, unit in (("height", 100, 230, "cm"), ("weight", 30, 200, "kg")):
        v = raw.get(k)
        if v not in (None, ""):
            try:
                n = int(float(v))
            except (TypeError, ValueError):
                raise PerformerError(L("{field} is a number", field=k)) from None
            if not lo <= n <= hi:
                raise PerformerError(L("{field} is {lo} to {hi} {unit}", field=k, lo=lo, hi=hi, unit=unit))
            out[k] = f"{n} {unit}"
    for k in ("birthday", "deathday"):
        v = str(raw.get(k) or "").strip()
        if v:
            try:
                date.fromisoformat(v)
            except ValueError:
                raise PerformerError(L("{field} is a date like 1995-04-23", field=k)) from None
            out[k] = v
    for k in ("career_start_year", "career_end_year"):
        v = raw.get(k)
        if v not in (None, ""):
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise PerformerError(L("{field} is a year", field=k)) from None
            if not 1950 <= n <= 2100:
                raise PerformerError(L("{field} is a year", field=k))
            out[k] = n
    if raw.get("fake_boobs") not in (None, ""):
        out["fake_boobs"] = bool(_yes_no(raw.get("fake_boobs")))
    aliases = [str(a).strip()[:80] for a in (raw.get("aliases") or []) if str(a).strip()]
    if aliases:
        out["aliases"] = list(dict.fromkeys(aliases))[:20]
    bio = str(raw.get("bio") or "").strip()
    if len(bio) > 5000:
        raise PerformerError(L("The bio is too long (5,000 characters at most)"))
    if bio:
        out["bio"] = bio
    links = []
    for link in raw.get("links") or []:
        if not isinstance(link, dict):
            continue
        url, label = str(link.get("url") or "").strip(), str(link.get("label") or "").strip()[:40]
        if not url:
            continue
        if not re.match(r"^https?://[^\s/$.?#][^\s]*$", url, re.I) or len(url) > 500:
            raise PerformerError(L("“{url}” isn't a web address (https://…)", url=url[:80]))
        links.append({"label": label or re.sub(r"^https?://(www\.)?", "", url, flags=re.I).split("/")[0], "url": url})
    if len(links) > MAX_LINKS:
        raise PerformerError(L("At most {n} links", n=MAX_LINKS))
    if links:
        out["links"] = links
    return out


def _editable(details: dict) -> dict:
    """Saved details back in the form's shape ("170 cm" -> 170), so they can be checked again."""
    out = dict(details)
    for k in ("height", "weight"):
        if isinstance(out.get(k), str):
            m = re.match(r"\s*(\d+)", out[k])
            out[k] = int(m.group(1)) if m else None
    return out


def _safe_photo_name(name) -> bool:
    """A photo name a FansDB / StashDB link saved (3.12.0): never a path, never anything else."""
    return isinstance(name, str) and re.fullmatch(r"sb-(fansdb|stashdb)-[0-9A-Za-z-]{1,64}-\d{1,2}\.webp", name) is not None


def _photo_hash(data: bytes) -> Optional[int]:
    """A 64-bit difference hash of a picture: near-identical pictures differ in a few bits."""
    import io
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as im:
            g = im.convert("L").resize((9, 8))
            px = list(g.getdata())
    except Exception:  # noqa: BLE001
        return None
    bits = 0
    for r in range(8):
        for c in range(8):
            bits = (bits << 1) | (px[r * 9 + c] > px[r * 9 + c + 1])
    return bits


def _photo_hash_file(path: str) -> Optional[int]:
    try:
        return _photo_hash(Path(path).read_bytes())
    except OSError:
        return None


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


# 3.12.0: the details compared between ThePornDB, FansDB and StashDB on the profile page
COMPARED = (("name", L("Name")), ("gender", L("Gender")), ("birthday", L("Birthday")), ("deathday", L("Died")),
            ("nationality", L("Nationality")), ("ethnicity", L("Ethnicity")), ("hair_colour", L("Hair")),
            ("eye_colour", L("Eyes")), ("height", L("Height")), ("measurements", L("Measurements")),
            ("cupsize", L("Cup size")), ("fake_boobs", L("Enhanced")), ("tattoos", L("Tattoos")),
            ("piercings", L("Piercings")), ("career_start_year", L("Career start")), ("career_end_year", L("Career end")))


def _compare_value(field: str, value):
    """A value as it's compared (None: not given). Free text is compared without case / spacing;
    tattoos and piercings by whether there are any (the sites word them differently)."""
    if value is None or value == "" or value == []:
        return None
    if field == "height":
        return _to_cm(value)
    if field in ("birthday", "deathday"):
        return str(value)[:10]
    if field in ("career_start_year", "career_end_year"):
        m = re.match(r"^\d{4}", str(value))
        return int(m.group(0)) if m else None
    if field in ("fake_boobs", "tattoos", "piercings"):
        return _yes_no(value)
    if field in ("measurements", "cupsize"):
        return re.sub(r"\s+", "", str(value)).upper() or None
    return " ".join(str(value).replace("-", " ").split()).casefold() or None


def _shown_value(field: str, value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return L("Yes") if value else L("No")
    if field == "height" and isinstance(value, int):
        return f"{value} cm"
    return str(value)


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
    _perf_sb: dict = {}      # (name key, fansdb | stashdb) -> performer_stashbox row (3.9.0)
    _perf_sb_photos: dict = {}  # profile id -> FansDB / StashDB photos shown after ThePornDB's (3.12.0)
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
        sb: dict = {}
        for r in self.db.performer_stashbox_rows():
            try:
                r["data"] = json.loads(r["data"] or "{}")
            except ValueError:
                r["data"] = {}
            sb[(r["name_key"], r["box"])] = r
        self._perf_sb = sb
        # 3.12.0: photos of FansDB / StashDB for names linked to a ThePornDB profile (file names in
        # the performer-photos folder, kept in the link's row), FansDB first
        folder = self.performer_photos_dir()
        sb_photos: dict[str, list[str]] = {}
        for (key, box), r in sorted(sb.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            nrow = names.get(key)
            pid = nrow.get("performer_id") if nrow and nrow.get("status") == "linked" else None
            if pid not in by_id:
                continue
            for n in (r["data"] or {}).get("photos") or []:
                f = folder / n
                if _safe_photo_name(n) and str(f) not in sb_photos.get(pid, []) and f.is_file():
                    sb_photos.setdefault(pid, []).append(str(f))
        self._perf_sb_photos = sb_photos
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
        """spellings.py hook: with merge_aliases on, a linked name shows as the main name."""
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
            # Photos added while the name wasn't on ThePornDB go with the profile now (2.17.0).
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
            raise PerformerError(L('No ThePornDB API key -- add it in Server Settings -> Credentials'))
        if self._perf_running:
            raise JobBlockedError(L('Performer matching is already running'))
        self._perf_running = True
        self._perf_last_error = None
        self._perf_step = {"stage": L('Starting')}
        try:
            async with activity.track(JOB_KEY, L('Matching performers'), progress=lambda: dict(self._perf_step),
                                      stop=True) as act:
                self._perf_load()
                result = {"matched": await self.match_new_performers(), "refreshed": await self.refresh_stale_performers(),
                          "photos": await self._backfill_photos() + await self.fill_stashbox_photos()}
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
            self._perf_step = {"stage": L('Looking up new names on ThePornDB'), "done": n - 1, "total": len(todo), "detail": name}
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
            self._perf_step = {"stage": L('Refreshing profiles'), "done": n - 1, "total": len(stale), "detail": p["name"]}
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
        """Other library names that are this performer's aliases point to the profile too
        (unless one was split off from it before, or was linked by hand)."""
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
            self._perf_step = {"stage": L('Fetching photos'), "detail": p["name"]}
            photos = await self._fetch_photos(p["id"], p.get("data") or {}, {**p, "data": {}})
            if photos:
                p["photos"], p["photo_path"] = photos, photos[0]
                self.db.set_performer_photos(p["id"], photos)
                done += 1
        return done

    @staticmethod
    def _photo_urls(data: dict) -> list[str]:
        """The main image, then ThePornDB's posters (in their order), at most MAX_PHOTOS."""
        urls = [data.get("image") or data.get("thumbnail") or data.get("face")]
        posters = [p for p in (data.get("posters") or []) if isinstance(p, dict) and p.get("url")]
        urls += [p["url"] for p in sorted(posters, key=lambda p: p.get("order") or 0)]
        out: list[str] = []
        for u in urls:
            if u and u not in out:
                out.append(u)
        return out[:MAX_PHOTOS]

    async def _fetch_photos(self, pid: str, data: dict, existing: Optional[dict]) -> list[str]:
        """Downloads the photos to cache/performers/<id>[_n].<ext> -- again only when
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
        is never merged into that profile again, and is looked up on its own."""
        self._perf_load()
        key = fold(name)
        row = self._perf_names.get(key)
        if not row or not row.get("performer_id"):
            raise PerformerError(L("That name isn't linked to anyone"))
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
            raise PerformerError(L('Unknown performer'), 404)
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

    def _key_for_shown_name(self, key: str, lib: dict) -> Optional[str]:
        """A name shown in the app that isn't in the library: a profile's own name (with merge
        aliases on, "Vana Rose" in the videos shows as ThePornDB's "Vanna Rose"; similar performers
        are listed by it too). The library name linked to that profile, the one most videos use."""
        best = None
        for k, r in self._perf_names.items():
            if r.get("status") != "linked" or k not in lib:
                continue
            p = self._perf_by_id.get(r.get("performer_id"))
            if p and fold(p["name"]) == key and (best is None or lib[k][1] > lib[best][1]):
                best = k
        return best

    def performer_profile(self, name: str) -> dict:
        """Everything the profile page shows for a performer name."""
        self._perf_load()
        key = fold(name)
        lib = self._library_performer_names()
        if key not in lib and key not in self._perf_names:
            key = self._key_for_shown_name(key, lib) or key
        row = self._perf_names.get(key)
        p = self._perf_by_id.get(row["performer_id"]) if row and row.get("performer_id") else None
        out: dict = {"name": name, "status": row["status"] if row else ("unchecked" if key in lib else "unknown"),
                     "configured": tpdb.configured(), "merge_aliases": self.settings.performers.merge_aliases,
                     "candidates": (row or {}).get("candidates") or [], "performer": None, "names": [], "similar": []}
        if not p:
            out["names"] = [{"name": lib[key][0] if key in lib else name, "videos": lib.get(key, (name, 0))[1],
                             "source": None, "main": True}]
            if key in lib:  # their own photos and placeholder (2.17.0)
                out["local"] = self._local_info(key, lib[key][0])
            out["stashbox"] = self._stashbox_links([key])
            out["stashbox_sites"] = stashbox.boxes()
            out["differences"] = self._site_differences(None, [key])
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
        # 3.9.0: FansDB / StashDB linked by hand -- their page, their links, and what ThePornDB leaves empty
        keys = [r["name_key"] for r in names] or [key]
        out["stashbox"] = self._stashbox_links(keys)
        out["stashbox_sites"] = stashbox.boxes()
        known = set(out["performer"]["links"].values())
        more = []
        for r in self._perf_sb.values():
            if r["name_key"] not in keys:
                continue
            sbd = (r["data"] or {}).get("details") or {}
            for k, v in sbd.items():
                if k in out["performer"]["extras"] and out["performer"]["extras"][k] in (None, ""):
                    out["performer"]["extras"][k] = f"{v} cm" if k == "height" else v
            for link in sbd.get("links") or []:
                if link["url"] not in known:
                    known.add(link["url"])
                    more.append(link)
        out["performer"]["more_links"] = more[:MAX_LINKS]
        out["differences"] = self._site_differences(p, keys)
        for s in p.get("similar") or []:
            other = self._perf_by_tpdb.get(s.get("tpdb_id"))
            if other and other["id"] != p["id"]:
                in_lib = any(r.get("performer_id") == other["id"] for r in self._perf_names.values())
                if in_lib:
                    out["similar"].append({"name": other["name"], "gender": self._gender_of(other),
                                           "photo": f"/api/performers/{other['id']}/photo?v={self._photo_version(other)}"
                                           if self._all_photos(other) else None})
        return out

    def _site_differences(self, p: Optional[dict], keys: list[str]) -> Optional[dict]:
        """3.12.0: the details each linked site gives (ThePornDB, FansDB, StashDB) side by side,
        and which differ. None when fewer than two sites are linked."""
        sources = []
        if p:
            sources.append(("tpdb", "ThePornDB", {"name": p["name"], **((p.get("data") or {}).get("extras") or {})}))
        for link in self._stashbox_links(keys):
            r = self._perf_sb.get((link["name_key"], link["box"])) or {}
            data = r.get("data") or {}
            sources.append((link["box"], link["label"], {"name": r.get("name"), "gender": data.get("gender"),
                                                           **(data.get("details") or {})}))
        if len(sources) < 2:
            return None
        fields = []
        for field, label in COMPARED:
            values = {key: _shown_value(field, d.get(field)) for key, _label, d in sources}
            if not any(v is not None for v in values.values()):
                continue
            compared = [_compare_value(field, d.get(field)) for _key, _label, d in sources]
            given = [c for c in compared if c is not None]
            if field == "height":
                differs = bool(given) and max(given) - min(given) > 2
            elif field == "nationality" and any(len(str(d.get(field) or "")) == 2 for _k, _l, d in sources):
                differs = None    # a country code next to a name: not compared
            else:
                differs = len(set(given)) > 1
            fields.append({"field": field, "label": label, "values": values, "differs": differs})
        return {"sites": [{"key": key, "label": label} for key, label, _d in sources], "fields": fields,
                "differing": sum(1 for f in fields if f["differs"])}

    # ---------------------------------------------------------------- photos as WebP (2.15.0)
    def _photos_of(self, p: dict) -> list[str]:
        """The ThePornDB photos (saved in data/performers; before 3.2.0 cache/performers)."""
        return list(p.get("photos") or ([p["photo_path"]] if p.get("photo_path") else []))

    def _site_photos(self, p: dict) -> list[str]:
        """3.12.0: the ThePornDB photos, then those of FansDB / StashDB while there's room (MAX_PHOTOS)."""
        own = self._photos_of(p)
        return own + self._perf_sb_photos.get(p["id"], [])[:max(0, MAX_PHOTOS - len(own))]

    def _all_photos(self, p: dict) -> list[str]:
        """What the profile shows, in order (2.16.0): a photo added by hand marked
        "show first", the site photos (_site_photos), then the other photos added by hand."""
        custom = self._perf_custom.get(p["id"], [])
        return ([c["path"] for c in custom if c["main"]] + self._site_photos(p)
                + [c["path"] for c in custom if not c["main"]])

    def _photo_version(self, p: dict) -> int:
        """Changes whenever the list changes, so browsers don't show a cached photo for a moved index."""
        custom = self._perf_custom.get(p["id"], [])
        mark = (sum(hash((c["id"], c["main"])) for c in custom) + sum(hash(f) for f in self._perf_sb_photos.get(p["id"], []))) & 0xFFFF
        return int(p.get("fetched_at") or 0) * 100000 + len(custom) * 65536 + mark

    def performer_photo_files(self) -> list[str]:
        """Every saved site photo of a performer (Maintenance -> Untracked files keeps them):
        ThePornDB's, and (3.12.0) those of FansDB / StashDB links -- shown or not."""
        self._perf_load()
        folder = self.performer_photos_dir()
        sb = [str(folder / n) for r in self._perf_sb.values() for n in (r["data"] or {}).get("photos") or [] if _safe_photo_name(n)]
        return [f for p in self._perf_by_id.values() for f in self._photos_of(p)] + sb

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
        workers = resources.cpu_cores()   # 3.12.0: the background CPU share (Server Settings -> Background work)
        loop = asyncio.get_running_loop()

        def convert(path: str):
            try:
                return images.convert_file(path)
            except (OSError, ValueError) as exc:
                return exc

        async with activity.track("performer_photos", L('Converting performer photos to WebP'), stop=True,
                                  progress=lambda: {"stage": L('Converting'), "done": st["done"], "total": total,
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
                    async with resources.use({"cpu"}, L('Converting performer photos to WebP'), JOB):   # 3.12.0: takes turns
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
    # placeholder gender, once set on its page, is kept in performer_local; when the
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
        """Placeholder silhouette for a profile without photos: ThePornDB's female / male,
        anything else (not given, trans, non-binary...) the neutral one ("unknown", 3.7.0)."""
        g = str(((p.get("data") or {}).get("extras") or {}).get("gender") or "").strip().lower()
        return g if g in ("female", "male") else "unknown"

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
        details = self._local_details(key)
        return {"id": local_id, "gender": (self._perf_local.get(key) or {}).get("gender") or "unknown",
                # 3.9.0: typed in by hand (clean_local_details); "extras" like a ThePornDB profile's
                "details": details, "extras": {k: v for k, v in details.items() if k not in ("aliases", "bio", "links")},
                "aliases": details.get("aliases") or [], "bio": details.get("bio"), "links": details.get("links") or [],
                "age": self._age(details.get("birthday"), details.get("deathday")),
                "photo": f"/api/performers/{local_id}/photo?v={v}" if photos else None,
                "photos": [f"/api/performers/{local_id}/photo?n={n}&v={v}" for n in range(len(photos))],
                "custom_photos": [{"id": c["id"], "main": bool(c["main"]), "by": c.get("created_by"),
                                   "url": f"/api/performers/{local_id}/custom-photo/{c['id']}"}
                                  for c in self._perf_custom.get(local_id, [])]}

    def _local_details(self, key: str) -> dict:
        row = self._perf_local.get(key) or {}
        try:
            d = json.loads(row.get("details") or "{}")
        except (TypeError, ValueError):
            d = {}
        return d if isinstance(d, dict) else {}

    def set_local_details(self, name: str, details: dict) -> dict:
        """3.9.0: details typed in for a performer not on ThePornDB (or FansDB / StashDB)."""
        self._perf_load()
        key = fold(name)
        lib = self._library_performer_names()
        if key not in lib:
            raise PerformerError(L('No videos with this performer'))
        row = self._perf_names.get(key)
        if row and row.get("status") == "linked" and row.get("performer_id") in self._perf_by_id:
            raise PerformerError(L("This performer has a ThePornDB profile: its details come from there"))
        clean = clean_local_details(details)
        self.db.set_performer_local_details(self._local_id(key), key, lib[key][0], clean)
        self._perf_load(force=True)
        return {"ok": True, "details": clean}

    # ---------------------------------------------------------------- FansDB / StashDB, by hand (3.9.0)
    def _stashbox_links(self, keys: list[str]) -> list[dict]:
        out = []
        for (key, box), r in sorted(self._perf_sb.items(), key=lambda kv: kv[0][1]):
            if key in keys and box in stashbox.BOXES and not any(o["box"] == box for o in out):
                try:
                    url = stashbox.site_url(box, r["sb_id"])
                except stashbox.StashBoxError:
                    url = None
                out.append({"box": box, "label": stashbox.BOXES[box]["label"], "id": r["sb_id"], "name": r["name"],
                            "name_key": key, "url": url, "fetched_at": r["fetched_at"], "by": r.get("linked_by")})
        return out

    async def stashbox_search(self, box: str, q: str) -> list[dict]:
        try:
            return await stashbox.search(box, q)
        except stashbox.StashBoxError as exc:
            raise PerformerError(str(exc), 502) from None

    async def stashbox_link(self, name: str, box: str, sb_id: str, user: str, photos: bool = True) -> dict:
        """Links a name to a FansDB / StashDB profile. Not on ThePornDB: the profile's details
        fill what wasn't typed in (what was typed in is kept), its links are added, and up to
        STASHBOX_PHOTOS of its photos are saved as the performer's own. On ThePornDB: only its
        page, links and what the ThePornDB profile leaves empty are shown."""
        if box not in stashbox.BOXES:
            raise PerformerError(L("Unknown site"))
        self._perf_load()
        key = fold(name)
        lib = self._library_performer_names()
        if key not in lib:
            raise PerformerError(L('No videos with this performer'))
        try:
            p = await stashbox.performer(box, sb_id)
        except stashbox.StashBoxError as exc:
            raise PerformerError(str(exc), 502) from None
        label = stashbox.BOXES[box]["label"]
        sbd = stashbox.details(p)
        before = self._perf_sb.get((key, box)) or {}
        kept = [n for n in (before.get("data") or {}).get("photos") or []
                if before.get("sb_id") == p["id"] and _safe_photo_name(n) and (self.performer_photos_dir() / n).is_file()]
        self.db.set_performer_stashbox(key, box, p["id"], p.get("name") or name,
                                       {"details": sbd, "images": stashbox.images(p)[:10], "gender": stashbox.gender(p),
                                        **({"photos": kept} if kept else {})}, user)
        row = self._perf_names.get(key)
        on_tpdb = bool(row and row.get("status") == "linked" and row.get("performer_id") in self._perf_by_id)
        added_photos = filled = 0
        if not on_tpdb:
            have = self._local_details(key)
            merged = _editable(have)
            for k, v in sbd.items():
                if k == "links":
                    urls = {link["url"] for link in merged.get("links") or []}
                    merged["links"] = (merged.get("links") or []) + [link for link in v if link["url"] not in urls]
                    merged["links"] = merged["links"][:MAX_LINKS]
                elif k == "aliases":
                    merged["aliases"] = list(dict.fromkeys((merged.get("aliases") or []) + v))[:20]
                elif merged.get(k) in (None, ""):
                    merged[k] = v
                    filled += 1
            try:
                clean = clean_local_details(merged)
            except PerformerError as exc:  # a value from the site the form wouldn't take: keep what was there
                logger.warning("{} details for {} not all kept: {}", label, name, exc)
                clean = have
            self.db.set_performer_local_details(self._local_id(key), key, lib[key][0], clean)
            g = stashbox.gender(p)
            if g and (self._perf_local.get(key) or {}).get("gender") in (None, "unknown"):
                self.db.upsert_performer_local(self._local_id(key), key, lib[key][0], g)
            self._perf_load(force=True)
            if photos:
                added_photos = await self._stashbox_photos(self._local_id(key), stashbox.images(p), label)
        else:
            self._perf_load(force=True)
            if photos and not kept:   # 3.12.0: shown after ThePornDB's while there's room
                added_photos = await self._stashbox_site_photos(key, box, row["performer_id"], stashbox.images(p), label)
            old = [n for n in (before.get("data") or {}).get("photos") or [] if n not in kept]
            self._remove_sb_photo_files(old)       # an earlier link of this site to someone else
        self._perf_load(force=True)
        logger.info("Performer {} linked to {} {} by {} ({} details filled, {} photos)", name, label, p["id"], user, filled, added_photos)
        return {"ok": True, "box": box, "id": p["id"], "name": p.get("name"), "filled": filled, "photos": added_photos,
                "on_tpdb": on_tpdb}

    STASHBOX_PHOTOS = 5

    async def _stashbox_photos(self, owner: str, urls: list[str], label: str) -> int:
        from .. import safe_fetch
        room = self.MAX_LOCAL_PHOTOS - len(self._perf_custom.get(owner, []))
        added = 0
        for url in urls[:min(self.STASHBOX_PHOTOS, max(0, room))]:
            try:
                data = await safe_fetch.fetch_image(url)
                await self.add_custom_photo(owner, data, label)
                added += 1
            except (safe_fetch.FetchRefused, PerformerError) as exc:
                logger.warning("{} photo not saved: {}", label, exc)
        return added

    async def _stashbox_site_photos(self, key: str, box: str, profile_id: str, urls: list[str], label: str) -> int:
        """3.12.0, a name on ThePornDB: the site's photos are saved in the performer-photos folder
        and shown after ThePornDB's, as many as there's room for (MAX_PHOTOS in all); a photo that
        looks like one already shown is skipped. Recorded in the link's row (data["photos"])."""
        from .. import images, safe_fetch
        p = self._perf_by_id.get(profile_id)
        row = self._perf_sb.get((key, box))
        if not p or not row:
            return 0
        room = MAX_PHOTOS - len(self._site_photos(p))
        if room <= 0:
            return 0
        folder = self.performer_photos_dir()
        folder.mkdir(parents=True, exist_ok=True)
        shown = self._site_photos(p)
        seen = [h for h in await asyncio.to_thread(lambda: [_photo_hash_file(f) for f in shown]) if h is not None]
        sid = re.sub(r"[^0-9A-Za-z-]", "", str(row["sb_id"]))[:64] or "x"
        names: list[str] = []
        for n, url in enumerate(urls[:10]):
            if len(names) >= room:
                break
            try:
                webp = await asyncio.to_thread(images.to_webp, await safe_fetch.fetch_image(url))
            except (safe_fetch.FetchRefused, ValueError) as exc:
                logger.warning("{} photo of {} not saved: {}", label, p["name"], exc)
                continue
            h = await asyncio.to_thread(_photo_hash, webp)
            if h is not None and any(_hamming(h, o) <= 6 for o in seen):
                continue     # the same picture as one shown already
            name = f"sb-{box}-{sid}-{n}.webp"
            tmp = folder / (name + ".part")
            await asyncio.to_thread(tmp.write_bytes, webp)
            os.replace(tmp, folder / name)
            names.append(name)
            if h is not None:
                seen.append(h)
        if names:
            self.db.set_performer_stashbox(key, box, row["sb_id"], row["name"], {**(row["data"] or {}), "photos": names},
                                           row.get("linked_by"))
        return len(names)

    def _remove_sb_photo_files(self, names: list[str]) -> None:
        """Only photos a FansDB / StashDB link saved (their recorded names), only in the photos folder."""
        folder = self.performer_photos_dir()
        for n in names:
            if _safe_photo_name(n):
                try:
                    (folder / n).unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("Couldn't delete the photo {}: {}", folder / n, exc)

    async def fill_stashbox_photos(self) -> int:
        """3.12.0, Scheduler job "Match & refresh performers": links made before 3.12.0 (or while
        there was no room) of names on ThePornDB get the site's photos saved at linking. -> performers."""
        self._perf_load()
        done = 0
        for (key, box), r in list(self._perf_sb.items()):
            nrow = self._perf_names.get(key)
            pid = nrow.get("performer_id") if nrow and nrow.get("status") == "linked" else None
            data = r["data"] or {}
            if pid not in self._perf_by_id or "photos" in data or not data.get("images") or box not in stashbox.BOXES:
                continue
            if self._shutting_down:
                break
            self._perf_step = {"stage": L("Fetching photos"), "detail": self._perf_by_id[pid]["name"]}
            n = await self._stashbox_site_photos(key, box, pid, data["images"], stashbox.BOXES[box]["label"])
            if not n:   # nothing new (no room, or all shown already): not tried again
                self.db.set_performer_stashbox(key, box, r["sb_id"], r["name"], {**data, "photos": []}, r.get("linked_by"))
            done += bool(n)
            self._perf_load(force=True)
        return done

    def stashbox_unlink(self, name: str, box: str) -> dict:
        """Forgets the link; details and photos copied as the performer's own stay (they can be
        edited / removed). 3.12.0: the site's photos shown with a ThePornDB profile go with it."""
        self._perf_load()
        key = fold(name)
        keys = [key]
        row = self._perf_names.get(key)
        if row and row.get("performer_id"):
            keys += [r["name_key"] for r in self._perf_names.values() if r.get("performer_id") == row["performer_id"]]
        photos = [n for k in dict.fromkeys(keys) for n in ((self._perf_sb.get((k, box)) or {}).get("data") or {}).get("photos") or []]
        gone = any([self.db.delete_performer_stashbox(k, box) for k in dict.fromkeys(keys)])
        if not gone:
            raise PerformerError(L("Not linked to that site"), 404)
        still = {n for (k, b), r in self._perf_sb.items() if (k, b) not in {(kk, box) for kk in keys}
                 for n in (r["data"] or {}).get("photos") or []}
        self._remove_sb_photo_files([n for n in photos if n not in still])
        self._perf_load(force=True)
        return {"ok": True}

    def set_performer_gender(self, name: str, gender: str) -> dict:
        """The placeholder of a performer without a ThePornDB profile."""
        if gender not in ("female", "male", "trans"):  # trans (3.5.0): badge colour; neutral silhouette
            raise PerformerError(L('Gender must be female, male or trans'))
        self._perf_load()
        key = fold(name)
        lib = self._library_performer_names()
        if key not in lib:
            raise PerformerError(L('No videos with this performer'))
        row = self._perf_names.get(key)
        if row and row.get("status") == "linked" and row.get("performer_id") in self._perf_by_id:
            raise PerformerError(L('The gender comes from the ThePornDB profile'))
        self.db.upsert_performer_local(self._local_id(key), key, lib[key][0], gender)
        self._perf_local[key] = {**(self._perf_local.get(key) or {}), "local_id": self._local_id(key), "name_key": key,
                                 "name": lib[key][0], "gender": gender}
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
    @staticmethod
    def badge_gender(raw: str) -> Optional[str]:
        """ThePornDB's gender -> the badge colour (3.5.0): female / male / trans (trans women and
        men, non-binary, intersex); None when it's not given."""
        g = " ".join(str(raw or "").replace("-", " ").lower().split())
        if not g:
            return None
        if "trans" in g or "non binary" in g or "nonbinary" in g or "intersex" in g:
            return "trans"
        return {"female": "female", "male": "male"}.get(g)

    def performer_genders(self) -> dict[str, str]:
        """folded name -> "female" / "male" / "trans" (3.5.0, performer badge colours): from the
        ThePornDB profile it's linked to, else the silhouette set on its page; unknown ones left out."""
        self._perf_load()
        out: dict[str, str] = {}
        for key, row in self._perf_local.items():
            if row.get("gender") in ("female", "male", "trans"):
                out[key] = row["gender"]
        for key, row in self._perf_names.items():
            if row.get("status") != "linked" or not row.get("performer_id"):
                continue
            p = self._perf_by_id.get(row["performer_id"])
            g = self.badge_gender(((p or {}).get("data") or {}).get("extras", {}).get("gender")) if p else None
            if g:
                out[key] = g
                out[fold(p["name"])] = g  # the badge may show the profile's own spelling (merge aliases)
            else:
                out.pop(key, None)  # linked: the profile decides, even when it doesn't say
        return out

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
        for key in self._perf_local:   # 3.9.0: details typed in by hand count too
            if key not in facts and self._local_details(key):
                facts[key] = self._browse_details({"data": {"extras": self._local_details(key)}})
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
                if videos > e["_best"]:  # link with the name most of the videos use
                    e["link"], e["_best"] = name, videos
            else:
                info = self._local_info(key, name)
                people[info["id"]] = {"name": name, "link": name, "linked": False, "videos": videos, "names": 1,
                                      "gender": info["gender"], "photo": info["photo"],
                                      "status": row["status"] if row else "unchecked",
                                      # 3.9.0: what was typed in on their page (age, hair, cup...), if anything
                                      **self._browse_details({"data": {"extras": info["details"]}}),
                                      # the gender set on their page (else not known)
                                      "gender_label": None if info["gender"] == "unknown" else info["gender"].title()}
        out = list(people.values())
        for e in out:
            e.pop("_best", None)
        return out

    def performer_wall(self, user_id: Optional[str] = None) -> dict:
        """3.9.0 "The wall" (/wall): who appears with whom. Only performers someone has
        matched or filled in -- linked to ThePornDB, or with details, photos, a gender or a
        FansDB / StashDB link of their own -- and the finished videos they're in. Names linked
        to the same profile are one person. The page works out the pairs itself. Per video also (3.9.1)
        what "Pick for me" sorts by: rating, site views / likes, when it was added, and this
        user's plays and whether they started it."""
        from .core import _acquired
        plays = self.db.user_play_counts(user_id) if user_id else {}
        started = self._wp_for(user_id) if user_id else {}
        self._perf_load()
        lib = self._library_performer_names()
        sb_keys = {k for k, _box in self._perf_sb}
        who: dict[str, str] = {}   # name key -> person id
        people: dict[str, dict] = {}
        for key, (name, videos) in lib.items():
            row = self._perf_names.get(key)
            p = self._perf_by_id.get(row["performer_id"]) if row and row.get("status") == "linked" and row.get("performer_id") else None
            if p:
                pid = p["id"]
                if pid not in people:
                    people[pid] = {"id": pid, "name": p["name"], "link": name, "gender": self._gender_of(p), "videos": 0,
                                   "photo": f"/api/performers/{pid}/photo?v={self._photo_version(p)}" if self._all_photos(p) else None,
                                   "_best": -1}
                if videos > people[pid]["_best"]:
                    people[pid]["link"], people[pid]["_best"] = name, videos
            else:
                local_id = self._local_id(key)
                local = self._perf_local.get(key) or {}
                edited = (self._local_details(key) or self._perf_custom.get(local_id) or key in sb_keys
                          or (local.get("gender") or "unknown") != "unknown")
                if not edited:
                    continue
                pid = local_id
                photos = self._local_photos(local_id)
                people[pid] = {"id": pid, "name": name, "link": name, "gender": local.get("gender") or "unknown", "videos": 0,
                               "photo": f"/api/performers/{pid}/photo?v={self._local_version(local_id)}" if photos else None}
            who[key] = pid
        videos = []
        for item in self._items.values():
            if item.status != "done":
                continue
            cast = list(dict.fromkeys(who[k] for k in (fold(n) for n in item.performers or []) if k in who))
            if not cast:
                continue
            for pid in cast:
                people[pid]["videos"] += 1
            stats = item.site_stats or {}
            try:
                rating = float(str(item.ratings or "").replace("%", "")) if item.ratings else None
            except ValueError:
                rating = None
            videos.append({"id": item.id, "title": item.title or "", "creator": item.creator or "",
                           "length": item.tech_duration or "", "cast": cast,
                           "thumb": (f"/api/library/{item.id}/thumbnail?v={item.thumbnail_updated_at or ''}"
                                     if item.thumbnail_path else None),
                           "preview": (f"/api/library/{item.id}/preview?v={item.preview_updated_at or ''}"
                                       if item.preview_path else None),
                           "rating": rating, "views": stats.get("views"), "likes": stats.get("likes"),
                           "added": _acquired(item), "plays": plays.get(item.id, 0), "started": item.id in started})
        out = [p for p in people.values() if p["videos"]]
        for p in out:
            p.pop("_best", None)
        return {"performers": out, "videos": videos}

    # ---------------------------------------------------------------- photos added by hand (2.16.0)
    MAX_CUSTOM_PHOTOS = 1  # 2.18.0: one of your own per ThePornDB profile; a new one replaces it
    MAX_LOCAL_PHOTOS = 30  # 3.9.0: a performer not on ThePornDB has only these, so several

    def _max_custom(self, owner: str) -> int:
        return self.MAX_LOCAL_PHOTOS if self._local_name(owner) else self.MAX_CUSTOM_PHOTOS

    def performer_photos_dir(self) -> Path:
        """ThePornDB photos (3.2.0): their own folder in the app's data folder.
        Before, they were in the cache folder's "performers" folder, among a folder
        per creator."""
        return self.settings.performer_photos_dir   # 3.12.0: its own folder setting

    def relocate_performer_photos(self) -> None:
        """At every start (3.2.0), quick when there's nothing to do:
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
        return self.settings.own_photos_dir   # 3.12.0: its own folder setting

    def custom_photo_file(self, performer_id: str, photo_id: str) -> Optional[str]:
        self._perf_load()
        c = next((c for c in self._perf_custom.get(performer_id, []) if c["id"] == photo_id), None)
        return c["path"] if c else None

    async def add_custom_photo(self, performer_id: str, data: bytes, user: str) -> dict:
        """A photo from your computer, converted to WebP (backend/images.py)."""
        from .. import images
        self._perf_load()
        owner_name = self._photo_owner_name(performer_id)
        if owner_name is None:
            raise PerformerError(L('Performer not found'))
        try:
            webp = await asyncio.to_thread(images.to_webp, data)
        except ValueError:
            raise PerformerError(L("That file isn't a picture the app can read (JPEG, PNG, WebP, GIF, BMP, TIFF…)")) from None
        # Own photos are limited: a ThePornDB profile keeps one (a new one replaces it), a
        # performer not on ThePornDB up to MAX_LOCAL_PHOTOS. Never more than one is replaced:
        # photos that came along when a name got its profile are removed by hand, not here.
        old = list(self._perf_custom.get(performer_id, []))
        limit = self._max_custom(performer_id)
        if limit > 1 and len(old) >= limit:
            raise PerformerError(L("{n} photos at most -- remove one first", n=limit))
        if limit == 1 and len(old) > 1:
            raise PerformerError(L("This profile already has {n} of your photos (from before it was linked to ThePornDB) -- remove all but one first", n=len(old)))
        photo_id = uuid.uuid4().hex
        folder = self._custom_photo_dir()
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{performer_id}_{photo_id}.webp"
        await asyncio.to_thread(dest.write_bytes, webp)
        keep_main = any(c["main"] for c in old)
        self.db.add_custom_photo(photo_id, performer_id, str(dest), user)
        for c in old[:max(0, len(old) + 1 - limit)]:
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

    async def add_custom_photo_from_url(self, performer_id: str, url: str, user: str) -> dict:
        """3.9.0: a photo from a web address, fetched by the server (backend/safe_fetch.py:
        public addresses only, a size limit, pictures only), then saved like an uploaded one."""
        from .. import safe_fetch
        if self._photo_owner_name(performer_id) is None:
            raise PerformerError(L('Performer not found'))
        try:
            data = await safe_fetch.fetch_image(url)
        except safe_fetch.FetchRefused as exc:
            raise PerformerError(str(exc)) from None
        return await self.add_custom_photo(performer_id, data, user)

    def remove_custom_photo(self, performer_id: str, photo_id: str) -> None:
        path = self.custom_photo_file(performer_id, photo_id)
        if not path:
            raise PerformerError(L('Photo not found'))
        self.db.delete_custom_photo(photo_id)
        try:
            os.unlink(path)
        except OSError as exc:
            logger.warning("Couldn't delete the removed photo {}: {}", path, exc)
        self._perf_load(force=True)

    def set_main_custom_photo(self, performer_id: str, photo_id: Optional[str]) -> None:
        if photo_id and not self.custom_photo_file(performer_id, photo_id):
            raise PerformerError(L('Photo not found'))
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
