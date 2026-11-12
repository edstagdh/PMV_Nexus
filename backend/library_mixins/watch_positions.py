"""
Continue where you left off (2.24.0): each person's place in each video
(db.watch_positions), kept in memory per person once read. The player saves it
while playing; every video the person is shown carries
"watch": {"pos": seconds, "dur": seconds} -- for the thin line on its thumbnail
and for continuing. Never sent to anyone else.
"""
from __future__ import annotations

from typing import Optional

FINISHED = 0.95  # at or past this share of the video it counts as watched (starts over next time)


class WatchPositionsMixin:
    _wp_cache: Optional[dict] = None

    def _wp_for(self, user_id: str) -> dict:
        if self._wp_cache is None:
            self._wp_cache = {}
        if user_id not in self._wp_cache:
            self._wp_cache[user_id] = self.db.watch_positions(user_id)
        return self._wp_cache[user_id]

    def save_watch_position(self, user_id: str, video_id: str, position: float, duration: float) -> dict:
        if video_id not in self._items:
            raise KeyError(video_id)
        position = max(0.0, min(float(position), float(duration)))
        if position >= duration * FINISHED:
            position = duration  # watched to the end
        self.db.set_watch_position(user_id, video_id, position, duration)
        self._wp_for(user_id)[video_id] = (position, duration)
        return {"pos": position, "dur": duration}

    def clear_watch_position(self, user_id: str, video_id: str) -> None:
        self.db.delete_watch_position(user_id, video_id)
        self._wp_for(user_id).pop(video_id, None)

    def forget_watch_positions(self, video_id: str) -> None:
        """The video left the library: everyone's place in it goes too."""
        self.db.delete_watch_position(None, video_id)
        for per_user in (self._wp_cache or {}).values():
            per_user.pop(video_id, None)

    def with_watch(self, items: list, user) -> list:
        """Adds "watch" to each video dict (this person's place in it), when there is one."""
        if user is None or not getattr(user, "id", None):
            return items
        mine = self._wp_for(str(user.id))
        out = []
        for item in items:
            if item and item.get("id") in mine:
                pos, dur = mine[item["id"]]
                item = {**item, "watch": {"pos": pos, "dur": dur}}
            out.append(item)
        return out
