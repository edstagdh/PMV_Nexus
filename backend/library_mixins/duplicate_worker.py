from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import numpy as np
from loguru import logger

from .. import dupfinder
from ..config import get_settings
from .. import activity, notifications
from ..notifications import title_of
from .core import JobBlockedError

# See backend/dupfinder.py for the matching logic (a re-implementation of
# VideoDuplicateFinder's) and its deliberate differences from VDF.
HASH_LOG_EVERY_N = 25


def _compare_signature(cfg) -> str:
    """Everything that changes what a stored match MEANS. If this differs from
    the signature of the last completed scan, old matches are discarded and
    every file is re-compared."""
    return json.dumps(
        [dupfinder.ALGO_VERSION, cfg.algorithm, cfg.percent, cfg.phash_quorum, cfg.samples,
         cfg.duration_tolerance_percent, cfg.use_audio, cfg.audio_min_similarity]
    )


def _compare_delta_item(i: int, d: dict, cfg) -> tuple[list[dict], int]:
    """BLOCKING numpy work for ONE delta file against its duration-compatible
    candidates. Pure function of the arrays in `d` (no DB, no app state), so
    it is safe to run in a worker thread. Returns (matches, rejected_by_audio)."""
    durs = d["durs"]
    tol = cfg.duration_tolerance_percent
    lo = np.searchsorted(durs, durs[i] - max(2.0, tol / 100.0 * durs[i]), side="left")
    hi = np.searchsorted(durs, dupfinder.duration_window(float(durs[i]), tol), side="right")
    cand = np.arange(lo, hi)
    cand = cand[cand != i]
    # Each unordered pair once: when both are delta items, only the lower index owns it.
    cand = cand[~(d["delta_mask"][cand] & (cand < i))]
    if cand.size:
        keep = np.abs(durs[cand] - durs[i]) <= np.maximum(2.0, tol / 100.0 * np.maximum(durs[cand], durs[i]))
        cand = cand[keep]
    if not cand.size:
        return [], 0

    results = dupfinder.compare_one_to_many(
        d["frames"][i], d["valid"][i], d["phash"][i],
        d["frames"][cand], d["valid"][cand], d["phash"][cand],
        algorithm=cfg.algorithm, percent=cfg.percent, phash_quorum=cfg.phash_quorum,
    )
    matches: list[dict] = []
    rejected = 0
    for j, res in zip(cand.tolist(), results):
        if d["size"][i] == d["size"][j] and d["qhash"][i] == d["qhash"][j]:
            matches.append({"a": i, "b": j, "pct": 100.0, "verdict": "identical", "audio": None, "details": None})
            continue
        if res is None or not res["matched"]:
            continue
        audio = None
        fa, fb = d["audio"].get(i), d["audio"].get(j)
        if cfg.use_audio and fa is not None and fb is not None:
            audio = dupfinder.audio_similarity(fa, fb)
            if audio < cfg.audio_min_similarity:
                rejected += 1  # same footage, different soundtrack
                continue
        matches.append({
            "a": i, "b": j, "pct": res["similarity_pct"], "verdict": "duplicate", "audio": audio,
            "details": {
                "algorithm": cfg.algorithm, "gray_pct": res["gray_pct"], "phash_pct": res["phash_pct"],
                "positions": res["positions"], "valid_positions": res["valid_positions"],
                "duration_diff": round(abs(float(durs[i]) - float(durs[j])), 2),
            },
        })
    return matches, rejected


