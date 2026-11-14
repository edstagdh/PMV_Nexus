"""
Disk-space guard (2.10.0). When the drive holding the downloads (or the
app's data: database, thumbnails) gets below `download.min_free_percent`
(default 10 %, 0 = off) free:

  - running downloads stop and go back to the queue (their partial files are
    deleted, to give the space back), and no new download starts;
  - everyone sees a banner (summary["disk"]), and the admins get a notification;
  - re-encodes, repairs and everything else carry on as usual.

Downloads start again by themselves once free space is back above the limit
(+1 point, so it doesn't flip on and off at the edge). Checked every
CHECK_SECONDS and before each download starts.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from ..notifications import notify

CHECK_SECONDS = 30
RESUME_MARGIN = 1.0  # percentage points above the limit before downloads resume


class DiskGuardMixin:
    _disk: dict = {"low": False}
    _disk_task: Optional[asyncio.Task] = None
    _space_paused: set = set()  # downloads stopped by the guard (back to the queue, partial files removed)

    def _disk_paths(self) -> list[Path]:
        s = self.settings
        return [Path(s.output_dir), Path(s.library_db_file).parent, Path(s.cache_dir)]

    def _measure_disk(self) -> dict:
        """The fullest of the drives the app writes to."""
        worst, seen = None, set()
        for path in self._disk_paths():
            p = path
            while not p.exists() and p != p.parent:
                p = p.parent
            try:
                dev = os.stat(p).st_dev
                if dev in seen:
                    continue
                seen.add(dev)
                usage = shutil.disk_usage(p)
            except OSError:
                continue
            pct = usage.free / usage.total * 100 if usage.total else 100.0
            if worst is None or pct < worst["free_percent"]:
                worst = {"path": str(path.resolve()), "free_percent": round(pct, 1), "free_bytes": usage.free,
                         "total_bytes": usage.total}
        return worst or {"path": None, "free_percent": 100.0, "free_bytes": 0, "total_bytes": 0}

    def disk_guard_status(self) -> dict:
        """For the summary (banner) and Server Settings."""
        return {**self._disk, "limit": self.settings.download.min_free_percent}

    def check_disk_space(self) -> bool:
        """Re-measures; returns True while downloads must wait. Starts/stops the guard."""
        limit = float(self.settings.download.min_free_percent or 0)
        m = self._measure_disk()
        was_low = self._disk.get("low", False)
        low = limit > 0 and m["free_percent"] < (limit + RESUME_MARGIN if was_low else limit)
        self._disk = {**m, "low": low, "since": self._disk.get("since") if low and was_low else (time.time() if low else None)}
        if low and not was_low:
            self._on_disk_low(limit)
        elif was_low and not low:
            self._on_disk_ok()
        return low

    def _on_disk_low(self, limit: float) -> None:
        d = self._disk
        gb = d["free_bytes"] / 1024 ** 3
        logger.warning("Low disk space: {}% free ({:.1f} GB) on {} -- downloads stopped until it's above {}%",
                       d["free_percent"], gb, d["path"], limit + RESUME_MARGIN)
        notify("system", "warning", f"Low disk space: downloads stopped ({d['free_percent']}% free)",
               body=f"Only {gb:.1f} GB free where the app saves videos ({d['path']}). Free up space to avoid damaged "
                    f"files; downloads start again by themselves above {limit + RESUME_MARGIN:g}%.", link="/tasks")
        for item_id, task in list(self._active_download_tasks.items()):
            if not task.done():
                self._space_paused.add(item_id)
                task.cancel()
        self.db._note_change(["disk"])

    def _on_disk_ok(self) -> None:
        logger.info("Disk space is back ({}% free): downloads resume", self._disk["free_percent"])
        notify("system", "info", f"Disk space is back ({self._disk['free_percent']}% free): downloads resume", link="/tasks")
        self.db._note_change(["disk"])
        self._ensure_worker()

    def _ensure_disk_guard(self) -> None:
        if self._disk_task is None or self._disk_task.done():
            self._disk_task = asyncio.create_task(self._disk_guard_loop())

    async def _disk_guard_loop(self) -> None:
        while not self._shutting_down:
            try:
                self.check_disk_space()  # a quick statvfs; it cancels tasks, so it stays on the loop
            except Exception:  # noqa: BLE001
                logger.opt(exception=True).warning("Disk-space check failed")
            await asyncio.sleep(CHECK_SECONDS)
