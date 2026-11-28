"""Faces (3.12.0, backend/faces.py): performers recognised in videos by their face, offline.

One job on Tasks ("Finding faces"), from Management -> Faces -> Scan, and by itself after a video's
seekbar previews or frames are made (Faces -> "Read new videos by themselves"):
1. the photos of every performer in the library (from ThePornDB and your own) are read once --
   again only when a photo changes;
2. the pictures of each video not read yet (or changed since): seekbar previews (every n-th tile),
   the thumbnail and the frames under the player; the faces found are kept in data/derived.db;
3. every video's faces are compared with every performer's photos. 3.13.0: each face counts for
   one performer only -- the one it looks most like, by at least "margin" points more than the next
   (a face of a performer the video already lists counts for them and suggests nobody else); the
   video's best few (at most "max_per_video") at or above "Suggest from" are suggested, or approved
   by themselves at or above "Approve by itself" (100 = never).

What you decide is kept in library.db (face_matches): Approve (3.13.0: the performer is added to the
video's performers, as a hand edit), Not them (never suggested again for that video) or Undo (back
to waiting; a performer Approve added is taken off the video again).

Only pictures the app made are read; no file is changed or removed, nothing leaves the computer.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from ..i18n import L
from ..resources import AUTO, JOB, resources
from ..spellings import fold
from .. import activity, faces

STATUSES = ("suggested", "approved", "rejected")
SAME_FACE = 0.85     # two faces of one video this alike are one face shown again: only the better one is kept
MATCH_VERSION = 2    # 3.13.0: how matches are made changed -> every video is compared again once
SORTS = ("score", "performer", "video", "found", "faces", "time")


class FacesMixin:
    _face_task: Optional[asyncio.Task] = None
    _face_stop = False
    _face_again: Optional[dict] = None
    _face_last: dict = {}
    _face_state: dict = {}

    # ------------------------------------------------------------ who's who
    def _face_person_of_key(self, key: str) -> str:
        """A performer name (folded) -> the id faces are kept under: its ThePornDB profile, else
        the local id of the name (the one its own photos use)."""
        row = self._perf_names.get(key)
        if row and row.get("status") == "linked" and row.get("performer_id") in self._perf_by_id:
            return row["performer_id"]
        return self._local_id(key)

    def _face_people(self) -> dict[str, dict]:
        """BLOCKING-free. person id -> {"name", "photos": [paths]} for the performers of finished videos."""
        self._perf_load()
        out: dict[str, dict] = {}
        for key, (name, _videos) in self._library_performer_names().items():
            pid = self._face_person_of_key(key)
            if pid in out:
                continue
            p = self._perf_by_id.get(pid)
            photos = self._all_photos(p) if p else self._local_photos(pid)
            most = self.settings.faces.max_photos_per_performer
            out[pid] = {"name": p["name"] if p else name, "photos": photos[:most] if most else photos}
        return out

    def _face_resolver(self) -> "_Resolver":
        """On the event loop: who a stored performer id is today (made once per list / job)."""
        self._perf_load()
        local = {r["local_id"]: (r["name_key"], r["name"]) for r in self._perf_local.values() if r.get("local_id")}
        names: dict[str, tuple[str, int]] = {}   # person -> the library name with the most videos
        for key, (name, videos) in self._library_performer_names().items():
            local[self._local_id(key)] = (key, name)
            pid = self._face_person_of_key(key)
            if videos > names.get(pid, ("", -1))[1]:
                names[pid] = (name, videos)
        return _Resolver(self, local, {pid: n for pid, (n, _v) in names.items()})

    def _face_on_video(self, item) -> frozenset:
        """The performers a video lists (their face ids): never suggested for it."""
        return frozenset(self._face_person_of_key(fold(n)) for n in item.performers or [] if fold(n))

    # ------------------------------------------------------------ status
    def faces_ready(self) -> bool:
        return self.settings.faces.enabled and faces.models_status()["ok"]

    def faces_overview(self) -> dict:
        self._perf_load()
        done = sum(1 for i in self._items.values() if i.status == "done")
        people = self._face_people()
        return {"enabled": self.settings.faces.enabled, "models": faces.models_status(), "model_files": faces.model_lines(),
                "videos": done, "performers": len(people), "performers_with_photos": sum(1 for v in people.values() if v["photos"]),
                **{f"read_{k}": v for k, v in self.derived.face_counts().items()},
                "matches": self.db.face_match_counts(),
                "running": bool(self._face_task and not self._face_task.done()), "state": dict(self._face_state) or None,
                "last": self._face_last or None}

    # ------------------------------------------------------------ the job
    def request_face_scan(self, source: str = "auto", video_ids: Optional[list[str]] = None,
                          again: bool = False, rematch: bool = False) -> bool:
        """Starts "Finding faces" (or, while it runs, once more after it). `video_ids`: only these
        (None: every video not read yet); `again`: read them again even if nothing changed;
        `rematch`: compare every video again (the scores to suggest / approve changed).
        False: it's off, or it was running already (it runs again after)."""
        if not self.faces_ready():
            return False
        if self._face_task and not self._face_task.done():
            nxt = self._face_again or {"source": source, "ids": set(), "all": False, "again": False, "rematch": False}
            if video_ids is None:
                nxt["all"] = True
            else:
                nxt["ids"].update(video_ids)
            nxt["again"] = nxt["again"] or again
            nxt["rematch"] = nxt["rematch"] or rematch
            self._face_again = nxt
            return False
        self._face_task = asyncio.create_task(self._face_worker(source, video_ids, again, rematch))
        return True

    def request_face_scan_auto(self) -> None:
        """After seekbar previews / frames were made (their workers call this)."""
        if self.settings.faces.scan_new:
            self.request_face_scan("auto")

    def stop_face_scan(self) -> None:
        self._face_stop = True

    async def _face_worker(self, source: str, video_ids, again: bool, rematch: bool) -> None:
        while True:
            self._face_again = None
            await self._face_run(source, video_ids, again, rematch)
            nxt = self._face_again
            if not nxt or self._face_stop or not self.faces_ready():
                self._face_again = None
                return
            source, again, rematch = nxt["source"], nxt["again"], nxt["rematch"]
            video_ids = None if nxt["all"] else sorted(nxt["ids"])

    async def _face_run(self, source: str, video_ids, again: bool, rematch: bool) -> None:
        cfg = self.settings.faces
        priority = AUTO if source == "auto" else JOB
        state = self._face_state = {"stage": L("Reading performer photos"), "done": 0, "total": 0, "current": None}
        self._face_stop = False
        stats = {"photos": 0, "videos": 0, "faces": 0, "suggested": 0, "approved": 0, "removed": 0}

        def progress() -> dict:
            item = self._items.get(state["current"] or "")
            return {"stage": state["stage"], "done": state["done"], "total": state["total"],
                    "detail": item.title if item else state.get("detail"), "item": activity.item_ref(item)}

        act = activity.start("faces", L("Finding faces"), source=source, progress=progress)
        act.stopper = self.stop_face_scan
        ref = {"activity": act.id}
        started = time.monotonic()
        try:
            # videos no longer in the library: their faces and matches go
            marks = self.derived.face_scan_marks()
            gone = [v for v in marks if v not in self._items]
            if gone:
                self.derived.delete_face_videos(gone)
                self.db.delete_face_matches_of(gone)

            # 1. performer photos
            people = self._face_people()
            who = self._face_resolver()
            todo, stale, refs = await asyncio.to_thread(self._face_ref_plan, people)
            refs_changed = bool(stale)
            if stale:
                self.derived.delete_face_refs(stale)
            state.update(total=len(todo), done=0)
            for pid, photos in todo.items():
                if self._face_stop:
                    break
                state["detail"] = people[pid]["name"]
                async with resources.use({"cpu"}, L("Faces: photos of {name}", name=people[pid]["name"]), priority, ref):
                    got = await faces.run(self._face_read_photos, pid, photos, cfg)
                refs[pid] = [e for e in refs.get(pid, []) if e[0] not in {p for p, _m in photos}] + got
                stats["photos"] += len(photos)
                refs_changed = True
                state["done"] += 1
            state["detail"] = None
            if self._face_stop:
                return self._face_finish(act, stats, started, stopped=True)
            index = faces.RefIndex({pid: [e for _n, e in rows] for pid, rows in refs.items()})
            full_compare = refs_changed or rematch or self.face_match_outdated()

            # 2. the videos' pictures
            state.update(stage=L("Reading videos"), done=0, total=0)
            if video_ids is not None:
                items = [self._items[v] for v in video_ids if v in self._items]
            else:
                items = sorted((i for i in self._items.values() if i.status == "done"),
                               key=lambda i: i.download_datetime or "", reverse=True)   # newest first
            plan = await asyncio.to_thread(self._face_video_plan, items, again)
            state["total"] = len(plan)
            scanned = []
            for video_id, sig in plan:
                if self._face_stop:
                    break
                item = self._items.get(video_id)
                if not item or item.status != "done":
                    state["done"] += 1
                    continue
                state["current"] = video_id
                try:
                    async with resources.use({"cpu"}, L("Faces: {title}", title=item.title or video_id), priority, ref):
                        n = await faces.run(self._face_read_video, item, sig, cfg)
                    stats["videos"] += 1
                    stats["faces"] += n
                    scanned.append(video_id)
                    if not full_compare:
                        await self._face_tally(stats, await faces.run(self._face_match_video, video_id, index, cfg, who), who)
                except faces.FacesUnavailable:
                    raise
                except Exception as exc:  # noqa: BLE001 -- one bad picture mustn't stop the rest
                    logger.warning("Faces: couldn't read the pictures of '{}' [{}]: {}", item.title, video_id, exc)
                state["done"] += 1
            state["current"] = None
            if self._face_stop:
                return self._face_finish(act, stats, started, stopped=True)

            # 3. new photos or new scores (or, 3.13.0, a new way of matching): every video read so far is compared again
            if full_compare:
                state.update(stage=L("Comparing"), done=0)
                ids = [v for v in self.derived.face_scan_marks() if v in self._items]
                state["total"] = len(ids)
                for video_id in ids:
                    if self._face_stop:
                        break
                    await self._face_tally(stats, await faces.run(self._face_match_video, video_id, index, cfg, who), who)
                    state["done"] += 1
                if not self._face_stop:
                    self.db.set_app_state("faces_match_version", MATCH_VERSION)
            self._face_finish(act, stats, started, stopped=self._face_stop)
        except faces.FacesUnavailable as exc:
            logger.warning("Faces: {}", exc)
            act.finish("failed", str(exc))
        except BaseException as exc:
            logger.exception("Finding faces stopped by an error: {}", exc)
            act.finish("failed", L("Stopped by an error (see the log)"))
            if not isinstance(exc, Exception):
                raise
        finally:
            self._face_state = {}

    async def _face_tally(self, stats: dict, result: dict, who) -> None:
        stats["suggested"] += result["suggested"]
        stats["approved"] += result["approved"]
        stats["removed"] += result.get("removed", 0)
        if result.get("auto"):
            await self._face_apply(result["auto"], who)

    def _face_finish(self, act, stats: dict, started: float, stopped: bool) -> None:
        took = time.monotonic() - started
        self._face_last = {"at": time.time(), "seconds": round(took, 1), **stats}
        summary = (f"{stats['videos']} videos read ({stats['faces']} faces), {stats['photos']} performer photos, "
                   f"{stats['suggested']} new suggestions" + (f", {stats['approved']} approved by themselves" if stats["approved"] else "")
                   + (f", {stats['removed']} waiting ones that didn't hold any more removed" if stats["removed"] else ""))
        logger.info("Faces: {} ({:.1f}s){}", summary, took, " -- stopped" if stopped else "")
        if stopped:
            act.finish("cancelled", (f"Stopped by {act.stopped_by} · " if act.stopped_by else "") + summary)
        else:
            act.finish("success", summary)

    # ------------------------------------------------------------ performer photos (worker threads)
    def _face_ref_version(self, cfg) -> int:
        """Stored with each performer photo: a new model or other photo settings read them again."""
        return faces.MODEL_VERSION * 1_000_000 + cfg.photo_min_face_px * 1000 + int(round(cfg.min_quality * 100))

    def _face_ref_plan(self, people: dict) -> tuple[dict, list, dict]:
        """BLOCKING. -> (photos to read {pid: [(file name, path, mtime)]}, rows to forget
        [(pid, file name)], what's known {pid: [(file name, embedding)]})."""
        have = {(r["performer_id"], r["photo"]): r for r in self.derived.face_ref_rows()}
        version = self._face_ref_version(self.settings.faces)
        todo: dict[str, list] = {}
        keep: set = set()
        refs: dict[str, list] = {}
        for pid, person in people.items():
            for path in person["photos"]:
                p = Path(path)
                key = (pid, p.name)
                row = have.get(key)
                try:
                    mtime = p.stat().st_mtime
                except OSError:          # can't be reached now (a drive that's off): what was read stays
                    mtime = row["photo_mtime"] if row else None
                    if row is None:
                        continue
                keep.add(key)
                if row and row["version"] == version and row["photo_mtime"] == mtime:
                    emb = faces.from_blob(row["embedding"]) if row["embedding"] else None
                    if emb is not None:
                        refs.setdefault(pid, []).append((p.name, emb))
                else:
                    todo.setdefault(pid, []).append((p.name, str(p), mtime))
        stale = [k for k in have if k not in keep]
        return todo, stale, refs

    def _face_read_photos(self, pid: str, photos: list, cfg) -> list:
        """BLOCKING (faces thread). The biggest face of each photo -> [(file name, embedding)]."""
        out = []
        version = self._face_ref_version(cfg)
        for name, path, mtime in photos:
            found = faces.find_faces(faces.read_image(path), min_size=cfg.photo_min_face_px, min_quality=cfg.min_quality, max_faces=3)
            best = found[0] if found else None
            self.derived.set_face_ref(pid, name, mtime, version,
                                      faces.to_blob(best["embedding"]) if best else None, best["quality"] if best else None)
            if best:
                out.append((name, best["embedding"]))
        return out

    # ------------------------------------------------------------ videos (worker threads)
    def _face_signature(self, item, cfg) -> tuple[str, dict]:
        """What would be read for a video, and a mark that changes when any of it changes."""
        parts: dict = {"v": faces.MODEL_VERSION,
                       "cfg": [cfg.storyboard_every, cfg.min_face_px, cfg.min_quality, cfg.max_faces_per_video]
                       + ([] if cfg.upscale_small else ["no-upscale"])}
        sources: dict = {}
        if cfg.use_storyboards:
            info = self.storyboard_info(item.id)
            if info:
                parts["sb"] = info.get("made_at")
                sources["storyboard"] = info
        if cfg.use_frames:
            info = self.frames_info(item.id)
            if info:
                parts["fr"] = info.get("made_at")
                sources["frames"] = info
        if cfg.use_thumbnail and item.thumbnail_path:
            try:
                parts["th"] = Path(item.thumbnail_path).stat().st_mtime
                sources["thumbnail"] = item.thumbnail_path
            except OSError:
                pass
        return json.dumps(parts, sort_keys=True), sources

    def _face_video_plan(self, items: list, again: bool) -> list[tuple[str, str]]:
        """BLOCKING. [(video id, signature)] of `items` to read (a list made on the event loop)."""
        cfg = self.settings.faces
        marks = self.derived.face_scan_marks()
        out = []
        for item in items:
            if item.status != "done":
                continue
            sig, sources = self._face_signature(item, cfg)
            if not sources:
                continue          # nothing to read yet (its previews / frames come later)
            mark = marks.get(item.id)
            if again or not mark or mark != (faces.MODEL_VERSION, sig):
                out.append((item.id, sig))
        return out

    def _face_pictures(self, item, sources: dict, cfg):
        """BLOCKING. (source, seconds or None, picture, upscale below) for each picture to read."""
        th = sources.get("thumbnail")
        if th:
            yield "thumbnail", None, faces.read_image(th), 0
        info = sources.get("frames")
        if info:
            folder = self.frames_folder(item.id)
            for f in info["frames"]:
                yield "frame", f.get("seconds"), faces.read_image(str(folder / f["file"])), 400
        info = sources.get("storyboard")
        if info:
            folder = self.storyboard_folder(item.id)
            w, h, cols, rows = int(info["width"]), int(info["height"]), int(info["cols"]), int(info["rows"])
            per = cols * rows
            total = int(info.get("frames") or info["sheets"] * per)
            sheet_no, sheet = -1, None
            for t in range(0, total, max(1, cfg.storyboard_every)):
                if self._face_stop:
                    return
                s = t // per
                if s != sheet_no:
                    sheet_no, sheet = s, faces.read_image(str(folder / f"{s}.webp"))
                if sheet is None:
                    continue
                r, c = divmod(t % per, cols)
                tile = sheet[r * h:(r + 1) * h, c * w:(c + 1) * w]
                if tile.size:
                    yield "storyboard", t * float(info["interval"]), tile, 400

    def _face_read_video(self, item, sig: str, cfg) -> int:
        """BLOCKING (faces thread). Finds the faces in a video's pictures and keeps the clearest
        different ones (at most max_faces_per_video). -> how many."""
        _sig, sources = self._face_signature(item, cfg)
        found = []
        for source, at, pic, upscale in self._face_pictures(item, sources, cfg):
            for f in faces.find_faces(pic, min_size=cfg.min_face_px, min_quality=cfg.min_quality,
                                      upscale_below=upscale if cfg.upscale_small else 0, max_faces=12):
                f.update(source=source, at=at)
                found.append(f)
        if self._face_stop:
            return 0
        found.sort(key=lambda f: -(f["quality"] * min(f["box"][2], f["box"][3])))
        kept: list = []
        for f in found:
            if len(kept) >= cfg.max_faces_per_video:
                break
            if any(float(f["embedding"] @ k["embedding"]) >= SAME_FACE for k in kept):
                continue
            kept.append(f)
        self.derived.replace_face_detections(item.id, faces.MODEL_VERSION, sig,
                                             [{**f, "embedding": faces.to_blob(f["embedding"])} for f in kept])
        return len(kept)

    def _face_match_video(self, video_id: str, index, cfg, who: "_Resolver") -> dict:
        """BLOCKING (faces thread). Compares one video's faces with the performers' and writes what
        changed. Your decisions are never overwritten: the rows are read and written under the
        database's lock, so an Approve / Not them clicked meanwhile is seen. -> counts, and the
        matches approved by themselves (their performer is added by the caller, on the event loop)."""
        result = {"suggested": 0, "approved": 0, "removed": 0, "auto": []}
        item = self._items.get(video_id)
        if not item or item.status != "done":
            return result
        dets = [(d, e) for d, e in ((d, faces.from_blob(b)) for d, b in self.derived.face_detections_of(video_id)) if e is not None]
        skip = self._face_on_video(item)
        found = index.assign(dets, skip, line=faces.SAME_PERSON, margin=cfg.margin / 100) if dets else {}
        good = sorted(((pid, m) for pid, m in found.items()
                       if m["score"] >= cfg.suggest_score and m["support"] >= cfg.min_support),
                      key=lambda pm: -pm[1]["score"])[:cfg.max_per_video]
        good_ids = {pid for pid, _m in good}
        auto_line = cfg.auto_approve_score if cfg.auto_approve_score < 100 else None
        with self.db._conn.lock:
            rows = self.db.face_match_rows(video_id=video_id)
            by_person: dict = {}
            for r in rows:
                by_person.setdefault(who.canonical(r["performer_id"]) or r["performer_id"], r)
            write, drop = [], []
            for pid, m in found.items():     # decided ones: only their score and face picture are updated
                row = by_person.get(pid)
                if row and row["status"] != "suggested" and row["performer_id"] == pid:
                    write.append({**row, "score": m["score"], "support": m["support"], "detection_id": m["detection"]})
            for pid, m in good:
                row = by_person.get(pid)
                if row and row["status"] != "suggested":
                    continue                 # Approve / Not them stay as you decided
                if auto_line is not None and m["score"] >= auto_line:
                    write.append({"performer_id": pid, "status": "approved", "auto": True, "score": m["score"],
                                  "support": m["support"], "detection_id": m["detection"], "decided_at": time.time(),
                                  "decided_by": "auto"})
                    result["approved"] += 1
                    result["auto"].append((video_id, pid))
                else:
                    write.append({"performer_id": pid, "status": "suggested", "score": m["score"], "support": m["support"],
                                  "detection_id": m["detection"]})
                    if not row:
                        result["suggested"] += 1
            for pid, row in by_person.items():   # waiting ones that don't hold any more
                if row["status"] == "suggested" and pid not in good_ids:
                    drop.append(row["performer_id"])
            if write or drop:
                self.db.save_face_matches(video_id, write, sorted(set(drop)))
            result["removed"] = len(set(drop))
        return result

    # ------------------------------------------------------------ the video's performers (3.13.0)
    async def _face_apply(self, pairs: list[tuple[str, str]], who: Optional["_Resolver"] = None) -> int:
        """Approved matches -> their performer is added to the video's performers (a hand edit,
        kept through syncs), unless the video lists them already. Recorded per match (applied,
        applied_name) so Undo takes off only what was added here. -> videos changed."""
        if not pairs:
            return 0
        who = who or self._face_resolver()
        by_name: dict[str, list[str]] = {}
        marks: list[tuple[str, str, Optional[int], Optional[str]]] = []
        for video_id, pid in pairs:
            item = self._items.get(video_id)
            cid = who.canonical(pid) or pid
            name = who.library_name(cid)
            if not item or item.status != "done" or not name:
                continue
            if cid in self._face_on_video(item):
                marks.append((video_id, pid, 0, None))           # listed already: nothing to add (or take off)
                continue
            by_name.setdefault(name, []).append(video_id)
            marks.append((video_id, pid, 1, name))
        changed = 0
        for name, ids in by_name.items():
            changed += (await self.bulk_edit(ids, {"performers": {"add": [name]}})).get("videos", 0)
        for video_id, pid, applied, name in marks:
            self.db.set_face_applied(video_id, pid, applied, name)
        if marks:
            logger.info("Faces: {} approved match(es) -> performer added to {} video(s)", len(marks), changed)
        return changed

    async def _face_unapply(self, rows: list[dict]) -> int:
        """Undo / Not them of approved matches: the performer Approve added is taken off the video."""
        by_name: dict[str, list[str]] = {}
        for r in rows:
            if r.get("applied") == 1 and r.get("applied_name") and r["video_id"] in self._items:
                by_name.setdefault(r["applied_name"], []).append(r["video_id"])
        changed = 0
        for name, ids in by_name.items():
            try:
                changed += (await self.bulk_edit(ids, {"performers": {"remove": [name]}})).get("videos", 0)
            except ValueError:
                pass
        for r in rows:
            self.db.set_face_applied(r["video_id"], r["performer_id"], None, None)
        return changed

    async def apply_pending_face_approvals(self) -> int:
        """At start: matches approved before 3.13.0 (applied unknown) get their performer added."""
        rows = [r for r in self.db.face_match_rows(status="approved") if r.get("applied") is None]
        if not rows:
            return 0
        n = await self._face_apply([(r["video_id"], r["performer_id"]) for r in rows])
        logger.info("Faces: {} match(es) approved before 3.13.0 -> performer added to {} video(s)", len(rows), n)
        return n

    async def faces_at_start(self) -> None:
        """At start (3.13.0): approvals made before 3.13.0 add their performer; when the way of
        matching changed, every video read so far is compared again once (it removes the waiting
        suggestions that don't hold any more)."""
        try:
            await self.apply_pending_face_approvals()
            if self.faces_ready() and self.face_match_outdated() and self.derived.face_scan_marks():
                logger.info("Faces: matching changed -- every video read so far is compared again")
                self.request_face_scan("auto", [], rematch=True)
        except Exception as exc:  # noqa: BLE001 -- never stops the app
            logger.opt(exception=exc).warning("Faces: start-up work failed: {}", exc)

    def face_match_outdated(self) -> bool:
        return self.db.get_app_state("faces_match_version", 0) != MATCH_VERSION

    # ------------------------------------------------------------ reviewing
    def face_matches(self, status: str = "suggested", *, video_id: Optional[str] = None, performer_id: Optional[str] = None,
                     min_score: float = 0, q: str = "", sort: str = "score", desc: bool = True,
                     offset: int = 0, limit: int = 60) -> dict:
        """One page of matches. 3.13.0: sorted by score / performer / video / found (when) /
        faces (how many) / time (in the video), filtered by a lowest score and by text (in the
        performer's or the video's name)."""
        if status not in STATUSES:
            raise ValueError(L("Unknown status"))
        if sort not in SORTS:
            raise ValueError(L("Unknown order"))
        who = self._face_resolver()
        needle = fold(q or "")
        rows = []
        for r in self.db.face_match_rows(status=status, video_id=video_id):
            if r["score"] < min_score:
                continue
            item = self._items.get(r["video_id"])
            if not item or item.status != "done":
                continue
            cid = who.canonical(r["performer_id"])
            if cid is None or (performer_id and cid != performer_id):
                continue
            name = who.display_name(cid)
            if needle and needle not in fold(name or "") and needle not in fold(item.title or ""):
                continue
            rows.append((r, item, cid, name))
        dets = self.derived.face_detections_info([r["detection_id"] for r, *_ in rows if r.get("detection_id")]) \
            if sort == "time" else {}
        key = {"score": lambda x: x[0]["score"], "faces": lambda x: (x[0]["support"], x[0]["score"]),
               "found": lambda x: x[0].get("decided_at") or x[0]["created_at"],
               "performer": lambda x: (fold(x[3] or ""), -x[0]["score"]), "video": lambda x: (fold(x[1].title or ""), -x[0]["score"]),
               "time": lambda x: ((dets.get(x[0].get("detection_id")) or {}).get("at_seconds") or 0)}[sort]
        rows.sort(key=key, reverse=desc)
        page = rows[offset:offset + limit]
        info = self.derived.face_detections_info([r["detection_id"] for r, *_ in page if r.get("detection_id")])
        return {"total": len(rows), "offset": offset, "limit": limit,
                "rows": [self._face_row(r, item, who.card(cid), info.get(r.get("detection_id"))) for r, item, cid, _n in page]}

    @staticmethod
    def _face_row(r: dict, item, person: Optional[dict], det: Optional[dict] = None) -> dict:
        d = r.get("detection_id")
        return {"video": {"id": item.id, "title": item.title, "creator": item.creator,
                          "thumb": f"/api/library/{item.id}/thumbnail?v={item.thumbnail_updated_at or ''}" if item.thumbnail_path else None},
                "performer": person, "performer_id": r["performer_id"], "status": r["status"], "score": r["score"],
                "support": r["support"], "auto": bool(r["auto"]), "decided_by": r.get("decided_by"),
                "decided_at": r.get("decided_at"), "created_at": r.get("created_at"), "added_to_video": r.get("applied") == 1,
                "source": (det or {}).get("source"), "at": (det or {}).get("at_seconds"),
                "crop": f"/api/faces/crop/{d}" if d else None,
                "face": f"/api/faces/picture/{d}?kind=face" if d and det else None,
                "frame": f"/api/faces/picture/{d}?kind=frame" if d and det else None}

    async def decide_face_matches(self, pairs: list[tuple[str, str]], action: str, user: str) -> dict:
        """approve (the performer is added to the video) / reject ("Not them": never suggested again
        for this video) / undo (waiting again) -- for one match or many. A performer an approval
        added is taken off the video again by Not them / Undo."""
        status = {"approve": "approved", "reject": "rejected", "undo": "suggested"}.get(action)
        if status is None:
            raise ValueError(L("Unknown action"))
        rows = [r for r in (self.db.get_face_match(v, p) for v, p in dict.fromkeys(pairs)) if r]
        if not rows:
            raise KeyError("no such match")
        who = self._face_resolver()
        if status != "approved":
            await self._face_unapply([r for r in rows if r["status"] == "approved"])
        for r in rows:
            self.db.decide_face_match(r["video_id"], r["performer_id"], status, user)
        added = 0
        if status == "approved":
            added = await self._face_apply([(r["video_id"], r["performer_id"]) for r in rows if r["status"] != "approved"
                                            or r.get("applied") is None], who)
        logger.info("Faces: {} match(es) {} by {}{}", len(rows),
                    {"approved": "approved", "rejected": "marked not them", "suggested": "back to waiting"}[status], user,
                    f" (performer added to {added} video(s))" if added else "")
        return {"ok": True, "status": status, "count": len(rows), "videos_changed": added}

    async def decide_face_match(self, video_id: str, performer_id: str, action: str, user: str) -> dict:
        return await self.decide_face_matches([(video_id, performer_id)], action, user)

    async def approve_face_matches_above(self, score: float, user: str) -> dict:
        pairs = [(r["video_id"], r["performer_id"]) for r in self.db.face_match_rows(status="suggested")
                 if r["score"] >= score and r["video_id"] in self._items]
        if not pairs:
            return {"ok": True, "approved": 0}
        res = await self.decide_face_matches(pairs, "approve", user)
        logger.info("Faces: {} suggestions at or above {} approved by {}", res["count"], score, user)
        return {"ok": True, "approved": res["count"]}

    def remove_face_matches_below(self, score: float, user: str) -> dict:
        n = self.db.delete_face_matches_below(score)
        logger.info("Faces: {} waiting match(es) below {} removed by {}", n, score, user)
        return {"ok": True, "removed": n}

    def face_crop(self, detection_id: int) -> Optional[bytes]:
        d = self.derived.face_detection(detection_id)
        return bytes(d["crop"]) if d and d.get("crop") and d["video_id"] in self._items else None

    def face_picture(self, detection_id: int, kind: str, size: int) -> Optional[bytes]:
        """BLOCKING. 3.13.0: the picture a face was found in (kind "frame": the seekbar picture,
        frame or thumbnail, with the face marked) or the face itself, bigger (kind "face": the
        face with some room around it, `size` px). Made from the video's own pictures each time."""
        import cv2
        d = (self.derived.face_detections_info([detection_id]) or {}).get(detection_id)
        item = self._items.get(d["video_id"]) if d else None
        if not d or not item or not d.get("box"):
            return None
        pic = self._face_source_picture(item, d["source"], d.get("at_seconds"))
        if pic is None:
            return None
        x, y, w, h = (int(v) for v in d["box"])
        H, W = pic.shape[:2]
        if kind == "face":
            side = int(max(w, h) * 1.8)
            cx, cy = x + w // 2, y + h // 2
            x0, y0 = max(0, cx - side // 2), max(0, cy - side // 2)
            part = pic[y0:min(H, y0 + side), x0:min(W, x0 + side)]
            if not part.size:
                return None
            scale = size / max(part.shape[:2])
            part = cv2.resize(part, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
        else:
            part = pic.copy()
            scale = max(1.0, min(4.0, size / max(W, H)))
            if scale > 1:
                part = cv2.resize(part, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            t = max(2, int(round(2 * scale)))
            cv2.rectangle(part, (int(x * scale), int(y * scale)), (int((x + w) * scale), int((y + h) * scale)), (0, 200, 255), t)
        ok, jpg = cv2.imencode(".jpg", part, [cv2.IMWRITE_JPEG_QUALITY, 88])
        return jpg.tobytes() if ok else None

    def _face_source_picture(self, item, source: str, at):
        """BLOCKING. The picture of a video a face was found in (as the scan read it), or None."""
        if source == "thumbnail":
            return faces.read_image(item.thumbnail_path) if item.thumbnail_path else None
        if source == "frame":
            info = self.frames_info(item.id)
            if not info or at is None:
                return None
            f = min(info["frames"], key=lambda f: abs((f.get("seconds") or 0) - at), default=None)
            return faces.read_image(str(self.frames_folder(item.id) / f["file"])) if f else None
        if source == "storyboard":
            info = self.storyboard_info(item.id)
            if not info or at is None:
                return None
            w, h, cols, rows = int(info["width"]), int(info["height"]), int(info["cols"]), int(info["rows"])
            t = int(round(at / float(info["interval"])))
            sheet = faces.read_image(str(self.storyboard_folder(item.id) / f"{t // (cols * rows)}.webp"))
            if sheet is None:
                return None
            r, c = divmod(t % (cols * rows), cols)
            tile = sheet[r * h:(r + 1) * h, c * w:(c + 1) * w]
            return tile if tile.size else None
        return None

    # ------------------------------------------------------------ video page / performer page
    def faces_for_video(self, video_id: str, manage: bool) -> dict:
        """The video's waiting suggestions (people who manage performers), and which of its
        performers an approved face match added (for a mark on their badge)."""
        item = self._items.get(video_id)
        if not item:
            raise KeyError(video_id)
        who = self._face_resolver()
        rows = self.db.face_match_rows(video_id=video_id)
        info = self.derived.face_detections_info([r["detection_id"] for r in rows if r.get("detection_id")]) if manage else {}
        out = {"suggested": [], "added": []}
        on_video = self._face_on_video(item)
        for r in rows:
            cid = who.canonical(r["performer_id"])
            if cid is None:
                continue
            if r["status"] == "approved" and r.get("applied") == 1 and r.get("applied_name"):
                out["added"].append({"name": r["applied_name"], "score": r["score"]})
            elif r["status"] == "suggested" and manage and cid not in on_video:
                out["suggested"].append(self._face_row(r, item, who.card(cid), info.get(r.get("detection_id"))))
        out["suggested"].sort(key=lambda m: -m["score"])
        return out

    def faces_waiting_for_performer(self, name: str) -> dict:
        """How many videos wait for you with this performer's face in them (performer page)."""
        who = self._face_resolver()
        key = fold(name)
        lib = self._library_performer_names()
        if key not in lib and key not in self._perf_names:   # a profile's own name (as performer_profile)
            key = self._key_for_shown_name(key, lib) or key
        pid = self._face_person_of_key(key)
        n = sum(1 for r in self.db.face_match_rows(status="suggested")
                if r["video_id"] in self._items and who.canonical(r["performer_id"]) == pid)
        return {"performer_id": pid, "waiting": n}


class _Resolver:
    """Stored performer id -> today's id and card. A name without a ThePornDB profile is kept under
    its local id; once the name is linked, its matches belong to the profile."""

    def __init__(self, lm, local: dict, names: Optional[dict] = None):
        self._lm, self._local, self._names = lm, local, names or {}

    def library_name(self, cid: str) -> Optional[str]:
        """The name to add to a video for this person: the library's own spelling with the most videos."""
        return self._names.get(cid) or self.display_name(cid)

    def display_name(self, cid: str) -> Optional[str]:
        p = self._lm._perf_by_id.get(cid)
        if p:
            return p["name"]
        found = self._local.get(cid)
        return found[1] if found else None

    def canonical(self, pid: str) -> Optional[str]:
        if pid in self._lm._perf_by_id:
            return pid
        found = self._local.get(pid)
        return self._lm._face_person_of_key(found[0]) if found else None

    def card(self, pid: str) -> Optional[dict]:
        lm = self._lm
        cid = self.canonical(pid)
        if cid is None:
            return None
        p = lm._perf_by_id.get(cid)
        if p:
            return {"id": cid, "name": p["name"],
                    "photo": f"/api/performers/{cid}/photo?v={lm._photo_version(p)}" if lm._all_photos(p) else None}
        found = self._local.get(cid)
        if not found:
            return None
        return {"id": cid, "name": found[1],
                "photo": f"/api/performers/{cid}/photo?v={lm._local_version(cid)}" if lm._local_photos(cid) else None}
