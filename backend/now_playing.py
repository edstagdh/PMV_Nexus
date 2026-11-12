"""
Currently Playing (2.20.0): which videos are being played right now, for the
"Currently Playing" page (permission live.view).

Every player tab reports while it plays (POST /api/now-playing, about every
20 s, and once when it stops). Only kept in memory: which tab plays which
video and when it last said so. Nothing about who plays it, or where in the
video they are, ever leaves this module -- the page gets the videos only.
A tab that stops reporting (closed, offline, asleep) drops out after
EXPIRE_SECONDS.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

EXPIRE_SECONDS = 90  # players report every 20 s; background tabs may be slower
MAX_TABS = 500  # a runaway client can't grow this without bound


class NowPlaying:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # tab id -> (video id, first reported at, last reported at)
        self._tabs: dict[str, tuple[str, float, float]] = {}
        self._on_change: Optional[Callable[[], None]] = None
        self._last_ids: tuple[str, ...] = ()

    def set_on_change(self, fn: Callable[[], None]) -> None:
        self._on_change = fn

    def report(self, tab: str, video_id: Optional[str]) -> None:
        """`video_id` None = this tab stopped playing."""
        now = time.time()
        with self._lock:
            if video_id is None:
                self._tabs.pop(tab, None)
            else:
                old = self._tabs.get(tab)
                started = old[1] if old and old[0] == video_id else now
                if old is None and len(self._tabs) >= MAX_TABS:
                    return
                self._tabs[tab] = (video_id, started, now)
        self.sweep()

    def video_ids(self) -> list[str]:
        """Videos playing now, the most recently started first (each once)."""
        self.sweep()
        with self._lock:
            first: dict[str, float] = {}
            for vid, started, _ in self._tabs.values():
                first[vid] = max(first.get(vid, 0), started)
        return sorted(first, key=lambda v: -first[v])

    def sweep(self) -> None:
        """Drops tabs that went quiet; tells the live updates when the list changed."""
        cutoff = time.time() - EXPIRE_SECONDS
        with self._lock:
            for tab in [t for t, v in self._tabs.items() if v[2] < cutoff]:
                del self._tabs[tab]
            ids = tuple(sorted({v[0] for v in self._tabs.values()}))
            changed = ids != self._last_ids
            self._last_ids = ids
        if changed and self._on_change:
            self._on_change()


now_playing = NowPlaying()
