"""
Safety checks for files uploaded from someone's computer (2.11.0), on top
of the extension list:

1. sniff(path, ext): the file's first bytes must be the signature of the
   container its extension claims (MP4/MOV "ftyp", Matroska/WebM EBML, AVI
   "RIFF....AVI ", WMV/ASF GUID, MPEG-TS sync bytes). A renamed program,
   script or archive is refused before FFmpeg even opens it.
2. ffprobe must find a real video track and a known video container
   (ALLOWED_FORMATS) -- done by local_upload with the probe result.
3. scan(path): an antivirus check, when one is available on the server --
   Microsoft Defender (MpCmdRun.exe, Windows) or ClamAV (clamdscan /
   clamscan). Setting upload.virus_scan: "auto" (scan when a scanner is
   there), "required" (refuse uploads without one), "off".
4. Every accepted upload is re-written by FFmpeg (re-packed or re-encoded)
   keeping only the first video and audio track: subtitles, attachments and
   data tracks -- and anything appended after the real video data -- don't
   make it into the library.

5. hash_lookup(path, key) (2.12.0, optional): the file's SHA-256 looked up on
   VirusTotal -- only the fingerprint leaves the server. Catches files known to
   be malicious; a file nobody has seen before (most personal videos) is simply
   "unknown" and passes. scanner_test() shows what the antivirus answers, for
   finding out why scanning doesn't work on a machine.

The file itself is never run: it's stored under a generated name and served
as video only (see the security headers in main.py).
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
from functools import lru_cache
from typing import Optional

from loguru import logger

VIDEO_EXTENSIONS = (".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".wmv", ".ts")

# ffprobe's format_name values accepted for each extension family.
ALLOWED_FORMATS = {"mov,mp4,m4a,3gp,3g2,mj2", "matroska,webm", "avi", "asf", "mpegts"}

_MP4_BOXES = (b"ftyp", b"moov", b"mdat", b"free", b"wide", b"skip", b"pnot")


def sniff(path: str, ext: str) -> Optional[str]:
    """None when the file starts like the container `ext` says; otherwise why not."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(400)
    except OSError as exc:
        return f"The file can't be read ({exc.__class__.__name__})"
    if len(head) < 16:
        return "The file is too small to be a video"
    ext = ext.lower()
    if ext in (".mp4", ".m4v", ".mov"):
        ok = head[4:8] in _MP4_BOXES
    elif ext in (".mkv", ".webm"):
        ok = head[:4] == b"\x1a\x45\xdf\xa3"
    elif ext == ".avi":
        ok = head[:4] == b"RIFF" and head[8:12] == b"AVI "
    elif ext == ".wmv":
        ok = head[:16] == bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c")
    elif ext == ".ts":
        # 188-byte packets each starting with 0x47 (or 192-byte M2TS packets).
        ok = (head[0] == 0x47 and len(head) > 188 and head[188] == 0x47) or \
             (len(head) > 196 and head[4] == 0x47 and head[196] == 0x47)
    else:
        return "This kind of file isn't allowed"
    if ok:
        return None
    return f"This doesn't look like a real {ext} video (its contents don't match the extension)"


# ------------------------------------------------------------------ antivirus
def _version_key(path: str) -> tuple:
    """Defender's platform folders are versions ("4.18.25010.11-0"): newest first."""
    ver = os.path.basename(os.path.dirname(path)).split("-")[0]
    return tuple(int(x) if x.isdigit() else 0 for x in ver.split("."))


@lru_cache(maxsize=1)
def scanner() -> Optional[dict]:
    """The antivirus found on this server: {"name", "cmd"} or None."""
    if os.name == "nt":
        program_data = os.environ.get("ProgramData", r"C:\ProgramData")
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        candidates = sorted(glob.glob(os.path.join(program_data, "Microsoft", "Windows Defender", "Platform", "*", "MpCmdRun.exe")),
                            key=_version_key, reverse=True)
        candidates.append(os.path.join(program_files, "Windows Defender", "MpCmdRun.exe"))
        for exe in candidates:
            if os.path.isfile(exe):
                return {"name": "Microsoft Defender", "cmd": [exe, "-Scan", "-ScanType", "3", "-DisableRemediation", "-File"]}
    for name, cmd in (("ClamAV (clamdscan)", ["clamdscan", "--no-summary", "--fdpass"]),
                      ("ClamAV (clamscan)", ["clamscan", "--no-summary"])):
        exe = shutil.which(cmd[0])
        if exe:
            return {"name": name, "cmd": [exe, *cmd[1:]]}
    return None


