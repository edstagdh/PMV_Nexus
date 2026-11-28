"""Faces (3.12.0, backend/faces.py): performers recognised in videos by their face, offline.

One job on Tasks ("Finding faces"), from Management -> Faces -> Scan, and by itself after a video's
seekbar previews or frames are made (Faces -> "Read new videos by themselves"):
1. the photos of every performer in the library (from ThePornDB and your own) are read once --
   again only when a photo changes;
2. the pictures of each video not read yet (or changed since): seekbar previews (every n-th tile),
   the thumbnail and the frames under the player; the faces found are kept in data/derived.db;
3. every video's faces are compared with every performer's photos; a performer who looks like a
   face in the video is suggested (score at or above "Suggest from"), or approved by itself (at or
   above "Approve by itself", 100 = never). Performers the video already lists aren't suggested.

What you decide is kept in library.db (face_matches): Approve, Not them (never suggested again for
that video) or Undo (back to waiting). An approved match shows on the video's page and the
performer's page as "Recognised by face"; the video's own performers are never changed.

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
            out[pid] = {"name": p["name"] if p else name, "photos": self._all_photos(p) if p else self._local_photos(pid)}
        return out

    def _face_resolver(self) -> "_Resolver":
        """On the event loop: who a stored performer id is today (made once per list / job)."""
        self._perf_load()
        local = {r["local_id"]: (r["name_key"], r["name"]) for r in self._perf_local.values() if r.get("local_id")}
        for key, (name, _videos) in self._library_performer_names().items():
            local[self._local_id(key)] = (key, name)
        return _Resolver(self, local)

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
        stats = {"photos": 0, "videos": 0, "faces": 0, "suggested": 0, "approved": 0}

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
                    if not (refs_changed or rematch):
                        self._face_tally(stats, await faces.run(self._face_match_video, video_id, index, cfg, who))
                except faces.FacesUnavailable:
                    raise
                except Exception as exc:  # noqa: BLE001 -- one bad picture mustn't stop the rest
                    logger.warning("Faces: couldn't read the pictures of '{}' [{}]: {}", item.title, video_id, exc)
                state["done"] += 1
            state["current"] = None
            if self._face_stop:
                return self._face_finish(act, stats, started, stopped=True)

            # 3. new photos or new scores: every video read so far is compared again
            if refs_changed or rematch:
                state.update(stage=L("Comparing"), done=0)
                ids = [v for v in self.derived.face_scan_marks() if v in self._items]
                state["total"] = len(ids)
                for video_id in ids:
                    if self._face_stop:
                        break
                    self._face_tally(stats, await faces.run(self._face_match_video, video_id, index, cfg, who))
                    state["done"] += 1
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

    @staticmethod
    def _face_tally(stats: dict, result: dict) -> None:
        stats["suggested"] += result["suggested"]
        stats["approved"] += result["approved"]

    def _face_finish(self, act, stats: dict, started: float, stopped: bool) -> None:
        took = time.monotonic() - started
        self._face_last = {"at": time.time(), "seconds": round(took, 1), **stats}
        summary = (f"{stats['videos']} videos read ({stats['faces']} faces), {stats['photos']} performer photos, "
                   f"{stats['suggested']} new suggestions" + (f", {stats['approved']} approved by themselves" if stats["approved"] else ""))
        logger.info("Faces: {} ({:.1f}s){}", summary, took, " -- stopped" if stopped else "")
        if stopped:
            act.finish("cancelled", (f"Stopped by {act.stopped_by} · " if act.stopped_by else "") + summary)
        else:
            act.finish("success", summary)

    # ------------------------------------------------------------ performer photos (worker threads)
    def _face_ref_plan(self, people: dict) -> tuple[dict, list, dict]:
        """BLOCKING. -> (photos to read {pid: [(file name, path, mtime)]}, rows to forget
        [(pid, file name)], what's known {pid: [(file name, embedding)]})."""
        have = {(r["performer_id"], r["photo"]): r for r in self.derived.face_ref_rows()}
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
                if row and row["version"] == faces.MODEL_VERSION and row["photo_mtime"] == mtime:
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
        for name, path, mtime in photos:
            found = faces.find_faces(faces.read_image(path), min_size=cfg.min_face_px, min_quality=cfg.min_quality, max_faces=3)
            best = found[0] if found else None
            self.derived.set_face_ref(pid, name, mtime, faces.MODEL_VERSION,
                                      faces.to_blob(best["embedding"]) if best else None, best["quality"] if best else None)
            if best:
                out.append((name, best["embedding"]))
        return out

    # ------------------------------------------------------------ videos (worker threads)
    def _face_signature(self, item, cfg) -> tuple[str, dict]:
        """What would be read for a video, and a mark that changes when any of it changes."""
        parts: dict = {"v": faces.MODEL_VERSION,
                       "cfg": [cfg.storyboard_every, cfg.min_face_px, cfg.min_quality, cfg.max_faces_per_video]}
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
                                      upscale_below=upscale, max_faces=12):
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
        database's lock, so an Approve / Not them clicked meanwhile is seen."""
        result = {"suggested": 0, "approved": 0}
        item = self._items.get(video_id)
        if not item or item.status != "done":
            return result
        dets = [(d, e) for d, e in ((d, faces.from_blob(b)) for d, b in self.derived.face_detections_of(video_id)) if e is not None]
        skip = self._face_on_video(item)
        found = index.best_matches(dets, skip) if dets else {}
        auto_line = cfg.auto_approve_score if cfg.auto_approve_score < 100 else None
        with self.db._conn.lock:
            rows = self.db.face_match_rows(video_id=video_id)
            by_person: dict = {}
            for r in rows:
                by_person.setdefault(who.canonical(r["performer_id"]) or r["performer_id"], r)
            write, drop = [], []
            for pid, m in found.items():
                good = m["score"] >= cfg.suggest_score and m["support"] >= cfg.min_support
                row = by_person.get(pid)
                if row and row["status"] != "suggested":
                    if row["performer_id"] == pid:      # decided: only its score and face picture are updated
                        write.append({**row, "score": m["score"], "support": m["support"], "detection_id": m["detection"]})
                    continue
                if not good:
                    if row:
                        drop.append(row["performer_id"])
                    continue
                if auto_line is not None and m["score"] >= auto_line:
                    write.append({"performer_id": pid, "status": "approved", "auto": True, "score": m["score"],
                                  "support": m["support"], "detection_id": m["detection"], "decided_at": time.time(),
                                  "decided_by": "auto"})
                    result["approved"] += 1
                else:
                    write.append({"performer_id": pid, "status": "suggested", "score": m["score"], "support": m["support"],
                                  "detection_id": m["detection"]})
                    if not row:
                        result["suggested"] += 1
            for pid, row in by_person.items():   # waiting ones for performers the video lists now, or not found any more
                if row["status"] == "suggested" and (pid in skip or pid not in found):
                    drop.append(row["performer_id"])
            if write or drop:
                self.db.save_face_matches(video_id, write, sorted(set(drop)))
        return result

    # ------------------------------------------------------------ reviewing
    def face_matches(self, status: str = "suggested", video_id: Optional[str] = None,
                     performer_id: Optional[str] = None, offset: int = 0, limit: int = 60) -> dict:
        if status not in STATUSES:
            raise ValueError(L("Unknown status"))
        who = self._face_resolver()
        pids = None
        if performer_id:
            pids = [r["performer_id"] for r in self.db.face_match_rows(status=status)
                    if who.canonical(r["performer_id"]) == performer_id]
        rows = self.db.face_match_rows(status=status, video_id=video_id, performer_ids=pids)
        out = []
        for r in rows:
            item = self._items.get(r["video_id"])
            person = who.card(r["performer_id"])
            if not item or item.status != "done" or not person:
                continue
            out.append(self._face_row(r, item, person))
        return {"total": len(out), "rows": out[offset:offset + limit]}

    @staticmethod
    def _face_row(r: dict, item, person: dict) -> dict:
        return {"video": {"id": item.id, "title": item.title, "creator": item.creator,
                          "thumb": f"/api/library/{item.id}/thumbnail?v={item.thumbnail_updated_at or ''}" if item.thumbnail_path else None},
                "performer": person, "performer_id": r["performer_id"], "status": r["status"], "score": r["score"],
                "support": r["support"], "auto": bool(r["auto"]), "decided_by": r.get("decided_by"),
                "decided_at": r.get("decided_at"),
                "crop": f"/api/faces/crop/{r['detection_id']}" if r.get("detection_id") else None}

    def decide_face_match(self, video_id: str, performer_id: str, action: str, user: str) -> dict:
        """approve / reject ("Not them": never suggested again for this video) / undo (waiting again)."""
        status = {"approve": "approved", "reject": "rejected", "undo": "suggested"}.get(action)
        if status is None:
            raise ValueError(L("Unknown action"))
        row = self.db.get_face_match(video_id, performer_id)
        if not row:
            raise KeyError(video_id)
        self.db.decide_face_match(video_id, performer_id, status, user)
        logger.info("Faces: {} in [{}] {} by {}", (self._face_resolver().card(performer_id) or {}).get("name", performer_id),
                    video_id, {"approved": "approved", "rejected": "marked not them", "suggested": "back to waiting"}[status], user)
        return {"ok": True, "status": status}

    def approve_face_matches_above(self, score: float, user: str) -> dict:
        n = 0
        for r in self.db.face_match_rows(status="suggested"):
            if r["score"] >= score and r["video_id"] in self._items:
                n += self.db.decide_face_match(r["video_id"], r["performer_id"], "approved", user)
        logger.info("Faces: {} suggestions at or above {} approved by {}", n, score, user)
        return {"ok": True, "approved": n}

    def face_crop(self, detection_id: int) -> Optional[bytes]:
        d = self.derived.face_detection(detection_id)
        return bytes(d["crop"]) if d and d.get("crop") and d["video_id"] in self._items else None

    # ------------------------------------------------------------ video page / performer page
    def faces_for_video(self, video_id: str, manage: bool) -> dict:
        item = self._items.get(video_id)
        if not item:
            raise KeyError(video_id)
        who = self._face_resolver()
        on_video = self._face_on_video(item)
        out = {"approved": [], "suggested": []}
        for r in self.db.face_match_rows(video_id=video_id):
            if r["status"] == "rejected" or (r["status"] == "suggested" and not manage):
                continue
            person = who.card(r["performer_id"])
            if person and person["id"] not in on_video:
                out[r["status"]].append(self._face_row(r, item, person))
        return out

    def faces_for_performer(self, name: str) -> list[dict]:
        """Videos where this performer was recognised by face (approved), not already listing them."""
        who = self._face_resolver()
        key = fold(name)
        lib = self._library_performer_names()
        if key not in lib and key not in self._perf_names:   # a profile's own name (as performer_profile)
            key = self._key_for_shown_name(key, lib) or key
        pid = self._face_person_of_key(key)
        out = []
        for r in self.db.face_match_rows(status="approved"):
            if who.canonical(r["performer_id"]) != pid:
                continue
            item = self._items.get(r["video_id"])
            if item and item.status == "done" and pid not in self._face_on_video(item):
                out.append({"id": item.id, "title": item.title, "creator": item.creator, "score": r["score"],
                            "thumb": f"/api/library/{item.id}/thumbnail?v={item.thumbnail_updated_at or ''}" if item.thumbnail_path else None})
        return out



class _Resolver:
    """Stored performer id -> today's id and card. A name without a ThePornDB profile is kept under
    its local id; once the name is linked, its matches belong to the profile."""

    def __init__(self, lm, local: dict):
        self._lm, self._local = lm, local

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
