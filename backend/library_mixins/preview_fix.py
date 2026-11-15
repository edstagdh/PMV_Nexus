"""
Previews as 8-bit H.264 (3.3.0): hover previews that are anything else (AV1,
VP9, 10-bit H.264 from the site...) are converted (ItemExtrasMixin.check_preview,
with a hardware encoder when that's faster: media_tools.preview_encoder):
  - when a preview is first played, and right after a video is downloaded,
  - for the previews already in the library by the Scheduler job "Make previews
    H.264" (created once by 3.3.0 to run at night; Run now starts it at once).
Only the cache's preview clips change; the videos stay as they are.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

from .. import activity

SEED_FLAG = "seeded_job_preview_h264_330"


class PreviewFixMixin:

    async def run_preview_fix(self) -> dict:
        """Scheduler job "Make previews H.264": checks every preview and converts the others."""
        state = {"checked": 0, "converted": 0, "failed": 0, "total": 0, "current": None}
        stop = {"now": False}
        items = [i for i in self._items.values() if i.preview_path]
        state["total"] = len(items)

        def progress() -> dict:
            return {"stage": "Checking", "done": state["checked"], "total": state["total"],
                    "detail": f"{state['converted']} converted" + (f" · {state['current']}" if state["current"] else "")}

        def stop_now() -> None:
            stop["now"] = True

        async with activity.track("preview_fix", "Making previews H.264", progress=progress, stop=stop_now):
            for item in items:
                if stop["now"] or self._shutting_down:
                    break
                state["checked"] += 1
                if item.id not in self._items or not item.preview_path or not Path(item.preview_path).is_file():
                    continue
                state["current"] = item.title
                try:
                    done = await self.check_preview(item.id)
                    if done:
                        state["converted"] += 1
                    elif done is False:
                        state["failed"] += 1
                        logger.warning("Couldn't convert the preview of '{}' [{}] to H.264 (see above)", item.title, item.id)
                except Exception as exc:  # noqa: BLE001 -- one bad file mustn't stop the rest
                    state["failed"] += 1
                    logger.warning("Couldn't convert the preview of '{}' [{}] to H.264: {}", item.title, item.id, exc)
                finally:
                    state["current"] = None
        result = {"checked": state["checked"], "converted": state["converted"], "failed": state["failed"]}
        logger.info("Make previews H.264: {}", result)
        return result

    def seed_preview_fix_job(self) -> None:
        """3.3.0: a one-time "Make previews H.264" job for the previews already in
        the library, tonight at 02:30 (only once; not for a library without previews)."""
        if self.db.get_app_state(SEED_FLAG, False):
            return
        try:
            if any(i.preview_path for i in self._items.values()):
                at = datetime.now().replace(hour=2, minute=30, second=0, microsecond=0)
                if at <= datetime.now():
                    at += timedelta(days=1)
                self.create_scheduled_job("preview_fix", "Make previews H.264 (one time)", True, "simple",
                                          {"kind": "once", "at": at.strftime("%Y-%m-%dT%H:%M")}, {})
                logger.info("Scheduler: added a one-time 'Make previews H.264' job for {}", at.strftime("%Y-%m-%d %H:%M"))
            self.db.set_app_state(SEED_FLAG, time.time())
        except Exception:  # noqa: BLE001
            logger.exception("Could not add the one-time 'Make previews H.264' job")