def _plan_sampling(done_items, stamps: dict, failures: dict, cfg) -> tuple[list, dict]:
    """Which videos need (re)reading, and why. Everything else reuses its
    stored fingerprint, so a routine scan only reads new or changed files.
    BLOCKING (one stat() per file)."""
    to_sample: list = []
    reasons = {"new": 0, "changed": 0, "settings": 0, "audio_retry": 0}
    for item in done_items:
        try:
            st = Path(item.video_path).stat()
        except OSError:
            continue
        known_bad = failures.get(item.id)
        if known_bad and known_bad["size"] == st.st_size and known_bad["mtime"] == st.st_mtime \
                and known_bad.get("samples") == cfg.samples:
            continue
        s = stamps.get(item.id)
        if s is None:
            reason = "new"
        elif s["source_size"] != st.st_size or s["source_mtime"] != st.st_mtime:
            reason = "changed"
        elif s["algo_version"] != dupfinder.ALGO_VERSION or s["num_samples"] != cfg.samples:
            reason = "settings"
        elif cfg.use_audio and s["audio_status"] == "skipped":
            reason = "audio_retry"
        else:
            continue
        reasons[reason] += 1
        to_sample.append((item, st))
    return to_sample, reasons


DUPLICATE_TRIGGER_DELAY = 60  # seconds after the last download/re-encode/repair


