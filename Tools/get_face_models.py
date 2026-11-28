"""Gets the two face models (3.12.0, Management -> Faces) into models/faces, checked by SHA-256.

    python -m Tools.get_face_models          # downloads what's missing or damaged
    python -m Tools.get_face_models --check  # only says whether they're there (exit 0) or not (exit 2)

The Windows Setup has them already (the release workflow runs this before building it);
install.sh runs it; a git checkout runs it once by hand. After that, faces need no network.
A file is written as <name>.part and only put in place when its size and SHA-256 are right;
nothing else is touched. Standard library only. Exit code: 0 ok, 1 a download failed, 2 missing (--check).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.face_models import MODEL_DIR, MODELS  # noqa: E402


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_good(m: dict, folder: Path = MODEL_DIR) -> bool:
    p = folder / m["file"]
    return p.is_file() and p.stat().st_size == m["size"] and _sha256(p) == m["sha256"]


def fetch(m: dict, folder: Path = MODEL_DIR, timeout: float = 60) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    part = folder / (m["file"] + ".part")
    try:
        req = urllib.request.Request(m["url"], headers={"User-Agent": "PMV-Nexus-get-face-models"})
        with urllib.request.urlopen(req, timeout=timeout) as r, open(part, "wb") as out:
            got = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                got += len(chunk)
                if got > m["size"]:
                    raise ValueError("bigger than expected")
                out.write(chunk)
        if part.stat().st_size != m["size"]:
            raise ValueError(f"{part.stat().st_size} bytes instead of {m['size']}")
        if _sha256(part) != m["sha256"]:
            raise ValueError("its SHA-256 isn't the expected one")
        os.replace(part, folder / m["file"])
    finally:
        if part.exists():
            part.unlink()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="only check, download nothing")
    ap.add_argument("--folder", type=Path, default=MODEL_DIR)
    args = ap.parse_args(argv)
    failed = missing = 0
    for m in MODELS.values():
        if is_good(m, args.folder):
            print(f"ok       {m['file']} ({m['license']})")
            continue
        if args.check:
            print(f"missing  {m['file']}")
            missing += 1
            continue
        print(f"getting  {m['file']} ({m['size'] / 1e6:.1f} MB, {m['license']}) ...", flush=True)
        try:
            fetch(m, args.folder)
            print(f"ok       {m['file']}")
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED   {m['file']}: {exc}", file=sys.stderr)
            failed += 1
    if failed:
        print("Faces won't work until the models are there: run this again later.", file=sys.stderr)
        return 1
    return 2 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
