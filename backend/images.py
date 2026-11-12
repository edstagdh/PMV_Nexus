"""
Images stored as WebP (2.15.0): performer photos are saved as WebP when they're
downloaded, and Maintenance -> Performers -> "Convert photos to WebP" converts
those saved before. WebP at quality 80 is usually 25-35% smaller than the same
JPEG and much smaller than PNG; photos larger than MAX_SIDE on their long side
are also scaled down (the app never shows them bigger).

Pillow does the work (requirements.txt). Rotation from EXIF is applied,
transparency is kept, animated images keep their first frame.
"""
from __future__ import annotations

import io
import os
from pathlib import Path

MAX_SIDE = 1600   # pixels, long side
QUALITY = 80      # WebP quality (0-100)
METHOD = 4        # WebP effort (0-6): 6 is ~2x slower for a few % smaller


def to_webp(data: bytes) -> bytes:
    """Any image Pillow reads -> WebP bytes. Raises ValueError if it isn't an image."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.seek(0)
            im = ImageOps.exif_transpose(im)
            if im.mode in ("P", "LA", "PA") or (im.mode == "RGBA"):
                im = im.convert("RGBA")
            elif im.mode != "RGB":
                im = im.convert("RGB")
            if max(im.size) > MAX_SIDE:
                im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, "WEBP", quality=QUALITY, method=METHOD)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError(f"Not a usable image ({exc.__class__.__name__})") from exc


def convert_file(path: str) -> tuple[str, int, int]:
    """Converts an image file to <same name>.webp and removes the original.
    Returns (new path, bytes before, bytes after). BLOCKING.
    If the original can't be removed (Windows: open elsewhere), the WebP is
    still used and the original is left for Maintenance -> Untracked files."""
    src = Path(path)
    data = src.read_bytes()
    webp = to_webp(data)
    dest = src.with_suffix(".webp")
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(webp)
    os.replace(tmp, dest)
    if src != dest:
        try:
            src.unlink()
        except OSError:
            pass
    return str(dest), len(data), len(webp)