class DuplicateWorkerMixin:
    _dup_plan_cache: tuple = (0.0, None)

    _dup_trigger_task = None
    _dup_trigger_events: set = set()
    _dup_trigger_scanning = False   # the delayed task is running its scan now (not waiting)
    _dup_rescan_after = False       # more events came in during that scan: one more scan after it

    def _duplicate_scan_triggers(self) -> dict[str, bool]:
        """Which events start an automatic scan (Server Settings -> Duplicate
        scan), read fresh so a change applies at once."""
        cfg = get_settings().duplicate_scan
        return {"download": cfg.scan_after_download, "reencode": cfg.scan_after_reencode, "repair": cfg.scan_after_repair}

    def request_duplicate_scan_after(self, event: str) -> None:
        """Called when a download, re-encode or repair finishes. If that
        event's flag is on, a scan starts DUPLICATE_TRIGGER_DELAY seconds
        after the LAST such event (a batch of downloads starts one scan, not
        one each). If a scan or another file-changing job is running then,
        it waits and tries again."""
        if not self._duplicate_scan_triggers().get(event):
            return
        self._dup_trigger_events = {*self._dup_trigger_events, event}
        if self._dup_trigger_task and not self._dup_trigger_task.done():
            if self._dup_trigger_scanning:
                # 3.2.0: never cancel a scan that's already running (that showed as
                # "Interrupted: Duplicate scan (after download) -- CancelledError" when
                # a download finished during the scan for the one before): let it
                # finish, then scan once more for what came in meanwhile.
                self._dup_rescan_after = True
                return
            self._dup_trigger_task.cancel()  # still waiting: start the wait again
        try:
            self._dup_trigger_task = asyncio.get_running_loop().create_task(self._delayed_duplicate_scan())
        except RuntimeError:  # no event loop (tests / shutdown)
            pass

    async def _delayed_duplicate_scan(self) -> None:
        try:
            while True:
                await asyncio.sleep(DUPLICATE_TRIGGER_DELAY)
                if self._shutting_down:
                    return
                if self._duplicate_scan_running:
                    continue
                events = ", ".join(sorted(self._dup_trigger_events)) or "event"
                self._dup_trigger_events = set()
                self._dup_rescan_after = False
                self._dup_trigger_scanning = True
                try:
                    logger.info("Automatic duplicate scan (after {})", events)
                    await self.run_duplicate_scan(title=f"Duplicate scan (after {events})", source="auto")
                except JobBlockedError as exc:
                    logger.debug("Automatic duplicate scan postponed: {}", exc)
                    self._dup_trigger_events |= set(events.split(", "))
                    continue
                except Exception:
                    logger.exception("Automatic duplicate scan failed")
                finally:
                    self._dup_trigger_scanning = False
                if self._dup_rescan_after and not self._shutting_down:
                    continue  # videos arrived during the scan: one more (after the delay)
                return
        except asyncio.CancelledError:
            return

    # ---------------------------------------------------------- notifications (3.2.0)
    _NOTIFIED_KEY = "dup_notified_pairs"
    DUP_NOTIFY_ONE_BY_ONE = 5   # more new pairs than this in one scan: one summary instead

    def _known_duplicate_pairs(self) -> set[str]:
        """Pairs already reported (or there before 3.2.0, so an update doesn't
        announce every old pair). Kept apart from duplicate_matches, which a
        full re-compare empties."""
        known = self.db.get_app_state(self._NOTIFIED_KEY, None)
        if known is None:
            known = [m["id"] for m in self.db.list_duplicate_matches()]
            self.db.set_app_state(self._NOTIFIED_KEY, known)
        return set(known)

    def _announce_new_duplicates(self, pairs: list[tuple[str, str, float]]) -> None:
        """A notification (category "duplicate", for the admins) per pair no scan
        found before -- except pairs in a group marked "not duplicates"; more
        than DUP_NOTIFY_ONE_BY_ONE at once: one summary."""
        try:
            known = self._known_duplicate_pairs()
            ignored = [set(e["item_ids"]) for e in self.db.list_ignored_issues(issue_type="duplicate")]
            new = []
            for a, b, pct in pairs:
                key = ":".join(sorted((a, b)))
                if key in known or any({a, b} <= group for group in ignored):
                    continue
                if a in self._items and b in self._items:
                    new.append((a, b, pct))
                known.add(key)
            # forget pairs whose videos are gone, so the list doesn't grow for ever
            known = {k for k in known if all(i in self._items for i in k.split(":"))}
            self.db.set_app_state(self._NOTIFIED_KEY, sorted(known))
            if not new:
                return
            if len(new) > self.DUP_NOTIFY_ONE_BY_ONE:
                notifications.notify("duplicate", "warning", f"{len(new)} possible duplicates found",
                                     "Compare them side by side and keep the best copy.", link="/admin/duplicates")
                return
            for a, b, pct in new:
                newer, older = sorted((self._items[a], self._items[b]), key=lambda i: i.created_at, reverse=True)
                notifications.notify("duplicate", "warning", f"Possible duplicate: {title_of(newer)}",
                                     f"Looks like {title_of(older)} ({pct:.0f}% match).", link="/admin/duplicates")
        except Exception:  # noqa: BLE001 -- never fail a scan over its notifications
            logger.opt(exception=True).warning("Couldn't announce the new duplicates")

    def request_duplicate_scan_now(self) -> None:
        """Fire-and-forget trigger -- starts run_duplicate_scan() as a
        background task without blocking the caller. Called by the
        download/re-encode workers right after they finish (gated by
        request_duplicate_scan_after()) and by the
        Admin Portal's manual "Run scan now" button. A Scheduler-
        configured "Duplicate scan" job calls run_duplicate_scan()
        directly instead, since the scheduler needs to track its own
        run-history row around the call."""
        logger.debug("Duplicate scan requested")
        asyncio.create_task(self._run_duplicate_scan_safely())

    async def _run_duplicate_scan_safely(self) -> None:
        try:
            await self.run_duplicate_scan()
        except JobBlockedError as exc:
            # Expected contention (another invasive operation, e.g. a
            # bulk repair, is already running) -- not a real failure.
            logger.info("Duplicate scan not started: {}", exc)
        except Exception:
            logger.exception("Background duplicate scan failed")

    def get_duplicate_scan_status(self) -> dict:
        last_run = self.db.get_app_state("duplicate_scan_last_run_at", None)
        done_items = [i for i in self._items.values() if i.status == "done"]
        stamps = self.db.list_dup_sample_stamps()
        failures = self.db.get_app_state("dup_sample_failures", {}) or {}
        pending = [i for i in done_items if i.id not in stamps and i.id not in failures]
        return {
            "running": self._duplicate_scan_running,
            "last_run_at": last_run,
            "total_done_items": len(done_items),
            "pending_backfill": len(pending),
            "unusable": len([i for i in done_items if i.id in failures]),
            "auto_triggers": self._duplicate_scan_triggers(),
            "auto_pending": bool(self._dup_trigger_task and not self._dup_trigger_task.done()),
            "last_result": self.db.get_app_state("duplicate_scan_last_result", None),
            **self._duplicate_scan_progress,
        }

    async def get_duplicate_scan_plan(self) -> dict:
        """How many videos the next scan will read (new or changed files);
        cached for a minute since it stats every file."""
        at, cached = self._dup_plan_cache
        if cached is not None and time.time() - at < 60:
            return cached
        cfg = get_settings().duplicate_scan
        done_items = [i for i in self._items.values() if i.status == "done" and i.video_path]
        failures = self.db.get_app_state("dup_sample_failures", {}) or {}
        to_sample, reasons = await asyncio.to_thread(
            _plan_sampling, done_items, self.db.list_dup_sample_stamps(), failures, cfg)
        plan = {"to_read": len(to_sample), "total": len(done_items), **reasons,
                "full_compare": self.db.get_app_state("dup_compare_signature", None) != _compare_signature(cfg)}
        self._dup_plan_cache = (time.time(), plan)
        return plan

    async def _migrate_legacy_duplicate_data(self) -> None:
        """One-time cleanup when upgrading from the old sliding-window pHash
        scanner. Its matches were unreliable (flat/dark frames scored 100%)
        and its 48x8x8-bit hash sequences cannot be converted into the new
        32x32 samples, so both are dropped. `local_phash` (Stash-style) is
        untouched -- it is a separate feature."""
        cleared = 0
        async with self._lock:
            for item in self._items.values():
                if item.dup_phash_sequence or item.dup_phash_source_hash:
                    item.dup_phash_sequence = []
                    item.dup_phash_generated_at = None
                    item.dup_phash_source_hash = None
                    item.dup_phash_source_size = None
                    item.dup_phash_source_mtime = None
                    self.db.save_item(item)
                    cleared += 1
        self.db.clear_duplicate_matches()
        self.db.set_app_state("dup_algo_version", dupfinder.ALGO_VERSION)
        logger.info("Duplicate finder upgrade: discarded old matches and legacy hashes of {} item(s)", cleared)

    async def run_duplicate_scan(self, verbose: bool = False, full: bool = False, title: str = "Duplicate scan",
                                 source: str = "manual") -> dict:
        """The scan, shown on the Tasks page while it runs (see _duplicate_scan)."""
        if self._duplicate_scan_running:
            raise JobBlockedError("Blocked: a duplicate scan is already running")
        if self._invasive_lock_holder is not None:
            raise JobBlockedError(f"Blocked: '{self._invasive_lock_holder}' is already running")
        # The pairs known before this scan (3.2.0): on the first scan with duplicate
        # notifications, every pair already found counts as known -- read before the
        # scan stores its own new ones (or a full re-compare empties the table).
        self._known_duplicate_pairs()
        async with activity.track("duplicate_scan", title, progress=self._duplicate_scan_activity, source=source,
                                  stop=True) as act:
            result = await self._duplicate_scan(verbose, full)
            act.summary = act.summary or f"read {result['hashed']} · {result['matches_found']} matches · {result['elapsed_seconds']}s"
            return result

    def _duplicate_scan_activity(self) -> dict:
        p = self._duplicate_scan_progress
        if p.get("phase") == "hashing":
            return {"stage": "Reading videos", "done": p["hashed"], "total": p["hash_total"]}
        if p.get("phase") == "comparing":
            return {"stage": "Comparing videos", "done": p["compared"], "total": p["compare_total"]}
        return {"stage": "Preparing"}

    async def _duplicate_scan(self, verbose: bool = False, full: bool = False) -> dict:
        """Duplicate detection in two phases (see backend/dupfinder.py).

        Phase 1 -- sampling: every completed item whose file is new or
        changed (size/mtime) or was sampled under different settings gets
        N 32x32 grayscale frames at fixed relative positions plus an audio
        fingerprint, stored in the dup_samples table. Files are sampled
        `duplicate_scan.workers` at a time, each in a worker thread. Files
        without enough informative frames (black/flat/undecodable) are
        recorded as "unusable" and not retried until they change.

        Phase 2 -- comparison: each newly sampled file (every file if
        `full`, or if the match-relevant settings changed since the last
        completed scan) is compared against duration-compatible files, in a
        worker thread per file with vectorised numpy. Visual matches are
        confirmed or vetoed by audio.

        `verbose=True` (Scheduler job_config {"verbose_logging": true})
        returns per-item/per-match lines in the result's "verbose_log".

        MUTUAL EXCLUSION: raises JobBlockedError immediately if this is
        already running or another invasive operation holds the lock."""
        if self._duplicate_scan_running:
            raise JobBlockedError("Blocked: a duplicate scan is already running")
        if not self._try_acquire_invasive_lock("duplicate_scan"):
            raise JobBlockedError(f"Blocked: '{self._invasive_lock_holder}' is already running")

        self._duplicate_scan_running = True
        started = time.time()
        cfg = get_settings().duplicate_scan
        verbose_log: list[str] | None = [] if verbose else None
        try:
            if self.db.get_app_state("dup_algo_version", None) != dupfinder.ALGO_VERSION:
                await self._migrate_legacy_duplicate_data()
            signature = _compare_signature(cfg)
            if full or self.db.get_app_state("dup_compare_signature", None) != signature:
                logger.info("Duplicate scan: settings changed or full rescan requested -- re-comparing everything")
                self.db.clear_duplicate_matches()
                full = True

            # ---- Phase 1: sampling ----
            done_items = [i for i in self._items.values() if i.status == "done" and i.video_path]
            stamps = self.db.list_dup_sample_stamps()
            failures: dict = self.db.get_app_state("dup_sample_failures", {}) or {}
            to_sample, reasons = await asyncio.to_thread(_plan_sampling, done_items, stamps, failures, cfg)
            logger.info(
                "Duplicate scan: reading {} of {} video(s) ({} new, {} changed, {} after a settings/version change, "
                "{} audio retry); the rest reuse stored fingerprints{}",
                len(to_sample), len(done_items), reasons["new"], reasons["changed"], reasons["settings"],
                reasons["audio_retry"], " -- re-comparing all pairs" if full else "",
            )

            self._duplicate_scan_progress = {
                "phase": "hashing", "hashed": 0, "hash_total": len(to_sample), "compared": 0, "compare_total": 0,
            }
            sampled_ids: set[str] = set()
            sem = asyncio.Semaphore(cfg.workers)

            async def sample_one(item, st) -> None:
                async with sem:
                    if self._shutting_down:
                        return
                    try:
                        sample = await asyncio.to_thread(
                            dupfinder.sample_file, item.video_path, cfg.samples, cfg.use_audio
                        )
                    except Exception as exc:  # noqa: BLE001
                        failures[item.id] = {"size": st.st_size, "mtime": st.st_mtime,
                                             "samples": cfg.samples, "error": str(exc)[:200]}
                        logger.warning("Duplicate scan: skipping {} -- {}", item.id, exc)
                        if verbose:
                            verbose_log.append(f"Unusable '{item.title or item.url}' [{item.id}]: {exc}")
                    else:
                        if item.id in self._items:
                            self.db.save_dup_sample(item.id, sample)
                            failures.pop(item.id, None)
                            sampled_ids.add(item.id)
                            if verbose:
                                verbose_log.append(
                                    f"Sampled '{item.title or item.url}' [{item.id}] "
                                    f"({sum(sample['valid'])}/{cfg.samples} frames, audio {sample['audio_status']})"
                                )
                    self._duplicate_scan_progress["hashed"] += 1
                    n = self._duplicate_scan_progress["hashed"]
                    if n % HASH_LOG_EVERY_N == 0:
                        logger.info("Duplicate scan: sampled {}/{} file(s) so far\u2026", n, len(to_sample))

            await asyncio.gather(*(sample_one(item, st) for item, st in to_sample))
            self.db.set_app_state("dup_sample_failures", failures)

            # ---- Phase 2: comparison ----
            live_ids = {i.id for i in done_items}
            rows = [
                r for r in self.db.load_dup_samples()
                if r["item_id"] in live_ids and r["algo_version"] == dupfinder.ALGO_VERSION
                and r["num_samples"] == cfg.samples
            ]
            rows.sort(key=lambda r: r["duration"])
            n, t = cfg.samples, len(rows)
            matches_found = rejected_audio = 0
            found_pairs: list[tuple[str, str, float]] = []
            if t:
                ids = [r["item_id"] for r in rows]
                frames = np.empty((t, n, dupfinder.FRAME_BYTES), dtype=np.uint8)
                valid = np.empty((t, n), dtype=bool)
                for k, r in enumerate(rows):
                    frames[k] = np.frombuffer(r["frames"], dtype=np.uint8).reshape(n, dupfinder.FRAME_BYTES)
                    valid[k] = np.frombuffer(r["valid"], dtype=np.uint8).astype(bool)
                phash = (dupfinder.phash_frames(frames) if cfg.algorithm != "grayscale"
                         else np.zeros((t, n), dtype=np.uint64))
                data = {
                    "frames": frames, "valid": valid, "phash": phash,
                    "durs": np.array([r["duration"] for r in rows], dtype=np.float64),
                    "size": [r["source_size"] for r in rows], "qhash": [r["quick_hash"] for r in rows],
                    "audio": {k: np.frombuffer(r["audio_fp"], dtype="<u4") for k, r in enumerate(rows)
                              if r["audio_status"] == "ok" and r["audio_fp"]},
                }
                delta = [k for k, i in enumerate(ids) if full or i in sampled_ids]
                data["delta_mask"] = np.zeros(t, dtype=bool)
                data["delta_mask"][delta] = True
                if not full:
                    for k in delta:
                        self.db.delete_duplicate_matches_for_item(ids[k])  # file changed: old edges are stale

                self._duplicate_scan_progress.update(phase="comparing", compare_total=len(delta), compared=0)
                titles = {i.id: (i.title or i.url) for i in done_items}
                for k in delta:
                    if self._shutting_down:
                        break
                    found, rej = await asyncio.to_thread(_compare_delta_item, k, data, cfg)
                    rejected_audio += rej
                    for m in found:
                        self.db.upsert_duplicate_match(
                            ids[m["a"]], ids[m["b"]], m["pct"], m["verdict"], None, m["audio"], m["details"]
                        )
                        matches_found += 1
                        found_pairs.append((ids[m["a"]], ids[m["b"]], m["pct"]))
                        if verbose:
                            verbose_log.append(
                                f"{m['verdict'].capitalize()} match: '{titles.get(ids[m['a']])}' [{ids[m['a']]}] "
                                f"<-> '{titles.get(ids[m['b']])}' [{ids[m['b']]}] ({m['pct']:.1f}%"
                                + (f", audio {m['audio']:.0f}%" if m["audio"] is not None else "") + ")"
                            )
                    self._duplicate_scan_progress["compared"] += 1

            elapsed = round(time.time() - started, 1)
            if not self._shutting_down:
                self._announce_new_duplicates(found_pairs)
                self.db.set_app_state("dup_compare_signature", signature)
                self.db.set_app_state("duplicate_scan_last_run_at", time.time())
                self.db.set_app_state("duplicate_scan_last_result", {
                    "read": len(sampled_ids), "of": len(done_items), "matches_found": matches_found,
                    "elapsed_seconds": elapsed, "full_compare": full,
                })
            self._dup_plan_cache = (0.0, None)
            logger.info(
                "Duplicate scan complete: {} sampled, {} unusable, {} pair(s) matched, {} vetoed by audio, {}s",
                len(sampled_ids), len(failures), matches_found, rejected_audio, elapsed,
            )
            result = {
                "hashed": len(sampled_ids), "unusable": len(failures), "matches_found": matches_found,
                "rejected_by_audio": rejected_audio, "elapsed_seconds": elapsed,
            }
            if verbose:
                result["verbose_log"] = verbose_log
            return result
        finally:
            self._duplicate_scan_running = False
            self._duplicate_scan_progress = {
                "phase": None, "hashed": 0, "hash_total": 0, "compared": 0, "compare_total": 0,
            }
            self._release_invasive_lock("duplicate_scan")
