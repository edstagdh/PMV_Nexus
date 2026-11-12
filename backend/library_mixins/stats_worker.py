"""
Backend for the Admin Portal's Stats page -- System stats (disk, process,
background-worker activity) and Library stats (item counts, storage,
codec/resolution/tag/performer breakdowns, re-encode/repair/duplicate/
share activity), plus daily historical snapshots for trend charts.

Two distinct things live here:
  - compute_system_stats() / compute_library_stats(): always a fresh,
    live computation from current in-memory state -- cheap (no file
    reads beyond a couple of stat() calls and shutil.disk_usage()), safe
    to call on every page load.
  - capture_stats_snapshot(): combines both into one dict and PERSISTS it
    to the stats_snapshots table -- this is what trend charts read from.
    Nothing calls this automatically; it's triggered by the Stats page's
    "Capture now" button, or a Scheduler job of type "stats_snapshot"
    (registered as "non_invasive" -- pure read-only aggregation, safe to
    run alongside anything else, including an invasive job).
"""
from __future__ import annotations

import os
import shutil
import time
from collections import Counter
from typing import Optional

from loguru import logger

from ._helpers import parse_filesize_mb
from ..spellings import spellings
from .. import activity

try:
    import psutil
except ImportError:  # pragma: no cover -- see requirements.txt
    psutil = None


