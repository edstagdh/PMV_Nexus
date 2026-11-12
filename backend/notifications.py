"""
Notifications (2.10.0): what finished or went wrong in the background.

    notify("download", "success", "Downloaded “Title”", link="/video/<id>", user_id=item.added_by)

Each one is saved (table `notifications`, see LibraryDB) and announced to
open pages through the live-update stream ("notifications" change). The UI
pops a toast for new ones (if the person's Settings allow that category)
and keeps them in the notification center (the bell); the bell's badge
counts unread warnings and errors.

Who sees it: `user_id` = that user (e.g. the person who added the video);
None = the admins (permission settings.manage). Admins see both.

Categories: download, reencode, repair, upload, share, job, system.
Levels: success, info, warning, error.

Never put secrets, codes or phone numbers in a notification.
"""
from __future__ import annotations

import time
from typing import Optional

from loguru import logger

KEEP_DAYS = 30
KEEP_MAX = 2000
CATEGORIES = ("download", "reencode", "repair", "upload", "share", "job", "system")
LEVELS = ("success", "info", "warning", "error")

_db = None
_added = 0


def bind(db) -> None:
    global _db
    _db = db


def notify(category: str, level: str, title: str, body: Optional[str] = None, link: Optional[str] = None,
           user_id: Optional[str] = None) -> None:
    """Never raises: a notification failing must not break the work it reports on."""
    global _added
    if _db is None:
        return
    try:
        _db.add_notification(user_id, category if category in CATEGORIES else "system",
                             level if level in LEVELS else "info", title[:200], (body or None) and body[:1000], link)
        _db._note_change(["notifications"])
        if category in ("download", "reencode", "upload", "share", "job") and level in ("success", "error"):
            from . import stats_store
            stats_store.bump(f"{category}s" if level == "success" else f"{category}_failures")
        _added += 1
        if _added % 200 == 0:
            _db.prune_notifications(time.time() - KEEP_DAYS * 86400, KEEP_MAX)
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).debug("Could not save a notification")


def video_link(item) -> str:
    return f"/video/{item.id}"


def title_of(item) -> str:
    return f"“{(getattr(item, 'title', '') or 'a video')[:80]}”"
