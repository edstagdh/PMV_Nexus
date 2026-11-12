"""
Admin-only user management: list/create/edit/reset-password/delete. Every
route here requires require_admin (checks specifically for the "*"
wildcard permission -- see security.py). Regular users have no access to
any of this; their own self-service actions (change own password, delete
own account) live in routers/me.py instead.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException

from ..library_manager import library_manager
from ..models import User, CreateUserRequest, UpdateUserRequest, AdminResetPasswordRequest
from ..security import (
    hash_password,
    require_admin,
    invalidate_user_sessions,
    clear_cached_token_version,
    validate_password_policy,
    AuthContext,
)

router = APIRouter()


@router.get("/api/users")
def list_users(user: AuthContext = Depends(require_admin)):
    return [u.to_public().model_dump() for u in library_manager.db.list_users()]


@router.post("/api/users")
def create_user(payload: CreateUserRequest, user: AuthContext = Depends(require_admin)):
    if not library_manager.db.get_role(payload.role):  # any existing role (2.12.0: roles can be added)
        raise HTTPException(status_code=400, detail=f"Unknown role '{payload.role}'")

    # Single master-admin invariant -- only one account may hold the
    # "admin" role at a time; there is deliberately no way to have two.
    if payload.role == "admin" and library_manager.db.count_users_by_role("admin") > 0:
        raise HTTPException(status_code=400, detail="Only one admin account is allowed at a time")

    if library_manager.db.get_user_by_username(payload.username):
        raise HTTPException(status_code=400, detail=f"Username '{payload.username}' is already taken")

    password_error = validate_password_policy(payload.password, payload.username)
    if password_error:
        raise HTTPException(status_code=400, detail=password_error)

    new_user = User(
        id=str(uuid.uuid4()),
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    library_manager.db.create_user(new_user)
    return new_user.to_public().model_dump()


@router.patch("/api/users/{user_id}")
def update_user(user_id: str, payload: UpdateUserRequest, user: AuthContext = Depends(require_admin)):
    target = library_manager.db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.username is not None and payload.username != target.username:
        if library_manager.db.get_user_by_username(payload.username):
            raise HTTPException(status_code=400, detail=f"Username '{payload.username}' is already taken")
        target.username = payload.username

    if payload.is_active is not None and payload.is_active != target.is_active:
        # The single admin account can never be disabled -- there is no
        # recovery path in the UI if the only admin locks themselves out
        # this way (the CLI/.env bootstrap paths only help when NO admin
        # exists at all, not when one exists but is disabled).
        if target.role == "admin" and not payload.is_active:
            raise HTTPException(status_code=400, detail="The admin account cannot be disabled")
        target.is_active = payload.is_active
        if not payload.is_active:
            # Disabling should end their current session(s) right away
            # (on their next request/poll -- see invalidate_user_sessions()'s
            # docstring for the exact timing), not just block future logins.
            # invalidate_user_sessions() bumps token_version directly in
            # the DATABASE via a raw SQL increment (see
            # db.bump_token_version()) -- NOT through this `target` object,
            # which still holds the value it was loaded with at the top of
            # this function. Syncing that return value back onto `target`
            # here is required: without it, the plain target.touch() +
            # update_user(target) below would overwrite the DB's
            # freshly-bumped token_version back down to the stale value,
            # silently undoing the bump in the database while the
            # in-memory cache (already correctly bumped) stayed ahead --
            # permanently diverging the two. That was a real bug: a
            # disabled-then-re-enabled account could become permanently
            # unable to log back in, since a fresh login cookie built from
            # the DB's reverted-down value could never match the cache's
            # still-bumped value on the very next request.
            target.token_version = invalidate_user_sessions(user_id)
        # Re-enabling needs no invalidation -- nothing to log out; they
        # simply couldn't log back in until this flips.

    target.touch()
    library_manager.db.update_user(target)
    return target.to_public().model_dump()


@router.post("/api/users/{user_id}/reset-password")
def admin_reset_password(user_id: str, payload: AdminResetPasswordRequest, user: AuthContext = Depends(require_admin)):
    target = library_manager.db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    password_error = validate_password_policy(payload.new_password, target.username)
    if password_error:
        raise HTTPException(status_code=400, detail=password_error)

    target.password_hash = hash_password(payload.new_password)
    target.touch()
    library_manager.db.update_user(target)
    # Always invalidates -- a password reset must end every existing
    # session for that account, no exceptions. If the admin resets their
    # OWN password through this same route, this also logs the admin out
    # of their current session -- deliberate, for consistency: a password
    # change invalidating old sessions applies uniformly, including to
    # whoever just made the change.
    invalidate_user_sessions(user_id)
    return {"ok": True}


@router.delete("/api/users/{user_id}")
def delete_user(user_id: str, user: AuthContext = Depends(require_admin)):
    target = library_manager.db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.role == "admin":
        raise HTTPException(status_code=400, detail="The admin account cannot be deleted")

    # Matches the agreed sequence exactly: end their session(s), disable
    # login, then remove the row -- rather than deleting outright and
    # risking a request that was already in flight against their account.
    invalidate_user_sessions(user_id)
    target.is_active = False
    target.touch()
    library_manager.db.update_user(target)
    library_manager.db.delete_user(user_id)
    clear_cached_token_version(user_id)
    return {"ok": True}


# ------------------------------------------------------------------ access (2.12.0)
# Roles, groups and per-person grants (backend/permissions.py). Admin only:
# whoever edits these could otherwise give themselves anything.
import re as _re  # noqa: E402

from pydantic import BaseModel, Field  # noqa: E402

from .. import permissions as perms  # noqa: E402
from ..models import Role  # noqa: E402
from ..security import invalidate_permissions  # noqa: E402

_NAME = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}$")
BUILTIN_ROLES = ("admin", "user")


class RoleBody(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    permissions: list[str] = []


class GroupBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field("", max_length=300)
    permissions: list[str] = []
    members: list[str] = []


class AccessBody(BaseModel):
    role: str
    groups: list[str] = []
    permissions: list[str] = []


def _changed() -> None:
    invalidate_permissions()
    library_manager.db._note_change(["permissions"])  # open pages refresh what they show


@router.get("/api/access")
def get_access(user: AuthContext = Depends(require_admin)):
    """Everything the Users page needs: the permission list, roles, groups and each person's access."""
    db = library_manager.db
    groups = db.list_groups()
    users = []
    for u in db.list_users():
        users.append({**u.to_public().model_dump(), "groups": [g["id"] for g in groups if u.id in g["members"]],
                      "permissions": db.get_user_extra_permissions(u.id), "effective": db.effective_permissions(u)})
    roles = [{"name": r.name, "permissions": r.permissions if r.permissions == ["*"] else perms.valid(r.permissions), "users": db.role_user_count(r.name),
              "builtin": r.name in BUILTIN_ROLES} for r in db.list_roles()]
    return {"catalog": perms.catalog(), "roles": roles, "groups": groups, "users": users}


