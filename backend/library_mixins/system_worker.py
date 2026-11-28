"""App-maintenance jobs: update checks and backups (see backend/app_info.py
and backend/backup.py). Both are registered in scheduler_worker.JOB_REGISTRY
and are also triggered from the UI (version label, Backups page)."""
from __future__ import annotations

import asyncio
import json
import time

from loguru import logger

from ..i18n import L
from ..resources import JOB, resources
from .. import backup, backup_jobs
from ..notifications import notify
from .. import activity
from ..app_info import APP_VERSION, fetch_latest_version, is_newer
from ..config import get_settings

UPDATE_STATE_KEY = "update_check"
MANUAL_CHECK_MIN_INTERVAL = 60  # seconds; the button is visible to every user

# Built-in Scheduler jobs. They always exist (re-created if missing), can
# be edited and disabled, but not deleted. (key, job_type, name, schedule[, job_config])
SYSTEM_JOBS = [
    ("backup", "backup", L("Weekly backup"), {"kind": "weekly", "day": "sunday", "time": "04:00"},
     backup_jobs.WEEKLY_DEFAULT),
    # 3.8.0: the database and settings every day (copies elsewhere / Filester: the job's options)
    ("backup_daily", "backup", L("Daily backup"), {"kind": "daily", "time": "02:30"}, backup_jobs.DAILY_DEFAULT),
    ("update_check", "update_check", L("Check for updates"), {"kind": "daily", "time": "09:00"}),
    ("duplicate_scan", "duplicate_scan", L("Daily duplicate scan"), {"kind": "daily", "time": "03:30"}),
    ("performer_sync", "performer_sync", L("Match & refresh performers"), {"kind": "daily", "time": "05:00"}),
    # 2.11.0: the Stats page's history of disk space and library totals.
    ("stats_snapshot", "stats_snapshot", L("Daily stats snapshot"), {"kind": "daily", "time": "23:50"}),
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
        self._show_backup_job_options(rows)
        for key, job_type, name, schedule, *config in SYSTEM_JOBS:
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
                job = self.create_scheduled_job(job_type, name, enabled, "simple", schedule, dict(config[0]) if config else {})
                self.db.update_scheduled_job(job["id"], {"system_key": key})
                logger.info("Scheduler: added system job '{}'", name)
            except Exception:  # noqa: BLE001
                logger.exception("Could not create system scheduled job '{}'", name)

    def _show_backup_job_options(self, rows: list[dict]) -> None:
        """3.8.0: backup jobs from before have no options -- they did what Server Settings said
        (include thumbnails, keep). Those settings are written into the job once, so the job does
        the same as before and its options show (and can be changed) in the Scheduler."""
        settings = get_settings()
        for r in rows:
            if r["job_type"] != "backup":
                continue
            try:
                cfg = json.loads(r.get("job_config") or "{}")
            except ValueError:
                cfg = {}
            if backup_jobs.is_configured(cfg):
                continue
            cfg = {"include": sorted(backup.CACHE_PARTS) if settings.backup.include_cache else [],
                   "keep": settings.backup.keep, "copy_to": [], "upload": "", "split_gb": 0}
            self.db.update_scheduled_job(r["id"], {"job_config": json.dumps(cfg)})
            logger.info("Scheduler: backup job '{}' now shows its options", r.get("name"))

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
        async with activity.track("update_check", L('Checking for app updates')):
            result = await fetch_latest_version()
        if result.get("error") and last and last.get("latest"):
            # a failed check keeps what the last good one found (the version, its page)
            result = {**last, "checked_at": result["checked_at"], "error": result["error"], "current": result["current"],
                      "update_available": bool(last.get("latest") and is_newer(last["latest"], APP_VERSION))}
        self.db.set_app_state(UPDATE_STATE_KEY, result)
        if result.get("update_available") and not result.get("error"):
            logger.info("Update available: {} -> {} ({} newer release(s)) {}", result["current"], result["latest"],
                        result.get("newer"), result.get("release_url"))
        elif result.get("error"):
            logger.warning("Update check failed: {}", result["error"])
        self.update_notices(result)
        await self.prune_installer_backups()   # 3.13.0: once a day with the update check
        return result

    async def prune_installer_backups(self) -> int:
        """3.13.0: backups made by Setup / the install scripts, deleted after the days set."""
        try:
            return await asyncio.to_thread(backup.prune_installer_backups, self.settings)
        except Exception as exc:  # noqa: BLE001 -- never stops the app or the update check
            logger.warning("Backups: couldn't check the installer's backups: {}", exc)
            return 0

    def update_notices(self, result: dict | None = None) -> None:
        """3.11.5: one highlighted "new version" notification per release, for the admins (made once:
        deleting it doesn't bring it back the next day). Ones about a version this app already has,
        or an older release than the newest, are removed. Also run at start (after an update)."""
        result = result if result is not None else (self.get_update_status() or {})
        try:
            latest = result.get("latest") if result.get("update_available") and is_newer(result.get("latest") or "", APP_VERSION) else None
            stale = []
            for row in self.db.notifications_of("update"):
                version = (row.get("link") or "").rstrip("/").rsplit("/", 1)[-1].lstrip("v")
                if not is_newer(version, APP_VERSION) or (latest and version != latest):
                    stale.append(row["id"])
            self.db.delete_notification_ids(stale)
            if latest and self.db.get_app_state("update_notified", None) != latest:
                newer = result.get("newer") or 1
                notify("update", "info", L("PMV Nexus {version} is available", version=latest),
                       body=(L("You have {current}; {n} releases are newer, and this is the newest.", current=APP_VERSION, n=newer)
                             if newer > 1 else L("You have {current}.", current=APP_VERSION)),
                       link=result.get("release_url") or result.get("releases_url"))
                self.db.set_app_state("update_notified", latest)
        except Exception:  # noqa: BLE001 -- never stop a check or the start over this
            logger.opt(exception=True).warning("Couldn't update the new-version notification")

    async def run_backup(self, include_cache: bool | None = None, include_secrets: bool = False,
                         trigger: str = "scheduled", include: list[str] | None = None, dest: str | None = None,
                         job_id: str | None = None, job_config: dict | None = None, upload: bool = False,
                         requested_by: str | None = None) -> dict:
        """A backup, then its restore test. `include` (3.7.0): backup.PARTS keys; without it
        the older switches (a scheduled backup: Server Settings' include_cache). Tasks shows
        each step (the files it's adding) and the whole job (by bytes, the test included).
        3.8.0, a scheduled job with options (backend/backup_jobs.py): its parts, its own keep and
        part size; after the restore test passed, the copies to other folders and the upload to
        Filester -- each one tried even when another fails; the job fails (and notifies) if any did."""
        settings = get_settings()
        cfg = backup_jobs.normalize(job_config, check_ready=False) if backup_jobs.is_configured(job_config) else None
        if cfg:
            include = cfg["include"]
        cache = settings.backup.include_cache if include_cache is None else include_cache
        parts = backup.parts_from(include, cache, include_secrets)
        if upload and not cfg:   # the Backups page's "Also upload to Filester" (job_id None: manual)
            cfg = backup_jobs.manual_upload(settings, parts)
        if cfg and cfg["upload"] and not backup.saved_password():
            raise RuntimeError(L("Set a backup password on the Backups page first: a backup is only uploaded encrypted."))
        copies = len(cfg["copy_to"]) + (1 if cfg["upload"] else 0) if cfg else 0
        state: dict = {"stage": L('Starting')}
        phase = {"create": [0, 0], "test": [0, 0], "copy": [0, 0]}  # [bytes done, bytes total]

        def overall() -> float | None:
            c, t, k = phase["create"], phase["test"], phase["copy"]
            # before the test starts its total is known: everything but the videos is read back
            test_total = t[1] or max(0, c[1] - state.get("video_bytes", 0))
            # copies: each read back once (2x), the upload once; known roughly before they start
            copy_total = k[1] or c[1] * copies * 2
            whole = c[1] + test_total + copy_total
            return None if not whole else min(100.0, (c[0] + t[0] + k[0]) / whole * 100)

        def on_create(stage, done=None, total=None, step=None, steps=None, bytes_done=None, bytes_total=None):
            if bytes_total is not None:
                phase["create"] = [bytes_done or 0, bytes_total]
            state.update(stage=stage, done=done, total=total, step=step, steps=(steps + 1) if steps else None,
                         percent=overall())

        def on_test(stage, done=None, total=None, bytes_done=None, bytes_total=None, **_):
            if bytes_total is not None:
                phase["test"] = [bytes_done or 0, bytes_total]
            steps = state.get("steps")
            state.update(stage=stage, done=done, total=total, step=steps, steps=steps, percent=overall())

        if "videos" in parts:
            state["video_bytes"] = sum(p["bytes"] for p in (await asyncio.to_thread(backup.plan_backup, settings, parts, None, dest))["parts"]
                                       if p["key"] == "videos")
        async with activity.track("backup", L('Creating a backup'), progress=lambda: dict(state)) as act:
            summary = await asyncio.to_thread(
                backup.create_backup, settings, include=sorted(parts), trigger=trigger,
                progress=on_create, password=backup.saved_password(), dest=dest,
                part_size=backup_jobs.part_bytes(cfg) if cfg else None, job=job_id if cfg else None,
                keep=cfg["keep"] if cfg else None, origin=self._backup_origin(trigger, job_id, requested_by),
                notes=backup_jobs.planned_copies(cfg),
            )
            act.summary = f"{summary['name']} ({summary['size_bytes'] / 1048576:.1f} MB)"
            logger.info("Backup created: {} ({} bytes)", summary["name"], summary["size_bytes"])
            # Restore test right away: a backup that can't be restored isn't one.
            state.update(stage=L("Restore test"), done=None, total=None)
            async with resources.use({"disk"}, L("Testing backup {name}", name=summary["name"]), JOB):   # 3.12.0
                test = await asyncio.to_thread(backup.verify_backup, settings, summary["name"], on_test)
            summary["test"] = test
            act.summary += " · restore test passed" if test["ok"] else " · RESTORE TEST FAILED"
            problems: list[str] = []
            if cfg and copies and test["ok"]:
                problems = await self._backup_copies(settings, summary, cfg, job_id, state, phase, overall)
                done = copies - len(problems)
                act.summary += f" · copies {done}/{copies}"
        if not test["ok"]:
            self._report_backup_test(summary["name"], test)
        elif problems:
            text = "; ".join(problems)
            logger.error("Backup {}: not every copy was made: {}", summary["name"], text)
            notify("system", "error", L("Backup {name} was made, but not every copy of it", name=summary["name"]),
                   body=text[:900], link="/admin/portal/backups")
            raise RuntimeError(L("The backup was made, but: {problems}", problems=text))
        return summary

    def _backup_origin(self, trigger: str, job_id: str | None, requested_by: str | None) -> str:
        """What started a backup, in words, for its backup_report.md (3.8.0)."""
        if job_id:
            try:
                job = self.db.get_scheduled_job(job_id) or {}
            except Exception:  # noqa: BLE001
                job = {}
            how = "run now" if trigger == "manual" else "on its schedule"
            return f"Scheduler job “{job.get('name') or job_id}” ({how})"
        if trigger == "manual":
            return "Backups page → Create backup now" + (f", by {requested_by}" if requested_by else "")
        return "Scheduler (a backup job from before 3.8.0)" if trigger == "scheduled" else trigger

    async def _backup_copies(self, settings, summary: dict, cfg: dict, job_id: str, state: dict, phase: dict,
                             overall) -> list[str]:
        """The job's copy folders, then Filester (3.8.0). Returns what went wrong, one line each."""
        from .. import cloud_filester
        name, size = summary["name"], summary["size_bytes"]
        targets = [("copy", f) for f in cfg["copy_to"]] + ([("upload", cfg["upload"])] if cfg["upload"] else [])
        phase["copy"] = [0, size * sum(2 if kind == "copy" else 1 for kind, _ in targets)]
        problems: list[str] = []
        base = 0
        first = state.get("steps") or 0      # the backup's own steps and its test come first
        for i, (kind, where) in enumerate(targets):
            label = L("Copying to {folder}", folder=where) if kind == "copy" else L("Uploading to Filester")
            state.update(stage=label, done=None, total=None, detail=None, step=first + i + 1, steps=first + len(targets),
                         percent=overall())

            def step(done: int, total: int, b=base, label=label) -> None:
                phase["copy"][0] = b + done
                state.update(stage=label, done=done // 1048576, total=max(1, total // 1048576),
                             detail=f"{done / 1048576:,.0f} / {total / 1048576:,.0f} MB", percent=overall())

            try:
                if kind == "copy":
                    folder = backup.check_copy_folder(settings, where)
                    await asyncio.to_thread(backup.copy_backup, settings, name, folder, step)
                    await asyncio.to_thread(backup.prune_copies, folder, job_id, cfg["keep"])
                    logger.info("Backup {} copied to {}", name, folder)
                else:
                    await asyncio.to_thread(cloud_filester.upload_backup, settings, name, job_id, cfg["keep"], step)
            except Exception as exc:  # noqa: BLE001 -- each place is tried; all problems are reported together
                logger.warning("Backup {}: {} failed: {}", name, where, exc)
                problems.append(f"{where}: {exc}")
            base += size * (2 if kind == "copy" else 1)
            phase["copy"][0] = base
        return problems

    def start_backup(self, include: list[str], trigger: str = "manual", dest: str | None = None,
                     upload: bool = False, requested_by: str | None = None) -> None:
        """The Backups page's "Create backup now" (3.7.0): runs in the background (a backup of the
        whole library can take hours), followed on Tasks; its notification says how it went."""
        task = getattr(self, "_backup_task", None)
        if (task and not task.done()) or any(a["kind"] == "backup" for a in activity.running()):
            raise ValueError(L("A backup is already being made -- see Tasks"))

        async def run():
            try:
                await self.run_backup(include=include, trigger=trigger, dest=dest, upload=upload, requested_by=requested_by)
            except Exception as exc:  # noqa: BLE001 -- the activity's notification reports it
                logger.warning("Backup failed: {}", exc)

        task = asyncio.create_task(run())
        self._backup_task = task  # keeps a reference (asyncio holds tasks weakly)

    async def run_backup_test(self, name: str | None = None, password: str | None = None) -> dict:
        """Restore test of one backup (default: the newest), without changing anything."""
        settings = get_settings()
        if not name:
            items = backup.list_backups(settings)
            if not items:
                raise ValueError(L('There are no backups to test'))
            name = max(items, key=lambda b: b.get("created_at") or 0)["name"]
        state: dict = {"stage": L('Starting')}

        def progress(stage: str, done: int | None = None, total: int | None = None, **_) -> None:
            state.update(stage=stage, done=done, total=total)

        async with activity.track("backup_test", L('Testing backup {name}', name=name), progress=lambda: dict(state)) as act:
            async with resources.use({"disk"}, L("Testing backup {name}", name=name), JOB):   # 3.12.0
                result = await asyncio.to_thread(backup.verify_backup, settings, name, progress, password)
            act.summary = L('Restore test passed') if result["ok"] else "Restore test FAILED: " + ", ".join(
                c["name"] for c in result["checks"] if not c["ok"])
        if not result["ok"]:
            self._report_backup_test(name, result)
        return {"name": name, **result}

    @staticmethod
    def _report_backup_test(name: str, result: dict) -> None:
        failed = [f"{c['name']}{': ' + c['detail'] if c['detail'] else ''}" for c in result["checks"] if not c["ok"]]
        logger.error("Restore test of backup {} failed: {}", name, "; ".join(failed))
        notify("system", "error", L('Backup {name} failed its restore test', name=name), body="; ".join(failed)[:900],
               link="/admin/portal/backups")
