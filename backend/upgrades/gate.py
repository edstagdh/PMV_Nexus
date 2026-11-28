"""While long upgrade steps run (runner.STATE["active"]), the API answers 503 "upgrading" --
except signing in, the upgrade's own progress and the language files -- so nothing reads or
changes the library half-way through an upgrade. The pages show the Upgrading screen (App.vue)."""
from __future__ import annotations

import json

from . import runner

_ALLOWED = ("/api/system/upgrade", "/api/auth/", "/api/setup/", "/api/i18n/", "/api/health", "/api/local/stop")


class UpgradeGate:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and runner.STATE["active"]:
            path = scope.get("path", "")
            if path.startswith("/api/") and not path.startswith(_ALLOWED):
                body = json.dumps({"detail": "PMV Nexus is being upgraded -- it can be used again in a moment.",
                                   "upgrading": True}).encode()
                await send({"type": "http.response.start", "status": 503,
                            "headers": [(b"content-type", b"application/json"), (b"retry-after", b"5"),
                                        (b"cache-control", b"no-store")]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
