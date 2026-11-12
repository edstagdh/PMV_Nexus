"""
Self-service routes for the currently-authenticated user's own account:
per-user settings (theme/page-size/view), own password change, and
self-account-deletion with double confirmation. Available to every
authenticated user regardless of role -- both "admin" and "user" already
have "self.settings"/"self.password_reset" (see db.py's default role
seeding), and self-account-deletion has no permission gate beyond just
being logged in, since it only ever acts on the caller's own row.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Response

from ..config import get_settings
from ..library_manager import library_manager
from ..models import ChangeOwnPasswordRequest, SelfDeleteAccountRequest, UserSettingsUpdateRequest
from ..security import (
    COOKIE_NAME,
    verify_password,
    hash_password,
    create_session_token,
    get_current_user,
    require_permission,
    invalidate_user_sessions,
    clear_cached_token_version,
    validate_password_policy,
    AuthContext,
)

router = APIRouter()

# Backs the frontend's userSettings.js -- theme/page-size/view preferences
# that follow the account across browsers/devices. Only recognized keys
# with a value of the expected type/range are actually saved (see
# update_my_settings below) -- anything else in a PATCH payload is
# silently dropped rather than erroring the whole request.
_ALLOWED_USER_SETTINGS = {
    "theme": lambda v: isinstance(v, str) and v in ("midnight", "slate", "daylight"),
    "page_size": lambda v: isinstance(v, int) and v in (25, 50, 100, 200),
    "view": lambda v: isinstance(v, str) and v in ("list", "grid", "table"),
    # New interface preferences (badges, text size, default sort, ...), kept
    # as one object the interface validates itself; capped in size.
    "ui": lambda v: isinstance(v, dict) and len(json.dumps(v)) <= 8000,
}


@router.get("/api/me/settings")
def get_my_settings(user: AuthContext = Depends(require_permission("self.settings"))):
    """Returns this user's saved preferences as a flat {key: value} object
    -- see db.py's user_settings table. A key simply isn't present in the
    response until it's been saved at least once; the frontend already
    falls back to its localStorage cache/defaults for anything missing
    here."""
    return library_manager.db.get_user_settings(user.id)


@router.patch("/api/me/settings")
def update_my_settings(payload: UserSettingsUpdateRequest, user: AuthContext = Depends(require_permission("self.settings"))):
    clean = {
        k: v for k, v in payload.settings.items()
        if k in _ALLOWED_USER_SETTINGS and _ALLOWED_USER_SETTINGS[k](v)
    }
    if clean:
        library_manager.db.set_user_settings(user.id, clean)
    return {"ok": True, "saved": clean}


@router.post("/api/me/password")
async def change_own_password(payload: ChangeOwnPasswordRequest, response: Response, user: AuthContext = Depends(require_permission("self.password_reset"))):
    target = library_manager.db.get_user_by_id(user.id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    password_ok = await asyncio.to_thread(verify_password, payload.current_password, target.password_hash)
    if not password_ok:
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    password_error = validate_password_policy(payload.new_password, target.username)
    if password_error:
        raise HTTPException(status_code=400, detail=password_error)
    if await asyncio.to_thread(verify_password, payload.new_password, target.password_hash):
        raise HTTPException(status_code=400, detail="Choose a password different from your current one.")

    target.password_hash = hash_password(payload.new_password)
    target.touch()
    library_manager.db.update_user(target)
    new_version = invalidate_user_sessions(user.id)

    # Unlike the admin-reset-someone-else's-password route (in
    # routers/users.py), THIS route would otherwise immediately invalidate
    # the very cookie the caller is using right now -- logging them out of
    # the session they just used to make the change, poor UX for a
    # self-service action. Re-issue a fresh, already-current cookie in the
    # same response so they stay logged in.
    role = library_manager.db.get_role(target.role)
    permissions = library_manager.db.effective_permissions(target)
    target.token_version = new_version
    fresh_token = create_session_token(target, permissions)
    settings = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=fresh_token,
        httponly=True,
        samesite="lax",
        max_age=settings.auth.session_ttl_hours * 3600,
    )
    return {"ok": True}


@router.post("/api/me/delete-account")
async def delete_own_account(payload: SelfDeleteAccountRequest, response: Response, user: AuthContext = Depends(get_current_user)):
    target = library_manager.db.get_user_by_id(user.id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.role == "admin":
        # The single admin account can't be removed via any path,
        # including by itself -- matches routers/users.py's delete_user()
        # rule for the same reason.
        raise HTTPException(status_code=400, detail="The admin account cannot be deleted")

    # Step 1: must type their own username exactly.
    if payload.confirm_username != target.username:
        raise HTTPException(status_code=400, detail="Username confirmation does not match")

    # Step 2: must provide their current password.
    password_ok = await asyncio.to_thread(verify_password, payload.current_password, target.password_hash)
    if not password_ok:
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    invalidate_user_sessions(user.id)
    target.is_active = False
    target.touch()
    library_manager.db.update_user(target)
    library_manager.db.delete_user(user.id)
    clear_cached_token_version(user.id)

    # Clears the cookie in THIS response too -- invalidate_user_sessions()
    # above already makes the cookie fail on any FUTURE request, but this
    # is what makes the current tab's browser actually forget it right
    # away rather than the frontend having to separately call
    # /api/auth/logout.
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}
