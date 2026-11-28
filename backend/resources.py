"""Sharing the computer between background work (3.12.0, Server Settings -> Background work).

Two kinds of heavy work take turns instead of running at once:
- "cpu":  decoding / encoding / hashing pictures (ffmpeg, perceptual hashes, comparing);
- "disk": reading or writing whole video files (fingerprints, seekbar previews, backups, moves).
Each has a number of slots (default 1). A step of work -- one video, one file -- asks for what it
needs, waits until that's free, and gives it back when the step is done, so long jobs take turns
video by video and none waits for hours.

Who goes first when several wait (lower = sooner): LIVE (converting a video for watching, never
waits), VIDEO (things started for one video: re-encode, repair, upload, a download's checks),
JOB (library jobs started by hand or the Scheduler), AUTO (automatic follow-ups: seekbar previews,
frames, shared scenes, faces). A waiting step is only passed by a lower-priority one that needs
none of what it waits for, so nothing waits forever.

While a video is being converted for watching, new heavy CPU steps wait until it's done (the ones
running finish). Downloads don't take a slot: they're limited by the network and by
"Downloads at once", and taking the disk slot would stop every download during a long backup.

background_limits(pid): every background ffmpeg runs at low CPU and disk priority, on the same
N cores (N = the CPU share of Server Settings), so together they never use more than that share.
"""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

from loguru import logger

LIVE, VIDEO, JOB, AUTO = 0, 1, 2, 3
KINDS = ("cpu", "disk")


@dataclass
class _Ticket:
    needs: frozenset
    label: str
    priority: int
    seq: int
    ref: Optional[dict] = None             # {"activity": id} / {"item": id}: which Tasks row
    since: float = field(default_factory=time.time)
    future: Optional[asyncio.Future] = None


class Resources:
    def __init__(self) -> None:
        self._seq = itertools.count()
        self._holding: list[_Ticket] = []
        self._waiting: list[_Ticket] = []
        self._live = 0
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._mutex = threading.Lock()

    # ------------------------------------------------------------ settings
    @staticmethod
    def _slots(kind: str) -> int:
        from .config import get_settings
        t = get_settings().tasks
        return t.cpu_slots if kind == "cpu" else t.disk_slots

    @staticmethod
    def cpu_cores() -> int:
        """How many cores background ffmpeg may use (the CPU share of Server Settings)."""
        from .config import get_settings
        total = os.cpu_count() or 2
        return max(1, min(total, round(total * get_settings().tasks.cpu_percent / 100)))

    # ------------------------------------------------------------ taking turns
    def _free(self, kind: str) -> bool:
        if kind == "cpu" and self._live:
            return False
        return sum(1 for t in self._holding if kind in t.needs) < self._slots(kind)

    def _grant_waiting(self) -> None:
        """Under _mutex: gives every waiting ticket that can go now its turn, by priority."""
        blocked: set = set()
        for t in sorted(self._waiting, key=lambda t: (t.priority, t.seq)):
            if t.needs & blocked:
                continue                       # someone earlier waits for that: don't pass them
            if all(self._free(k) for k in t.needs):
                self._waiting.remove(t)
                self._holding.append(t)
                if t.future is not None and not t.future.done():
                    t.future.get_loop().call_soon_threadsafe(_set_result, t.future)
            else:
                blocked |= t.needs

    async def acquire(self, needs: Iterable[str], label: str, priority: int = JOB, ref: Optional[dict] = None) -> _Ticket:
        needs = frozenset(k for k in needs if k in KINDS)
        if ref is None:   # inside a job on Tasks: that job's row
            from . import activity
            act = activity._current.get()
            ref = {"activity": act.id} if act is not None and act.finished_at is None else None
        loop = asyncio.get_running_loop()
        self._loop = loop
        t = _Ticket(needs, label, priority, next(self._seq), ref, future=loop.create_future())
        if not needs:
            return t
        with self._mutex:
            self._waiting.append(t)
            self._grant_waiting()
        try:
            await t.future
        except BaseException:
            self.release(t)                    # cancelled while waiting (or just granted)
            raise
        return t

    def release(self, t: _Ticket) -> None:
        with self._mutex:
            if t in self._waiting:
                self._waiting.remove(t)
            if t in self._holding:
                self._holding.remove(t)
            self._grant_waiting()

    @contextlib.asynccontextmanager
    async def use(self, needs: Iterable[str], label: str, priority: int = JOB, ref: Optional[dict] = None):
        """async with resources.use({"cpu", "disk"}, "Seekbar previews: <title>", AUTO): one step."""
        t = await self.acquire(needs, label, priority, ref)
        try:
            yield t
        finally:
            self.release(t)

    @contextlib.contextmanager
    def use_blocking(self, needs: Iterable[str], label: str, priority: int = JOB, ref: Optional[dict] = None):
        """The same from a worker thread (a backup writing its zip): waits on the app's event loop."""
        loop = self._loop
        if loop is None or not loop.is_running() or _on_loop(loop):
            yield None                         # no running app loop (tools, tests): nothing to share
            return
        t = asyncio.run_coroutine_threadsafe(self.acquire(needs, label, priority, ref), loop).result()
        try:
            yield t
        finally:
            self.release(t)

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # ------------------------------------------------------------ watching first
    def live_started(self) -> None:
        with self._mutex:
            self._live += 1

    def live_ended(self) -> None:
        with self._mutex:
            self._live = max(0, self._live - 1)
            self._grant_waiting()

    # ------------------------------------------------------------ Tasks page
    def snapshot(self) -> dict:
        with self._mutex:
            def rows(ts):
                return [{"label": t.label, "needs": sorted(t.needs), "since": t.since, **(t.ref or {})}
                        for t in sorted(ts, key=lambda t: (t.priority, t.seq))]
            return {"slots": {k: self._slots(k) for k in KINDS}, "live": self._live, "cpu_cores": self.cpu_cores(),
                    "total_cores": os.cpu_count() or 1, "holding": rows(self._holding), "waiting": rows(self._waiting)}