@router.post("/api/roles")
def create_role(body: RoleBody, user: AuthContext = Depends(require_admin)):
    name = body.name.strip().lower()
    if not _NAME.match(name):
        raise HTTPException(status_code=400, detail="Use letters, numbers, spaces, - _ . (up to 40)")
    if library_manager.db.get_role(name):
        raise HTTPException(status_code=400, detail=f"There's already a role called '{name}'")
    library_manager.db.upsert_role(Role(name=name, permissions=perms.valid(body.permissions)))
    _changed()
    return {"ok": True}


@router.put("/api/roles/{name}")
def update_role(name: str, body: RoleBody, user: AuthContext = Depends(require_admin)):
    role = library_manager.db.get_role(name)
    if not role:
        raise HTTPException(status_code=404, detail="No such role")
    if name == "admin":
        raise HTTPException(status_code=400, detail="The admin role always has every permission")
    role.permissions = perms.valid(body.permissions)
    library_manager.db.upsert_role(role)
    _changed()
    return {"ok": True}


@router.delete("/api/roles/{name}")
def delete_role(name: str, user: AuthContext = Depends(require_admin)):
    if name in BUILTIN_ROLES:
        raise HTTPException(status_code=400, detail="The admin and user roles can't be deleted")
    n = library_manager.db.role_user_count(name)
    if n:
        raise HTTPException(status_code=400, detail=f"{n} {'person has' if n == 1 else 'people have'} this role — give them another one first")
    library_manager.db.delete_role(name)
    _changed()
    return {"ok": True}


def _save_group(group_id: str, body: GroupBody) -> None:
    db = library_manager.db
    name = body.name.strip()
    if any(g["name"].casefold() == name.casefold() and g["id"] != group_id for g in db.list_groups()):
        raise HTTPException(status_code=400, detail=f"There's already a group called '{name}'")
    known = {u.id for u in db.list_users()}
    db.save_group(group_id, name, body.description.strip(), perms.valid(body.permissions))
    db.set_group_members(group_id, [m for m in dict.fromkeys(body.members) if m in known])
    _changed()


@router.post("/api/groups")
def create_group(body: GroupBody, user: AuthContext = Depends(require_admin)):
    group_id = uuid.uuid4().hex
    _save_group(group_id, body)
    return {"id": group_id}


@router.put("/api/groups/{group_id}")
def update_group(group_id: str, body: GroupBody, user: AuthContext = Depends(require_admin)):
    if not any(g["id"] == group_id for g in library_manager.db.list_groups()):
        raise HTTPException(status_code=404, detail="No such group")
    _save_group(group_id, body)
    return {"ok": True}


@router.delete("/api/groups/{group_id}")
def delete_group(group_id: str, user: AuthContext = Depends(require_admin)):
    library_manager.db.delete_group(group_id)
    _changed()
    return {"ok": True}


@router.put("/api/users/{user_id}/access")
def set_user_access(user_id: str, body: AccessBody, user: AuthContext = Depends(require_admin)):
    """One person's role, groups and own extra permissions."""
    db = library_manager.db
    target = db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if body.role != target.role:
        if target.role == "admin" or body.role == "admin":
            raise HTTPException(status_code=400, detail="There's exactly one admin; that can't be changed here")
        if not db.get_role(body.role):
            raise HTTPException(status_code=400, detail=f"Unknown role '{body.role}'")
        target.role = body.role
        target.touch()
        db.update_user(target)
    known = {g["id"] for g in db.list_groups()}
    db.set_user_groups(user_id, [g for g in dict.fromkeys(body.groups) if g in known])
    db.set_user_extra_permissions(user_id, perms.valid(body.permissions))
    _changed()
    return {"ok": True, "effective": db.effective_permissions(target)}
