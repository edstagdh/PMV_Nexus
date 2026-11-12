"""
PMVHaven-platform site-connection routes -- login status + triggering a
new login for a configured site (pmvhaven, porntubeai, ...). Admin-only
(settings.manage) -- regular users have no reason to see or change these,
since they can't add/download videos in the first place.

Slated to move under the Admin Portal's Connections page in a later
roadmap step -- these routes themselves won't change, just where the
frontend links to them from.
"""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException

from ..config import get_settings
from ..pmv_client import get_client, PMVLoginError
from ..security import require_permission, AuthContext
from ..sites import list_site_keys

router = APIRouter()


@router.get("/api/pmv/status")
def pmv_status(user: AuthContext = Depends(require_permission("settings.manage"))):
    """Login status for every configured site (pmvhaven, porntubeai, ...),
    keyed by site key -- e.g. {"pmvhaven": {"logged_in": true},
    "porntubeai": {"logged_in": false}}."""
    sites = get_settings().sites
    return {key: {"logged_in": get_client(key).has_saved_session(), "label": sites[key].label or key,
                  "domain": urlparse(sites[key].base_url).netloc} for key in list_site_keys()}


@router.post("/api/pmv/login")
async def pmv_login(site: str = "pmvhaven", user: AuthContext = Depends(require_permission("settings.manage"))):
    """Logs into one specific site -- pass ?site=porntubeai to log into
    the sister site instead of the default (pmvhaven). Same shared
    PMVHAVEN_EMAIL/PMVHAVEN_PASSWORD credentials are used regardless of
    which site is being logged into."""
    try:
        await get_client(site).login()
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown site '{site}'")
    except PMVLoginError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Login failed: {exc}") from exc
    return {"ok": True, "site": site}
