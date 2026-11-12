"""
Small helpers shared by more than one router module. Kept here (not in
main.py) so a router can import these without importing main.py itself,
which would be circular once main.py starts importing the routers to
include them in the app.
"""
from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from fastapi import Request
from fastapi.responses import StreamingResponse

from ..open_files import stream_chunks

# Used by library.py's stream/preview routes to parse an incoming HTTP
# Range header (e.g. "bytes=1048576-").
RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")

# Characters that are illegal in Windows/POSIX folder names, or that could
# be used to escape the intended parent directory (path separators, null
# bytes). Used by safe_folder_name() below, which backs the "+ New folder"
# button in the folder-browser modal (settings.py's browse-dirs routes).
FOLDER_NAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_folder_name(name: str) -> str:
    """Strips characters that are illegal in Windows/POSIX folder names (and
    strips leading/trailing dots/whitespace), so a name typed into the "New
    folder" prompt can't accidentally create a nested path or fail outright
    on Windows' reserved characters. Doesn't attempt to be exhaustive about
    every OS quirk (e.g. Windows reserved device names like CON/PRN) --
    good enough for a single-user local tool where the input is the
    person's own typed text, not untrusted external data. Raises ValueError
    if nothing usable is left after cleaning."""
    cleaned = FOLDER_NAME_RE.sub("", name).strip().strip(".")
    if not cleaned:
        raise ValueError("Not a valid folder name")
    return cleaned


def stream_file(file_path: Path, request: Request) -> StreamingResponse:
    """Serves file_path with HTTP range-request support -- what lets the
    video player seek instead of only ever playing from the start. Used by
    library.py's /stream and /preview routes. Open files are tracked (see
    open_files.py) so deleting or renaming a video can close them first."""
    file_size = file_path.stat().st_size
    media_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    range_header = request.headers.get("range")

    if range_header:
        match = RANGE_RE.match(range_header)
        if match:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
            end = min(end, file_size - 1)
            chunk_size = end - start + 1

            headers = {
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(chunk_size),
            }
            return StreamingResponse(stream_chunks(file_path, start, chunk_size), status_code=206, media_type=media_type, headers=headers)

    headers = {"Content-Length": str(file_size), "Accept-Ranges": "bytes"}
    return StreamingResponse(stream_chunks(file_path), media_type=media_type, headers=headers)
