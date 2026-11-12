"""
Files the server currently has open for streaming (videos, previews).

A browser that stops reading a video -- a closed player, a paused <video>
in another tab -- can leave the server's streaming response waiting with
the file still open. On Windows an open file can't be deleted or renamed
("[WinError 32] being used by another process"). Before deleting or
renaming, callers ask release(path): every open stream of that file is
closed, which ends those responses and frees the file at once.
"""
from __future__ import annotations

import os
import threading
from collections import defaultdict
from pathlib import Path

_lock = threading.Lock()
_open: dict[str, set] = defaultdict(set)


def _key(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def register(path, handle) -> None:
    with _lock:
        _open[_key(path)].add(handle)


def unregister(path, handle) -> None:
    with _lock:
        key = _key(path)
        _open[key].discard(handle)
        if not _open[key]:
            del _open[key]


def release(path) -> int:
    """Closes every open stream of `path`; returns how many there were."""
    with _lock:
        handles = list(_open.pop(_key(path), ()))
    for handle in handles:
        try:
            handle.close()
        except Exception:  # noqa: BLE001 -- already closed / mid-read: either way it's let go
            pass
    return len(handles)


def stream_chunks(path: Path, start: int = 0, length: int | None = None, chunk_size: int = 65536):
    """Yields the file's bytes [start, start+length); stops quietly if
    release() closes the file meanwhile."""
    handle = open(path, "rb")
    register(path, handle)
    try:
        handle.seek(start)
        remaining = length
        while remaining is None or remaining > 0:
            try:
                data = handle.read(chunk_size if remaining is None else min(chunk_size, remaining))
            except ValueError:  # closed by release()
                break
            if not data:
                break
            if remaining is not None:
                remaining -= len(data)
            yield data
    finally:
        unregister(path, handle)
        handle.close()