def scan(path: str, timeout: int = 1800) -> tuple[str, str]:
    """("clean" | "infected" | "error" | "none", detail). BLOCKING."""
    found = scanner()
    if not found:
        return "none", "No antivirus found on the server"
    try:
        r = subprocess.run([*found["cmd"], path], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Virus scan with {} failed: {}", found["name"], exc)
        return "error", f"{found['name']} couldn't scan it"
    if found["name"].startswith("Microsoft"):
        # 0 = nothing found, 2 = a threat was found.
        if r.returncode == 0:
            return "clean", found["name"]
        if r.returncode == 2:
            return "infected", f"{found['name']} found a threat in it"
    else:
        # ClamAV: 0 = clean, 1 = virus found, 2 = error.
        if r.returncode == 0:
            return "clean", found["name"]
        if r.returncode == 1:
            line = (r.stdout or "").strip().splitlines()[-1:] or [""]
            return "infected", f"{found['name']}: {line[0].split(':', 1)[-1].strip() or 'a threat was found'}"
    logger.warning("Virus scan with {} gave exit code {}: {}", found["name"], r.returncode, (r.stderr or r.stdout)[-500:])
    return "error", f"{found['name']} couldn't scan it (exit code {r.returncode})"


def scanner_test() -> dict:
    """Scans a small harmless file and returns exactly what the antivirus said
    (Server settings -> Uploads -> Test the scanner)."""
    import tempfile
    scanner.cache_clear()
    found = scanner()
    if not found:
        return {"ok": False, "scanner": None, "detail": "No antivirus found (looked for Microsoft Defender's MpCmdRun.exe, clamdscan, clamscan)."}
    with tempfile.NamedTemporaryFile("wb", suffix=".mp4", delete=False) as fh:
        fh.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
        path = fh.name
    try:
        r = subprocess.run([*found["cmd"], path], capture_output=True, text=True, timeout=300)
        out = ((r.stdout or "") + (r.stderr or "")).strip()[-1500:]
        verdict, _ = scan(path)
        return {"ok": verdict == "clean", "scanner": found["name"], "command": " ".join(found["cmd"]), "exit_code": r.returncode,
                "verdict": verdict, "output": out}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "scanner": found["name"], "detail": f"{exc.__class__.__name__}: {exc}"}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def sha256_of(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


VT_URL = "https://www.virustotal.com/api/v3/files/{}"
# The EICAR antivirus test file's SHA-256: every engine flags it, so looking it
# up proves the key works (virustotal_key_test) without sending anything of ours.
EICAR_SHA256 = "275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f"


def _vt_get(digest: str, api_key: str, timeout: float):
    import httpx
    return httpx.get(VT_URL.format(digest), headers={"x-apikey": api_key}, timeout=timeout)


def hash_lookup(path: str, api_key: str, timeout: float = 30) -> tuple[str, str]:
    """("malicious" | "clean" | "unknown" | "error", detail). BLOCKING (hashes the file).
    Only the SHA-256 is sent to VirusTotal; every lookup and its answer is logged
    (never the key)."""
    import httpx
    digest = sha256_of(path)
    logger.info("VirusTotal: looking up SHA-256 {}", digest)
    verdict, detail = _hash_verdict(digest, api_key, timeout, httpx)
    (logger.warning if verdict in ("malicious", "error") else logger.info)("VirusTotal answer for {}: {} -- {}", digest[:16], verdict, detail)
    return verdict, detail


def virustotal_key_test(api_key: str) -> dict:
    """Looks up the EICAR test file: a working key gets "malicious" back."""
    import httpx
    verdict, detail = _hash_verdict(EICAR_SHA256, api_key, 15, httpx)
    logger.info("VirusTotal key test: {} -- {}", verdict, detail)
    if verdict == "malicious":
        return {"ok": True, "message": "Key accepted: VirusTotal answered (its test file is flagged, as it should be)"}
    return {"ok": False, "message": detail}


def _hash_verdict(digest: str, api_key: str, timeout: float, httpx) -> tuple[str, str]:
    try:
        r = _vt_get(digest, api_key, timeout)
    except httpx.HTTPError as exc:
        return "error", f"VirusTotal didn't answer ({exc.__class__.__name__})"
    if r.status_code == 404:
        return "unknown", f"VirusTotal hasn't seen this file before (SHA-256 {digest[:16]}…)"
    if r.status_code in (401, 403):
        return "error", "VirusTotal refused the API key"
    if r.status_code == 429:
        return "error", "VirusTotal's free limit was reached (4 lookups a minute, 500 a day)"
    if r.status_code != 200:
        return "error", f"VirusTotal answered {r.status_code}"
    stats = ((r.json().get("data") or {}).get("attributes") or {}).get("last_analysis_stats") or {}
    bad = int(stats.get("malicious") or 0)
    if bad >= 2 or (bad and not stats.get("harmless") and not stats.get("undetected")):
        return "malicious", f"{bad} antivirus engines on VirusTotal flag this file"
    return "clean", f"Known to VirusTotal, not flagged ({bad} of {sum(int(v or 0) for v in stats.values())} engines)"
