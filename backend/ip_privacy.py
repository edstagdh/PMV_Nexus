"""
How client IP addresses appear in Admin -> Logs. An address can identify a
person, so config.yaml's logging.ip_mode chooses:

    hashed  (default) a keyed pseudonym like "ip-3f9a1c0b7d2e": the same
            address always gives the same token, so repeated failed sign-ins
            from one place can still be followed, but the address can't be
            read back (HMAC with a key derived from app_secret_key, so it
            can't be reversed by hashing every IPv4 address either).
    masked  only the last part kept: "192.168.1.23" -> "***.***.***.23",
            IPv6 -> "****::42".
    full    the address as is (troubleshooting on a private network).
    off     no address at all.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
from functools import lru_cache
from typing import Optional

from loguru import logger

from .config import get_settings

MODES = ("hashed", "masked", "full", "off")


def mask(ip: str) -> str:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return "***"
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if isinstance(address, ipaddress.IPv4Address):
        return "***.***.***." + str(address).rsplit(".", 1)[1]
    return "****::" + (address.exploded.rsplit(":", 1)[1].lstrip("0") or "0")


@lru_cache
def _config() -> tuple[str, bytes]:
    settings = get_settings()
    mode = (settings.logging.ip_mode or "hashed").strip().lower()
    if mode not in MODES:
        logger.warning("logging.ip_mode must be one of {} (got {!r}) -- using 'hashed'", ", ".join(MODES), mode)
        mode = "hashed"
    key = hmac.new(settings.app_secret_key.encode("utf-8"), b"log-ip-pseudonym", hashlib.sha256).digest()
    return mode, key


def reset() -> None:
    """Re-read logging.ip_mode (after Server Settings -> Logging changed it)."""
    _config.cache_clear()


def apply(ip: Optional[str]) -> Optional[str]:
    """The address as the logs may show it."""
    if not ip:
        return None
    mode, key = _config()
    if mode == "full":
        return ip
    if mode == "off":
        return None
    if mode == "masked":
        return mask(ip)
    return "ip-" + hmac.new(key, ip.encode("utf-8"), hashlib.sha256).hexdigest()[:12]


def current_mode() -> str:
    return _config()[0]
