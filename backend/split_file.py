"""One file made of numbered parts (3.7.0): a backup split into "<name>.zip.001",
"<name>.zip.002", ... -- the usual naming of split archives, which 7-Zip opens from
the .001 part (or join them: `copy /b name.zip.0* name.zip` on Windows, `cat` elsewhere).

SplitFile is an ordinary seekable binary file to zipfile / pyzipper: writing past a part's
size starts the next part, reading and seeking go through the parts in order. So a split
backup is one normal zip, cut into pieces -- nothing in the zip format changes.
"""
from __future__ import annotations

import io
import re
from collections import OrderedDict
from pathlib import Path
from typing import Optional

PART_RE = re.compile(r"\.(\d{3})$")
OPEN_HANDLES = 8  # parts kept open at once


def part_path(base: Path, n: int) -> Path:
    return base.with_name(f"{base.name}.{n:03d}")


def parts_of(base: Path) -> list[Path]:
    """The existing parts of `base` (base.001, base.002, ... without gaps), in order."""
    out, n = [], 1
    while part_path(base, n).is_file():
        out.append(part_path(base, n))
        n += 1
    return out


def base_of(path: Path) -> Path:
    """"x.zip.001" -> "x.zip"; anything else unchanged."""
    return path.with_name(PART_RE.sub("", path.name)) if PART_RE.search(path.name) else path


class SplitFile(io.RawIOBase):
    """mode "r": read the existing parts of `base`; "w": write new parts of at most
    `part_size` bytes each; "r+": read and change an existing set (more parts of
    `part_size` are added when writing past its end)."""

    def __init__(self, base: Path, mode: str = "r", part_size: Optional[int] = None):
        super().__init__()
        if mode not in ("r", "w", "r+"):
            raise ValueError(mode)
        if mode != "r" and not part_size:
            raise ValueError("part_size is needed to write")
        self.base, self.mode, self.part_size = Path(base), mode, part_size
        self._handles: OrderedDict[int, object] = OrderedDict()
        self._pos = 0
        if mode == "w":
            for old in parts_of(self.base):
                old.unlink()
            self._sizes: list[int] = []
        else:
            parts = parts_of(self.base)
            if not parts:
                raise FileNotFoundError(str(part_path(self.base, 1)))
            self._sizes = [p.stat().st_size for p in parts]

    # -- io.RawIOBase
    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return self.mode != "r"

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    @property
    def size(self) -> int:
        return sum(self._sizes)

    @property
    def parts(self) -> list[Path]:
        return [part_path(self.base, i + 1) for i in range(len(self._sizes))]

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_CUR:
            offset += self._pos
        elif whence == io.SEEK_END:
            offset += self.size
        if offset < 0:
            raise ValueError("negative seek position")
        self._pos = offset
        return offset

    def _handle(self, i: int):
        h = self._handles.pop(i, None)
        if h is None:
            path = part_path(self.base, i + 1)
            mode = "rb" if self.mode == "r" else ("r+b" if path.exists() else "w+b")
            h = open(path, mode)  # noqa: SIM115 -- closed in close() / when evicted
            if len(self._handles) >= OPEN_HANDLES:
                _, old = self._handles.popitem(last=False)
                old.close()
        self._handles[i] = h
        return h

    def _locate(self, pos: int) -> tuple[int, int]:
        """(part index, offset in it) of a position inside the data (reading)."""
        for i, size in enumerate(self._sizes):
            if pos < size:
                return i, pos
            pos -= size
        return len(self._sizes), pos

    def readinto(self, buf) -> int:
        view = memoryview(buf).cast("B")
        done = 0
        while done < len(view) and self._pos < self.size:
            i, off = self._locate(self._pos)
            h = self._handle(i)
            h.seek(off)
            n = h.readinto(view[done:done + min(len(view) - done, self._sizes[i] - off)])
            if not n:
                break
            done += n
            self._pos += n
        return done

    def write(self, data) -> int:
        """Every part but the last is exactly part_size bytes, so a position's part is
        position // part_size (zipfile also goes back to fill in a header it wrote earlier)."""
        if self.mode == "r":
            raise io.UnsupportedOperation("not writable")
        view = memoryview(data).cast("B")
        done = 0
        while done < len(view):
            i, off = divmod(self._pos, self.part_size)
            if i == len(self._sizes) and off == 0:
                self._sizes.append(0)  # the next part
            elif i >= len(self._sizes) or off > self._sizes[i]:
                raise io.UnsupportedOperation("write past the end")  # zipfile never leaves gaps
            chunk = view[done:done + self.part_size - off]
            h = self._handle(i)
            h.seek(off)
            h.write(chunk)
            self._sizes[i] = max(self._sizes[i], off + len(chunk))
            done += len(chunk)
            self._pos += len(chunk)
        return done

    def truncate(self, size: Optional[int] = None) -> int:
        """Cuts the data at `size` (default: here): parts after it are deleted."""
        if self.mode == "r":
            raise io.UnsupportedOperation("not writable")
        size = self._pos if size is None else size
        keep, rest = [], size
        for i, part_size in enumerate(self._sizes):
            if rest <= 0:
                break
            keep.append(min(part_size, rest))
            rest -= part_size
        for i in range(len(keep), len(self._sizes)):  # whole parts past the end
            h = self._handles.pop(i, None)
            if h:
                h.close()
            part_path(self.base, i + 1).unlink(missing_ok=True)
        if keep and keep[-1] < self._sizes[len(keep) - 1]:
            self._handle(len(keep) - 1).truncate(keep[-1])
        self._sizes = keep
        return size

    def flush(self) -> None:
        for h in self._handles.values():
            h.flush()

    def close(self) -> None:
        if not self.closed:
            for h in self._handles.values():
                h.close()
            self._handles.clear()
        super().close()
