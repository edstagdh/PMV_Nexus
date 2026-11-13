# Changelog

All notable changes to PMV Nexus are listed here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). The current version is in the `VERSION` file.

## [2.34.0] - 2026-11-13

### Added
- **Choose how songs are found** (`songs.method`): **Shazam**, **Shazam, then Gemini** (Gemini on the spots Shazam
  found nothing — what 2.33.0's switch did), **Shazam and Gemini** (both listen: a second opinion when Shazam names
  the wrong song, both answers are listed), or **Gemini only** (for videos without well-known songs). The setting
  (Management → Songs) is used by the Scheduler job and Find songs now; a video's **Find songs** and
  **Select → Find songs** let you pick for that run. The Gemini choices need its API key. 2.33.0's `songs.gemini`
  switch becomes "Shazam, then Gemini" when it was on.
- **Setup's last page after an update: "See what's new in version …"** — opens the app's Help → Changelog (starting
  the app if it isn't running). A new install still offers "Open PMV Nexus now".
- **The log says which credentials changed:** "Change credentials · Google Gemini (optional AI): API key (saved)"
  — names and saved / cleared only, never the values.

## [2.33.4] - 2026-11-13

### Fixed
- **Uninstalling left files behind** (e.g. `backend\sources\__pycache__\...pyc`, and with them the folders): Python's
  compiled files are made while the app runs, so the uninstaller didn't know them and only a few folders were on
  its list. It now removes the app's code folders as a whole (`backend`, `frontend`, `Tools`, `userscript`, `docs`)
  and every `__pycache__` folder in the app folder (also in `extensions`). Your data and `config.yaml` are still
  only deleted when you say so.

## [2.33.3] - 2026-11-13

### Fixed
- **Uninstalling while the app runs:** the uninstaller now finds a running PMV Nexus (tray, console window, its
  FFmpeg work, the service), says so, and closes it before removing anything — the same way Setup does for an
  update. Cancel keeps it installed and running; if it can't be closed, nothing is uninstalled and the message
  names what's still running. (Applies to installs of 2.33.0 or later, which have the closing script.)

## [2.33.2] - 2026-11-13

### Fixed
- **Setup said PMV Nexus couldn't be closed, or was still running when it wasn't.** It counted every program
  running in (or from) the app folder as the app — an editor, a terminal or any other program opened there too —
  and those can't or mustn't be closed. Now only the app's own programs count (its Python, FFmpeg / FFprobe, the
  service wrapper), other programs are never touched, and if something can't be closed the message names it.

## [2.33.1] - 2026-11-13

### Fixed
- The Windows Setup didn't build ("'BEGIN' expected", line 302): a comment in its code contained `{url}`, whose
  closing brace ended the comment early (2.31.0's browser page). Nothing else changed.

## [2.33.0] - 2026-11-13

### Added
- **Optional AI song recognition — Google Gemini.** For the spots Shazam didn't recognise, Find songs can ask
  Gemini: turn on **Ask Gemini (AI) about samples Shazam missed** in Management → Songs and save a free API key
  in Server settings → Credentials → Google Gemini (**Test key** checks it without using quota). Gemini gets a
  20-second sound clip per spot (`songs.gemini_max_samples`, 3 per video by default) and names a song only when it
  says it's sure. Its finds are marked "Gemini (AI)" and never count as "sure" on their own. The free models are
  tried best first (the list is refreshed from Google's), falling back to the next when one is busy or out of
  quota; when all are out, Gemini is skipped for an hour. Off by default.
- **Updates close the running app.** Windows Setup finds a running PMV Nexus (tray, console window, anything run
  from its folder — the service was already handled), says so, closes it (asking the app first, the same as the
  tray's Exit; ending what's left only if it doesn't close) and starts it again after the update. The app answers
  such requests only from the same computer, with a token from `runtime/control.json` (`POST /api/local/stop`).
- The Start-menu shortcut waits for an app that's still starting instead of starting a second one.

### Changed
- **Updates replace the app's code folders as a whole** (`backend`, `frontend`, `Tools`, `userscript`, `docs`), so
  files a new version no longer has don't stay behind. Data, `config.yaml`, extensions, `.venv` and `runtime` are
  kept.

### Removed
- **AudD** (a paid service after a short trial): its credentials, settings and key test. Finding songs uses Shazam,
  plus Gemini if you turn it on. A saved AudD token stays in `data/secrets.db`, unused; `songs.audd_*` settings are
  ignored.

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
  Google Gemini AI), performer profiles (ThePornDB), uploads of your own files.
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
