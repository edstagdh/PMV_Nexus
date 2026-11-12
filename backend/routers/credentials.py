"""Admin -> Credentials: manage every password / API key / account setting in
the app (stored encrypted, see backend/secrets_store.py). Secret values are
write-only -- the API never returns them, except the external API key, which
the admin needs to copy into the userscript."""
from __future__ import annotations

import asyncio

from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import secrets_store
from ..config import get_settings
from ..security import AuthContext, require_permission

router = APIRouter()
_admin = require_permission("settings.manage")


class CredentialsUpdate(BaseModel):
    # name -> new value; "" or null clears it. Names not included are untouched.
    values: dict[str, Optional[str]]


class ApiKeyTest(BaseModel):
    api_key: Optional[str] = None  # test this value instead of the saved one


def reload_settings() -> None:
    """Makes running components pick up changed credentials immediately."""
    from ..library_manager import library_manager
    from ..pmv_client import _clients

    get_settings.cache_clear()
    fresh = get_settings()
    library_manager.settings = fresh
    for client in _clients.values():
        client.settings = fresh


def _field_public(name: str, d: dict, stored: dict) -> dict:
    entry = stored.get(name)
    out = {
        "name": name, "label": d["label"], "kind": d["kind"],
        "is_set": entry is not None, "updated_at": entry["updated_at"] if entry else None,
        "revealable": bool(d.get("revealable")), "generate": bool(d.get("generate")),
    }
    if d["kind"] != "secret":
        out["value"] = entry["value"] if entry else (d.get("default") or "")
    return out


@router.get("/api/credentials")
def list_credentials(user: AuthContext = Depends(_admin)):
    stored = secrets_store.read_all()
    groups = []
    for key, label, help_text in secrets_store.GROUPS:
        fields = [_field_public(n, d, stored) for n, d in secrets_store.FIELDS.items()
                  if d["group"] == key and not d.get("internal")]
        groups.append({"key": key, "label": label, "help": help_text, "fields": fields})
    return {"groups": groups, "storage": str(secrets_store.DB_FILE)}


@router.put("/api/credentials")
def update_credentials(payload: CredentialsUpdate, user: AuthContext = Depends(_admin)):
    for name, value in payload.values.items():
        d = secrets_store.FIELDS.get(name)
        if d is None or d.get("internal"):
            raise HTTPException(status_code=400, detail=f"Unknown credential: {name}")
        if d["kind"] == "number" and value and not value.strip().isdigit():
            raise HTTPException(status_code=400, detail=f"{d['label']} must be a number")
        if d["kind"] == "toggle" and value not in ("1", "0", "", None):
            raise HTTPException(status_code=400, detail=f"{d['label']} must be on or off")
    secrets_store.write(payload.values)
    reload_settings()
    return {"ok": True}


@router.post("/api/credentials/{name}/generate")
def generate_credential(name: str, user: AuthContext = Depends(_admin)):
    d = secrets_store.FIELDS.get(name)
    if not d or not d.get("generate"):
        raise HTTPException(status_code=400, detail="This credential cannot be generated")
    value = secrets_store.generate_token()
    secrets_store.write({name: value})
    reload_settings()
    return {"ok": True, "value": value}


@router.get("/api/credentials/{name}/reveal")
def reveal_credential(name: str, user: AuthContext = Depends(_admin)):
    d = secrets_store.FIELDS.get(name)
    if not d or not d.get("revealable"):
        raise HTTPException(status_code=403, detail="This credential cannot be revealed")
    return {"value": secrets_store.values().get(name)}


@router.post("/api/credentials/test/pmvhaven-api")
async def test_pmvhaven_api_key(payload: ApiKeyTest, user: AuthContext = Depends(_admin)):
    """Calls an endpoint that requires authentication (the account's favorites)
    to confirm the key is accepted."""
    settings = get_settings()
    key = (payload.api_key or "").strip() or settings.pmvhaven_api_key
    if not key:
        return {"ok": False, "message": "No API key saved yet"}
    site = settings.sites.get("pmvhaven")
    base = (site.base_url if site else "https://pmvhaven.com").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{base}/api/user/favorites", params={"limit": 1},
                                    headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    except httpx.HTTPError as exc:
        return {"ok": False, "message": f"Could not reach PMVHaven: {exc.__class__.__name__}"}
    if resp.status_code == 200:
        return {"ok": True, "message": "API key accepted by PMVHaven"}
    if resp.status_code in (401, 403):
        return {"ok": False, "message": "PMVHaven rejected this API key"}
    if resp.status_code == 429:
        return {"ok": False, "message": "Rate limited by PMVHaven -- try again in a minute"}
    return {"ok": False, "message": f"Unexpected response from PMVHaven (HTTP {resp.status_code})"}


@router.post("/api/credentials/test/virustotal")
async def test_virustotal_key(payload: ApiKeyTest, user: AuthContext = Depends(_admin)):
    """Looks up the EICAR test file's fingerprint with the typed (or saved) key."""
    from .. import upload_safety
    key = (payload.api_key or "").strip() or get_settings().virustotal_api_key
    if not key:
        return {"ok": False, "message": "No API key saved yet"}
    return await asyncio.to_thread(upload_safety.virustotal_key_test, key)
