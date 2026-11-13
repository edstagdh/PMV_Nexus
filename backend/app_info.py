"""App identity, update checks and tool/dependency status.

Everything the About / Tools pages and the header version label need lives
here so routers stay thin. The single source of truth for the version is the
VERSION file in the project root (also what the update check compares against
on GitHub).
"""
from __future__ import annotations

import importlib.metadata
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional

import httpx

ROOT_DIR = Path(__file__).resolve().parent.parent
REPO_OWNER = "edstagdh"
REPO_NAME = "PMV_Nexus"
REPO_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}"
RELEASES_URL = f"{REPO_URL}/releases"
# HEAD resolves to the repository's default branch (currently "master").
REMOTE_VERSION_URL = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/HEAD/VERSION"
LICENSE_NAME = "MIT"

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _read_version() -> str:
    try:
        return (ROOT_DIR / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


APP_VERSION = _read_version()


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2' -> (1, 2, 3) / (1, 2, 0). Non-numeric suffixes ignored."""
    parts = re.findall(r"\d+", (text or "").split("-")[0])[:3]
    nums = [int(p) for p in parts] + [0] * (3 - len(parts))
    return tuple(nums)


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


async def fetch_latest_version(timeout: float = 15.0) -> dict:
    """Reads VERSION from the repo's main branch. Never raises -- failures come
    back as {"error": ...} so a scheduled job or button click can report them."""
    result = {
        "checked_at": time.time(), "current": APP_VERSION, "latest": None,
        "update_available": False, "error": None, "releases_url": RELEASES_URL,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(REMOTE_VERSION_URL, headers={"Cache-Control": "no-cache"})
        if resp.status_code == 404:
            result["error"] = "VERSION file not found on GitHub (the repository may be private, or not published yet)"
        elif resp.status_code != 200:
            result["error"] = f"GitHub returned HTTP {resp.status_code}"
        else:
            latest = resp.text.strip()
            if not re.match(r"^v?\d+(\.\d+){0,2}", latest):
                result["error"] = f"Unexpected VERSION content: {latest[:40]!r}"
            else:
                result["latest"] = latest
                result["update_available"] = is_newer(latest, APP_VERSION)
    except httpx.HTTPError as exc:
        result["error"] = f"Could not reach GitHub: {exc.__class__.__name__}"
    return result


# ----------------------------------------------------------------- tool status
def _run_version(cmd: list[str]) -> Optional[str]:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=10, stdin=subprocess.DEVNULL,
                           creationflags=_NO_WINDOW)
        line = (r.stdout or r.stderr).decode(errors="replace").splitlines()
        return line[0].strip() if line else None
    except (OSError, subprocess.TimeoutExpired):
        return None


@lru_cache(maxsize=1)
def ffmpeg_version_line() -> Optional[str]:
    if not shutil.which("ffmpeg"):
        return None
    line = _run_version(["ffmpeg", "-version"])
    m = re.search(r"ffmpeg version (\S+)", line or "")
    return m.group(1) if m else line


def _binary_status(name: str) -> dict:
    path = shutil.which(name)
    if not path:
        return {"name": name, "kind": "binary", "ok": False, "version": None, "path": None,
                "hint": "Install FFmpeg and make sure it is on PATH (https://ffmpeg.org/download.html)."}
    line = _run_version([name, "-version"]) or ""
    m = re.search(rf"{name} version (\S+)", line)
    return {"name": name, "kind": "binary", "ok": True, "version": m.group(1) if m else line,
            "path": path, "hint": None}


def _requirements() -> list[tuple[str, str]]:
    """(distribution name, version spec) from requirements.txt."""
    out = []
    try:
        lines = (ROOT_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for raw in lines:
        line = raw.split("#")[0].strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?\s*(.*)$", line)
        if m:
            out.append((m.group(1), m.group(3).strip()))
    return out


def _spec_satisfied(installed: str, spec: str) -> bool:
    m = re.match(r"^>=\s*([\d.]+)", spec)
    return not m or parse_version(installed) >= parse_version(m.group(1))


def _package_status(name: str, spec: str) -> dict:
    try:
        version = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return {"name": name, "kind": "python", "ok": False, "version": None, "required": spec or None,
                "hint": "pip install -r requirements.txt"}
    ok = _spec_satisfied(version, spec)
    return {"name": name, "kind": "python", "ok": ok, "version": version, "required": spec or None,
            "hint": None if ok else f'pip install --upgrade "{name}{spec}"'}


def _mediainfo_status() -> dict:
    try:
        from pymediainfo import MediaInfo
        ok = bool(MediaInfo.can_parse())
    except Exception:  # noqa: BLE001
        ok = False
    return {"name": "MediaInfo library", "kind": "library", "ok": ok, "version": None, "path": None,
            "hint": None if ok else "Install MediaInfo (bundled with pymediainfo on Windows; "
                                    "`apt install libmediainfo0v5` on Debian/Ubuntu)."}


def tool_status() -> dict:
    """BLOCKING (runs a few short subprocesses) -- call from a sync route or a thread."""
    tools = [_binary_status("ffmpeg"), _binary_status("ffprobe"), _mediainfo_status()]
    packages = [_package_status(n, s) for n, s in _requirements()]
    return {
        "python": {"version": platform.python_version(), "executable": sys.executable,
                   "ok": sys.version_info >= (3, 11)},
        "platform": platform.platform(),
        "tools": tools,
        "packages": packages,
        "all_ok": all(t["ok"] for t in tools[:3]) and all(p["ok"] for p in packages),
    }


def system_info() -> dict:
    return {
        "name": "PMV Nexus",
        "version": APP_VERSION,
        "license": LICENSE_NAME,
        "repo_url": REPO_URL,
        "releases_url": RELEASES_URL,
        "issues_url": f"{REPO_URL}/issues",
        "new_issue_url": f"{REPO_URL}/issues/new",
        "python": platform.python_version(),
        "platform": platform.platform(terse=True),
        "ffmpeg": ffmpeg_version_line(),
    }


DOC_FILES = {
    "guide": ROOT_DIR / "docs" / "GUIDE.md",
    "changelog": ROOT_DIR / "CHANGELOG.md",
    "license": ROOT_DIR / "LICENSE",
    "terms": ROOT_DIR / "DISCLAIMER.md",  # terms of use (2.28.0)
}


def read_doc(key: str) -> Optional[str]:
    path = DOC_FILES.get(key)
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None
