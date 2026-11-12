"""
The notification center (2.10.0, backend/notifications.py): each user sees
their own notifications; admins (settings.manage) also see the admins' ones
(jobs, disk space...).

  GET    /api/notifications?after=<id>&problems=1&limit=   newest first + unread count
  POST   /api/notifications/read      {"ids": [..]} or {} for all
  DELETE /api/notifications           clears them
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..library_manager import library_manager
from ..security import AuthContext, get_current_user

router = APIRouter()


def _admin(user: AuthContext) -> bool:
    return user.has_permission("settings.manage")


class ReadRequest(BaseModel):
    ids: Optional[list[int]] = None


@router.get("/api/notifications")
def list_notifications(after: int = Query(0, ge=0), problems: bool = False, limit: int = Query(100, ge=1, le=300),
                       user: AuthContext = Depends(get_current_user)):
    db = library_manager.db
    return {"items": db.list_notifications(user.id, _admin(user), after=after, limit=limit, problems_only=problems),
            **db.notification_counts(user.id, _admin(user))}


@router.post("/api/notifications/read")
def mark_read(body: ReadRequest, user: AuthContext = Depends(get_current_user)):
    library_manager.db.mark_notifications_read(user.id, _admin(user), body.ids)
    return library_manager.db.notification_counts(user.id, _admin(user))


@router.delete("/api/notifications")
def clear(user: AuthContext = Depends(get_current_user)):
    library_manager.db.clear_notifications(user.id, _admin(user))
    return {"ok": True}
