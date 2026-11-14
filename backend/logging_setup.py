"""
Central loguru configuration. Call setup_logging() once, as early as
possible (before other modules that log get a chance to run), so all
output -- including uvicorn/fastapi's own stdlib logging -- funnels
through loguru: the console, the rotated text file (backend.log) and the
searchable store behind Admin -> Logs (log_store.py).

apply_logging() (re)builds those outputs from the current settings; Admin
-> Server Settings -> Logging calls it, so changes apply without a restart.
With logging switched off nothing is recorded anywhere.
"""
from __future__ import annotations

import logging
import sys
import threading

from loguru import logger

from .config import get_settings
from .log_store import init_log_store

_configured = False
_handler_ids: list[int] = []
_apply_lock = threading.Lock()


class InterceptHandler(logging.Handler):
    """Redirects stdlib `logging` records (uvicorn, fastapi, etc.) into loguru
    so everything ends up in the same places with the same format."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        # The logger's name ("uvicorn.error", ...) tells Admin -> Logs where it came from.
        logger.bind(source=record.name).opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def apply_logging() -> dict:
    """Replaces the app's log outputs with what the settings ask for.
    Returns which outputs are now active."""
    cfg = get_settings().logging
    settings = get_settings()
    with _apply_lock:
        for handler_id in _handler_ids:
            try:
                logger.remove(handler_id)
            except ValueError:
                pass
        _handler_ids.clear()
        active = {"console": False, "file": False, "app_log": False}
        if cfg.enabled:
            if cfg.console:
                _handler_ids.append(logger.add(sys.stderr, level=cfg.level, colorize=True, backtrace=False, diagnose=False))
                active["console"] = True
            if cfg.file:
                _handler_ids.append(logger.add(
                    settings.log_dir / "backend.log",
                    rotation=cfg.rotation,  # "1 month" -> new file each month; "50 MB" -> by size
                    retention=None if cfg.retention == "forever" else cfg.retention,  # how long old rotated logs are kept
                    compression="zip",  # rotated-out files get zipped
                    level=cfg.level,
                    enqueue=True,  # thread/async-safe writes
                    backtrace=True,
                    diagnose=False,  # keep False in prod: True would log local var values
                    encoding="utf-8",
                ))
                active["file"] = True
            # The store always exists (Admin -> Logs reads what's already there); it only
            # receives new entries while it's switched on.
            store = init_log_store(settings.log_dir / "app_logs.db",
                                   retention_days=cfg.app_log_retention_days, max_rows=cfg.app_log_max_rows)
            store.retention_days = max(1, cfg.app_log_retention_days)
            store.max_rows = max(1000, cfg.app_log_max_rows)
            if cfg.app_log:
                # Not enqueue=True: the sink itself only queues a row (see log_store.py),
                # and running it in the caller keeps exception tracebacks available.
                _handler_ids.append(logger.add(store.sink, level=cfg.level, backtrace=False, diagnose=False, catch=True))
                active["app_log"] = True
        else:
            init_log_store(settings.log_dir / "app_logs.db",
                           retention_days=cfg.app_log_retention_days, max_rows=cfg.app_log_max_rows)
    return active


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    logger.remove()  # drop loguru's default stderr handler so we control format/level
    apply_logging()

    # Redirect stdlib logging (uvicorn, fastapi, httpx, etc.) into loguru.
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi", "httpx"):
        std_logger = logging.getLogger(name)
        std_logger.handlers = [InterceptHandler()]
        std_logger.propagate = False

    # Access logs are pure noise from polling -- only surface non-2xx
    # responses (uvicorn logs those at WARNING/ERROR anyway). Changes made
    # through the API are logged as actions instead (action_log.py).
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # httpx logs one INFO line per outbound request (every page fetch,
    # thumbnail download, upload chunk...) -- capped so only real problems show.
    logging.getLogger("httpx").setLevel(logging.WARNING)
