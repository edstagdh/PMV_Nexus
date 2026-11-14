"""
Generic background-job Scheduler -- runs ANY registered job type
(duplicate scan, library repair, and future types) on a schedule an
admin configures via the Admin Portal's Scheduler page, or immediately
via "Run now" regardless of that schedule.

Deliberately implemented as its own asyncio background task -- NOT a
real OS thread. Every other worker in this app already achieves
independent, isolated concurrency this way, all cooperating inside one
single event loop -- a real OS thread would force self._lock (an
asyncio.Lock, which only works correctly within one event loop), the
single sqlite3 connection, and the shared self._items dict to all become
genuinely thread-safe, a real ongoing complexity/risk cost for no actual
benefit here.

JOB_REGISTRY is the single place a new job type gets wired in: a label
for the UI, the name of an async method on LibraryManager that does the
actual work, and a `category` -- "invasive" (writes/modifies data
anywhere; mutually exclusive with every other invasive job, regardless
of type -- see CoreMixin._try_acquire_invasive_lock()) or "non_invasive"
(read-only or a no-op placeholder; never contends with anything).
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger

from .. import activity
from .core import JobBlockedError

try:
    from croniter import croniter
except ImportError:  # pragma: no cover -- see requirements.txt
    croniter = None

SCHEDULER_TICK_SECONDS = 30.0
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

JOB_REGISTRY: dict[str, dict] = {
    "duplicate_scan": {
        "label": "Duplicate scan",
        "method": "run_duplicate_scan",  # see library_mixins/duplicate_worker.py
        "category": "invasive",
    },
    "library_repair": {
        "label": "Library repair (all videos)",
        "method": "_run_scheduled_library_repair",
        "category": "invasive",
    },
    "rss_feed_update": {
        "label": "RSS feed update (placeholder)",
        "method": "_run_scheduled_rss_feed_update",
        "category": "non_invasive",
    },
    "stats_snapshot": {
        "label": "Capture stats snapshot",
        "method": "capture_stats_snapshot",  # see library_mixins/stats_worker.py
        "category": "non_invasive",
    },
    "update_check": {
        "label": "Check for app updates",
        "method": "run_update_check",  # see library_mixins/system_worker.py
        "category": "non_invasive",
    },
    "site_sync": {
        "label": "Sync library with PMVHaven",
        "method": "run_site_sync",  # see library_mixins/site_sync_worker.py
        "category": "invasive",
    },
    "performer_sync": {
        "label": "Match & refresh performers (ThePornDB)",
        "method": "run_performer_sync",  # see library_mixins/performers.py
        "category": "non_invasive",
    },
    "backup_test": {
        "label": "Restore test of the newest backup",
        "method": "run_backup_test",  # see library_mixins/system_worker.py
        "category": "non_invasive",
    },
    "song_scan": {
        "label": "Find songs in videos without music",
        "method": "run_song_scan_job",  # see library_mixins/songs.py
        "category": "non_invasive",
    },
    "audio_fix": {
        "label": "Make audio playable (convert audio browsers can't play to AAC)",
        "method": "run_audio_fix",  # see library_mixins/audio_fix.py (3.2.0)
        "category": "invasive",
    },
    "backup": {
        "label": "Backup database + settings",
        "method": "run_backup",  # see library_mixins/system_worker.py
        "category": "non_invasive",
    },
}


def _parse_hhmm(time_str: str) -> tuple[int, int]:
    try:
        hh_str, mm_str = time_str.split(":")
        return int(hh_str), int(mm_str)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid time '{time_str}' -- expected HH:MM") from exc


def _compute_next_run_at(schedule_mode: str, schedule_config: dict, from_time: Optional[float] = None) -> float:
    now = from_time if from_time is not None else time.time()

    if schedule_mode == "advanced":
        if croniter is None:
            raise RuntimeError(
                "croniter is not installed -- run `pip install croniter` to use Advanced (cron) schedules"
            )
        expression = schedule_config.get("expression", "")
        itr = croniter(expression, datetime.fromtimestamp(now))
        return itr.get_next(datetime).timestamp()

    kind = schedule_config.get("kind")
    dt_now = datetime.fromtimestamp(now)

    if kind == "once":
        # One time (3.2.0), at a local date and time ("2026-11-14T02:00"); a time
        # that has passed runs at the next check. After it ran, the job is switched off.
        try:
            return datetime.fromisoformat(str(schedule_config.get("at") or "")).timestamp()
        except ValueError as exc:
            raise ValueError(f"Invalid date and time '{schedule_config.get('at')}' -- expected YYYY-MM-DDTHH:MM") from exc

    if kind == "interval_hours":
        hours = float(schedule_config.get("hours", 24))
        return now + hours * 3600

    if kind == "daily":
        hh, mm = _parse_hhmm(schedule_config.get("time", "03:00"))
        candidate = dt_now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate <= dt_now:
            candidate += timedelta(days=1)
        return candidate.timestamp()

    if kind == "weekly":
        hh, mm = _parse_hhmm(schedule_config.get("time", "03:00"))
        day_name = (schedule_config.get("day") or "monday").lower()
        if day_name not in WEEKDAYS:
            raise ValueError(f"Unknown weekday: {day_name}")
        target_idx = WEEKDAYS.index(day_name)
        candidate = dt_now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        days_ahead = (target_idx - dt_now.weekday()) % 7
        candidate += timedelta(days=days_ahead)
        if candidate <= dt_now:
            candidate += timedelta(days=7)
        return candidate.timestamp()

    raise ValueError(f"Unknown schedule kind: {kind}")


class SchedulerWorkerMixin:

    def _ensure_scheduler_worker(self) -> None:
        if self._scheduler_task is None or self._scheduler_task.done():
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def _scheduler_loop(self) -> None:
        while True:
            if self._shutting_down:
                return
            try:
                await self._scheduler_tick()
            except Exception:
                logger.exception("Scheduler tick failed")
            await asyncio.sleep(SCHEDULER_TICK_SECONDS)

    async def _scheduler_tick(self) -> None:
        jobs = self.db.list_scheduled_jobs()
        now = time.time()
        for job in jobs:
            if not bool(job.get("enabled", 1)):
                continue
            if job["id"] in self._scheduler_active_jobs:
                continue
            next_run_at = job.get("next_run_at")
            if next_run_at is None or now >= next_run_at:
                task = asyncio.create_task(self._execute_scheduled_job(job, trigger="schedule"))
                self._scheduler_active_jobs[job["id"]] = task

    async def _execute_scheduled_job(self, job: dict, trigger: str) -> None:
        """Runs one job to completion (or until cancelled/blocked),
        writing a scheduled_job_runs row the whole way. Three distinct
        terminal statuses beyond plain success/failure:
          - "interrupted": the server shut down mid-run.
          - "blocked": this job's own runner raised JobBlockedError
            because a DIFFERENT invasive operation was already running
            (see CoreMixin._try_acquire_invasive_lock()) -- expected
            contention, not a real error, so it's logged at info level
            rather than with a full exception traceback.
          - "failed": anything else went wrong."""
        job_id = job["id"]
        job_type = job["job_type"]
        name = job.get("name") or job_type
        registry_entry = JOB_REGISTRY.get(job_type)
        run_id = self.db.create_job_run(job_id, trigger)
        logger.info("Scheduler: starting job '{}' ({}) [{}]", name, job_type, trigger)

        job_config_raw = job.get("job_config") or "{}"
        job_config = json.loads(job_config_raw) if isinstance(job_config_raw, str) else job_config_raw

        status = "failed"
        summary: Optional[str] = None
        result = None
        # Shown on the Tasks page under the job's name; the job's own
        # activity.track() fills in its progress (see backend/activity.py).
        act = activity.start(job_type, name, source="schedule" if trigger == "schedule" else "manual", job_id=job_id)
        act_token = activity._current.set(act)
        try:
            if not registry_entry:
                raise ValueError(f"Unknown job type: {job_type}")
            method = getattr(self, registry_entry["method"])
            # Only duplicate_scan currently reads a job_config option
            # (verbose_logging) -- every other registered job type's
            # runner method takes no arguments at all.
            if job_type == "duplicate_scan":
                result = await method(verbose=job_config.get("verbose_logging", False))
            else:
                result = await method()
            status = "cancelled" if act.stopped_by else "interrupted" if self._shutting_down else "success"
            summary = (f"Stopped by {act.stopped_by}" if act.stopped_by
                       else json.dumps(result) if isinstance(result, dict) else str(result))
        except asyncio.CancelledError:
            if act.stopped_by:  # the Stop button (Tasks / Scheduler page): this run ends here
                status, summary = "cancelled", f"Stopped by {act.stopped_by}"
            else:
                status, summary = "interrupted", "Cancelled by server shutdown"
                raise
        except JobBlockedError as exc:
            status = "blocked"
            summary = str(exc)
            logger.info("Scheduler: job '{}' ({}) blocked: {}", name, job_id, exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Scheduler: job '{}' ({}) failed", name, job_id)
            status = "failed"
            summary = str(exc)
        finally:
            activity._current.reset(act_token)
            act.finish(status, activity.summarize(result) if status == "success" else summary)
            self.db.finish_job_run(run_id, status, summary)
            schedule_config = job["schedule_config"]
            if isinstance(schedule_config, str):
                schedule_config = json.loads(schedule_config)
            enabled = bool(job.get("enabled", 1))
            next_run_at = None
            once = job["schedule_mode"] == "simple" and schedule_config.get("kind") == "once"
            if once and enabled:
                # One-time job: done once it ran; tried again later when it couldn't run.
                if status == "blocked":
                    next_run_at = time.time() + 1800
                elif status == "interrupted":
                    next_run_at = time.time() + 300
                else:
                    enabled = False
            elif enabled:
                try:
                    next_run_at = _compute_next_run_at(job["schedule_mode"], schedule_config)
                except Exception:
                    logger.warning("Scheduler: could not compute next run time for job {}", job_id)
            fields = {"last_run_at": time.time(), "last_run_status": status, "next_run_at": next_run_at}
            if once and not enabled:
                fields["enabled"] = False
            self.db.update_scheduled_job(job_id, fields)
            self._scheduler_active_jobs.pop(job_id, None)
            logger.info("Scheduler: job '{}' ({}) finished with status '{}'", name, job_id, status)

    async def _shutdown_scheduler(self) -> None:
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()

        active = dict(self._scheduler_active_jobs)
        if not active:
            logger.info("Scheduler shutdown: no jobs were running")
            return

        logger.warning(
            "Scheduler shutdown: {} job(s) still running -- cancelling and logging as interrupted: {}",
            len(active), list(active.keys()),
        )
        for task in active.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*active.values(), return_exceptions=True)
        logger.info("Scheduler shutdown complete -- {} job(s) marked interrupted in run history", len(active))

    # ------------------------------------------------------- public API
    def _serialize_job(self, row: dict) -> dict:
        return {
            **row,
            "enabled": bool(row["enabled"]),
            "schedule_config": json.loads(row["schedule_config"]),
            "job_config": json.loads(row.get("job_config") or "{}"),
            "running": row["id"] in self._scheduler_active_jobs,
            "progress": activity.for_job(row["id"]) if row["id"] in self._scheduler_active_jobs else None,
            "system": bool(row.get("system_key")),
        }

    def list_scheduled_jobs(self) -> list[dict]:
        return [self._serialize_job(row) for row in self.db.list_scheduled_jobs()]

    def create_scheduled_job(
            self, job_type: str, name: str, enabled: bool, schedule_mode: str, schedule_config: dict,
            job_config: Optional[dict] = None,
    ) -> dict:
        if job_type not in JOB_REGISTRY:
            raise ValueError(f"Unknown job type: {job_type}")
        next_run_at = _compute_next_run_at(schedule_mode, schedule_config) if enabled else None
        job_id = str(uuid.uuid4())
        now = time.time()
        self.db.create_scheduled_job({
            "id": job_id, "job_type": job_type, "name": name, "enabled": enabled,
            "schedule_mode": schedule_mode, "schedule_config": json.dumps(schedule_config),
            "job_config": json.dumps(job_config or {}),
            "created_at": now, "updated_at": now, "last_run_at": None, "last_run_status": None,
            "next_run_at": next_run_at,
        })
        logger.info("Scheduler: created job '{}' ({}) -- next run at {}", name, job_type, next_run_at)
        return self._serialize_job(self.db.get_scheduled_job(job_id))

    def update_scheduled_job(self, job_id: str, updates: dict) -> dict:
        existing = self.db.get_scheduled_job(job_id)
        if not existing:
            raise ValueError("Job not found")

        merged_mode = updates.get("schedule_mode", existing["schedule_mode"])
        merged_config = updates.get("schedule_config", json.loads(existing["schedule_config"]))
        merged_enabled = updates.get("enabled", bool(existing["enabled"]))

        fields = dict(updates)
        fields["schedule_config"] = json.dumps(merged_config)
        if "job_config" in fields:
            fields["job_config"] = json.dumps(fields["job_config"])
        fields["updated_at"] = time.time()
        fields["next_run_at"] = _compute_next_run_at(merged_mode, merged_config) if merged_enabled else None

        self.db.update_scheduled_job(job_id, fields)
        logger.info("Scheduler: updated job '{}'", job_id)
        return self._serialize_job(self.db.get_scheduled_job(job_id))

    def delete_scheduled_job(self, job_id: str) -> None:
        if job_id in self._scheduler_active_jobs:
            raise ValueError("Can't delete a job while it's running -- wait for it to finish first")
        existing = self.db.get_scheduled_job(job_id)
        if not existing:
            raise ValueError("Job not found")
        if existing.get("system_key"):
            raise ValueError("This is a built-in system job -- it can be disabled, but not deleted")
        self.db.delete_scheduled_job(job_id)
        logger.info("Scheduler: deleted job '{}' ({})", existing["name"], job_id)

    def request_scheduled_job_run_now(self, job_id: str) -> None:
        job = self.db.get_scheduled_job(job_id)
        if not job:
            raise ValueError("Job not found")
        if job_id in self._scheduler_active_jobs:
            raise ValueError("This job is already running")
        logger.info("Scheduler: manual run requested for job '{}' ({})", job["name"], job_id)
        task = asyncio.create_task(self._execute_scheduled_job(job, trigger="manual"))
        self._scheduler_active_jobs[job_id] = task

    def activity_overview(self) -> dict:
        """Tasks page: library-wide work running now (backend/activity.py plus
        uploads), what finished recently, and the next Scheduler runs."""
        now = time.time()
        upcoming = sorted(
            ({"job_id": j["id"], "name": j["name"], "kind": j["job_type"], "next_run_at": j["next_run_at"]}
             for j in self.db.list_scheduled_jobs()
             if j.get("enabled") and j.get("next_run_at") and j["id"] not in self._scheduler_active_jobs),
            key=lambda j: j["next_run_at"])
        return {
            "running": activity.running() + self.upload_activities(),
            "finished": [a for a in activity.finished() if now - (a["finished_at"] or 0) < 86400],
            "upcoming": upcoming[:6],
            "auto_duplicate_scan": bool(self._dup_trigger_task and not self._dup_trigger_task.done()
                                        and not self._duplicate_scan_running),
            "server_time": now,
        }

    def get_job_run_history(self, job_id: str, limit: int = 50) -> list[dict]:
        return self.db.list_job_runs(job_id, limit)

    # ------------------------------------------------------- job runners
    async def _run_scheduled_library_repair(self) -> dict:
        """Scheduler-triggered library-repair job runner. Uses the SAME
        invasive-operation lock as start_guarded_repair_all() (the Admin
        Portal button's own entry point) -- both funnel through
        CoreMixin._try_acquire_invasive_lock("library_repair"), so a
        scheduled repair and a manually-triggered one via the button
        correctly exclude each other too, not just against duplicate
        scan. Raises JobBlockedError (caught specially by
        _execute_scheduled_job() above) if something else invasive is
        already running."""
        if not self._try_acquire_invasive_lock("library_repair"):
            raise JobBlockedError(f"Blocked: '{self._invasive_lock_holder}' is already running")
        started = time.time()
        try:
            queued = self.request_repair_all(max_concurrent=self._repair_max_concurrent or 1)
            async with activity.track("library_repair", "Repairing all videos",
                                      progress=lambda: self._repair_all_activity(queued), stop=self._stop_repair_all):
                await self._wait_for_repairs()
            elapsed = round(time.time() - started, 1)
            return {"queued": queued, "elapsed_seconds": elapsed}
        finally:
            self._release_invasive_lock("library_repair")

    async def _run_scheduled_rss_feed_update(self) -> dict:
        """Placeholder job type -- non-invasive (no real work, no data
        modified yet), so it never touches the invasive lock and can run
        alongside anything. Exists so the Scheduler's job-type picker
        already has a third real, end-to-end-wired example ready for
        actual RSS logic later."""
        logger.info("Scheduler: 'RSS feed update' job ran -- placeholder only, no real work performed yet")
        await asyncio.sleep(0)
        return {"note": "Placeholder job type -- no RSS functionality implemented yet"}
