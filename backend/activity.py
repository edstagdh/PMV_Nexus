"""
Library-wide background work the Tasks page shows (2.9.0): Scheduler jobs,
duplicate scans, syncs, backups, performer matching, repair-all, folder
moves... -- everything that isn't one video's download / re-encode / repair
(those are shown from the videos themselves).

A job wraps its work in `async with activity.track(kind, title, progress=fn)`.
`fn()` returns what it's doing right now -- {"stage", "done", "total",
"detail"}, any of them -- and is read whenever the Tasks page asks, so the
job keeps its own counters and doesn't have to push updates.

The Scheduler starts an activity for each run (named after the job) and
makes it the current one (a ContextVar); a job method that calls track()
inside it fills in that same activity instead of starting a second one.
So "Weekly backup" run by the Scheduler and "Create backup" pressed on the
Backups page both show, each under its own name.

Stopping (2.10.0): a job that can be stopped says so -- track(stop=True)
cancels the task running it (the job's own `finally` blocks clean up), or
track(stop=fn) calls fn instead (repair-all empties its queue);
stop_when=fn limits it to safe moments (a sync only while it's still
reading). Activity.request_stop() is what the Stop button calls.

Each finished activity is announced through `on_finish` (set by the
library manager: a notification for the admins).

Finished activities are kept in memory (the last KEEP_FINISHED) for the
"Finished recently" list; the Scheduler's run history is the lasting record.
"""
from __future__ import annotations

import asyncio
import contextvars
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from typing import Callable, Optional

from loguru import logger

KEEP_FINISHED = 30

_current: contextvars.ContextVar[Optional["Activity"]] = contextvars.ContextVar("activity", default=None)
_running: dict[str, "Activity"] = {}
_finished: deque = deque(maxlen=KEEP_FINISHED)
_notify: Optional[Callable[[], None]] = None
on_finish: Optional[Callable[["Activity"], None]] = None


def set_notifier(fn: Callable[[], None]) -> None:
    """fn() is called (on the event loop) when an activity starts or ends,
    so open Tasks pages reload (LibraryDB._note_change -> SSE)."""
    global _notify
    _notify = fn


def _changed() -> None:
    if _notify:
        try:
            _notify()
        except Exception:  # noqa: BLE001
            logger.debug("activity notifier failed", exc_info=True)


