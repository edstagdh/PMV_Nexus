"""Extensions (2.30.0): optional add-ons in the `extensions/` folder next to run.py.
The folder isn't part of the app and isn't created by it; with no extensions,
nothing about them shows anywhere.

An extension is a Python package -- `extensions/<name>/__init__.py` -- with a
`register(api)` function, called once at start with an ExtensionAPI. Through it an
extension can:
  - add actions to a video's menu and/or the library's "picked videos" bar, and
    panels to Server settings → Extensions. Both use the app's generic forms (no
    interface code in the extension): the handler returns a form to show, the
    person fills it in, the handler gets the values and returns the next form or
    "done" -- see Form below;
  - add sources (backend/sources), credentials (encrypted, shown under Server
    settings → Credentials), permissions (Users → roles), API routes, and
    start / stop hooks;
  - keep its own data on a video (item.ext[<extension>]) and use the app's
    activity list (Tasks), notifications and proxy.
Its Python packages go in `extensions/<name>/requirements.txt`
(`python -m Tools.install_extensions` installs them; so does the Windows Setup).

A broken extension is skipped (and shown as failed under Server settings →
Extensions); the app runs without it.

Form (what an action / panel handler returns):
  {"form": {"title": str, "description": str, "submit": "Save", "step": "<yours>",
            "fields": [{"name", "label", "type", "value", "options", "required", "help", "placeholder"}]},
   "message": "shown above the form", "level": "info|success|error"}
  {"done": True, "message": "...", "level": "success"}       -- closes the dialog / shows the message
Field types: text, password, number, textarea, select, radio, checkbox, checkboxes
(options: [{"value", "label", "disabled"}]), info (just text), link (value = URL).
The handler gets an ActionContext: values (the submitted fields), step (from the
form it returned, None the first time), item_ids, user.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Union

from loguru import logger

ROOT_DIR = Path(__file__).resolve().parent.parent
EXT_DIR = ROOT_DIR / "extensions"

Result = dict
Handler = Callable[["ActionContext"], Union[Result, Awaitable[Result]]]


@dataclass
class ActionContext:
    values: dict
    step: Optional[str]
    item_ids: list[str]
    user: Any  # security.AuthContext


@dataclass
class Action:
    id: str
    label: str
    handler: Handler
    where: tuple[str, ...] = ("video", "bulk")   # a video's menu / the picked-videos bar
    icon: str = ""
    permission: Optional[str] = None
    extension: str = ""
    needs_done_video: bool = True                # only for downloaded videos


@dataclass
class Panel:
    id: str
    label: str
    handler: Handler
    permission: str = "settings.manage"
    extension: str = ""


@dataclass
class Loaded:
    name: str
    ok: bool
    version: str = ""
    description: str = ""
    error: str = ""
    startup: list = field(default_factory=list)
    shutdown: list = field(default_factory=list)


actions: dict[str, Action] = {}
panels: dict[str, Panel] = {}
loaded: list[Loaded] = []
_routers: list = []


class ExtensionAPI:
    """What `register(api)` gets. Everything else in the app is reachable too, but
    only this is kept stable between versions."""

    def __init__(self, record: Loaded):
        self._rec = record
        self.name = record.name
        self.folder = EXT_DIR / record.name

    # -- description
    def describe(self, version: str = "", description: str = "") -> None:
        self._rec.version, self._rec.description = version, description

    # -- what it adds
    def add_action(self, id: str, label: str, handler: Handler, where=("video", "bulk"), icon: str = "",
                   permission: Optional[str] = None, needs_done_video: bool = True) -> None:
        key = f"{self.name}.{id}"
        actions[key] = Action(key, label, handler, tuple(where), icon, permission, self.name, needs_done_video)

    def add_panel(self, id: str, label: str, handler: Handler, permission: str = "settings.manage") -> None:
        key = f"{self.name}.{id}"
        panels[key] = Panel(key, label, handler, permission, self.name)

    def add_router(self, router) -> None:
        _routers.append(router)

    def add_source(self, source) -> None:
        from . import sources
        sources.register(source)

    def add_credentials(self, group: str, label: str, help: str, fields: dict) -> None:
        """fields: name -> {"label", "kind": "text|secret|number|toggle", "default"?}. Shown under
        Server settings → Credentials; read with api.secret(name)."""
        from . import secrets_store
        for name, spec in fields.items():
            secrets_store.FIELDS[name] = {"group": group, **spec}
        if not any(g[0] == group for g in secrets_store.GROUPS):
            secrets_store.GROUPS.append((group, label, help))

    def add_permission(self, key: str, area: str, label: str, description: str, library_manage: bool = False) -> None:
        from . import permissions
        permissions.CATALOG[key] = (area, label, description)
        if library_manage and key not in permissions.LIBRARY_MANAGE:
            permissions.LIBRARY_MANAGE.append(key)

    def on_startup(self, fn: Callable) -> None:
        self._rec.startup.append(fn)

    def on_shutdown(self, fn: Callable) -> None:
        self._rec.shutdown.append(fn)

    # -- using the app
    @property
    def library(self):
        from .library_manager import library_manager
        return library_manager

    def settings(self):
        from .config import get_settings
        return get_settings()

    def secret(self, name: str) -> Optional[str]:
        from . import secrets_store
        return secrets_store.values().get(name)

    def data_dir(self) -> Path:
        """A folder for the extension's own files (data/extensions/<name>, next to the library database)."""
        path = self.settings().library_file.parent / "extensions" / self.name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def item_data(self, item) -> dict:
        """This extension's data on a video (saved with the video; change it, then save_item)."""
        return item.ext.setdefault(self.name, {})

    def save_item(self, item) -> None:
        item.touch()
        self.library.db.save_item(item)

    def notify(self, level: str, title: str, body: str = "", link: Optional[str] = None,
               user_id: Optional[str] = None) -> None:
        from .notifications import notify
        notify("extension", level, title, body=body, link=link, user_id=user_id)

    @property
    def activity(self):
        """backend/activity.py: `async with api.activity.track(kind, title, progress=..., source="extension")`
        shows on Tasks. With source="extension" the admins get no "finished / failed" notification for
        it -- send your own with notify()."""
        from . import activity
        return activity

    def proxy_url(self) -> Optional[str]:
        """The proxy, when Server settings → Proxy sends extensions' uploads through it."""
        from . import proxy
        return proxy.for_uploads()

    def bump_stat(self, name: str, n: int = 1) -> None:
        from . import stats_store
        stats_store.bump(name, n)


