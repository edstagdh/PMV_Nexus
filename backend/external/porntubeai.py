"""
porntubeai.com -- PMVHaven's sister site for AI-themed PMV content.

Lives under backend/external/ -- this package groups reference/config
material for additional platform sites, as opposed to backend/sites.py
(the actual site-resolution logic, shared and used at runtime regardless
of how many sites are configured).

Runs on the exact same Vue/Nuxt platform as PMVHaven itself (confirmed by
inspecting a real video page: identical component structure, identical
"timeline-thumbnail-item" scrubber markup, identical Video Details grid,
identical login form field ids/types), and shares the same CDN bucket
(pmvhavencloud.s3.eu-west-par.io.cloud.ovh.net) that PMVHaven's own
cdn_base already points at.

Because of that, this file carries NO parsing logic of its own --
scraper_v1.py and scraper_v2.py already work unchanged for any site on
this platform. This module exists purely so the actual config values
(base_url/cdn_base/login_path) live next to a comment explaining where
they came from, instead of being an unexplained block in config.yaml.

The real, active configuration is the `sites.porntubeai` block in
config.yaml -- this file (backend/external/porntubeai.py) is reference
documentation, not something imported anywhere. If porntubeai.com's
markup ever diverges from PMVHaven's (a scraper field starts coming back
empty/wrong for porntubeai URLs specifically, but not for PMVHaven),
that's the signal this platform-sharing assumption has broken, and
site-specific overrides would need to be introduced in scraper_v1.py at
that point (e.g. branch on the video's site to pick a different
selector) -- not before.

Credentials: porntubeai.com uses the SAME PMVHAVEN_EMAIL / PMVHAVEN_PASSWORD
from .env as pmvhaven.com does -- confirmed by the user, one account works
across both sites. Login session state is still kept separate per site
(see PMVClient.storage_state_file in pmv_client.py), since they're
different domains with independent cookies.
"""

# Reference only -- see the `sites.porntubeai` block in config.yaml for the
# values actually used at runtime.
REFERENCE_CONFIG = {
    "base_url": "https://porntubeai.com",
    "cdn_base": "https://pmvhavencloud.s3.eu-west-par.io.cloud.ovh.net/",
    "login_path": "/login",
}
