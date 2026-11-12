"""App-maintenance jobs: update checks and backups (see backend/app_info.py
and backend/backup.py). Both are registered in scheduler_worker.JOB_REGISTRY
and are also triggered from the UI (version label, Backups page)."""
from __future__ import annotations

import asyncio
import json
import time

from loguru import logger

from .. import backup
from ..notifications import notify
from .. import activity
from ..app_info import fetch_latest_version
from ..config import get_settings

UPDATE_STATE_KEY = "update_check"
MANUAL_CHECK_MIN_INTERVAL = 60  # seconds; the button is visible to every user

# Built-in Scheduler jobs. They always exist (re-created if missing), can
# be edited and disabled, but not deleted. (key, job_type, name, schedule)
SYSTEM_JOBS = [
    ("backup", "backup", "Weekly backup", {"kind": "weekly", "day": "sunday", "time": "04:00"}),
    ("update_check", "update_check", "Check for updates", {"kind": "daily", "time": "09:00"}),
    ("duplicate_scan", "duplicate_scan", "Daily duplicate scan", {"kind": "daily", "time": "03:30"}),
    ("performer_sync", "performer_sync", "Match & refresh performers", {"kind": "daily", "time": "05:00"}),
    # 2.11.0: the Stats page's history of disk space and library totals.
    ("stats_snapshot", "stats_snapshot", "Daily stats snapshot", {"kind": "daily", "time": "23:50"}),
]
# Jobs created by 1.1-2.0 ("seeded" once, deletable); adopted as system jobs.
_LEGACY_SEED_FLAGS = {"backup": "seeded_job_backup", "update_check": "seeded_job_update_check"}
_OLD_UPDATE_CHECK_SCHEDULE = {"kind": "weekly", "day": "monday", "time": "09:00"}


class SystemWorkerMixin:

    def seed_default_system_jobs(self) -> None:
        """Makes sure every SYSTEM_JOBS entry exists. Jobs seeded by older
        versions are adopted (keeping the admin's schedule; the update check
        moves from weekly to daily if it was never changed). The daily
        duplicate scan starts disabled only if automatic scanning had been
        switched off explicitly before; likewise performer matching."""
        rows = self.db.list_scheduled_jobs()
        have = {r.get("system_key") for r in rows}
        for key, job_type, name, schedule in SYSTEM_JOBS:
            if key in have:
                continue
            try:
                legacy = None
                if self.db.get_app_state(_LEGACY_SEED_FLAGS.get(key, ""), False):
                    legacy = next((r for r in rows if r["job_type"] == job_type and not r.get("system_key")), None)
                if legacy:
                    fields = {"system_key": key}
                    if key == "update_check" and json.loads(legacy["schedule_config"]) == _OLD_UPDATE_CHECK_SCHEDULE:
                        fields.update(self._schedule_fields(legacy, schedule))
                    self.db.update_scheduled_job(legacy["id"], fields)
                    continue
                enabled = not ((key == "duplicate_scan" and self._dup_auto_was_off())
                               or (key == "performer_sync" and self._perf_auto_was_off()))
                job = self.create_scheduled_job(job_type, name, enabled, "simple", schedule, {})
                self.db.update_scheduled_job(job["id"], {"system_key": key})
                logger.info("Scheduler: added system job '{}'", name)
            except Exception:  # noqa: BLE001
                logger.exception("Could not create system scheduled job '{}'", name)

    def _schedule_fields(self, row: dict, schedule: dict) -> dict:
        from .scheduler_worker import _compute_next_run_at
        nxt = _compute_next_run_at("simple", schedule) if row["enabled"] else None
        return {"schedule_mode": "simple", "schedule_config": json.dumps(schedule), "next_run_at": nxt}

    @staticmethod
    def _dup_auto_was_off() -> bool:
        """True when settings_overrides.json has the pre-2.1 auto_enabled
        flag explicitly set to false."""
        from ..config import _load_overrides
        return _load_overrides().get("duplicate_scan", {}).get("auto_enabled") is False

    @staticmethod
    def _perf_auto_was_off() -> bool:
        """2.7.x had "match in the background" (performers.auto_match); off -> the job starts disabled."""
        from ..config import _load_overrides
        return _load_overrides().get("performers", {}).get("auto_match") is False

    def get_update_status(self) -> dict | None:
        return self.db.get_app_state(UPDATE_STATE_KEY, None)

    async def run_update_check(self, force: bool = True) -> dict:
        last = self.get_update_status()
        if not force and last and time.time() - last.get("checked_at", 0) < MANUAL_CHECK_MIN_INTERVAL:
            return last
        async with activity.track("update_check", "Checking for app updates"):
            result = await fetch_latest_version()
        self.db.set_app_state(UPDATE_STATE_KEY, result)
        if result.get("update_available"):
            logger.info("Update available: {} -> {}", result["current"], result["latest"])
        elif result.get("error"):
            logger.warning("Update check failed: {}", result["error"])
        return result

    async def run_backup(self, include_cache: bool | None = None, include_secrets: bool = False,
                         trigger: str = "scheduled") -> dict:
        settings = get_settings()
        cache = settings.backup.include_cache if include_cache is None else include_cache
        state: dict = {"stage": "Starting"}

        def progress(stage: str, done: int | None = None, total: int | None = None) -> None:
            state.update(stage=stage, done=done, total=total)  # from the backup's worker thread

        async with activity.track("backup", "Creating a backup", progress=lambda: dict(state)) as act:
            summary = await asyncio.to_thread(
                backup.create_backup, settings, include_cache=cache, include_secrets=include_secrets, trigger=trigger,
                progress=progress, password=backup.saved_password(),
            )
            act.summary = f"{summary['name']} ({summary['size_bytes'] / 1048576:.1f} MB)"
            logger.info("Backup created: {} ({} bytes)", summary["name"], summary["size_bytes"])
            # Restore test right away: a backup that can't be restored isn't one.
            test = await asyncio.to_thread(backup.verify_backup, settings, summary["name"], progress)
            summary["test"] = test
            act.summary += " · restore test passed" if test["ok"] else " · RESTORE TEST FAILED"
        if not test["ok"]:
            self._report_backup_test(summary["name"], test)
        return summary

    async def run_backup_test(self, name: str | None = None, password: str | None = None) -> dict:
        """Restore test of one backup (default: the newest), without changing anything."""
        settings = get_settings()
        if not name:
            items = backup.list_backups(settings)
            if not items:
                raise ValueError("There are no backups to test")
            name = max(items, key=lambda b: b.get("created_at") or 0)["name"]
        state: dict = {"stage": "Starting"}

        def progress(stage: str, done: int | None = None, total: int | None = None) -> None:
            state.update(stage=stage, done=done, total=total)

        async with activity.track("backup_test", f"Testing backup {name}", progress=lambda: dict(state)) as act:
            result = await asyncio.to_thread(backup.verify_backup, settings, name, progress, password)
            act.summary = "Restore test passed" if result["ok"] else "Restore test FAILED: " + ", ".join(
                c["name"] for c in result["checks"] if not c["ok"])
        if not result["ok"]:
            self._report_backup_test(name, result)
        return {"name": name, **result}

    @staticmethod
    def _report_backup_test(name: str, result: dict) -> None:
        failed = [f"{c['name']}{': ' + c['detail'] if c['detail'] else ''}" for c in result["checks"] if not c["ok"]]
        logger.error("Restore test of backup {} failed: {}", name, "; ".join(failed))
        notify("system", "error", f"Backup {name} failed its restore test", body="; ".join(failed)[:900],
               link="/admin/portal/backups")
