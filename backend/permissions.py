"""
Permissions (2.12.0): what someone may do, granted through any mix of
  - their role (each user has one: admin, user, or roles the admin creates),
  - groups they're in (e.g. "Info helpers" with names.manage),
  - permissions given to them directly.
Their effective permissions are the union of all three (allow-only: nothing
takes a permission away). "*" (the admin role) is everything; only the admin manages users, roles,
groups and grants, and "*" can't be given to anything else.

"library.manage" -- the old catch-all for managing the library -- still
works and means all of LIBRARY_MANAGE below, so roles made before 2.12.0
keep their powers.

Effective permissions are worked out per request (cached; see
security.effective_permissions), so a change to a role, group or person
applies on that person's next click -- no new sign-in needed.
"""
from __future__ import annotations
from .i18n import L

from typing import Iterable

# key: (area, label, what it allows)
CATALOG: dict[str, tuple[str, str, str]] = {
    "library.view": (L("Watching"), L("Browse the library"), L("See the library, video pages, playlists (and make their own)")),
    "library.stream": (L("Watching"), L("Play videos"), L("Stream and download the video files")),
    "library.search": (L("Watching"), L("Search"), L("Use search and filters")),
    "live.view": (L("Watching"), L("Currently Playing"), L("See which videos are being played right now (never who plays them)")),
    "videos.add": (L("Videos"), L("Add videos"), L("Add videos by link, retry failed downloads")),
    "videos.upload": (L("Videos"), L("Upload videos"), L("Upload videos from their computer")),
    "videos.edit": (L("Videos"), L("Edit details"), L("Edit details, rename, thumbnails, previews, bulk edit")),
    "videos.delete": (L("Videos"), L("Remove videos"), L("Remove videos from the library (and delete their files)")),
    "videos.maintain": (L("Videos"), L("Re-encode & repair"), L("Re-encode, repair/update one video or all of them")),
    "names.manage": (L("Library care"), L("Names"), L("Rename and merge tags / performers / music, spellings")),
    "performers.manage": (L("Library care"), L("Performers"), L("Link performers to ThePornDB, split names, run matching")),
    "duplicates.manage": (L("Library care"), L("Duplicates"), L("Duplicate scan, merge and ignore duplicates")),
    "maintenance.run": (L("Library care"), L("Maintenance"), L("Sync with PMVHaven, untracked files")),
    "songs.manage": (L("Library care"), L("Songs"), L("Find the songs in videos, add or dismiss the suggestions")),
    "tasks.view": (L("Server"), L("See Tasks"), L("The Tasks page: what's running and waiting")),
    "tasks.manage": (L("Server"), L("Manage Tasks"), L("Reorder the queues, stop running jobs")),
    "stats.view": (L("Server"), L("Stats"), L("The Stats page")),
    "logs.view": (L("Server"), L("Logs"), L("Read the logs")),
    "settings.manage": (L("Server"), L("Server Settings"), L("Server Settings, Scheduler, Backups, Tools")),
    "self.settings": (L("Own account"), L("Own settings"), L("Change their own My Settings")),
    "self.password_reset": (L("Own account"), L("Own password"), L("Change their own password")),
}

# A list: extensions can add theirs (backend/extensions.py, 2.30.0).
LIBRARY_MANAGE = ["videos.add", "videos.upload", "videos.edit", "videos.delete", "videos.maintain",
                  "names.manage", "performers.manage", "duplicates.manage", "maintenance.run", "tasks.view", "tasks.manage",
                  "songs.manage"]
# settings.manage used to cover these too.
SETTINGS_MANAGE = ("stats.view",)


def expand(perms: Iterable[str]) -> list[str]:
    """The given grants with the old umbrella permissions spelled out."""
    out = set(perms or [])
    if "library.manage" in out:
        out.update(LIBRARY_MANAGE)
    if "settings.manage" in out:
        out.update(SETTINGS_MANAGE)
    if "tasks.manage" in out:
        out.add("tasks.view")
    return sorted(p for p in out if p in CATALOG)  # leftovers like the old "view.grid" are dropped


def valid(perms: Iterable[str]) -> list[str]:
    """Only known permission keys (and "*"), deduplicated."""
    return sorted({p for p in perms or [] if p in CATALOG or p == "library.manage"})


def catalog() -> list[dict]:
    return [{"key": k, "area": a, "label": l, "description": d} for k, (a, l, d) in CATALOG.items()]
