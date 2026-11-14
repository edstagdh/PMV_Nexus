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

from typing import Iterable

# key: (area, label, what it allows)
CATALOG: dict[str, tuple[str, str, str]] = {
    "library.view": ("Watching", "Browse the library", "See the library, video pages, playlists (and make their own)"),
    "library.stream": ("Watching", "Play videos", "Stream and download the video files"),
    "library.search": ("Watching", "Search", "Use search and filters"),
    "live.view": ("Watching", "Currently Playing", "See which videos are being played right now (never who plays them)"),
    "videos.add": ("Videos", "Add videos", "Add videos by link, retry failed downloads"),
    "videos.upload": ("Videos", "Upload videos", "Upload videos from their computer"),
    "videos.edit": ("Videos", "Edit details", "Edit details, rename, thumbnails, previews, bulk edit"),
    "videos.delete": ("Videos", "Remove videos", "Remove videos from the library (and delete their files)"),
    "videos.maintain": ("Videos", "Re-encode & repair", "Re-encode, repair/update one video or all of them"),
    "names.manage": ("Library care", "Names", "Rename and merge tags / performers / music, spellings"),
    "performers.manage": ("Library care", "Performers", "Link performers to ThePornDB, split names, run matching"),
    "duplicates.manage": ("Library care", "Duplicates", "Duplicate scan, merge and ignore duplicates"),
    "maintenance.run": ("Library care", "Maintenance", "Sync with PMVHaven, untracked files"),
    "songs.manage": ("Library care", "Songs", "Find the songs in videos, add or dismiss the suggestions"),
    "tasks.view": ("Server", "See Tasks", "The Tasks page: what's running and waiting"),
    "tasks.manage": ("Server", "Manage Tasks", "Reorder the queues, stop running jobs"),
    "stats.view": ("Server", "Stats", "The Stats page"),
    "logs.view": ("Server", "Logs", "Read the logs"),
    "settings.manage": ("Server", "Server Settings", "Server Settings, Scheduler, Backups, Tools"),
    "self.settings": ("Own account", "Own settings", "Change their own My Settings"),
    "self.password_reset": ("Own account", "Own password", "Change their own password"),
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
