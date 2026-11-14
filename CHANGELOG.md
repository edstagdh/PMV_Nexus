# Changelog

All notable changes to PMV Nexus are listed here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). The current version is in the `VERSION` file.

## [3.1.0] - 2026-11-14

### Added
- **Notifications for possible duplicates:** when a duplicate scan (after downloads, re-encodes or repairs, the daily
  one or Scan now) finds a pair it hadn't found before: "Possible duplicate: “New video” — looks like “Older video”
  (94% match)", opening Management → Duplicates. More than 5 new pairs at once give one summary instead. Pairs you
  marked "not duplicates" stay quiet, and pairs found before 3.1.0 aren't announced. For admins; pop-ups can be
  switched off in My Settings → Notifications → "Possible duplicates found".
- **Video frames under the player** (like PMVHaven's video timeline): a row of pictures from across the video with
  their time — click one to jump there; the one you're in is highlighted. PMVHaven's own timeline frames when the
  video has them, otherwise 8 made from the file (uploads, videos gone from the site), evenly spread. Small WebP
  files (at most 320 px) in the cache folder's `.frames`; new videos get them by themselves, others when their page
  is opened, or all at once from Management → Maintenance → Video frames. My Settings → Video page can hide them.
- **Audio browsers can't play is converted to AAC** (PCM, AC-3, DTS, TrueHD, ALAC, MP2 played silent in some or all
  browsers): right after a download, and for the videos already in the library by the new Scheduler job **Make audio
  playable** — added once, for the first night after updating (02:00), or start it with Run now. The picture isn't
  touched.
- **One-time Scheduler jobs:** a job can run once at a date and time, then switches itself off.
- **Screenshots** in the README and the Guide (also shown in Help → Guide, which can now show pictures) — a demo
  library with invented titles, names and numbers and abstract generated videos. Examples in the app (My Settings,
  Server Settings, search help) use invented names too.
- **♥ badge on videos in your Favorites** (like the 💀 one for videos gone from the site): on thumbnails (also the
  small ones) and in the table; it updates as soon as you add or remove a favorite. Can be turned off in
  Settings → Badges ("♥ In my Favorites").

### Changed
- **Video page:** the player starts at the top — the Library / Back link above it is gone, and **Discover again** is
  now **🎲 Random**, next to the video's title. **Library** in the sidebar takes you back to the library as you left
  it (filters, page) from any page, so the "Back button" setting in My Settings → Video page is gone too.
- **"My Settings" and "Server Settings"** are written that way everywhere.
- **Video page:** the description has a **Description** heading; **Technical Details** are split into General / Video /
  Audio / Other, side by side when there's room; the videos in **Related videos** and **More from …** have the ⋮ menu;
  **Repair/Update** is spelled that way.
- **ThePornDB performer photos have their own folder**, `data/performers`, instead of the cache folder (which has a
  folder per creator). The app moves them by itself on the first start.
- **Your place in a video is saved every 5 s instead of 10** while it plays (also still on pause, seek, end and
  closing the tab).
- **Windows shows "PMV Nexus" instead of "pythonw"**: the Start-menu, desktop and start-with-Windows shortcuts now
  open a small `PMVNexus.exe` (with the app's icon and name), so Task Manager's Startup tab, Explorer and the Start
  menu show PMV Nexus. It just starts the app's Python and exits — the running app itself is still Python. Run the
  new Setup over your install; it replaces the shortcuts.

### Fixed
- **"Interrupted: Duplicate scan (after download) — CancelledError"** in the notifications: a download finishing
  while the scan for the one before was running cancelled that scan. It now finishes, and one more scan follows
  for the videos that came in meanwhile.
- **The page background stopped after one screen** on long pages (Help, settings...) in some browsers: it now
  reaches the bottom of every page.
- **Performer photos were missing after restoring a backup with other folders** (the database still had the old
  computer's paths, e.g. `G:\…\cache\performers\…`, and the log showed "File … does not exist"): photos — ThePornDB's
  and those added by hand — are now looked for in today's folders at every start and found again. A missing photo is
  simply not shown, no longer an error in the log.
- **The video page didn't update after Repair/Update** (views, likes, description... stayed as they were until you
  reloaded the page): it now reads the new details as soon as the video changes.
- **A black window flashed for a moment when hovering a video** (the first time per video, on Windows when the app
  runs without its console — tray, shortcuts, service): FFprobe, checking the hover preview, opened a console
  window. No program the app starts (FFprobe, FFmpeg, virus scanners...) opens a window any more.

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
