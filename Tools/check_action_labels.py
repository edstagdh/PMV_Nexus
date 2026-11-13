"""
Checks that every API route that changes something has a readable name for
Management -> Logs (backend/action_log.py _LABELS). Run from the project folder:

    python -m Tools.check_action_labels

Prints the routes without one and exits with 1 when there are any.
"""
from __future__ import annotations

import sys

from backend.action_log import _LABELS, _SELF_LOGGED
from backend.main import app

_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


def _routes(routes):
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from _routes(inner.routes)
        elif getattr(r, "path", "").startswith("/api/") and getattr(r, "methods", None) and r.methods & _CHANGING:
            yield r


def main() -> int:
    missing = sorted({(r.path, r.endpoint.__name__) for r in _routes(app.router.routes)
                      if r.endpoint.__name__ not in _LABELS and r.endpoint.__name__ not in _SELF_LOGGED})
    for path, name in missing:
        print(f"no log label: {name} ({path})")
    print(f"{len(missing)} route(s) without a label" if missing else "Every changing route has a log label.")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