def load_all(app=None) -> None:
    """Imports every extensions/<name>/ package and calls its register(api)."""
    if loaded or not EXT_DIR.is_dir():
        return
    import sys
    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))
    for folder in sorted(p for p in EXT_DIR.iterdir() if p.is_dir() and (p / "__init__.py").is_file()):
        if folder.name.startswith((".", "_")):
            continue
        rec = Loaded(folder.name, ok=False)
        loaded.append(rec)
        try:
            module = importlib.import_module(f"extensions.{folder.name}")
            register = getattr(module, "register", None)
            if not callable(register):
                raise RuntimeError("no register(api) function in its __init__.py")
            register(ExtensionAPI(rec))
            rec.ok = True
            logger.info("Extension loaded: {}{}", folder.name, f" {rec.version}" if rec.version else "")
        except Exception as exc:  # noqa: BLE001 -- one broken extension mustn't stop the app
            rec.error = f"{type(exc).__name__}: {exc}"[:500]
            for store in (actions, panels):
                for key in [k for k, v in store.items() if v.extension == folder.name]:
                    del store[key]
            logger.exception("Extension {} couldn't be loaded -- the app runs without it", folder.name)
    if app is not None:
        for router in _routers:
            app.include_router(router)


async def _call_all(attr: str) -> None:
    for rec in loaded:
        if not rec.ok:
            continue
        for fn in getattr(rec, attr):
            try:
                result = fn()
                if inspect.isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001
                logger.exception("Extension {}: {} hook failed", rec.name, attr)


async def startup() -> None:
    await _call_all("startup")


async def shutdown() -> None:
    await _call_all("shutdown")


async def run_handler(handler: Handler, ctx: ActionContext) -> Result:
    result = handler(ctx)
    if inspect.isawaitable(result):
        result = await result
    return result if isinstance(result, dict) else {"done": True}


def summary(user) -> dict:
    """What the interface needs: the actions and panels this person may use."""
    def allowed(perm: Optional[str]) -> bool:
        return perm is None or user.has_permission(perm)
    return {
        "actions": [{"id": a.id, "label": a.label, "where": list(a.where), "icon": a.icon,
                     "needs_done_video": a.needs_done_video}
                    for a in actions.values() if allowed(a.permission)],
        "panels": [{"id": p.id, "label": p.label, "extension": p.extension}
                   for p in panels.values() if allowed(p.permission)],
        "extensions": [{"name": r.name, "ok": r.ok, "version": r.version, "description": r.description,
                        "error": r.error} for r in loaded] if user.has_permission("settings.manage") else [],
    }


__all__ = ["ActionContext", "ExtensionAPI", "load_all", "startup", "shutdown", "summary", "actions", "panels",
           "loaded", "run_handler", "asyncio"]