def _set_result(fut: asyncio.Future) -> None:
    if not fut.done():
        fut.set_result(True)


def _on_loop(loop: asyncio.AbstractEventLoop) -> bool:
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


resources = Resources()


# ---------------------------------------------------------------- background processes
def background_limits(pid: int, cores: Optional[int] = None) -> None:
    """A background ffmpeg: the shared cores (or fewer), low CPU priority and low disk priority.
    Best effort -- a process that already ended, or a system without a setting, is left as it is."""
    from .media_tools import limit_process_cores
    n = resources.cpu_cores()
    limit_process_cores(pid, min(n, cores) if cores else n)
    try:
        import psutil
        p = psutil.Process(pid)
        p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if sys.platform == "win32" else 10)
        if sys.platform == "win32":
            p.ionice(psutil.IOPRIO_LOW)
        elif hasattr(psutil, "IOPRIO_CLASS_BE"):
            p.ionice(psutil.IOPRIO_CLASS_BE, value=7)   # lowest of the normal class (IDLE could starve it)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Couldn't lower the priority of process {}: {}", pid, exc)


def run_background(cmd: list, *, input=None, capture_output: bool = False, timeout: Optional[float] = None,
                   check: bool = False, cores: Optional[int] = None, **kwargs):
    """subprocess.run() for a background ffmpeg / ffprobe: the same arguments and result, but the
    process gets background_limits() as soon as it starts."""
    import subprocess
    if capture_output:
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)
    if input is not None:
        kwargs.setdefault("stdin", subprocess.PIPE)
    if sys.platform == "win32":
        kwargs.setdefault("creationflags", getattr(subprocess, "CREATE_NO_WINDOW", 0))
    with subprocess.Popen(cmd, **kwargs) as proc:
        background_limits(proc.pid, cores)
        try:
            out, err = proc.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            out, err = proc.communicate()
            raise subprocess.TimeoutExpired(proc.args, timeout, output=out, stderr=err) from exc
        except BaseException:
            proc.kill()
            raise
        code = proc.poll()
    done = subprocess.CompletedProcess(proc.args, code, out, err)
    if check:
        done.check_returncode()
    return done


class ThreadTurns:
    """For a worker thread going through many files (a backup): holds the slot while it works and
    gives it back every `every` seconds, so other heavy steps get their turn in between.

        turns = ThreadTurns({"disk"}, label, JOB)
        for f in files:
            turns.take()      # waits for the slot when not holding it
            ...work on f...
            turns.pause()     # gives it back when held long enough
        turns.done()
    Without a running app loop (tools, tests) it does nothing."""

    def __init__(self, needs: Iterable[str], label, priority: int = JOB, every: float = 2.0):
        self.needs, self.label, self.priority, self.every = set(needs), label, priority, every
        self._ticket: Optional[_Ticket] = None
        self._since = 0.0

    def take(self) -> None:
        if self._ticket is not None:
            return
        loop = resources._loop
        if loop is None or not loop.is_running() or _on_loop(loop):
            return
        self._ticket = asyncio.run_coroutine_threadsafe(resources.acquire(self.needs, self.label, self.priority), loop).result()
        self._since = time.monotonic()

    def pause(self) -> None:
        if self._ticket is not None and time.monotonic() - self._since >= self.every:
            self.done()

    def done(self) -> None:
        if self._ticket is not None:
            resources.release(self._ticket)
            self._ticket = None
