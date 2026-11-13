# Changelog

All notable changes to PMV Nexus are listed here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). The current version is in the `VERSION` file.

## [2.32.0] - 2026-11-13

The first version prepared for public release. Versions before it were private development builds; their
history isn't listed here.

### What PMV Nexus does
- **Adds videos by link** from PMVHaven, PornTubeAI and HMVHaven, using only the sites' official API with your
  own API key: the video's details, and the file from the site's own download endpoint (only videos the site
  offers for download). It never reads the sites' web pages or signs in to them.
- **Library:** grid / list / table views, search with prefixes and wildcards, filters (tags, performers, codec,
  quality, orientation, duration, rating), bulk editing and bulk actions, rename, repair / update, duplicate
  detection (grayscale and perceptual-hash comparison, with audio checks), songs found in videos (Shazam, optional
  AudD), performer profiles (ThePornDB), uploads of your own files.
- **Watching:** a built-in player with 720p conversion while you watch, seekbar previews, A-B repeat, a
  mini-player, playlists (manual and smart), continue watching, Currently Playing, Discover.
- **Re-encoding** to HEVC or AV1 with a queue, CPU limits and progress.
- **Users and roles** with fine-grained permissions, notifications, a Tasks page, a Scheduler, statistics and an
  action log.
- **Backups** (optionally password-protected), restore into newer versions and onto another computer (folders
  checked and moved), a weekly backup job.
- **Installs:** a Windows Setup (new library or from a backup, folders, admin account, browser, port; runs as an
  app with a tray icon or as a Windows service), `install.sh` for Linux / macOS (optional systemd user service),
  `install.bat`.
- **Extensions:** optional add-ons in an `extensions/` folder (actions, settings panels, credentials,
  permissions, sources), with nothing shown when there are none.
- A companion userscript (`userscript/pmv-nexus.user.js`) that adds videos from the site and marks ones you
  already have.

### Changed in this version
- **No more site sign-in or web-page reading:** the hidden Firefox sign-in (Server settings → Connections), the
  site account credentials (PMVHaven / PornTubeAI / HMVHaven email and password) and the `download.website_fallback`
  option are gone. Downloads and repairs use the API only, as they have by default since 2.27.0.
  - No longer installed: Playwright and its Firefox, `beautifulsoup4`, `lxml`. Setup removes the old
    `runtime\ms-playwright` folder; elsewhere it can be deleted by hand (`runtime/ms-playwright`, or the
    `ms-playwright` folder in your user cache).
  - `config.yaml`: `sites.*.cdn_base`, `sites.*.login_path` and `download.website_fallback` are ignored if present.
  - Stored site passwords stay in `data/secrets.db`, unused.
- **Terms of use:** personal use only — accounts are for you and the people you live with, not for a public or
  commercial service; if you let others use your install you're responsible for their use too.
