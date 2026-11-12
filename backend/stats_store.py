"""
Day-by-day stats counters (2.11.0): bump("downloads") / bump("download_bytes",
size)... into the stats_daily table, keyed by the local date. Never raises --
counting must not break the work being counted. Read by the Stats page
(library_mixins/stats_worker.py -> stats_history()).

Metrics: downloads, download_failures, download_bytes, reencodes,
reencode_failures, reencode_saved_bytes, repairs, repair_failures, uploads,
upload_failures, upload_bytes, shares, share_failures, jobs, job_failures.
"""
from __future__ import annotations

from datetime import datetime

from loguru import logger

_db = None


def bind(db) -> None:
    global _db
    _db = db


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def bump(metric: str, value: float = 1) -> None:
    if _db is None or not metric or not value:
        return
    try:
        _db.bump_daily(today(), metric, value)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("Could not count {}", metric)
