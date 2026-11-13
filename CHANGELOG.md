# Changelog

All notable changes to PMV Nexus are listed here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). The current version is in the `VERSION` file.

## [3.0.0] - 2026-11-13

**The first public release — open beta.** Versions before it were private development builds; their history isn't
listed here. Bug reports and ideas are welcome: [Issues](https://github.com/edstagdh/PMV_Nexus/issues). Security
problems: see [SECURITY.md](SECURITY.md).

### What PMV Nexus does
- **Adds videos by link** from PMVHaven, PornTubeAI and HMVHaven, using only the sites' official API with your
  own API key: the video's details, and the file from the site's own download endpoint (only videos the site
  offers for download). It never reads the sites' web pages or signs in to them.
- **Library:** grid / list / table views, search with prefixes and wildcards, filters (tags, performers, codec,
  quality, orientation, duration, rating), bulk editing and bulk actions, rename, repair / update, duplicate
  detection (grayscale and perceptual-hash comparison, with audio checks), performer profiles (ThePornDB),
  uploads of your own files.
- **Songs in videos:** Shazam (free), optionally Google Gemini AI (free-tier API key) — Shazam only, Gemini on
  Shazam's misses, both as a second opinion, or Gemini only; chosen in the settings and per run. Finds are
  suggestions you add or dismiss; the AI's are marked.
- **Watching:** a built-in player with 720p conversion while you watch, seekbar previews, A-B repeat, a
  mini-player, playlists (manual and smart), continue watching, Currently Playing, Discover.
- **Re-encoding** to HEVC or AV1 with a queue, CPU limits and progress.
- **Users and roles** with fine-grained permissions, notifications, a Tasks page, a Scheduler, statistics and an
  action log (which also names the credentials that were changed — never their values).
- **Backups** (optionally password-protected), restore into newer versions and onto another computer (folders
  checked and moved), a weekly backup job.
- **Installs:**
  - **Windows Setup:** a new library or one from a backup, folders, admin account, browser and port; runs as an
    app with a tray icon or as a Windows service; uses Python 3.11–3.14 when installed (newest first), otherwise
    installs Python 3.12.
  - **Updates:** run the newer Setup — it closes a running app (and starts it again), replaces the app's code
    folders as a whole, remakes a `.venv` made with an unsupported Python, and offers "See what's new".
  - **Uninstalling** closes a running app first and leaves nothing of the app behind; your library and settings
    only go if you say so.
  - `install.sh` for Linux / macOS (optional systemd user service) and `install.bat`.
- **Extensions:** optional add-ons in an `extensions/` folder (actions, settings panels, credentials,
  permissions, sources), with nothing shown when there are none.
- A companion **userscript** (`userscript/pmv-nexus.user.js`) that adds videos from the site and marks the ones
  you already have; it updates itself from this repository.

### Upgrading from a private 2.x build
- Run the 3.0.0 Setup over it (or replace the files and run `install.bat` / `install.sh`); the library, users and
  settings are kept, and the database is upgraded on the first start.
- **Removed:** the site sign-in (Server settings → Connections) and site account credentials, reading the sites'
  web pages (`download.website_fallback`), Share (now a private extension), and AudD. Their stored credentials stay
  in `data/secrets.db`, unused; old `config.yaml` keys for them are ignored. `songs.gemini: true` becomes the method
  "Shazam, then Gemini".
- No longer installed: Playwright and its Firefox, `beautifulsoup4`, `lxml`, `kurigram`. Setup removes the old
  `runtime\ms-playwright` folder; elsewhere it can be deleted by hand.
- **Terms of use:** personal use only — accounts are for you and the people you live with, not for a public or
  commercial service; if you let others use your install you're responsible for their use too.
