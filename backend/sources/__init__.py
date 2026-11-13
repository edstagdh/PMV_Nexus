"""Sources (2.30.0): where videos come from. A source knows which links are its
own, what a video's library id is, how to get a video's details (and the link to
its file), and how to download files. The library (adding, fetching details,
downloading, repairing) only talks to sources through this interface.

Built in: PMVHaven and its sister sites (sources/pmv_platform.py), one source per
`sites:` entry in config.yaml. Extensions (backend/extensions.py) can add more with
`api.add_source(MySource())`.

A source's item ids must not collide with another source's: the built-in ones use
the site's own 24-character video id; others should prefix theirs (e.g. "abc-123").
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from ..video_meta import VideoMetadata  # noqa: F401 -- re-exported: what details() returns

ProgressCallback = Optional[Callable[[int, Optional[int]], None]]


class Source:
    """Override what the source supports; the defaults say "not supported"."""
    key: str = ""
    label: str = ""

    def owns(self, url: str) -> bool:
        """Is this link one of this source's videos?"""
        return False

    def item_id(self, url: str) -> str:
        """The library id for this link (ValueError if it has none)."""
        raise ValueError(f"{self.label or self.key} can't read a video id from {url}")

    async def details(self, item, need_file: bool = True) -> tuple[VideoMetadata, Optional[dict]]:
        """(details, raw data to save next to the video or None). need_file: the result
        must include the file's link (meta.video_url). Raises RuntimeError with a message
        for the user when it can't."""
        raise RuntimeError(f"{self.label or self.key} can't fetch video details")

    async def download(self, url: str, dest: Path, progress: ProgressCallback = None,
                       headers: Optional[dict] = None) -> None:
        """Saves `url` (one of the links details() returned) to `dest`."""
        raise RuntimeError(f"{self.label or self.key} can't download files")


_registered: dict[str, Source] = {}


def register(source: Source) -> None:
    if not source.key:
        raise ValueError("A source needs a key")
    _registered[source.key] = source


def _builtin() -> dict[str, Source]:
    from ..config import get_settings
    from .pmv_platform import PMVPlatformSource
    return {key: PMVPlatformSource(key) for key in get_settings().sites}


def all_sources() -> dict[str, Source]:
    """Built-in sources first (from config.yaml), then the ones extensions added."""
    return {**_builtin(), **_registered}


def get(key: str) -> Source:
    found = all_sources().get(key)
    if found is None:
        raise RuntimeError(f"This video's source ({key}) isn't available -- its extension may be missing")
    return found


def for_url(url: str) -> Source:
    for source in all_sources().values():
        if source.owns(url):
            return source
    names = ", ".join(s.label or s.key for s in all_sources().values())
    raise ValueError(f"'{url}' isn't a link of a supported site ({names})")
