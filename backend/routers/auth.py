"""
Login/logout/auth-status. login and logout are the only routes reachable
with no existing session at all (no Depends(get_current_user)), by
definition -- everything else in the app requires one of these to have
succeeded first.
"""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from loguru import logger

from .. import ip_privacy
from ..hardening import login_throttle

from ..config import get_settings
from ..library_manager import library_manager
from ..models import LoginRequest
from ..security import (
    COOKIE_NAME,
    verify_password,
    hash_password,
    create_session_token,
    get_current_user,
    AuthContext,
    setup_required,
    complete_first_run_setup,
    password_policy_rules,
)
from pydantic import BaseModel

router = APIRouter()


class FirstRunSetupRequest(BaseModel):
    setup_code: str
    username: str
    password: str


def _start_session(user, response: Response) -> dict:
    """Issues the session cookie and returns the same AuthContext shape as
    /api/auth/status (permissions included -- see the note in login())."""
    role = library_manager.db.get_role(user.role)
    permissions = library_manager.db.effective_permissions(user)
    library_manager.db.update_last_login(user.id, time.time())
    token = create_session_token(user, permissions)
    response.set_cookie(
        key=COOKIE_NAME, value=token, httponly=True, samesite="lax",
        max_age=get_settings().auth.session_ttl_hours * 3600,
    )
    return AuthContext(
        id=user.id, username=user.username, role=user.role,
        permissions=permissions, token_version=user.token_version,
    ).model_dump()


@router.get("/api/auth/password-policy")
def password_policy():
    """Unauthenticated (the setup form needs it): the password rules, for the live checklist."""
    return password_policy_rules()


@router.get("/api/setup/status")
def first_run_setup_status():
    """Unauthenticated: tells the login screen whether to show the
    "Create admin account" form instead (no accounts exist yet)."""
    return {"setup_required": setup_required()}


@router.post("/api/setup/admin")
def first_run_setup(payload: FirstRunSetupRequest, response: Response):
    try:
        user = complete_first_run_setup(payload.setup_code, payload.username, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "user": _start_session(user, response)}


_DUMMY_HASH: list = []


def _dummy_hash() -> str:
    if not _DUMMY_HASH:
        _DUMMY_HASH.append(hash_password("not-a-real-password-just-for-timing"))
    return _DUMMY_HASH[0]


@router.post("/api/auth/login")
async def login(payload: LoginRequest, request: Request, response: Response):
    # As logging.ip_mode allows (hashed by default) -- never the raw address unless set to "full".
    client = ip_privacy.apply(request.client.host if request.client else None)

    def failed(reason: str) -> None:
        # Shown in Admin -> Logs; the password is never logged.
        logger.bind(kind="action", user=payload.username[:64], source="auth", category="auth",
                    event="auth.login_failed", ip=client, reason=reason).warning(
            f"Sign-in failed for '{payload.username[:64]}' ({reason})")

    # Too many failed sign-ins for this name or from this address: wait (hardening.LoginThrottle).
    keys = (f"user:{payload.username.strip().casefold()[:64]}", f"ip:{request.client.host if request.client else '?'}")
    wait = login_throttle.wait_seconds(*keys)
    if wait:
        failed("throttled")
        raise HTTPException(status_code=429, detail=f"Too many failed sign-ins — try again in {max(1, round(wait / 60))} min",
                            headers={"Retry-After": str(wait)})

    user = library_manager.db.get_user_by_username(payload.username)
    # bcrypt is blocking/CPU-bound -- run it off the event loop thread so a
    # login attempt can't stall every other in-flight request. An unknown
    # name is checked against a dummy hash, so it takes as long as a wrong
    # password (the timing doesn't tell which names exist).
    password_ok = await asyncio.to_thread(verify_password, payload.password,
                                          user.password_hash if user else _dummy_hash())
    if not user or not password_ok:
        login_throttle.failed(*keys)
        failed("unknown user" if not user else "wrong password")
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not user.is_active:
        # Only said to someone who knows the password.
        failed("account disabled")
        raise HTTPException(status_code=401, detail="This account has been disabled. Contact an administrator.")
    login_throttle.succeeded(*keys)

    role = library_manager.db.get_role(user.role)
    permissions = library_manager.db.effective_permissions(user)

    library_manager.db.update_last_login(user.id, time.time())
    token = create_session_token(user, permissions)

    settings = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.auth.session_ttl_hours * 3600,
    )
    # Returns the SAME shape /api/auth/status returns (an AuthContext,
    # which includes permissions) -- not user.to_public(), which has no
    # permissions field. A shape mismatch here was a real past bug: the
    # frontend's store.currentUser.permissions ended up undefined right
    # after login, so hasPermission() failed closed for everything until
    # the next page load re-fetched a properly-shaped user via
    # /api/auth/status.
    auth_ctx = AuthContext(
        id=user.id,
        username=user.username,
        role=user.role,
        permissions=permissions,
        token_version=user.token_version,
    )
    logger.bind(kind="action", user=user.username, source="auth", category="auth",
                event="auth.login", ip=client).info(f"{user.username} signed in")
    return {"ok": True, "user": auth_ctx.model_dump()}


@router.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/api/auth/status")
def auth_status(user: AuthContext = Depends(get_current_user)):
    # Includes role/permissions (not just a bare username) -- the frontend
    # uses this to gate admin-only UI (Settings gear, Add-video button,
    # per-item action buttons, which view modes are offered, etc).
    return {"authenticated": True, "user": user.model_dump()}
