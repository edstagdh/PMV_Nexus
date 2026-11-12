"""
Site profile registry.

This app supports more than one PMVHaven-style site -- PMVHaven itself, and
its sister site porntubeai.com (AI-themed content), both running on the
same underlying Vue/Nuxt platform and sharing the same CDN buckets
(pmvhavencloud.s3...). Login flow, page markup (scraper_v1.py), and the v2
JSON metadata API (scraper_v2.py) are all identical between sites -- the
only thing that differs is which domain a given video URL belongs to and
that site's base_url/cdn_base/login_path.

This module is pure lookup logic, no network I/O. It reads the `sites`
section of config.yaml (see config.py's Settings.sites) and answers two
questions:
  - resolve_site_key(url): given a video page URL, which configured site
    (by key, e.g. "pmvhaven") does it belong to?
  - get_site(site_key): the SiteConfig (base_url/cdn_base/login_path) for
    a given site key.

To add a new sister site later: add a `sites.<new_key>` block to
config.yaml with its own base_url/cdn_base/login_path. Nothing in this
file, pmv_client.py, scraper_v1.py, or scraper_v2.py needs to change --
they're all already written to work with any site key found in
settings.sites.
"""
from __future__ import annotations

from urllib.parse import urlparse

from .config import get_settings, SiteConfig


def resolve_site_key(url: str) -> str:
    """Given a full video page URL (e.g. 'https://porntubeai.com/video/...'
    or 'https://pmvhaven.com/video/...'), returns the configured site key
    whose base_url's domain matches. Matches by network location (domain),
    not a raw string prefix, so 'http://' vs 'https://' or a trailing
    slash difference in config.yaml can't cause a false negative.

    Raises ValueError if the URL doesn't belong to any configured site --
    callers (library_manager.add()) should surface that as a normal
    user-facing "unsupported URL" error, not let it propagate as a crash."""
    settings = get_settings()
    target_netloc = urlparse(url).netloc.lower()
    for key, site in settings.sites.items():
        if urlparse(site.base_url).netloc.lower() == target_netloc:
            return key
    configured = ", ".join(settings.sites.keys())
    raise ValueError(f"'{url}' does not match any configured site (configured sites: {configured})")


def get_site(site_key: str) -> SiteConfig:
    """Looks up one configured site's SiteConfig (base_url/cdn_base/
    login_path) by key. Raises KeyError if site_key isn't present in
    config.yaml's `sites` section -- in practice that should only happen
    if a LibraryItem's persisted `site` field no longer matches
    config.yaml (e.g. a site block was renamed/removed after items using
    it were already downloaded)."""
    return get_settings().sites[site_key]


def list_site_keys() -> list[str]:
    """All currently configured site keys, e.g. ["pmvhaven", "porntubeai"].
    Used by main.py to default/validate the `site` query param on the
    PMVHaven-session endpoints (status/login), and could back a frontend
    site picker later."""
    return list(get_settings().sites.keys())