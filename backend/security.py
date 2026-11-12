from __future__ import annotations

import hmac
import re
import uuid
from typing import Optional

import bcrypt
from fastapi import Depends, Request, HTTPException, status
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from loguru import logger
from pydantic import BaseModel

from .config import get_settings
from .models import User

COOKIE_NAME = "pmv_manager_session"


def _serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    return URLSafeTimedSerializer(settings.app_secret_key, salt="pmv-manager-auth")


# ============================================================
# PASSWORDS
# ============================================================
def hash_password(password: str) -> str:
    """Bcrypt-hashes a plaintext password for storage. Blocking/CPU-bound --
    call via asyncio.to_thread from async request handlers (e.g. the login
    route in main.py, or the future /api/users routes when an admin
    creates/resets an account) so it doesn't stall the event loop. Fine to
    call directly from sync contexts, like this module's own
    ensure_bootstrap_admin() below or the create_admin.py CLI, since those
    only ever run once at startup / as a one-off script, not per request."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Checks a plaintext password against a stored bcrypt hash. Same
    blocking-call caveat as hash_password() above -- wrap in
    asyncio.to_thread when called from an async route. Never raises -- a
    malformed/corrupt hash (which shouldn't happen, but isn't worth
    crashing the login route over) is treated as "doesn't match" rather
    than propagating an exception."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:  # noqa: BLE001
        return False


# Password policy -- the one place that decides, used by first-run setup,
# changing your own password, and an admin creating a user or resetting a
# password. Kept easy to type with a TV remote on an on-screen keyboard: no
# required capitals or symbols (they mean switching keyboard pages); length
# and "not guessable" do the work instead. password_policy_rules() serves the
# same rules to the interface for its live checklist.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 64
_COMMON_PASSWORDS = frozenset({
    "password", "password1", "password12", "password123", "passw0rd", "12345678", "123456789", "1234567890",
    "87654321", "11111111", "00000000", "qwerty12", "qwerty123", "qwertyui", "asdfghjk", "abc12345", "abcd1234",
    "1q2w3e4r", "1qaz2wsx", "iloveyou1", "letmein1", "welcome1", "admin123", "administrator", "changeme1",
    "football1", "monkey123", "dragon123", "sunshine1", "princess1", "baseball1", "trustno1", "pmvhaven1",
    "pmvhaven123",
})


def _is_simple_run(password: str) -> bool:
    """All one character, or a straight run like 12345678 / abcdefgh / 87654321."""
    p = password.lower()
    if len(set(p)) == 1:
        return True
    steps = {ord(b) - ord(a) for a, b in zip(p, p[1:])}
    return steps in ({1}, {-1})


def password_problems(password: str, username: Optional[str] = None) -> list[str]:
    """Every rule `password` breaks (empty list = acceptable)."""
    password = password or ""
    found = []
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        found.append(f"Use {PASSWORD_MIN_LENGTH} to {PASSWORD_MAX_LENGTH} characters.")
    if not (any(c.isalpha() for c in password) and any(c.isdigit() for c in password)):
        found.append("Use at least one letter and one number.")
    name = (username or "").strip().lower()
    if len(name) >= 3 and name in password.lower():
        found.append("Don't include your username.")
    if password.lower() in _COMMON_PASSWORDS or _is_simple_run(password):
        found.append("That password is too easy to guess.")
    return found


def password_policy_rules() -> dict:
    return {
        "min_length": PASSWORD_MIN_LENGTH, "max_length": PASSWORD_MAX_LENGTH,
        "rules": [
            {"id": "length", "text": f"{PASSWORD_MIN_LENGTH} to {PASSWORD_MAX_LENGTH} characters"},
            {"id": "mix", "text": "At least one letter and one number"},
            {"id": "username", "text": "Doesn't include your username"},
            {"id": "common", "text": "Not an easy-to-guess password (like 12345678 or password1)"},
        ],
        "note": "Capitals and symbols are allowed but not required.",
    }


def validate_password_policy(password: str, username: Optional[str] = None) -> Optional[str]:
    """None if `password` is acceptable, else one message naming every broken
    rule -- callers reject the request with it as the detail."""
    problems = password_problems(password, username)
    return " ".join(problems) if problems else None


# ============================================================
# SESSION TOKEN
# ============================================================
class AuthContext(BaseModel):
    """What get_current_user() resolves a valid session cookie down to.
    Built entirely from the cookie's own signed payload -- id/username/
    role/permissions were all baked in at login time (see
    create_session_token()) -- so authorizing an ordinary request never
    costs a database query. The one thing that IS checked against live
    server state on every request is token_version, via a cheap in-memory
    cache (see _get_cached_token_version()'s docstring) -- that's what
    makes a role change / deactivation / password reset take effect on
    the affected user's very next request instead of only once their old
    cookie naturally expires."""
    id: str
    username: str
    role: str
    permissions: list[str]
    token_version: int

    def has_permission(self, permission: str) -> bool:
        return "*" in self.permissions or permission in self.permissions


def create_session_token(user: User, permissions: list[str]) -> str:
    """Builds the signed session cookie payload for a freshly-authenticated
    user. `permissions` is passed in explicitly (the caller already looked
    it up via db.get_role(user.role) as part of the login flow) rather
    than resolved again in here, since this module deliberately doesn't
    reach into the database for the common case -- see the module-level
    note on _token_version_cache below for the one place it does."""
    payload = {
        "user_id": user.id,
        "username": user.username,
        "role": user.role,
        "permissions": permissions,
        "token_version": user.token_version,
    }
    return _serializer().dumps(payload)


def _read_session_payload(token: str) -> Optional[dict]:
    settings = get_settings()
    max_age = settings.auth.session_ttl_hours * 3600
    try:
        return _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


# ============================================================
# SESSION INVALIDATION
#
# In-memory cache of the current token_version per user_id, so that
# checking whether a session cookie is still valid costs one dict lookup,
# not a database query, on every ordinary request. Populated lazily (the
# first request for a given user_id after a server restart pays for one
# DB read to warm the cache) and kept in sync immediately by
# invalidate_user_sessions() below, which callers MUST use any time a
# change should force that user's existing sessions to stop working right
# away rather than just whenever their cookie eventually expires on its
# own: a role change, deactivating/reactivating an account, or a password
# change/reset (self-service or admin-initiated).
#
# Safe as a plain in-process dict specifically because this app is
# explicitly single-process/single-instance (see the README's
# "Reliability & design notes") -- there is no second worker process that
# could be holding a stale copy of this cache.
# ============================================================
_token_version_cache: dict[str, int] = {}


def _get_cached_token_version(user_id: str) -> Optional[int]:
    """Returns the current token_version for user_id, using the in-memory
    cache when warm and falling back to a single DB read (then caching the
    result) on a cold cache -- e.g. right after a server restart, or the
    very first request from a given user in this process's lifetime.
    Returns None if the account no longer exists or has been deactivated,
    which get_current_user() treats the same as "not authenticated"."""
    if user_id in _token_version_cache:
        return _token_version_cache[user_id]
    # Deferred import: avoids a module-load-time circular import between
    # security.py and library_manager.py. library_manager.py itself never
    # imports security.py, so this dependency is safely one-directional --
    # importing lazily here just keeps that explicit rather than accidental.
    from .library_manager import library_manager
    user = library_manager.db.get_user_by_id(user_id)
    if not user or not user.is_active:
        return None
    _token_version_cache[user_id] = user.token_version
    return user.token_version


def invalidate_user_sessions(user_id: str) -> int:
    """Bumps user_id's token_version in the database AND updates the
    in-memory cache in the same call, so every existing session cookie for
    that user (on any device/browser) fails get_current_user()'s check on
    its very next request. Call this after: a role change, deactivating or
    reactivating an account, or a password change/reset. Returns the new
    token_version (not currently used by callers, but handy for logging)."""
    from .library_manager import library_manager
    new_version = library_manager.db.bump_token_version(user_id)
    _token_version_cache[user_id] = new_version
    invalidate_permissions(user_id)
    return new_version


def clear_cached_token_version(user_id: str) -> None:
    """Drops user_id from the in-memory cache entirely -- call this when a
    user is DELETED outright (as opposed to deactivated), so a stale cache
    entry can't linger for an id that no longer exists in the database at
    all."""
    _token_version_cache.pop(user_id, None)
    invalidate_permissions(user_id)


# ============================================================
# AUTH DEPENDENCIES
# ============================================================
async def get_current_user(request: Request) -> AuthContext:
    """FastAPI dependency: raises 401 if the request has no valid,
    still-current session cookie. "Still-current" means both: the
    signature/expiry check passes (same mechanism as before this
    rewrite), AND the cookie's embedded token_version still matches
    what's on record for that user right now (see
    _get_cached_token_version) -- so a user who was just demoted,
    deactivated, or had their password changed is logged out on their
    very next request rather than staying logged in until the cookie's
    own multi-day expiry."""
    token = request.cookies.get(COOKIE_NAME)
    payload = _read_session_payload(token) if token else None
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user_id = payload.get("user_id")
    current_version = _get_cached_token_version(user_id) if user_id else None
    if current_version is None or current_version != payload.get("token_version"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired -- please log in again")

    live = effective_permissions(user_id)
    return AuthContext(
        id=user_id,
        username=payload.get("username", ""),
        role=live[0] if live else payload.get("role", "user"),
        permissions=live[1] if live else payload.get("permissions", []),
        token_version=payload.get("token_version", 0),
    )


# ------------------------------------------------------------------ live permissions (2.12.0)
_perm_cache: dict = {}


def effective_permissions(user_id: str):
    """(role, permissions) for user_id right now: role + groups + own grants
    (backend/permissions.py). Cached; invalidate_permissions() after any change
    to roles, groups, memberships or a person's grants -- it then applies on
    their next request, without signing them out."""
    hit = _perm_cache.get(user_id)
    if hit is not None:
        return hit
    from .library_manager import library_manager
    user = library_manager.db.get_user_by_id(user_id)
    if not user:
        return None
    hit = (user.role, library_manager.db.effective_permissions(user))
    _perm_cache[user_id] = hit
    return hit


def invalidate_permissions(user_id=None) -> None:
    if user_id is None:
        _perm_cache.clear()
    else:
        _perm_cache.pop(user_id, None)


def require_any(*permissions: str):
    """Like require_permission(), but any one of `permissions` is enough."""

    async def _dependency(user: AuthContext = Depends(get_current_user)) -> AuthContext:
        if not any(user.has_permission(p) for p in permissions):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail=f"Missing required permission: {' or '.join(permissions)}")
        return user

    return _dependency


def require_permission(permission: str):
    """Returns a FastAPI dependency that 401s (not authenticated at all) or
    403s (authenticated, but this user's role doesn't grant `permission`)
    unless the request's session has that permission. Usage in a route:

        @app.post("/api/library/{video_id}/rename")
        async def rename_item(..., user: AuthContext = Depends(require_permission("library.manage"))):
            ...

    Not yet applied to any route in main.py as of this change -- that's
    the next step (route-by-route permission gating). This function and
    require_admin() below exist now so that step is purely additive."""

    async def _dependency(user: AuthContext = Depends(get_current_user)) -> AuthContext:
        if not user.has_permission(permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing required permission: {permission}")
        return user

    return _dependency


async def require_admin(user: AuthContext = Depends(get_current_user)) -> AuthContext:
    """Shortcut for "this route is admin-only". Checks for the literal
    wildcard permission ("*") rather than `user.role == "admin"`, so it
    stays correct even if a future role ends up holding most, but not
    quite all, permissions -- role NAME is not what's being checked,
    actual grant is."""
    if not user.has_permission("*"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


# ============================================================
# EXTERNAL API KEY (unrelated to user accounts -- unchanged)
# ============================================================
def verify_api_key(request: Request) -> None:
    """Alternate auth path for external tools (e.g. the PMVHaven userscript)
    that can't use the cookie-based session -- checked via a static header
    instead."""
    settings = get_settings()
    if not settings.app_api_key:
        raise HTTPException(status_code=503, detail="API key auth is not configured on the server")
    provided = request.headers.get("X-API-Key", "")
    if not hmac.compare_digest(provided, settings.app_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")


# ============================================================
# BOOTSTRAP ADMIN
# ============================================================
def ensure_bootstrap_admin() -> None:
    """Called once at server startup (see main.py's lifespan(), before
    library_manager.start()). If the `users` table is completely empty --
    a genuinely fresh install, or a pre-upgrade database that just picked
    up the new users/roles/user_settings tables -- creates exactly one
    admin account from INITIAL_ADMIN_USERNAME / INITIAL_ADMIN_PASSWORD in
    .env. Those two variables are ONLY ever read here, and ONLY matter
    while `users` is empty: this function returns immediately, doing
    nothing, if even one user already exists. Once that first admin has
    been created, INITIAL_ADMIN_USERNAME/INITIAL_ADMIN_PASSWORD can be
    safely deleted from .env (harmless to leave them in place too, since
    they'll simply never be read again).

    If the table is empty and those variables are NOT set (the normal case
    now), first-run setup is enabled instead: a one-time setup code is
    printed to the console/log, and the browser shows a "Create admin
    account" form that requires it (see start_first_run_setup())."""
    from .library_manager import library_manager
    if not library_manager.db.is_users_empty():
        return

    settings = get_settings()
    username = settings.initial_admin_username
    password = settings.initial_admin_password
    if not username or not password:
        start_first_run_setup()
        return

    user = User(
        id=str(uuid.uuid4()),
        username=username,
        password_hash=hash_password(password),
        role="admin",
        display_name="Administrator",
    )
    try:
        library_manager.db.create_user(user)
    except Exception as exc:  # noqa: BLE001
        # Extremely unlikely in practice (single-process app, this only
        # ever runs once at startup before the server accepts any
        # requests) -- but don't let a transient DB hiccup here silently
        # leave the app with zero admin accounts and no visible error.
        raise RuntimeError(f"Could not create the initial admin account: {exc}") from exc

    logger.info(
        "Bootstrapped the initial admin account '{}' from INITIAL_ADMIN_USERNAME/"
        "INITIAL_ADMIN_PASSWORD in .env. Those two variables are no longer "
        "needed and can now be removed from .env.",
        username,
    )


# ------------------------------------------------------------ first-run setup
# While no account exists, the browser offers a "Create admin account" form.
# It requires a one-time code printed to the server console/log, so whoever
# first reaches the web UI over the network cannot claim the admin account
# without also having access to the machine running the app.
_setup_code: Optional[str] = None


def start_first_run_setup() -> None:
    global _setup_code
    import secrets as pysecrets
    _setup_code = "-".join(pysecrets.token_hex(2).upper() for _ in range(3))
    banner = "=" * 64
    logger.warning(
        "\n{0}\n  FIRST-RUN SETUP: no accounts exist yet.\n"
        "  Open the web UI and create the admin account using this code:\n\n"
        "      {1}\n\n  (valid until the admin account is created or the app restarts)\n{0}",
        banner, _setup_code,
    )


def setup_required() -> bool:
    from .library_manager import library_manager
    return _setup_code is not None and library_manager.db.is_users_empty()


def complete_first_run_setup(code: str, username: str, password: str) -> User:
    """Creates the first admin. Raises ValueError with a user-facing message."""
    global _setup_code
    from .library_manager import library_manager
    if not setup_required():
        raise ValueError("Setup has already been completed")
    if not hmac.compare_digest((code or "").strip().upper(), _setup_code):
        raise ValueError("Wrong setup code -- copy it from the app's console window or log")
    username = (username or "").strip()
    if not username:
        raise ValueError("Username is required")
    problem = validate_password_policy(password, username)
    if problem:
        raise ValueError(problem)
    user = User(
        id=str(uuid.uuid4()), username=username, password_hash=hash_password(password),
        role="admin", display_name="Administrator",
    )
    library_manager.db.create_user(user)
    _setup_code = None
    logger.info("First-run setup complete: created admin account '{}'", username)
    return user