class StatsWorkerMixin:

    def compute_system_stats(self) -> dict:
        stats: dict = {"uptime_seconds": round(time.time() - self._start_time, 1)}

        for label, path in (("output_dir", self.settings.output_dir), ("cache_dir", self.settings.cache_dir)):
            try:
                usage = shutil.disk_usage(path)
                stats[f"{label}_disk"] = {
                    "total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free,
                    "free_percent": round(usage.free / usage.total * 100, 1) if usage.total else None,
                    "device": os.stat(path).st_dev,  # same number = same drive (the Stats page shows it once)
                }
            except Exception:
                stats[f"{label}_disk"] = None

        try:
            stats["database_size_bytes"] = self.settings.library_db_file.stat().st_size
        except OSError:
            stats["database_size_bytes"] = None

        stats["ffmpeg_available"] = shutil.which("ffmpeg") is not None

        if psutil is not None:
            try:
                proc = psutil.Process(os.getpid())
                stats["process"] = {
                    "memory_rss_bytes": proc.memory_info().rss,
                    # A first-ever call returns 0.0 (psutil needs a prior
                    # call to compare against) -- meaningful from the
                    # second Stats page load onward within the same
                    # process lifetime.
                    "cpu_percent": proc.cpu_percent(interval=None),
                }
            except Exception:
                stats["process"] = None
        else:
            stats["process"] = None
        stats["psutil_available"] = psutil is not None

        sharing_active = sum(
            1 for item in self._items.values()
            for s in item.shares.values() if s.status == "sharing"
        )
        stats["workers"] = {
            "downloading": sum(1 for i in self._items.values() if i.status == "downloading"),
            "fetching_metadata": sum(1 for i in self._items.values() if i.status == "fetching"),
            "pending_download": sum(1 for i in self._items.values() if i.status == "pending"),
            "repairing_active": len(self._active_repair_tasks),
            "repair_queue_len": len(self._repair_queue),
            "reencoding_active": 1 if self._current_reencode_id else 0,
            "reencode_queue_len": len(self._reencode_queue),
            "sharing_active": sharing_active,
            "duplicate_scan_running": self._duplicate_scan_running,
            "scheduler_jobs_running": len(self._scheduler_active_jobs),
        }
        return stats

    def compute_library_stats(self) -> dict:
        # "merged" items are resolved/terminal bookkeeping, not real
        # library entries -- excluded here for the same reason
        # get_summary() already excludes them from total_items.
        items = [i for i in self._items.values() if i.status != "merged"]

        status_counts = Counter(i.status for i in items)

        total_size_bytes = 0
        items_with_known_size = 0
        for i in items:
            mb = parse_filesize_mb(i.tech_filesize)
            if mb is not None:
                total_size_bytes += int(mb * 1024 * 1024)
                items_with_known_size += 1

        codec_counts = Counter(i.tech_codec for i in items if i.tech_codec)
        resolution_counts = Counter(i.tech_resolution for i in items if i.tech_resolution)

        tag_counts: Counter = Counter()
        performer_counts: Counter = Counter()
        for i in items:
            tag_counts.update(spellings.canon_list("tags", i.tags))
            performer_counts.update(spellings.canon_list("performers", i.performers))

        reencoded_count = sum(1 for i in items if i.reencode_status == "done")

        share_counts: dict[str, dict[str, int]] = {}
        for i in items:
            for dest, result in i.shares.items():
                share_counts.setdefault(dest, {})
                share_counts[dest][result.status] = share_counts[dest].get(result.status, 0) + 1

        duplicate_groups = self._find_duplicate_groups(75.0)  # from IssuesMixin

        return {
            "total_items": len(items),
            "status_counts": dict(status_counts),
            "total_size_bytes": total_size_bytes,
            "items_with_known_size": items_with_known_size,
            "top_codecs": codec_counts.most_common(10),
            "top_resolutions": resolution_counts.most_common(10),
            "top_tags": tag_counts.most_common(15),
            "top_performers": performer_counts.most_common(15),
            "reencoded_count": reencoded_count,
            # Cumulative counters -- see the increment_counter() call
            # sites in reencode_worker.py/repair_worker.py for exactly
            # what feeds each of these, and why they only reflect
            # activity from when that tracking was added onward, not the
            # library's full history.
            "cumulative_reencode_bytes_saved": self.db.get_counter("stat_total_reencode_bytes_saved"),
            "cumulative_reencodes_performed": self.db.get_counter("stat_total_reencodes_performed"),
            "cumulative_repairs_performed": self.db.get_counter("stat_total_repairs_performed"),
            "cumulative_repair_fixes_applied": self.db.get_counter("stat_total_repair_fixes_applied"),
            "duplicate_group_count": len(duplicate_groups),
            "share_counts": share_counts,
        }

    async def capture_stats_snapshot(self) -> dict:
        """Computes both System and Library stats right now and persists
        the combined result as one row in stats_snapshots -- this is what
        actually feeds trend charts (see get_stats_trends() below).
        Registered in the Scheduler's JOB_REGISTRY as "stats_snapshot",
        category "non_invasive" (pure read-only aggregation -- never
        contends with an invasive job like duplicate scan or bulk
        repair). `async def` purely for consistency with every other
        registered job runner's signature; nothing here actually awaits
        anything, since every input is already in memory or a cheap
        stat() call."""
        async with activity.track("stats_snapshot", "Saving a stats snapshot"):
            system = self.compute_system_stats()
            library = self.compute_library_stats()
            snapshot = self.db.create_stats_snapshot({"system": system, "library": library})
        logger.info(
            "Stats snapshot captured: {} items, {} bytes total",
            library["total_items"], library["total_size_bytes"],
        )
        return {"captured_at": snapshot["captured_at"]}

    def get_stats_trends(self, days: int = 30) -> list[dict]:
        since = time.time() - days * 86400
        return self.db.list_stats_snapshots(since=since, limit=400)

    # ------------------------------------------------------------ 2.11.0 history
    def record_watch(self, user_id: str, item_id: str, seconds: float, started: bool) -> None:
        """The player reports watched time in small pieces (at most a couple of minutes each)."""
        item = self._items.get(item_id)
        if item is None or item.status != "done":
            raise KeyError(item_id)
        seconds = max(0.0, min(float(seconds), 180.0))
        if seconds or started:
            from .. import stats_store
            self.db.add_watch(stats_store.today(), user_id, item_id, round(seconds, 1), 1 if started else 0)

    def stats_history(self, range_key: str = "30d") -> dict:
        """The Stats page: library growth, activity and watching over a range,
        bucketed by day (<= 92 days), week (<= 2 years) or month."""
        from datetime import date, datetime, timedelta
        today = date.today()
        items = [i for i in self._items.values() if i.status != "merged"]

        def item_day(i) -> Optional[date]:
            raw = i.download_datetime
            try:
                if raw:
                    return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone().date()
            except ValueError:
                pass
            return datetime.fromtimestamp(i.created_at).date() if i.created_at else None

        days = {"7d": 7, "30d": 30, "90d": 90, "1y": 365}.get(range_key)
        done = [(item_day(i), i) for i in items if i.status == "done"]
        first = min((d for d, _ in done if d), default=today)
        start = today - timedelta(days=days - 1) if days else first
        span = (today - start).days + 1
        unit = "day" if span <= 92 else "week" if span <= 730 else "month"

        def bucket(d: date) -> str:
            if unit == "day":
                return d.isoformat()
            if unit == "week":
                return (d - timedelta(days=d.weekday())).isoformat()
            return d.strftime("%Y-%m-01")

        labels, d = [], start
        while d <= today:
            b = bucket(d)
            if not labels or labels[-1] != b:
                labels.append(b)
            d += timedelta(days=1)
        index = {b: n for n, b in enumerate(labels)}

        def series() -> list:
            return [0] * len(labels)

        # Library growth (whole history, from the videos themselves): totals at the end of each bucket.
        sizes = {i.id: int((parse_filesize_mb(i.tech_filesize) or 0) * 1024 * 1024) for _, i in done}
        before = [i for d0, i in done if d0 and d0 < start]
        count, size = len(before), sum(sizes[i.id] for i in before)
        added_n, added_b = series(), series()
        for d0, i in done:
            if d0 and d0 >= start:
                k = index[bucket(d0)]
                added_n[k] += 1
                added_b[k] += sizes[i.id]
        total_n, total_b = [], []
        for k in range(len(labels)):
            count += added_n[k]
            size += added_b[k]
            total_n.append(count)
            total_b.append(size)

        # Activity counters (from 2.11.0 on) -- downloads come from the videos (full history).
        metrics = ("download_failures", "reencodes", "reencode_failures", "reencode_saved_bytes", "repairs",
                   "repair_failures", "uploads", "upload_failures", "shares", "share_failures", "jobs", "job_failures")
        activity_s = {m: series() for m in metrics}
        for row in self.db.daily_stats(start.isoformat()):
            if row["metric"] in activity_s:
                k = index.get(bucket(date.fromisoformat(row["day"])))
                if k is not None:
                    activity_s[row["metric"]][k] += row["value"]
        local = {i.id for _, i in done if i.site == "local"}
        downloads = series()
        for d0, i in done:
            if d0 and d0 >= start and i.id not in local:
                downloads[index[bucket(d0)]] += 1

        # Watching.
        users = {u.id: u.username for u in self.db.list_users()}
        watch_s, plays_s = series(), series()
        per_item: Counter = Counter()
        plays_item: Counter = Counter()
        per_user: dict[str, list] = {}
        for row in self.db.watch_rows(start.isoformat()):
            k = index.get(bucket(date.fromisoformat(row["day"])))
            if k is None:
                continue
            watch_s[k] += row["seconds"]
            plays_s[k] += row["plays"]
            per_item[row["item_id"]] += row["seconds"]
            plays_item[row["item_id"]] += row["plays"]
            u = per_user.setdefault(row["user_id"], [0.0, 0])
            u[0] += row["seconds"]
            u[1] += row["plays"]
        perf_w: Counter = Counter()
        tag_w: Counter = Counter()
        top_videos = []
        for item_id, secs in per_item.most_common():
            item = self._items.get(item_id)
            if item is None:
                continue
            for p in spellings.canon_list("performers", item.performers):
                perf_w[p] += secs
            for t in spellings.canon_list("tags", item.tags):
                tag_w[t] += secs
            if len(top_videos) < 10:
                top_videos.append({"id": item_id, "title": item.title, "creator": item.creator,
                                   "seconds": round(secs), "plays": plays_item[item_id]})

        # Disk space over time (from the daily snapshots).
        disk = []
        for snap in self.db.list_stats_snapshots(since=datetime.combine(start, datetime.min.time()).timestamp(), limit=2000):
            dd = ((snap.get("system") or {}).get("output_dir_disk")) or {}
            if dd.get("total_bytes"):
                disk.append({"t": snap["captured_at"], "free_bytes": dd["free_bytes"], "total_bytes": dd["total_bytes"]})

        return {
            "range": range_key, "unit": unit, "labels": labels, "since": start.isoformat(),
            "growth": {"videos": total_n, "bytes": total_b, "added": added_n, "added_bytes": added_b},
            "activity": {"downloads": downloads, **activity_s},
            "watch": {"seconds": [round(x) for x in watch_s], "plays": plays_s},
            "top_watched": top_videos,
            "top_performers": [{"name": n, "seconds": round(s)} for n, s in perf_w.most_common(10)],
            "top_tags": [{"name": n, "seconds": round(s)} for n, s in tag_w.most_common(10)],
            "per_user": sorted(({"user": users.get(uid, "(deleted user)"), "seconds": round(v[0]), "plays": v[1]}
                                for uid, v in per_user.items()), key=lambda r: -r["seconds"]),
            "disk": disk,
            "counters_since": self.db.get_app_state("stats_daily_since", None),
        }

    def live_machine(self) -> dict:
        """Whole-machine CPU / memory / network for the Stats page (updated every few seconds)."""
        if psutil is None:
            return {}
        now = time.monotonic()
        out = {"cpu_percent": psutil.cpu_percent(interval=None)}
        vm = psutil.virtual_memory()
        out["memory"] = {"total": vm.total, "used": vm.total - vm.available, "percent": vm.percent}
        try:
            net = psutil.net_io_counters()
            prev = getattr(self, "_net_prev", None)
            if prev and now > prev[0]:
                dt = now - prev[0]
                out["net"] = {"rx_per_s": max(0, (net.bytes_recv - prev[1]) / dt), "tx_per_s": max(0, (net.bytes_sent - prev[2]) / dt)}
            self._net_prev = (now, net.bytes_recv, net.bytes_sent)
        except Exception:  # noqa: BLE001
            pass
        out["app"] = self._app_usage()
        out["downloading"] = [{"title": i.title, "speed": i.speed, "percent": i.progress_percent}
                              for i in self._items.values() if i.status == "downloading"]
        return out

    def _app_usage(self) -> dict:
        """This app's share of the machine: its own process plus everything it
        started (FFmpeg for downloads, re-encodes, previews...). CPU is scaled
        to the whole machine (0-100 %, like the CPU gauge), not one core."""
        if psutil is None:
            return {}
        cache = getattr(self, "_proc_cache", None)
        if cache is None:
            cache = self._proc_cache = {}
        try:
            me = psutil.Process(os.getpid())
            procs = [me, *me.children(recursive=True)]
        except Exception:  # noqa: BLE001
            return {}
        cores = psutil.cpu_count() or 1
        total_mem = psutil.virtual_memory().total
        app_cpu = helpers_cpu = 0.0
        app_mem = helpers_mem = 0
        helpers = 0
        alive = set()
        for p in procs:
            alive.add(p.pid)
            proc = cache.setdefault(p.pid, p)  # cpu_percent() compares with this object's previous call
            try:
                cpu = proc.cpu_percent(interval=None) / cores
                mem = proc.memory_info().rss
            except Exception:  # noqa: BLE001 -- it just ended
                continue
            if p.pid == me.pid:
                app_cpu, app_mem = cpu, mem
            else:
                helpers += 1
                helpers_cpu += cpu
                helpers_mem += mem
        for pid in [k for k in cache if k not in alive]:
            cache.pop(pid, None)
        mem = app_mem + helpers_mem
        return {"cpu_percent": round(app_cpu + helpers_cpu, 1), "memory_bytes": mem,
                "memory_percent": round(mem / total_mem * 100, 1) if total_mem else None,
                "self": {"cpu_percent": round(app_cpu, 1), "memory_bytes": app_mem},
                "helpers": {"count": helpers, "cpu_percent": round(helpers_cpu, 1), "memory_bytes": helpers_mem}}
