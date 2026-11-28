"""App identity, update checks and tool/dependency status.

Everything the About / Tools pages and the header version label need lives
here so routers stay thin. The single source of truth for the version is the
VERSION file in the project root (also what the update check compares against
on GitHub).
"""
from __future__ import annotations
from .i18n import L

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
# 3.11.5: published GitHub Releases only (a pushed vX.Y.Z tag) -- not the VERSION on the main branch,
# not drafts or pre-releases, not test builds of the Release workflow (those are artifacts, not releases).
RELEASES_API_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases?per_page=30"
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
    """The newest published release on GitHub (3.11.5: releases, not the main branch's VERSION).
    Never raises -- failures come back as {"error": ...} so a scheduled job or button click can
    report them. "newer": how many releases are newer than this app; "release_url": the newest one's page."""
    result = {
        "checked_at": time.time(), "current": APP_VERSION, "latest": None, "update_available": False,
        "newer": 0, "release_url": None, "release_name": None, "published_at": None, "error": None,
        "releases_url": RELEASES_URL,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(RELEASES_API_URL, headers={
                "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": f"PMV-Nexus/{APP_VERSION}"})
        if resp.status_code == 404:
            result["error"] = "The repository wasn't found on GitHub (it may be private)"
        elif resp.status_code == 403:
            result["error"] = "GitHub refused for now (too many checks from this address) -- it's tried again tomorrow"
        elif resp.status_code != 200:
            result["error"] = f"GitHub returned HTTP {resp.status_code}"
        else:
            published = []
            for rel in resp.json() if isinstance(resp.json(), list) else []:
                tag = str(rel.get("tag_name") or "")
                if rel.get("draft") or rel.get("prerelease") or not re.match(r"^v?\d+\.\d+(\.\d+)?$", tag):
                    continue
                published.append((parse_version(tag), tag.lstrip("v"), rel))
            if published:
                published.sort(key=lambda r: r[0], reverse=True)
                _, version, rel = published[0]
                result.update(latest=version, release_url=rel.get("html_url") or RELEASES_URL,
                              release_name=rel.get("name") or f"PMV Nexus {version}", published_at=rel.get("published_at"))
                result["newer"] = sum(1 for v, _, _ in published if v > parse_version(APP_VERSION))
                result["update_available"] = result["newer"] > 0
    except (httpx.HTTPError, ValueError) as exc:
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
                "hint": L('Install FFmpeg and make sure it is on PATH (https://ffmpeg.org/download.html).')}
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
        line, _, marker = raw.split("#")[0].strip().partition(";")
        if not line or not _marker_applies(marker):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?\s*(.*)$", line.strip())
        if m:
            out.append((m.group(1), m.group(3).strip()))
    return out


def _marker_applies(marker: str) -> bool:
    """The only markers requirements.txt uses: sys_platform == / != "..." (e.g. pystray, Windows only)."""
    m = re.fullmatch(r"\s*sys_platform\s*(==|!=)\s*[\"']([^\"']+)[\"']\s*", marker)
    if not m:
        return True
    return (sys.platform == m.group(2)) == (m.group(1) == "==")


def _spec_satisfied(installed: str, spec: str) -> bool:
    """requirements.txt pins the versions the app was tested with (3.7.0: "=="); older ones gave ">="."""
    m = re.match(r"^(==|>=)\s*([\d.]+)", spec)
    if not m:
        return True
    if m.group(1) == "==":
        return parse_version(installed) == parse_version(m.group(2))
    return parse_version(installed) >= parse_version(m.group(2))


def _package_status(name: str, spec: str) -> dict:
    try:
        version = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return {"name": name, "kind": "python", "ok": False, "version": None, "required": spec or None,
                "hint": "pip install -r requirements.txt"}
    ok = _spec_satisfied(version, spec)
    return {"name": name, "kind": "python", "ok": ok, "version": version, "required": spec or None,
            "hint": None if ok else "pip install -r requirements.txt"}


def _mediainfo_status() -> dict:
    try:
        from pymediainfo import MediaInfo
        ok = bool(MediaInfo.can_parse())
    except Exception:  # noqa: BLE001
        ok = False
    return {"name": L("MediaInfo library"), "kind": "library", "ok": ok, "version": None, "path": None,
            "hint": None if ok else L('Install MediaInfo (bundled with pymediainfo on Windows; `apt install libmediainfo0v5` on Debian/Ubuntu).')}


def tool_status() -> dict:
    """BLOCKING (runs a few short subprocesses) -- call from a sync route or a thread."""
    tools = [_binary_status("ffmpeg"), _binary_status("ffprobe"), _mediainfo_status()]
    packages = [_package_status(n, s) for n, s in _requirements()]
    return {
        "python": {"version": platform.python_version(), "executable": sys.executable,
                   "ok": (3, 12) <= sys.version_info[:2] <= (3, 14)},
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


_SCREENSHOT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}\.(?:webp|png)$")


def doc_screenshot(name: str) -> Optional[Path]:
    """A picture from docs/screenshots/ (the Guide's), by plain file name only."""
    if not _SCREENSHOT_NAME.match(name or ""):
        return None
    path = ROOT_DIR / "docs" / "screenshots" / name
    return path if path.is_file() else None


def read_doc(key: str) -> Optional[str]:
    path = DOC_FILES.get(key)
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None
