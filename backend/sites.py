"""
Site profile registry: PMVHaven and its sister sites (porntubeai.com,
hmvhaven.com) run on the same platform and API -- the only thing that differs
is which domain a video link belongs to and that site's base_url.

Pure lookup, no network I/O. Reads the `sites` section of the settings
(config.py's Settings.sites, built-in DEFAULT_SITES + config.yaml):
  - resolve_site_key(url): which configured site a video link belongs to;
  - get_site(site_key): that site's SiteConfig.
Another site on the same platform = another `sites.<key>` block in config.yaml.
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
    """Looks up one configured site's SiteConfig (base_url/label) by key. Raises KeyError if site_key isn't present in
    config.yaml's `sites` section -- in practice that should only happen
    if a LibraryItem's persisted `site` field no longer matches
    config.yaml (e.g. a site block was renamed/removed after items using
    it were already downloaded)."""
    return get_settings().sites[site_key]


def list_site_keys() -> list[str]:
    """All configured site keys, e.g. ["pmvhaven", "porntubeai"] (one source each)."""
    return list(get_settings().sites.keys())