class Activity:
    def __init__(self, kind: str, title: str, source: str = "manual", job_id: Optional[str] = None,
                 progress: Optional[Callable[[], dict]] = None):
        self.id = uuid.uuid4().hex
        self.kind, self.title, self.source, self.job_id = kind, title, source, job_id
        self.progress = progress
        self.stage: Optional[str] = None
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self.status = "running"
        self.summary: Optional[str] = None
        self.task: Optional[asyncio.Task] = _current_task()
        self.stopper: Optional[Callable[[], None]] = None
        self.stop_when: Optional[Callable[[], bool]] = None
        self.stopped_by: Optional[str] = None

    def can_stop(self) -> bool:
        if self.finished_at is not None or self.stopper is None or self.stopped_by is not None:
            return False
        try:
            return self.stop_when() if self.stop_when else True
        except Exception:  # noqa: BLE001
            return False

    def request_stop(self, by: str) -> None:
        if not self.can_stop():
            raise ValueError("This job can't be stopped right now")
        self.stopped_by = by or "someone"
        self.stage = "Stopping…"
        self.progress = None
        self.stopper()
        _changed()

    def _cancel_task(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()

    def set_stage(self, stage: Optional[str]) -> None:
        self.stage = stage

    def finish(self, status: str, summary: Optional[str] = None) -> None:
        if self.finished_at is not None:
            return
        self.status, self.summary, self.finished_at = status, summary, time.time()
        self.progress = None  # drop references to the job's state
        _running.pop(self.id, None)
        _finished.appendleft(self)
        _changed()
        if on_finish:
            try:
                on_finish(self)
            except Exception:  # noqa: BLE001
                logger.debug("activity on_finish failed", exc_info=True)

    def to_dict(self) -> dict:
        out = {"id": self.id, "kind": self.kind, "title": self.title, "source": self.source, "job_id": self.job_id,
               "status": self.status, "stage": self.stage, "summary": self.summary,
               "started_at": self.started_at, "finished_at": self.finished_at,
               "done": None, "total": None, "percent": None, "detail": None,
               "can_stop": self.can_stop(), "stopped_by": self.stopped_by}
        if self.progress is not None:
            try:
                p = self.progress() or {}
            except Exception:  # noqa: BLE001 -- a progress probe must never break the Tasks page
                p = {}
            for k in ("stage", "done", "total", "detail"):
                if p.get(k) is not None:
                    out[k] = p[k]
            if p.get("percent") is not None:
                out["percent"] = round(float(p["percent"]), 1)
        if out["percent"] is None and out["total"]:
            out["percent"] = round(min(100.0, (out["done"] or 0) / out["total"] * 100), 1)
        return out


def start(kind: str, title: str, source: str = "manual", job_id: Optional[str] = None,
          progress: Optional[Callable[[], dict]] = None) -> Activity:
    act = Activity(kind, title, source, job_id, progress)
    _running[act.id] = act
    _changed()
    return act


def _current_task() -> Optional[asyncio.Task]:
    try:
        return asyncio.current_task()
    except RuntimeError:  # not on the event loop
        return None


def get(activity_id: str) -> Optional[Activity]:
    return _running.get(activity_id)


def current() -> Optional[Activity]:
    return _current.get()


@asynccontextmanager
async def track(kind: str, title: str, progress: Optional[Callable[[], dict]] = None, source: str = "manual",
                stage: Optional[str] = None, stop=None, stop_when: Optional[Callable[[], bool]] = None):
    """Shows the wrapped work on the Tasks page. Inside a Scheduler run (or
    another track()), the existing activity is used -- this only gives it
    its progress / stage / stop. stop=True: the Stop button cancels the
    task running it; stop=fn: calls fn."""
    outer = _current.get()
    if outer is not None and outer.finished_at is None:
        prev = (outer.progress, outer.stage, outer.stopper, outer.stop_when)
        if progress is not None:
            outer.progress = progress
        if stage:
            outer.stage = stage
        if stop:
            outer.stopper = stop if callable(stop) else outer._cancel_task
            outer.stop_when = stop_when
        try:
            yield outer
        finally:
            if outer.finished_at is None and outer.stopped_by is None:
                outer.progress, outer.stage, outer.stopper, outer.stop_when = prev
        return
    act = start(kind, title, source, progress=progress)
    act.stage = stage
    if stop:
        act.stopper = stop if callable(stop) else act._cancel_task
        act.stop_when = stop_when
    token = _current.set(act)
    try:
        yield act
    except BaseException as exc:
        cancelled = type(exc).__name__ == "CancelledError"
        if cancelled and act.stopped_by:
            act.finish("cancelled", f"Stopped by {act.stopped_by}")
        else:
            status = "interrupted" if cancelled else ("blocked" if type(exc).__name__ == "JobBlockedError" else "failed")
            act.finish(status, str(exc)[:300] or type(exc).__name__)
        raise
    else:
        act.finish("cancelled" if act.stopped_by else "success",
                   f"Stopped by {act.stopped_by}" if act.stopped_by else act.summary)
    finally:
        _current.reset(token)


def summarize(result) -> Optional[str]:
    """A job's result for the Tasks page: {"matched": 7, "refreshed": 0} -> "matched 7 · refreshed 0"."""
    if isinstance(result, dict):
        parts = [f"{k.replace('_', ' ')} {v}" for k, v in result.items()
                 if isinstance(v, (int, float, str)) and not isinstance(v, bool) and len(str(v)) < 60]
        return " · ".join(parts[:6]) or None
    return str(result)[:200] if result not in (None, "") else None


def running() -> list[dict]:
    return [a.to_dict() for a in sorted(_running.values(), key=lambda a: a.started_at)]


def finished() -> list[dict]:
    return [a.to_dict() for a in _finished]


def count() -> int:
    return len(_running)


def for_job(job_id: str) -> Optional[dict]:
    """The running activity of a Scheduler job (the Scheduler page shows its progress)."""
    act = next((a for a in _running.values() if a.job_id == job_id), None)
    return act.to_dict() if act else None
