"""Discover/Random: picks a random downloaded video for a user, never
repeating one until the user has been shown the whole library. When
everything has been seen, the history is reset except for the most recent
KEEP_RECENT_FRACTION of the library, so the next picks still avoid the
videos shown most recently."""
from __future__ import annotations

import math
import random

KEEP_RECENT_FRACTION = 0.2


class DiscoverMixin:

    def pick_discover_item(self, user_id: str) -> dict | None:
        pool = [i.id for i in self._items.values() if i.status == "done" and i.video_path]
        if not pool:
            return None
        pool_set = set(pool)
        seen = [i for i in self.db.list_discover_history(user_id) if i in pool_set]  # newest first
        unseen = list(pool_set.difference(seen))
        reset = False
        if not unseen:
            keep = seen[:math.ceil(len(pool) * KEEP_RECENT_FRACTION)] if len(pool) > 1 else []
            self.db.reset_discover_history(user_id, keep)
            unseen = list(pool_set.difference(keep)) or pool
            seen = keep
            reset = True
        choice = random.choice(unseen)
        self.db.record_discover(user_id, choice)
        return {"id": choice, "seen": len(seen) + 1, "total": len(pool), "reset": reset}
