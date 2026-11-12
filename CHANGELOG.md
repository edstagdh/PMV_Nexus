# Changelog

All notable changes to PMV Nexus are listed here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). The current version is in the `VERSION` file.

## [2.30.0] - 2026-11-13

### Added
- **Extensions:** optional add-ons in an `extensions/` folder next to `run.py` (not part of the app, never created
  by it; with none installed nothing about them shows). Each is a Python package with a `register(api)` function
  (`backend/extensions.py` documents the API). An extension can add:
  - actions to a video's menu and the library's picked-videos bar, and settings panels under the new
    **Server settings → Extensions** tab (listed only when an extension is installed; shows each one as loaded or
    failed). Extensions draw no interface: they return forms (text, password, number, choices, checkboxes, info
    lines, links) and the app shows them step by step (`GET /api/extensions`,
    `POST /api/extensions/actions/{id}`, `POST /api/extensions/panels/{id}`);
  - credentials (stored encrypted, shown under Credentials), permissions (in the roles editor), sources, API routes,
    start / stop hooks, its own data on a video, Tasks-page entries and notifications.
  - A broken extension is skipped and the app runs without it.
  - `python -m Tools.install_extensions` installs their packages (`extensions/<name>/requirements.txt`); the Windows
    Setup runs it when the folder exists.
- **Sources** (`backend/sources/`): where a video comes from is now a source module — details, the download link
  and the download itself. PMVHaven, PornTubeAI and HMVHaven are one built-in source type (`pmv_platform.py`);
  extensions can add more.

### Removed
- **Share (Telegram, Gofile, Pixeldrain)** is no longer part of the app: the Share actions, the share dialog,
  the Sharing switch, the Telegram sign-in, the share credentials, the `videos.share` permission, the share stats and
  `Tools/generate_telegram_session.py`. The `kurigram` and `pytgcrypto` packages are no longer installed.
  Earlier share results stay stored on the videos (unused). Upgrading: `git rm` the removed files (see the release
  notes); the stored share credentials stay in `data/secrets.db`.

### Changed
- Server settings → Proxy: the per-destination upload switches became one **Extensions' uploads** switch
  (`proxy.uploads`; shown only when an extension is installed). Any of the old switches that was on turns it on.
- `features.share` in `config.yaml` is no longer used (ignored when present).

## [2.29.0] - 2026-11-13

### Added
- **Tray icon** (`run.py --tray`, Windows; the installer's shortcuts use it): the app runs without a console window.
  - Click the icon / **Open PMV Nexus**: opens the app in the browser — the default one, or `tray.browser` in
    `config.yaml` (a program + arguments, e.g. a browser profile; `{url}` = the app's address).
  - Hover: what it's doing ("2 downloads, 1 re-encode, 1 task, 1 playing", or "running at …").
  - Right-click: Open, **Copy the setup code** (only while no account exists; also shown in a notification),
    Open the log folder, Open the app folder, **Exit** — asks first when work is running, then stops cleanly.
  - Starting it again while it runs just opens the browser. A folder that can't be reached at start shows a message
    box and offers the console window, where it can be chosen.
  - New dependency on Windows: `pystray`.
- **Windows installer creates the admin account** for a new library ("Your admin account" page: username, password
  twice, the app's password rules), so no setup code is needed; a restore uses the backup's accounts. Shortcuts and
  "start with Windows" now start the tray version; a "PMV Nexus (console)" shortcut runs it with its log window.

## [2.28.0] - 2026-11-12

### Changed
- **The app is now called PMV Nexus** — everywhere: the interface (tab titles, sidebar, sign-in, Help → About, the
  "was updated" notice), the README and guide, the Windows installer (`installer/windows/PMVNexus.iss`, Setup file
  `PMV-Nexus-Setup-<version>.exe`, install folder, Start-menu / desktop shortcuts, service `PMVNexus` /
  `PMVNexusService.exe`, firewall rule), `install.sh` (systemd user service `pmv-nexus`), `install.bat`,
  `start.bat`, `run.py`, the API's user agent and the repository links (`edstagdh/PMV_Nexus`).
- New backups are named `pmvnexus_backup_<date>_<time>.zip`; existing backups keep their names and stay listed,
  testable and restorable.
- **Userscript renamed** to `userscript/pmv-nexus.user.js` ("PMV Nexus - PMVHaven companion", v2.19). Tampermonkey
  treats a renamed script as a new one: remove the old one, install this, and enter the address and API key again.

### Added
- **Terms of use and disclaimer** (`DISCLAIMER.md`): an independent project, not affiliated with PMVHaven or any
  other site; adults only; the user follows each site's terms (download only what a site offers for download, for
  personal use), respects copyright and doesn't redistribute; no media is hosted by the project; no warranty.
  - The README opens with a short version of it.
  - The **Windows installer** shows the terms and needs "I accept" to continue.
  - The **first-run setup page** (creating the admin account) needs "I'm 18 or older and I accept these terms".
  - **Help → Terms of use** shows the full text.

## [2.27.0] - 2026-11-12

### Changed
- **PMVHaven and its sister sites through their API only** — no more reading web pages:
  - Video details from `/api/videos/<id>`; the file from the site's own download endpoint
    `/api/videos/<_id>/download` (the `_id` comes from the details; it's what the site's Download button uses), in
    original quality.
  - Videos the site doesn't offer for download now fail with a clear message instead of falling back to the
    playback stream (HLS) or the direct storage link — in line with the site's terms (§9: download only "where
    downloading is permitted"; §5: no scrapers).
  - Your **PMVHaven API key**, when saved, is sent with these requests (`Authorization: Bearer`), so the site knows
    they're yours. Not required for public videos.
  - The **hidden-Firefox sign-in (Connections) isn't needed for downloads any more**; the health check no longer
    counts a missing sign-in as a problem.
  - Repair / update and the thumbnail picker's timeline also use the API only.
- New `download.website_fallback` (config.yaml, default `false`): `true` brings back reading the web page when the
  API can't help — the pre-2.27.0 way.

## [2.26.2] - 2026-11-12

### Changed
- **Windows installer shows what it's doing.** Every long step (the Python environment and packages, Firefox for
  logins, FFmpeg, reading / unpacking / restoring a backup, the service) now runs in the background under a
  "Please wait" page with a moving bar, the step's name and the latest line of its output — or, for backups,
  "Unpacking 1,200 of 3,400 files" — instead of a window that looked frozen.
- **Backup password checked first:** "Checking the password..." takes a moment and nothing is unpacked until it's
  right; a wrong one says so ("Wrong password - try again"). Then "Password accepted. Unpacking the backup...", and
  the folders page confirms "Backup opened (made by version X)".
- **Userscript (v2.18):** the Windows installer writes the chosen port into the userscript's default address
  (`userscript/pmv-nexus.user.js` in the install folder). New menu command **Change Download
  Manager Address / API Key** — an installed userscript keeps the address it was first given, so change it there
  after the app's port changes.
- `Tools/setup_core.py`: `inspect --verify-only` (quick check of a backup and its password) and `--progress`.

## [2.26.1] - 2026-11-12

### Fixed
- **Windows installer didn't compile** ("Unknown preprocessor directive", line 340): a message continued on a
  line starting with `#13#10`, which Inno Setup's preprocessor reads as one of its own directives; the same message
  also used `StringChangeEx` as a value (it changes a variable in place).
- Note for updating by zip: 2.26.0 needs the 2.25.1 files (`backend/backup.py`, `backend/folders.py`,
  `backend/library_mixins/core.py`, `backend/library_mixins/item_extras.py`, ...) — without them `run.py` stops with
  "cannot import name 'restore_plan'". This release's zip contains everything changed since 2.25.0.

## [2.26.0] - 2026-11-12

### Added
- **Windows installer (`Setup.exe`)**, built with Inno Setup (`installer/windows/`):
  - Installs just for you (no admin) or for all users; Start-menu and optional desktop shortcut; how the app runs:
    only when started, also at sign-in, or as a **Windows service** (WinSW; all-users installs).
  - Uses a Python 3.10–3.13 already on the PC; if there's none it tells you, downloads Python 3.12.10 from python.org
    (accepted only if it matches the file the release was built with) and installs it.
  - Creates `.venv`, installs the packages and the Playwright Firefox (in `runtime\`), optionally downloads FFmpeg
    (in `runtime\`); details in `data\logs\install.log`.
  - First install: new library or **from a backup** (+ its password), the videos and cache folders, other folders
    only when one from the backup can't be reached, the port. Firewall rule for the port (all-users installs).
  - Updates keep `config.yaml`, `data` and the service; uninstall asks before deleting the library and settings.
- **Release workflow** (`.github/workflows/release.yml`): pushing a tag `vX.Y.Z` that matches `VERSION` builds
  `Setup.exe` on GitHub Actions and publishes a GitHub Release with it, its SHA-256 and the CHANGELOG section;
  "Run workflow" makes a test build without publishing.
- **`install.sh`** for Linux / macOS: the same basic install in the console, plus an optional systemd user service.
- `Tools/setup_core.py`: what the installers call (INI in/out) — reading a backup's folders, then applying the
  restore or the new settings with the same checks as `run.py --restore`. `Tools/open_app.py`: the shortcut — opens
  the app in the browser, starting it first if needed.
- `run.py --open-browser` (open the browser once the app answers) and `--no-start` (only restore / check folders).
  FFmpeg and the Playwright browser in `runtime/` are used when present.

## [2.25.1] - 2026-11-12

### Added
- **Folders checked when installing and restoring** (backend/folders.py). The console lists where the app keeps its
  files — videos, thumbnails & previews, site logins, database, backups, logs — with *found / will be created /
  NOT REACHABLE*, and lets you change any of them (type its number; Enter keeps them):
  - `install.bat`, new setup or re-run: choose the folders before the port.
  - Restoring (`run.py --restore`, `install.bat` with a backup, or a restore staged on the Backups page and applied
    at the next start): the folders come from the **backup's** settings; one that can't be reached here (a drive
    this computer doesn't have, a Windows path on Linux) must get a new place. The restored files that belong in a
    changed folder are written there, the new places are saved in `config.yaml` (and dropped from the in-app
    overrides), and at the start the library finds the videos, thumbnails and previews in the folders now set —
    by `<creator>/<file>` when the backup came from another computer (data/relink_pending.json).
  - Every start: if a folder in the settings can't be reached (e.g. the videos drive isn't plugged in), `run.py`
    asks for its new place in a console window, or stops with a clear message instead of a traceback.
- New backups record the app folder they were made in (`root` in the manifest), for finding files after moving.

### Changed
- A restore applied without a console window (e.g. started as a service) whose folders can't be reached waits
  (stays staged) instead of half-restoring; files that still can't be written are counted in one line.

## [2.25.0] - 2026-11-12

### Added
- **Backup password:** Management → Backups → *Backup password*. While one is set, every new backup (manual or
  scheduled) is an AES-256 encrypted zip — 7-Zip / WinRAR open it with the password, nothing opens it without.
  Only a small `backup_info.json` (version, date, contents) stays readable so the list can show it (🔒). The
  password is kept in the encrypted credential store and can't be recovered. Restore and Test use it, and ask for
  the password when it doesn't open a backup; a protected backup is decrypted into `data/restore_staged.zip` when
  the restore is staged, and that copy is deleted once the restore is applied (or cancelled). New dependency:
  `pyzipper`.
- **Port in `config.yaml`:** `server.port` (default 8676) and `server.host` (default 0.0.0.0) are read by `run.py`
  at start; `--port` / `--host` still override them for one run.
- **`run.py --restore <backup.zip>`** (also `start.bat --restore …`): set up a new install — or another computer —
  from a backup in one step: the backup is put back, then the app starts with its accounts and settings (no setup
  code). Asks for the password of a protected backup.
- **`install.bat`** (Windows first install): checks Python, creates `.venv`, installs the packages and Playwright's
  Firefox, checks ffmpeg, then asks for a backup zip to set up from — or the port for a new setup
  (`Tools/first_install.py` writes it to `config.yaml`). Safe to run again.
- **Restore test** checks that this app version can restore the backup.
- README: *Backups & restore* (contents, password, restoring, setting up from a backup, other versions); the
  port and `install.bat` in Installation / Running the app.

### Changed
- **Backups from older versions:** every backup since 1.2.0 restores into this version (the database and settings
  are brought up to date at the start after the restore) — tested end to end with backups made by 1.2.0, 1.10.0,
  2.0.0, 2.10.0 and 2.17.3. A backup made by a newer version than the app is refused with "Update the app first".

### Fixed
- The restore test failed for backups made before 2.2.0 (it required the `playlists` table, added on start).
- The *Create backup* log entry didn't name the backup file.
- `run.py --restore` with a relative path, typed in another folder, looked in the app folder.
- README: removed a leftover fragment of the old `.env` table and the outdated OpenCV note; the Re-encode and
  Views lines are current.

## [2.24.4] - 2026-11-12

### Changed
- **Panic page:** instead of an empty white page, an ordinary-looking article — one of five (lib/decoyPages.js:
  baking, houseplants, hiking, home desk, groceries) from invented lifestyle sites, with made-up everyday ads, its
  own tab title and icon, address `/read/<article>`, dark or light to match the app's theme (custom themes too), a
  different one each time. Links and buttons on it do nothing; Back returns to the app.

### Fixed
- Opening `/login` while signed in (e.g. with Back) showed "Page not found"; it now opens the library.

## [2.24.3] - 2026-11-12

### Changed
- **Panic keys belong to the site:** their keydown is claimed (like YouTube's shortcuts), so the browser doesn't act
  on them here outside text fields — Firefox's search-as-you-type no longer opens on the first key and swallows the
  second. Not with another Ctrl / Alt / Shift / ⌘ held (Ctrl + F, Ctrl + A still work), not in text fields, other
  sites unaffected. Setting them warns about this, and names any player shortcut (K, Space, F…) that stops working.
- **Default volume 10 %** (was 2 %) for a browser that never changed it; phones still play at full volume.

## [2.24.2] - 2026-11-12

### Changed
- **Panic keys, setting them:** "Recording keys…" shows each key as it's detected; after letting go, "The app
  detected these keys: A + D" asks to **Use these keys**, **Try again** or **Cancel** (saved only when confirmed).
  At most 3 keys (more are ignored, with a note). Once saved: "Only pressing A + D all at once sets it off."

### Fixed
- Recording new panic keys that included the current ones set off the panic page.

## [2.24.1] - 2026-11-12

### Fixed
- **Server unreachable:** only some pages (those whose own request failed, like the library) showed an error in
  their content; others (e.g. Currently Playing) kept old content. Now every page's content shows the same
  "Can't reach the server" box while the banner is up, and comes back refreshed by itself (the video page keeps its
  player). Network failures anywhere read "Can't reach the server — is it running? Is the network up?" instead of
  the browser's own wording ("NetworkError when attempting to fetch resource.", "Failed to fetch").

## [2.24.0] - 2026-11-12

### Added
- **Continue where you left off:** each person's place in each video is saved (every 10 s while playing, on pause,
  seek, end, video change and when the tab closes; `PUT/DELETE /api/library/{id}/position`, table `watch_positions`,
  not in the log) and the video starts there next time, with **Start over**. 95 %+ counts as watched.
  Videos the API returns carry `watch: {pos, dur}` for the person asking only.
- **Watched line on thumbnails:** a thin red line along the bottom showing how far you got.
  Both switchable in **My settings → Video page**.
- **Panic keys:** keys pressed together (set in My settings → Video page) stop all playback and show an empty
  page (`/blank`: no title, no icon, no notifications). Only listens; browser shortcuts unaffected; in text fields
  only combinations with Ctrl / Alt / ⌘ count.

## [2.23.0] - 2026-11-12

### Changed
- **The app's own confirmation window** for every "Are you sure?" (27 places), instead of the browser's: browsers
  can offer to stop asking, after which their confirmation answers by itself. Destructive ones have a red button
  named after the action (Delete, Remove, Stop…); Escape / a click outside / Back = Cancel, Enter = OK.
- **Library layouts:** grid, list and table for admins; everyone else always gets the grid, without the layout buttons.
- **Video page:** "Next random" is now **Discover again**, as a highlighted button.

### Fixed
- **Progress bars on Tasks** (downloads, re-encodes, jobs) stopped at 36rem; they now span the row.

## [2.22.1] - 2026-11-12

### Added
- **Re-encode without the window:** Server settings → Re-encode → **Ask for the codec and settings each time**
  (`reencode.ask_each_time`, on by default). Off: ⋮ / video page **Re-encode** queues straight away in the default
  codec with the server's settings, and Library → Select → Re-encode… only confirms. A video already in the default
  codec still gets the window.

### Changed
- Server settings → Converting while watching: mentions **⚙ → 720p** (the player's button moved there in 2.19.0).

## [2.22.0] - 2026-11-12

### Added
- **Re-encode window:** one **Re-encode…** entry (⋮ menu, video page) instead of one per codec. Pick the codec and
  see its Server-settings quality, preset and options, changeable for this re-encode only (kept on the video as
  `reencode_options`; only what differs from the server is sent). Library → Select → Re-encode… uses the same window.
  `GET /api/reencode/defaults`; `POST /api/library/{id}/reencode` and `/api/bulk/reencode` take `options`.
- **Switch re-encoding / sharing off** (Server settings → Re-encode / Credentials, `features:` in config):
  hidden everywhere for everyone, new requests refused (queued ones finish). `POST /api/settings/features`.

### Changed
- **"Share to…" is now "Share"** (⋮ menu, video page, Library → Select, the window's title).
- **Download moved into the player's ⚙ menu**, under 720p.

### Fixed
- **"IndexError: tuple index out of range" in the Tasks list** (list_scheduled_jobs) and similar random database
  errors: the one SQLite connection was used by several threads at once (plain routes run on worker threads).
  Every statement now runs, and is read in full, under one lock.

## [2.21.0] - 2026-11-12

### Added
- **Seekbar previews:** the frame at that moment above the seek bar while you point at it (or drag it on a phone).
  Per video: frames every 2 s (fewer for videos over ~13 min, at most 400), tiled into WebP sheets of 10 × 10 in
  `<cache>/.storyboards/<video id>/`, with the WebVTT thumbnail track built on request
  (`GET /api/library/{id}/storyboard.vtt`, sheets at `.../storyboard/{n}.webp`). Made one at a time at low priority:
  after a download or upload, when a video without them is played (next in line), or for the whole library from
  **Management → Maintenance → Seekbar previews** (`GET/POST /api/maintenance/storyboards`, permission Maintenance);
  shown on Tasks as "Making seekbar previews" and stoppable. Videos with frequent key frames only decode those
  (seconds per video); others are decoded fully. Removed with the video, moved with the cache folder, listed in
  Untracked files when orphaned, not backed up.

## [2.20.0] - 2026-11-12

### Added
- **Currently Playing** page (sidebar, `/playing`): the videos being played right now, updating live. Only the videos —
  never who plays them, where they are in them or since when. New permission **Currently Playing** (`live.view`, in
  the default *user* role on new installs; existing installs: add it under Users → Roles).
  The player reports "this tab plays video X" (`POST /api/now-playing`, every 20 s while playing and when it stops;
  kept in memory only, dropped after 90 s of silence; not written to the log). `GET /api/now-playing` lists the videos.

### Fixed
- **Playlists in fullscreen on a phone stayed in portrait after the next video** (2.19.0): the browser's own
  fullscreen-video rotation let go of the app's orientation lock while the next video loaded, and the app thought
  it was still locked. The app now asks for the video's orientation again whenever the screen turns away from it
  (and a few times while a new video starts).

## [2.19.1] - 2026-11-12

### Changed
- **Fullscreen title while a playlist plays:** `Playlist | Title` (e.g. "long videos | video that is very long").
- **Fullscreen title on phones:** at most two lines ending in "…", a little smaller on narrow screens; held upright it
  gets its own row under the buttons at the top (it ran under them), and the shading behind it is no longer cut off.

## [2.19.0] - 2026-11-12

### Added
- **Library → Filters → Performer details:** cup size, age range, hair, eyes, enhanced / natural — videos where one
  performer has all of them (from ThePornDB profiles). Kept in smart playlists and in the address.
  `GET /api/library` takes `p_cups`, `p_hair`, `p_eyes`, `p_age_min`, `p_age_max`, `p_enhanced`;
  `GET /api/library/performer-filters` lists the choices.
- **⏭ Next** in the player while a playlist plays.
- **Title at the top in fullscreen** (with the controls).
- **Swipe for brightness / volume** on the sides in fullscreen on phones and tablets (left / right). **My settings →
  Video page → Sides of the video in fullscreen** switches back to double-tap to skip.

### Changed
- **720p (converting while watching)** moved from the control bar into the **⚙ menu**, above Speed (shows On / Off).

### Fixed
- **Playlists in fullscreen on a phone:** after a horizontal video the screen could turn to portrait for a moment
  before the next one. The screen is now turned (and kept) by the app from the next video's known size, before it
  loads.

## [2.18.0] - 2026-11-12

### Added
- **Library → Select: Find songs, Repair / update, Re-encode…, Share to…** for the picked videos. Re-encode asks
  for the codec, Share for the destinations; everything is queued at once and the result lists what was skipped and
  why. One line in the log for the whole selection (`POST /api/bulk/{action}`, each action needs its own permission).
- **Performers page → Filters:** gender, nationality, ethnicity, hair, eyes, cup size, age / height / weight ranges,
  still active, enhanced, tattoos, piercings — from the ThePornDB details already saved (heights / weights in any
  unit are turned into cm / kg). Only fields someone has are shown.
- **Playlist covers:** **Cover…** on a playlist page — collage of the first 4 videos (default), one video's cover, or
  your own picture (WebP, in `data/playlist_covers`, in every backup).
- **A-B repeat** in the player, off by default (**My settings → Video page → A-B repeat**).
- **Shazam tries a missed sample again** 7 s later (Songs settings, on by default; `songs.shazam_retry`).
- **Converting while watching shows on Tasks**, with progress and **Stop** (the viewer falls back to the original).

### Changed
- The player shows the video's **length** after the seek bar instead of the time left (click still switches).
- The ⋮ menu offers **Re-encode to HEVC** and **to AV1** (default first, minus the video's own codec); before, only
  the default codec was offered.
- **Performer photos:** at most 10 from ThePornDB (was 8) plus **one** of your own; a new own photo replaces the old one.
- "Technical details" is now "Technical Details" on the video page.

### Fixed
- **Find songs on a video while another scan ran** only said "Waiting for another scan to finish…": it now shows on
  Tasks as waiting, and starts when the other finishes.
- **Pop-ups stayed open after the browser's Back / Forward.** They now close.
- **A running re-encode's `_temp.mp4` file was listed in Untracked files.** It's hidden while it's being written; a
  leftover one is listed as "unfinished re-encode".

## [2.17.3] - 2026-11-11

### Fixed
- **Filters: clicking a filter's title removed a selected value.** The titles (Performers, Tags, Creators, Codecs)
  were labels wrapped around the whole box, and the browser forwards a click on a label to the first control inside —
  the × of the first chip. They now point at the box's search field: clicking a title puts the cursor there and
  opens the list. The same happened with **Add** / **Remove** in Bulk edit; fixed the same way.

## [2.17.2] - 2026-11-11

### Added
- **Performer photos: easier to look through.** Arrows on the sides (always visible on touch screens), drag or swipe
  sideways (the photo follows; a short drag snaps back, up/down still scrolls the page), ←/→ keys when the photo is
  focused, and a "2 / 5" counter. Moving by hand restarts the automatic change, so your pick stays a while.

## [2.17.1] - 2026-11-11

### Added
- **Search by codec:** `codec:` (also `codecs:`) searches the codec with its version, with wildcards: `codec:hevc`,
  `codec:avc*`, `codec:*l5.1*`.
- **My settings → Library → Group codec versions** (on by default): Filters list each codec once (AV1, AVC, HEVC…)
  instead of every profile / level; picking one finds all its versions. Turn it off to pick exact versions.

## [2.17.0] - 2026-11-11

### Added
- **Performers page** (sidebar → Performers): everyone in your videos, one card per person (names linked to one
  ThePornDB profile together) with photo and video count. Matched to ThePornDB first, always; then the others under
  "Not on ThePornDB". Search, sort (most videos / name / fewest), show everyone / matched / not matched (remembered).
- **Grey placeholder** (female / male silhouette) wherever a performer has no photo: ThePornDB's gender for matched
  ones; for the others female by default, switchable on their page (**Placeholder: Female / Male**).
- **Photos for performers not on ThePornDB**: their page has **Add photos** too (`performer_local`, photos keyed by a
  local id). When such a name is matched to ThePornDB later, its photos move to the profile.
- **Search wildcards:** `*` stands for any letters inside one tag or name (`tag:4k*`, `tag:*remix`, `tag:h*vc`).
  Plain words already matched any part (`tag:avc` finds "H264-AVC"); the search tips now say so.

### Fixed
- **Library reloading every second during a re-encode** (the blinking dot from 2.16.1): the re-encode's progress was
  saved to the database — and announced as a changed video — on every tick. It's now saved at most every 10 seconds;
  Tasks still follows it live.
- **Search tips** opened underneath the Filters panel; they now open over it.
- Re-encode progress stayed at 0% with FFmpeg 6 and older (they write sizes as "kB", FFmpeg 7 as "KiB").
- Performer photo links accept more photos per performer (ThePornDB's plus up to 30 of your own).

## [2.16.1] - 2026-11-11

### Fixed
- **Library: blinking orange dot.** When something kept changing a listed video (a running job, edits from another
  tab), the page reloaded about every second and showed its loading dot each time; a filtered list loads slower, so
  it was easy to see. Refreshes after live changes are now silent and happen at most every 3 seconds; the dot only
  shows for loads you start (filters, sort, page) that take longer than a quarter of a second.
- The filter lists are only re-drawn when the tags / performers in the library actually change, not on every live
  update (smoother with a big library while jobs run).
- The browser console (verbose level) says why the library refreshed by itself, e.g. "a listed video changed".

## [2.16.0] - 2026-11-11

### Added
- **Your own performer photos.** On a performer's profile, **Add photos** (Performers permission) adds pictures from
  your computer — several at once or dropped on the panel — next to her ThePornDB photos. Each is converted to WebP
  on upload (`backend/images.py`; 30 MB at most). **Show first** makes one her main photo (also on similar-performer
  cards); photos can be removed. They're stored in `data/performer_photos`, kept when the profile refreshes, and
  included in every backup.

## [2.15.0] - 2026-11-11

### Added
- **Performer photos as WebP.** Photos from ThePornDB are converted to WebP when downloaded (quality 80, at most 1600
  pixels on the long side; `backend/images.py`). **Maintenance → Performers → Convert photos to WebP** converts the
  ones saved before, once: in the background on Tasks, several at a time, can be stopped and resumed; it reports the
  space freed. A photo whose original can't be removed (open elsewhere on Windows) is listed in Untracked files.
- New dependency: `Pillow` (`pip install -r requirements.txt`).

### Fixed
- **Maintenance → Untracked files** listed every performer photo as untracked. Photos performers use are now known;
  unused ones show as "performer photo". The 720p playback temp folder (`.transcode`) is no longer scanned.

## [2.14.2] - 2026-11-11

### Fixed
- **Management → Logs: clear names for every change.** Entries were named after the code's function, so adding a
  song showed just "Add", and others read "Pmv login", "Reencode item" or "Performers skip". Every change now has a
  written name, e.g. "Add song to Music · “Song — Artist” → “Video”", "Dismiss suggested song", "Find songs · “Video”",
  "Changed user's role / groups / permissions · “name”", "Server settings: Uploads".
- Roles, groups and stopped jobs are named in their entries; a song added under a corrected name shows that name.
- Routine entries are recorded at Debug level (hidden by default): watched time (every 30 s while watching),
  notifications marked read, the end of a 720p playback, and each received upload (its start and result are still
  shown).

### Added
- `python -m Tools.check_action_labels` lists any route that changes something without a name for the logs.

## [2.14.1] - 2026-11-11

### Fixed
- **Find songs** failed on every video with "Shazam didn't answer (SignatureError)". The sound samples were WAV files
  without their lengths in the header (FFmpeg can't fill them in when writing to a pipe), which Shazam's signature
  maker rejects before anything is sent. The header is now written by the app. A sample whose signature still can't
  be made is skipped instead of ending the scan.

## [2.14.0] - 2026-11-11

### Added
- **Finding songs in videos.**
  - A video's ⋮ menu → **Find songs**: 12-second samples of its sound (one every 30 s, at most 12) are recognised by
    Shazam (shazamio, no key); samples Shazam misses go to AudD when its API token is saved (at most 4 per video).
  - What's found is suggested, with how many samples heard it and where. **Add** (name can be corrected first) puts
    it in the video's Music as a hand edit; **Dismiss** hides it for good for that video. Rescans don't bring back
    added or dismissed songs, or songs the video already lists.
  - **Management → Songs**: every waiting suggestion per video, counts, **Add all sure ones** (heard in 2+ samples),
    **Find songs now**, and the settings (`songs:` in `config.example.yaml`).
  - Scheduler job **Find songs in videos without music**: up to 20 videos per run, newest first; a video is listened
    to again after 30 days if it still has no music. Shown on Tasks, can be stopped.
  - New permission **Songs** (`songs.manage`, part of the old "manage the library"). AudD token in Credentials, with
    **Test key**.
- New dependency: `shazamio` (`pip install -r requirements.txt`).

## [2.13.0] - 2026-11-11

### Added
- **Converting while watching (720p).**
  - A **720p** button in the player streams a copy the server converts on the fly: H.264, at most 720p (or 480p),
    as HLS played with hls.js. Seeking restarts the conversion at that point. The choice is remembered per browser.
  - A video the browser can't play (e.g. HEVC / AV1 without support, or sound with no picture) switches to it by
    itself.
  - Limited: each conversion gets 50% of the cores by default, at low priority, and FFmpeg is paused when it's about
    a minute ahead of the viewer. At most 2 at once, one per person. Idle conversions end after 20 minutes; the temp
    files (`<cache>/.transcode`) are deleted when playback stops and when the app starts.
  - **Server settings → Re-encode → Converting while watching** (`playback:` in `config.example.yaml`).
- **VirusTotal:** **Test key** in Credentials (looks up the harmless EICAR test file). Every lookup and its answer
  is logged, the "Uploaded …" log line includes the result, and the Upload page shows the checks that ran.

### Fixed
- The VirusTotal fingerprint check ran, but nothing showed that it did: its result was only logged when a file was
  refused.
- Playing and downloading a video file now needs the **Play videos** permission (it only needed Browse the library).

## [2.12.0] - 2026-11-11

### Added
- **Permissions, roles and groups.**
  - Each feature is a permission: add videos, upload, edit details, remove, re-encode & repair, share, names,
    performers, duplicates, maintenance, see / manage Tasks, stats, logs, server settings, own settings / password.
  - Someone's permissions = their role + their groups + permissions given to them directly.
  - **Users → Roles / Groups** (create, edit, delete) and **Access** per person, which shows what they end up with.
  - Changes apply on the person's next click, no new sign-in; their menus update by themselves.
  - Roles with the old "manage the library" permission keep all of its powers.
- **Add to playlists** from Library → Select: tick one or more playlists, or make a new one from the picked videos.
- **Stats:** "XX% free" for the download and cache folders (one line when they're on the same drive), and CPU /
  memory used by this app (with its FFmpeg jobs) shown against the whole computer.
- **Uploads:** **Test the scanner** shows exactly what the antivirus answers. Optional **fingerprint check** on
  VirusTotal (only the SHA-256 is sent; `upload.hash_check`, key in Credentials) refuses known-malicious files.
- **Changing the download / cache folder without moving** now links videos found at the same place under the new
  folder (e.g. after moving to a new computer).
- Guide: **Restoring**, with scenarios (undo a mistake, damaged database, new computer, settings only).

### Changed
- `/docs`, `/redoc` and `/openapi.json` are for the admin only; others get a "no permission" page that goes back to
  the library after 5 seconds. The docs pages now load under the security policy.
- Microsoft Defender is found through the system's folder settings, newest version first.

## [2.11.1] - 2026-11-10

### Fixed
- **Scroll bars under the tab names** (Server settings, My settings, Management, Help). Each tab was pulled 1 px
  over the line under it, which made the row 1 px taller than its box. The line is now drawn inside the row. On a
  narrow screen the tabs still scroll sideways, with a thin bar only when they don't fit.
- **Pages had different widths** (each page set its own maximum: from 52 to 137 rem). Every page now uses the full
  width of the window, with the same margins, and Management's tabs line up with their content.

## [2.11.0] - 2026-11-10

### Added
- **Management → Names.**
  - Every tag, performer and music name with its video count. Rename one, or merge a misspelling into the right
    name ("abella danguer" → "abella danger"): every video that has it is changed.
  - **Remember** keeps the fix, so the same misspelling shows corrected on later downloads and syncs.
  - **Possible typos** suggests names a letter or two apart, with one-click merge.
  - **Remembered fixes** can be forgotten.
  - The upper/lower case Spellings section moved here from Maintenance.
  - A performer's ThePornDB link moves with a renamed name.
- **Bulk edit.** Library → **Select**: pick videos (across pages), then add or remove tags, performers and music
  on all of them at once. Saved as your edits.
- **Restore test** of backups.
  - Runs after every backup and from **Test** on the Backups page. It checks without changing anything:
    - the zip's checksums;
    - the manifest, and that every file restores inside the app's folders;
    - the database (SQLite integrity check, plus its videos, users and playlists);
    - the settings files.
  - Failures are notified.
  - New Scheduler job type: "Restore test of the newest backup".
- **Stats, rebuilt.**
  - Live CPU, memory, network, disks and current downloads, updated every 5 s.
  - For 7 / 30 / 90 days, a year or all time:
    - library growth (videos and size, from the whole history);
    - activity per day/week/month (downloads, uploads, re-encodes, shares, repairs and failures);
    - watching (time actually played, plays, most watched videos / performers / tags, per person);
    - free disk space.
  - Activity and watching are recorded from this version on; disk space by the new **Daily stats snapshot**
    System job.
- **Uploads, made safe.**
  - One at a time, first in first out, with your place in line.
  - Only .mp4 .m4v .mkv .webm .mov .avi .wmv .ts files can be picked (others are refused before sending).
  - The server checks the file's contents match its type, scans it with Microsoft Defender or ClamAV when
    installed (Server settings → Uploads → Virus scan: auto / required / off) and needs FFmpeg to find a known
    video container with a video track.
  - Every upload is re-written (re-packed or re-encoded) keeping only its picture and sound.
  - Files are received in a private `.incoming` folder, cleaned up on failure, cancel and restart.
  - An upload stops the moment more data arrives than announced.
  - Limits: largest file, unfinished uploads per person, free space for every upload in progress.
  - Only the uploader (or the admin) can see or act on an upload.
- **Security hardening.**
  - Security headers, including a strict Content-Security-Policy.
  - Changes coming from other sites (other ports on the same machine included) are refused.
  - Request bodies are capped at 2 MB (uploads excepted).
  - Sign-in is throttled after repeated failures; an unknown username takes as long as a wrong password, and a
    disabled account is only revealed to someone with the right password.
  - The server no longer names itself, and API answers aren't cached.
  - Regular users get file names instead of server paths, and not who added a video.
  - Tasks and duplicate-scan data are admin-only, and so are the exact Python / OS / FFmpeg versions.
  - The public health check shows only ok/not ok.
  - A restore skips any entry that would be written outside the app's folders.

### Changed
- Your own Settings are now **My settings** (sidebar and page).

### Fixed
- An upload that got stuck or failed could hold files in the download folder; temporary files never land there
  now.

## [2.10.1] - 2026-11-10

### Changed
- **Sidebar:** the admin section is now called **Server** and holds **Tasks**, **Management** (was "Admin portal") and
  **Settings** (the server's settings; your own Settings stay at the bottom). Tasks moved into this section, since
  only admins can open it. With the section folded, Tasks still shows while something is running. Addresses and
  permissions are unchanged.

### Fixed
- **Favorites are always private.** They can't be made public any more (one that was is made private again), and
  nobody else sees or changes someone's Favorites, the admin included.

## [2.10.0] - 2026-11-10

### Added
- **Stop running jobs** (Tasks and Scheduler pages): duplicate scans, performer matching, syncs with PMVHaven
  (only while still reading) and Repair/Update all (videos still waiting leave the queue; the ones being repaired
  finish). The run is recorded as "Stopped by …" in the job's history.
- **Notifications.**
  - The bell (sidebar; mobile bar) keeps what happened in the background: downloads, re-encodes, repairs,
    uploads and shares that finished or failed. Admins also get jobs and low disk space.
  - The badge counts unread problems. Two views: Problems and All. Mark read, Clear.
  - New notifications pop up as toasts; clicking one opens the video or page it's about.
  - **Settings → Notifications** turns the pop-ups off per kind.
  - Each person is told about the videos they added. Videos are now recorded with who added them; older ones
    notify the admins.
  - During Repair/Update all, only failures are announced. A burst of updates shows as one "N updates" toast.
- **A "lost the connection to the server" banner** when the app can't reach the server for a few seconds.
- **Disk-space guard.**
  - Below 10% free on the drive with the videos or the app's data, downloads stop. A download in progress goes
    back to the queue and its partial file is deleted.
  - A banner tells everyone and the admins get a notification. Waiting downloads say "Waiting for disk space".
  - Re-encodes and everything else carry on.
  - Downloads restart by themselves above 11%.
  - The limit is in Server settings → Download (0 = off), along with the current free space.

### Changed
- **Bigger queue buttons** on the Tasks page: First / Up / Down / Last, with clear arrows and a word each, in place
  of the thin ⤒ ↑ ↓ ⤓.
- **ThePornDB "too many requests":** the app now waits as long as ThePornDB asks (Retry-After, at most 2 minutes),
  retries up to 3 times, and from then on spaces its requests further apart (up to 5 s) until restarted.
  Requests were, and still are, at least 1 second apart.

### Fixed
- A video waiting to download is listed under Waiting even if an older repair or share of it failed.

## [2.9.0] - 2026-11-10

### Added
- **Jobs on the Tasks page.** Everything that runs in the background now shows on **Tasks**, not only each
  video's downloads, re-encodes, repairs and share uploads:
  - **Jobs running now:** Scheduler jobs, duplicate scans (by hand, scheduled or after downloads), syncs with
    PMVHaven and their previews, backups (including **Create backup**), performer matching, repair-all, syncing
    details from metadata files, looking for or deleting untracked files, moving the download or cache folder,
    uploads being checked or converted, and the startup upload-date fill. Each shows its stage, "N of M done",
    a percentage and bar (or a moving bar when the total isn't known), what it's on, how long it has been
    running, and whether it was scheduled, started by hand or automatic.
  - **Coming up:** the next Scheduler runs, and a duplicate scan waiting to start after downloads.
  - **Finished in the last day:** each job's result ("matched 7 · refreshed 0", the backup's name and size,
    "read 12 · 0 matches") or why it failed or didn't start.
  - The sidebar's Tasks count includes running jobs.
- **Progress on the Scheduler page:** a running job shows its stage and progress instead of only "Running…".

### Fixed
- **Repair/Update all queued only the first video** (a code indentation slip). It queues every downloaded video
  again.

## [2.8.0] - 2026-11-10

### Changed
- **Performer matching is a Scheduler job.** Looking up new performer names on ThePornDB, refreshing old
  profiles and fetching missing photos used to run in the background (after each download and every 30 minutes).
  Now it's the System job **Match & refresh performers** in **Admin portal → Scheduler**.
  - It runs daily at 05:00 at first. Change when it runs, or turn it off, like any other job.
  - Each run is in the job's history, with how many names were matched, profiles refreshed and photos fetched.
  - **Match now** and **Retry not found** (Admin portal → Maintenance → Performers) run that job straight away.
    That page also shows when it runs next and how the last run went.
  - The "Match new performer names in the background" switch (Server settings → Performers) is gone. If it was
    off, the job starts turned off.

## [2.7.2] - 2026-11-10

### Added
- **Several photos on a performer's profile.** Her ThePornDB photo and posters (up to 8) are downloaded and kept.
  The profile shows them one at a time, fading to the next every 5 seconds. It pauses while the pointer is over the
  photo, and the dots under it pick a photo. Profiles fetched before this get their extra photos by themselves in
  the background (no extra ThePornDB calls).

### Fixed
- **Performer photos are no longer cropped.** The photo box is portrait (2:3, like ThePornDB's photos) and always
  shows the whole picture.
- **Telegram sign-in when Telegram wants a login email first** (`SETUP_EMAIL_REQUIRED`). Telegram sends no code
  until the account has a login email. The sign-in now asks for an email, then for the code Telegram emails to it,
  and then the login code arrives by email. If Telegram asks for a payment or Premium instead, the app says so.
- The sign-in shows Telegram's own answer for how it sent the code ("Telegram's answer: APP", "SMS"…).
- Checked: the kurigram 2.2.27 update didn't change how sign-in works (`send_code` is now another name for
  `send_phone_number_code`), so it isn't why codes don't arrive.

## [2.7.1] - 2026-11-10

### Fixed
- **Telegram sign-in: where's the code?**
  - The app now says exactly how Telegram sent the login code. Usually it's a message in the Telegram app, in the
    chat named "Telegram" on a device you're already signed in on. That message often arrives without a
    notification. Other ways: SMS, a call, a missed call, email or Fragment.
  - **Didn't get it? Send it by SMS / call** asks Telegram for another way once its waiting time is over (when
    Telegram offers one).
  - If Telegram first wants a login email on the account, the app says so and how to set it up.
  - The log records how the code was sent (never the code).

## [2.7.0] - 2026-11-10

### Added
- **Performer profiles from ThePornDB.** Click a performer on a video's page to open her profile:
  - photo, age and details (birthplace, nationality, height, measurements, hair and eyes, tattoos, active years…);
  - bio, aliases and links;
  - **similar performers** that are in your library;
  - all her videos, plus **Her videos in the library**.
- **Matching:** each performer name in the library is matched once to a ThePornDB profile, and the profile's id is
  kept from then on, so the right person's data is always fetched.
  - A single clear match (her name or one of her aliases) links by itself.
  - A name that could be several people waits in **Admin portal → Maintenance → Performers**, with photos and
    details of each candidate. Pick one, search ThePornDB yourself, or skip the name.
  - New names are matched in the background after downloads and every 30 minutes. **Match now** and **Retry not
    found** run it at once.
  - Profiles and photos are fetched once, stored locally, and refreshed every 30 days (configurable).
- **Aliases count as one performer:** when ThePornDB lists "Riley R" as an alias of Riley Reid, both names point to
  her profile. With **Server settings → Performers → merge aliases** on (default), they show as "Riley Reid"
  everywhere: chips, filters, search, suggestions and related videos.
  - A wrong merge is undone on her profile with **Not her — split off**. That name is never merged into her again
    and is looked up on its own.
  - **Wrong person?** on a profile picks a different ThePornDB profile.
  - Your videos keep the names PMVHaven gave them; only what's shown changes.
- **Server settings → Performers:** merge aliases, background matching, refresh interval. The ThePornDB API key goes
  in **Credentials → ThePornDB**.
- API: `/api/performers/profile?name=`, `/api/performers/{id}/photo`, `/api/performers/overview`,
  `/api/performers/search`, `…/link`, `…/skip`, `…/split`, `…/{id}/refresh`, `…/match-now`,
  `POST /api/settings/performers`.

### Changed
- **Admin portal:** Users, Duplicates, Maintenance, Scheduler, Backups, Stats, Logs and Tools are now tabs of one
  page (sidebar → **Admin portal**, `/admin/portal/<tab>`). The sidebar's Admin section is down to **Server
  settings** and **Admin portal**. Old addresses (e.g. `/admin/logs`) still work.
- Performer chips on the video page open her profile instead of filtering the library. The profile's **Her videos
  in the library** does the filtering; chips on library cards still filter.

### Fixed
- **Logs** now say what each action was about. Before, entries like "Add to library external", "Discover (random
  video)", "Start local upload" or "Update user" didn't name a video or user. The target now comes from:
  - the video, playlist, user, scheduled job, backup, upload or performer in the request;
  - the video in the reply (e.g. the one Discover picked, or the one just added);
  - the request's harmless fields: title, name, username, URL, video ids, codec, destinations…
  Those fields also appear in the entry's details (`req_…`). Passwords, keys, codes and phone numbers are never
  read. "Set favorite" now reads "Add to Favorites" / "Remove from Favorites", and the userscript's add reads
  "Add to library (userscript)".
  - A video added from a link is logged with its link, and the entry is updated to the video's title as soon as its
    details are fetched (instead of showing "Fetching details...").

## [2.6.0] - 2026-11-10

### Added
- **Proxy** (Server settings → **Proxy**): an HTTP or SOCKS5 proxy, with an optional login. The address and login
  are stored encrypted, like other credentials. Use it for:
  - **the sites** (PMVHaven, HMVHaven, PornTubeAI):
    - **Split downloads:** downloads run on the direct connection and through the proxy at the same time, e.g. one
      each, twice as many at once. You choose how many go through the proxy. A warning shows when the total is
      more than 2, which most likely gets downloads refused or slowed.
    - **Everything:** every request to the sites goes through the proxy, including pages, details, downloads and
      sign-in.
  - **share uploads**, chosen per destination: Telegram, Gofile, Pixeldrain.
  - **Test the proxy:** checks the direct connection and the proxy side by side — whether each works, the address
    the sites see, latency, whether PMVHaven answers, and download speed (about 10 MB). The result says whether the
    proxy would slow things down.
  - **Check before use** (on by default): the proxy is tested before it's used (at most every 5 minutes). While it
    doesn't answer, nothing is sent through it: downloads wait for the direct connection, and uploads fail with a
    message instead of going out directly.
  - The tab shows what each connection is downloading right now.
  - HLS videos can only go through an HTTP proxy (FFmpeg doesn't support SOCKS). In split mode with a SOCKS proxy
    they use the direct connection.
  - API: `POST /api/settings/proxy`, `GET /api/settings/proxy/status`, `POST /api/settings/proxy/test`.
- **Telegram sign-in in the app** (Server settings → Connections → **Telegram**): enter the phone number, then the
  code Telegram sends, then the cloud password if two-step verification is on. **Check the login** confirms the
  saved login still works; **Sign in again** fixes an expired one. The new login replaces the old one only once it
  succeeds, and the old file is kept as `.bak`. The terminal script is no longer needed. API: `/api/telegram/*`.
- **Shares on the video page:** a **Shares** section in the details shows a badge for each destination the video
  was shared to. A successful share links to it; a failed one shows why on hover.
- **Settings → Video page → Mini-player:** turn the mini-player off and leaving a video page stops the video.

### Changed
- **Downloads at the same time** (Download tab) now really runs that many downloads at once. Before, downloads
  always ran one after another whatever it said. Keep it at 1; to download more at once, split with a proxy. More
  than 2 shows a warning.
- Requirements: `socksio` (SOCKS proxies). Run `pip install -r requirements.txt`.

### Fixed
- An expired Telegram login (`AUTH_KEY_UNREGISTERED`) now says to sign in again in Server settings → Connections,
  instead of only showing Telegram's raw error.

## [2.5.0] - 2026-11-10

### Added
- **Favorites:** every user has a built-in **Favorites** playlist. It's listed first, can't be deleted or renamed,
  and can still be made public. **Add to Favorites** (♥) on the video page adds or removes the video. API:
  `GET/POST /api/playlists/favorites`.
- **One spelling per tag, performer and music value:** values that differ only in upper/lower case ("Natural Tits"
  / "Natural tits") now count as one everywhere — suggestion lists, filters, the video page, related videos and
  stats. The app shows the spelling most videos use (on a tie, the one on the newest video). Your videos keep what
  PMVHaven wrote, so this choice follows the library: once newer videos make another spelling the most used, it
  takes over. **Admin → Maintenance → Spellings** lists every value written more than one way and lets you pick the
  spelling to always use. API: `GET/POST /api/library/spellings`.

### Changed
- **Search** finds videos containing every word you type, in any order. "alyx star natasha nice" now finds
  "… with Natasha Nice and Alyx Star". Put words in "quotes" to require them together as written. Works with
  `title:` and the other field searches too.

### Fixed
- **Phones:** while a playlist plays, it now shows right under the player instead of after the details and the
  creator's other videos (also on any window too narrow for the side column).
- **Phones, playlist in fullscreen:** when Autoplay moves to the next video, the screen turns to suit it —
  portrait for a vertical video, landscape for a horizontal one — instead of staying as it was.

## [2.4.0] - 2026-11-10

### Added
- **Upload page, step by step:**
  - **Files browsers can't play are fixed before they're added.** An MP4 with a browser codec is kept as it is.
    Other files (MKV, MOV, …) whose picture browsers can show are re-packed into MP4 without re-encoding
    (lossless, fast). Anything else is re-encoded. You pick **AVC (H.264), HEVC or AV1** with its quality and
    preset; the starting choice comes from the new **Server settings → Uploads**. The video joins the library
    only after this step.
  - **Server settings → Uploads:** the codec to convert to, AVC quality/preset (HEVC and AV1 use the Re-encode
    tab's), and **Convert automatically** so uploaders aren't asked.
  - **Thumbnail step:** pick one of 8 frames spread over the video, any frame with a slider, or upload your own
    image (JPEG/PNG/WebP).
  - **Preview step:** the 5 suggested clip start times each get a slider (clips stay inside the video and don't
    overlap). ▶ plays one clip and **Play all clips** plays them in a row, at 5% volume with its own volume
    slider. Clips can be added or removed. **Make the preview** builds the hover preview from them.
  - The performers / tags / music / flags boxes list every existing value and stay open while you pick several.
- **Playlist page:**
  - **Add videos** searches the library (same search as the library, with the search tips) and adds with one
    click.
  - **List or grid** view (remembered per browser).
  - Every video has the full **⋮ menu** plus **Play from here** and **Remove from this playlist**.
- **Add to playlist** (one video) is now a box like the tags input: the playlists the video is in are chips
  (× takes it out); type to find another and pick it. Below it, **New playlist with this video**. The name box,
  menu and Create button are now the same height.
- **Server settings → Logging:** switch logging off altogether (nothing recorded anywhere), or choose the outputs
  (Admin → Logs, the log file, the console), the level, log-file rotation and how long old files are kept, how long
  Admin → Logs keeps entries, and how client addresses appear. Applied without a restart (these were only in
  `config.yaml` before). Admin → Logs says when new entries aren't being recorded. API: `POST /api/settings/logging`.
- **Search tips:** a **?** in the library's search box shows examples of field searches (`title:`, `creator:`,
  `performer:`, `tag:`, `music:`, `description:`). Click one to use it.
- **Settings → Video page → Edit details: keep suggestions open** (on by default). The list stays open after you
  pick a value; turned off, it closes until you type again.
- API: `GET /api/library/local/{id}` (upload status), `POST /api/library/local/{id}/convert`,
  `GET /api/library/{id}/frame?t=&w=` (a frame as JPEG), `PUT /api/library/{id}/thumbnail/image` (your own image),
  `GET /api/library/{id}/preview-plan`, `POST /api/library/{id}/preview` (preview from chosen start times),
  `POST /api/settings/upload`.

### Changed
- **New video player: Video.js 10** replaces Plyr everywhere (video page and mini-player):
  - frosted controls, a settings menu (speed), and a **volume slider** when you hover the speaker, now in the
    mini-player too;
  - Loop and Download buttons kept;
  - on phones and tablets: a big **play/pause button in the middle** whenever the controls show, tap to show the
    controls, double-tap the sides to skip.
  - Hotkeys work anywhere on the video page; in the mini-player only while the player has focus.
- **Sorting:**
  - "Date added" and "Date downloaded" are merged into **Date acquired**: when the file arrived (downloaded or
    uploaded), or when the item was added if it has no download date. Old links and saved defaults that used
    "Date added" now use Date acquired.
  - "Date uploaded to the site" is now called **Uploaded to PMVHaven**.
  - The table's "Downloaded" column is now "Acquired".
- **Sidebar:** **Upload** is a button under **Add videos**, in the same style. Both keep their height when the
  Admin section is open (the sidebar scrolls instead of squeezing them).
- **Filters panel:** it stays at the top with the toolbar while you scroll and sits over the videos until you close
  it. Opening it while scrolled down shows it right away. Long panels scroll inside.
- **Edit details:** about 80% of the window wide, with Performers, Tags, Music and Content flags side by side. Each
  shows every existing value in a list under its box.
- **Mini-player on phones** (upright) is about half the screen wide. In the mini-player the Loop and Download
  buttons are hidden to save space.
- Playlist pages: removing from the playlist moved into the ⋮ menu (the row's × is gone).

### Fixed
- **Playlists in fullscreen:** with Autoplay on, the next video now plays without leaving fullscreen. The page
  catches up with the current video when you exit fullscreen.
- **Phones:** no black bar under the video on the video page. Portrait videos fit, with their controls inside the
  picture in and out of fullscreen.

## [2.3.0] - 2026-11-10

### Added
- **Upload** page (sidebar): add a video from your computer with its title, creator, upload date, description,
  performers, tags, music and content flags. The file streams straight to the download folder (no temporary copy,
  any size, with progress and Cancel); thumbnail, preview clip, technical details and a metadata JSON are made from
  the file. Uploaded videos (site "local") have no "Open on PMVHaven", are skipped by Sync with PMVHaven, and Repair
  remakes their thumbnail / preview / details from the file. API: `POST /api/library/local` (details) then
  `PUT /api/library/local/{id}` (the file as the request body).
- **Sort by upload date** (the date the video was published on the site), newest or oldest first, in the library,
  the table view (new "Uploaded" column) and the default-sort setting. Videos with no known date come last.
- **Mini-player:** drag it anywhere by its bar (remembered per browser; double-click the bar to reset), and it
  survives a tab reload — same video, playlist and position, paused. Reloading a video page also continues from
  where you were, paused.
- Creator suggestions (`/api/library/suggest?field=creators`), used by the Upload page.

### Fixed
- After a Repair made a new preview clip, hovering still showed the old (broken) one until the page was reloaded.
  Previews now carry a version (`preview_updated_at`), like thumbnails.
- Preview generation choked on videos that carry **cover art** as an extra picture stream ("Codec 'mjpeg' is not
  supported by the bitstream filter"); it worked only on the retry. The colour fix and the preview filters now use
  the real video stream only, and re-encodes no longer risk picking the cover art either. The colour fix is skipped
  on FFmpeg 6 and older, which can't apply it.
- **Logs** now name the playlist for playlist actions ("Create playlist · “Road trip”", "Delete playlist · …").
- **Edit details:** pressing Enter to take a suggestion now closes the suggestion list (it used to stay open,
  reset to the full list); typing again reopens it. Same for every field in the dialog and on the Upload page.

## [2.2.0] - 2026-11-10

### Added
- **Mini-player (background playback):** leave a video page while it plays and the video keeps playing in a small
  floating player while you browse — the same player, no reload. Title / ⤢ goes back to its page, ✕ stops it, and
  opening another video replaces it. Keys like Space control the page, not the hidden video, while it's open.
- **Playlists** (sidebar → Playlists), for every user:
  - add a video from its ⋮ menu / video page (**Add to playlist…**), or every video in a library view (all pages)
    with the new **Playlist** button next to the filters;
  - **smart playlists** that keep a library filter and fill themselves with matching videos;
  - **private** (owner only) or **public** (every user can see and play; only the owner or admin can change);
  - **Play all**, **Shuffle**, or start from any video; on the video page the playlist shows beside the player
    with Previous / Next, **Shuffle**, **Repeat** and **Autoplay**, and it continues in the mini-player;
  - reorder by drag-and-drop or ↑ / ↓, remove videos, rename, describe, delete.
- **Browser tab titles** name the page: the video's title on a video page, the playlist's name on a playlist,
  otherwise the page ("Users - PMV Nexus", "Sign in - …"); the library is just "PMV Nexus".
- **Clear all filters** button at the bottom of the library's Filters panel (search and sorting stay).
- API: `/api/playlists` (list, create), `/api/playlists/{id}` (get, PATCH, DELETE), `…/items` (add by ids or by
  library query), `…/items/remove`, `…/order`, `/api/playlists/for-item/{id}`.

### Fixed
- A local preview clip couldn't be made for **HEVC** videos (e.g. when PMVHaven's own preview is broken):
  "Codec 'hevc' is not supported by the bitstream filter 'h264_metadata'". The colour fix now matches the codec
  (HEVC uses hevc_metadata, AV1 and others none), and if it still fails the clip is made without it — which also
  fixes preview generation on FFmpeg 6 and older, where that option isn't allowed on inputs at all.

### Changed
- Removing a video from the library also removes it from every playlist; keeping one copy on the Duplicates page
  puts that copy in place of the removed one in playlists. Deleting a user deletes their playlists.

## [2.1.0] - 2026-11-10

### Added
- **hmvhaven.com** support — same platform as porntubeai.com: add, download, sync and connect it like the others.
  PMVHaven, PornTubeAI and HMVHaven are now built in, so existing `config.yaml` files need no change.
- **Separate logins per site:** Credentials → PornTubeAI account / HMVHaven account, each with "Use the PMVHaven
  account" (on by default) or its own email and password.
- **Sync with PMVHaven** also checks porntubeai.com and hmvhaven.com videos against their own site with the same API
  key. A site whose API can't be reached is skipped (its videos are never marked removed) and named in the report.
- **Re-encode to AV1** (SVT-AV1) as well as HEVC: pick the codec in Server settings → Re-encode, each with its own
  quality (CRF) and speed preset. A video already in the chosen codec is offered the other one, so **AV1 → HEVC**
  works for devices that can't play AV1. The tab warns when FFmpeg lacks the encoder.
- **CPU limit for re-encodes** as a number of cores (X / Y) or a percentage; the encoder is pinned to those cores.
- **Duplicate scan triggers** per event: after a download, after a re-encode, after a repair (Server settings →
  Duplicate scan). Several events in a row start one scan, a minute after the last one.
- **System jobs** on the Scheduler page — Weekly backup, Check for updates (now daily) and **Daily duplicate scan** —
  always present, editable and disable-able, not deletable.
- New **logo** (sidebar, sign-in page, browser tab).
- Userscript **v2.17**: also runs on hmvhaven.com.

### Changed
- The duplicate scan's single "Automatic" switch is replaced by the triggers above plus the daily system job
  (an "Automatic: off" setting carries over: triggers off, daily job disabled). The Duplicates page links to it.
- "Open on …" / "View on …" name the video's own site; "💀 Gone from the site" tooltips.

### Fixed
- **Re-encode settings saved in the app were ignored** — re-encodes always used `config.yaml`. They now apply.
- Changes to download and other settings needed a restart to reach the running workers; they now apply at once.
- **Removing a video that hadn't finished downloading left its thumbnail, preview and metadata files behind.**
  They're deleted now. (Maintenance → Untracked files finds any left by earlier versions.)

## [2.0.0] - 2026-11-10

The new interface is now **the** interface: it opens at `/`, and the classic interface is gone.

### Added
- **Server settings** (Admin): tabs for **Download** (download and cache folders with a folder browser and
  "move existing files", parallel downloads, retries, chunk size, funscripts, metadata JSON), **Re-encode**,
  **Duplicate scan** (method, similarity, frames, duration tolerance, audio check, parallel reads — previously
  only in `config.yaml`), **Credentials** and **Connections**.
- **Users**, **Scheduler** (jobs with run history and detail logs), **Backups**, **Stats** (with 30-day trends)
  and **Tools** pages.
- **Maintenance** now also has *Sync from metadata files*, *Sync with PMVHaven* (preview, apply, report with links
  to each video) and *Repair / update everything*.
- **Settings → Account → Delete my account** for regular users.
- **Tasks:** waiting downloads can be moved — ⤒ download next, ↑ / ↓, ⤓ download last — so something else can go
  next while a long download runs (`POST /api/library/{id}/download/move`, `download_queue_order` in the summary).
- The sidebar's Admin section lists every admin page and can be folded away (remembered per browser).
- Admin-only pages show a notice instead of an empty page for other users.

### Changed
- The interface is served at `/`. Old addresses keep working: `/next/…` redirects to the same page, and classic
  addresses (`/admin`, `/admin/download`, `/admin/reencode`, `/admin/connections`, `/admin/credentials`,
  `/admin/tasks`, `/admin/issues`, `/admin/library`, `/users`) open the matching new page.
- Built files moved from `frontend/next/` to `frontend/` (`index.html` + `assets/`).
- Help texts and docs point to *Admin → Server settings → Credentials / Connections*.

### Fixed
- After a re-encode (and in the duplicate scan's fallback frame reader) the video fingerprint failed with
  "Unrecognized option 'vsync'" on FFmpeg 7 and newer, which removed that option. The app now uses
  `-fps_mode` on FFmpeg 5.1+ and `-vsync` only on older versions.

### Removed
- The classic interface (`frontend/index.html`, `app.js`, `styles.css`, `mobile.css`, `js/…`) and its
  "Classic interface" / "Classic pages" links.

## [1.13.0] - 2026-11-10

### Added
- New interface: **Tasks** page — running now, waiting (with queue position; move queued re-encodes up or
  down) and failed downloads (Retry), with live progress, speed and ETA; cancel a re-encode or repair, or
  remove a download. The sidebar shows the number of tasks.
- New interface: **Help** — the Guide (with a contents list), the Changelog, and About (version, update check,
  prefilled bug report, server details, license, credits). Links like `/next/help/changelog` open a tab directly.

### Changed
- The new interface's sidebar: Tasks sits under Discover, Help above Settings; only the remaining classic
  Admin pages are still listed under "Classic pages" (admins only).

## [1.12.0] - 2026-11-10

### Added
- **Orientation filter** (Filters → Quality): horizontal, vertical or square videos.
- **Autocomplete in Edit details**: typing suggests performers, tags, music and content flags that already
  exist in the library ("skyla" → "Skylar Vox", "Skylar Snow"), names starting with what you typed first,
  the most used first (`GET /api/library/suggest`).
- **Compare** (Duplicates page) has a volume slider; it starts at 5% and remembers your setting.
- `GET /api/external/index`: every downloaded video's id and every removed duplicate, for the userscript.
- The **userscript** is now part of the project (`userscript/pmv-nexus.user.js`, v2.16):
  it keeps a local copy of the downloaded list (synced daily and on demand from its menu), so listing
  badges appear instantly without asking the app for every card, and it shows removed duplicates as
  "⧉ Duplicate" with the title of the copy you kept.
- `/api/external/exists` also returns a `label` for downloaded videos ("Already downloaded").

### Fixed
- Upload dates were missing for videos whose metadata file is in PMVHaven's own format (`uploadDate`), and
  for videos whose uploader's account was deleted (file named "- 2024-09-05 - Title …"). They're filled in
  at the next start.

## [1.11.0] - 2026-11-09

### Added
- **Quality filter** (Filters → Quality): pick exact qualities (e.g. only 1080p, or 1080p and 2160p) and/or
  "1080p or better". Judged on the short side, so vertical videos count like horizontal ones.
- **Admin → Maintenance → Untracked files** (new interface): lists files in the download and cache folders
  that no video points to (e.g. a duplicate's file that was in use when it was removed), with kind, size and
  date; select and delete them.
- **Duplicates page**: "View on PMVHaven" for each copy, the **Uploaded** date next to Downloaded, an
  "Identical files" marker, and what the next scan will read (new/changed videos) plus how the last one went.
- **Logs** reworked as a table (time, level, event, user, address, what happened), filters for category, event,
  user, level and text, the full JSON entry on click, and **Export** to JSON Lines. Every change made in the
  app is recorded as an event like `library.rename_item` or `auth.login_failed`.
- **Client addresses in the logs** follow `logging.ip_mode` in config.yaml: `hashed` (default — a stable
  pseudonym like `ip-3f9a1c0b7d2e`), `masked`, `full` or `off`. Click an address to see everything from it.
- **Password policy** that's easy to type with a TV remote: 8–64 characters with a letter and a number, not
  your username, not an easy-to-guess password, not your current one. Capitals and symbols are allowed but
  not required. A live checklist shows it in Settings → Account and on the first-run setup form.
- Videos now store their PMVHaven upload date; existing ones are filled in from their metadata files at
  startup, and syncs keep it current.

### Changed
- A copy removed with "Keep this one" stays linked to the kept video, so it isn't downloaded again and the
  userscript endpoint (`/api/external/exists`) now also returns `label` ("Duplicate of “…”") and
  `duplicate_of` for it. Removing the kept video removes those links too; merging the kept video into
  another moves its links along.
- Duplicates page: "Best quality" / "Smallest file" only mark a copy that clearly stands out — identical
  copies get no marks. Compare and Not duplicates buttons are coloured so they stand out.

### Fixed
- Removing or renaming a video could fail on Windows with "[WinError 32] … being used by another process"
  when the app itself still had the file open for a player that stopped reading. Open streams of a file
  are now closed before it is deleted, renamed, moved or replaced by a re-encode. If a file still can't be
  deleted, the Duplicates page says so and Maintenance → Untracked files can remove it later.
- Sign-in entries in the logs (1.9.0/1.10.0) stored the raw IP address; existing entries are scrubbed and
  new ones follow `logging.ip_mode`.

### Removed
- `frontend/js/pages/admin.js` and `frontend/js/pages/users.js` (classic interface; unused since the admin
  pages moved to `frontend/js/pages/admin/`).

## [1.10.0] - 2026-11-09

### Added
- **Admin → Duplicates** in the new interface: each group of matched videos side by side with quality,
  codec, size, bitrate, duration, rating, download date and PMVHaven status, marked "Best quality",
  "Smallest file" or "Shorter". Keep one copy (the others are removed), remove a single one, mark the group
  "Not duplicates", or **Compare** them playing side by side with shared play/pause/seek. Scan status, Scan now,
  automatic scanning and the minimum match are on the page.
- **Edit details** (video page and ⋮ menu): add or remove performers, tags, music and content flags by hand.
  Edits are kept when the library is synced, updated or repaired; "Use the site's" drops an edit again. The
  sync report no longer lists edited fields as changes.
- **Change thumbnail → Pick a frame from the video**: scrub or step frame by frame through the downloaded
  video and use any frame, for when the site's timeline images are broken or black.
- Library filter **On PMVHaven**: any / still on PMVHaven / 💀 gone.
- Colour for uploader names (Settings → Appearance), used with the performer colour in related videos,
  cards and the list view.

### Changed
- Page sizes are now 30, 60 (default), 90 and 120, so pages end on full rows with 5 or 6 videos per row.
  A previously saved size moves to the nearest new one.
- Related-video badges: rating and 💀 sit in the top corners and no longer count toward the 3 that fit in
  the bottom row.
- Vertical videos' thumbnails and previews are shown whole, with black bars at the sides.

### Fixed
- Hover previews that are 10-bit H.264 ("High 10", which browsers can't decode) stayed blank. They are now
  converted to a browser-friendly format the first time they're shown.
- Fullscreen in the new player only used part of the screen.
- A settings change made right before closing or reloading the tab could be lost.

## [1.9.0] - 2026-11-09

### Added
- **Admin → Logs** (new interface, sidebar → Logs): everything the app and its background jobs log, plus every
  change made by a user (who, what, on which video, result, duration) and every sign-in / failed sign-in.
  Search, filter by type, level, user and source, expand an entry for details or the error traceback, and
  "Live" adds new entries as they happen. Kept in `data/logs/app_logs.db` for 90 days / 500,000 entries
  (`logging.app_log_retention_days` / `app_log_max_rows`). Needs the `logs.view` permission (admins have it).
- Settings are now split into tabs: Appearance, Library, Video page, Account.
- **Custom theme**: pick background, panel, text and accent colours on a dark or light base.
- **Coloured chips** for performers, tags and music (video page and list view), each colour configurable.
- Number of related videos (0–40, 0 hides the column) and which badges they show (up to 3).
- `config.example.yaml`: the committed settings template. The first start copies it to `config.yaml`.

### Changed
- `config.yaml` is no longer part of the repository (it holds paths on your own machine) and is in `.gitignore`.
  Existing installs keep their `config.yaml` unchanged.
- Open tabs of the new interface now share one live-update connection instead of one each.
- Text-size slider applies the new size when you let go, instead of while dragging.

### Fixed
- Hover previews could stop appearing after hovering several videos: a preview is now fully unloaded when
  you move away, freeing its connection. A preview that is missing or can't be played now says so.
- Stopping the app with Ctrl+C ended with a `KeyboardInterrupt` traceback.

### Removed
- `.env_Example` (credentials have been managed in the app since 1.2.0).

## [1.8.0] - 2026-11-09

### Added
- New interface (`/next`):
  - **Settings page** (sidebar → Settings), saved to your account: theme, text size (80–150%), 💀 badge colour,
    default sort, which thumbnail badges to show, table hover-preview size, Back-button behaviour, and
    changing your password.
  - **Video actions**: rename, change thumbnail, repair/update, re-encode (and cancel), share, open on
    PMVHaven and remove. Buttons on the video page and in the list view, a ⋮ menu in the grid and table views.
  - **Thumbnail badges** like the classic interface: codec, file size, orientation, rating, 💀 and work in
    progress (re-encode/repair/share). Codec now sits in the bottom row next to quality and duration, which
    are always shown.
  - **Video page layout**: details under the player, **related videos** on the right (matched by shared
    performers, creator and tags) and **more from the same uploader** below the details.
  - **Loop button** inside the player bar (remembered), as in the classic player.
  - Codecs filter is now a searchable list like performers/tags/creators.
  - The table view shows a large preview (200% by default) while hovering a row.
- `GET /api/library/{id}/related` for the related-videos list.

### Changed
- New interface: **Back** on a video page returns to the library (with filters, page and scroll position)
  even after opening several videos with Discover; "previous page" behaviour is available in Settings.
- New interface: the default sort is date downloaded, newest first (changeable in Settings).
- New interface: text is 10% larger by default, and the whole interface scales with the text-size setting.

### Fixed
- New interface: the video page showed only the site's top 5 tags instead of all of them.
- New interface: the view and page-size choices were not actually saved to the account.

## [1.7.0] - 2026-11-09

### Added
- **New interface (preview) at `/next`**, built with Vue 3, running alongside the classic interface until it
  replaces it. This first part covers:
  - Left sidebar (collapsible; a drawer on phones) with Library, Discover, Add videos, theme, sign-out and links
    to the classic Tasks/Admin/Help pages.
  - Sign-in and first-run setup.
  - Library with grid, list and table views, search (`/` to focus), filters (performers, tags, creators, codec,
    duration range, minimum rating, match all, 💀 only), sorting and paging. The whole view is kept in the URL,
    so back/forward, refresh and bookmarks restore it.
  - Video page with the player (loop switch, speed, download), details, and clickable performers/tags/creator
    that open the library filtered by them.
  - Live updates: the library and video pages update by themselves when videos change or finish downloading.
  - Midnight, Slate and Daylight themes; view and page size are saved to your account.
- "✨ New UI" link in the classic header.
- Live update stream (`/api/events`) for the web interface, replacing polling there.
- Library API: filters for creators, duration range, minimum rating, gone-from-site and "match all"; sorting by
  views.

### Fixed
- Sorting by date downloaded or re-encode status silently sorted by date added across pages.
- Sorting by resolution put 720p above 1080p.
- The classic library showed the wrong total number of videos.
- Closing the app could hang while a browser tab of the new interface was open.

### Developer
- New `ui/` folder (Vue 3 + Vite source). `frontend/next/` holds the built files and is committed; Node.js is
  only needed to change the interface (`ui/README.md`).

## [1.6.0] - 2026-11-09

### Added
- **🎲 Discover** button in the header (every user): opens a random downloaded video. Videos are not repeated
  until you have been shown the whole library; then a new round starts that still skips the most recent 20%.
  History is kept per user.

### Changed
- Videos no longer on PMVHaven are marked with a 💀 badge (hover for the explanation) instead of "Gone".
- Between 1025 and 1599 px wide, Discover and Help are icon-only and the username is moved from the Settings
  button into its tooltip, so the header fits on one row from about 1350 px.

## [1.5.0] - 2026-11-09

### Added
- **Details before download**: queued, downloading and failed tasks have a **Details** button that opens the
  full video page (title, creator, description, rating, views, tags, performers, music) with the thumbnail in
  place of the player and a live status line (queued / downloading % and speed / failed with the reason).
- Admin Tasks rows show a thumbnail plus creator, rating and the first tags.

### Changed
- The "Removed from site" badge is now a short **Gone** badge (hover for the full explanation).

### Fixed
- Opening a video that is not on the library page currently loaded (from Issues, a link or another page)
  showed no player and no action buttons, and its rename/share/remove/thumbnail/repair actions did nothing.
  The video page now loads the video itself.

## [1.4.0] - 2026-11-09

### Added
- **Removed from site** marker on library cards/rows and a notice on the video page for videos the last sync
  could not find on PMVHaven; the video page also shows when the video was last synced.
- **Update notice**: a tab left open while the app is updated shows "updated to vX — Reload" (checked every
  minute).
- Sync report and log now break changes down by field (e.g. `ratings=812, tags (reordered)=530`) and say
  whether the site returned tag votes.

### Changed
- Tag lists whose entries are unchanged but whose ranking moved are reported as "reordered" (still applied).
- Descriptions that differ only in whitespace or line endings are no longer treated as changed.

## [1.3.0] - 2026-11-09

### Added
- **Sync with PMVHaven** (Admin → Library Maintenance, and the Scheduler job "Sync library with PMVHaven"):
  compares downloaded PMVHaven videos with the site through its API (100 videos per request) and updates
  tags, performers, music, rating, description and site stats (views, likes, dislikes, favorites).
  **Preview changes** shows what would change without saving anything.
- Videos the site no longer returns are flagged as removed. Videos added under an old-site id are matched
  by their legacy id.
- PMVHaven API client with request spacing and automatic retry on rate limits.

### Changed
- The metadata JSON next to each video is updated by the sync (local technical details, download URL and
  re-encode marker are kept), so "Sync database from files" keeps the new values.

### Fixed
- The update check looked for `VERSION` on a `main` branch; it now uses the repository's default branch.

### Notes
- Video files, file names and titles are never changed by the sync; title changes on the site are only
  reported.
- Tags are only updated when the site returns tag votes, so the ranked tag list is never cut down to the
  site's short tag list.

## [1.2.0] - 2026-11-09

### Added
- **Credentials** page (Admin Portal): manage the PMVHaven account, PMVHaven API key, userscript API key,
  Telegram, Gofile and Pixeldrain credentials in the app. Secrets are write-only in the UI; the userscript
  key can be generated and shown for copying.
- **Test key** button for the PMVHaven API key.
- **First-run setup**: on a fresh install the browser shows a "Create admin account" form protected by a
  one-time setup code printed in the console.

### Changed
- Credentials are stored encrypted (Fernet) in `data/secrets.db`, with the key in `data/secret.key`, instead of
  `.env`. On the first start of this version any values still in `.env` are imported once; `.env` is ignored
  afterwards.
- The session-signing key is generated automatically.
- Backups with "Include secrets" now contain the encrypted credential store and its key.
- `Tools/generate_telegram_session.py` reads the Telegram credentials from the store
  (`python -m Tools.generate_telegram_session`).

### Removed
- The startup error when no admin account exists; first-run setup replaces it. `INITIAL_ADMIN_USERNAME` /
  `INITIAL_ADMIN_PASSWORD` in an old `.env` are still honoured.

## [1.1.0] - 2026-11-09

### Added
- **Version label** next to the app name in the header. Click it to check GitHub for a newer version;
  a dot appears on it when an update is available.
- **Update checks** as a Scheduler job ("Check for app updates"). A weekly job is created automatically
  on first start and can be edited or removed on the Scheduler page.
- **Help menu** in the header with an in-app **Guide**, **Changelog** and **About** page, a link to the
  GitHub repository and a **Report an issue** link that opens a prefilled bug report.
- **Tools** page (Admin Portal): status of Python, FFmpeg/ffprobe, the MediaInfo library, Playwright's
  Firefox and every package in `requirements.txt`, with install hints.
- **Backups** page (Admin Portal): back up the database and settings, optionally the thumbnail/preview
  cache and secrets; download, delete and restore backups. A weekly "Backup database + settings"
  Scheduler job is created automatically. Old backups are pruned (`backup.keep`, default 10).
- Restores are staged in the app and applied by `run.py` on the next start, before the database opens.
  Overwritten files are kept as `*.pre-restore`.
- MIT `LICENSE`, this changelog and `docs/GUIDE.md`.

## [1.0.0] - 2026-11-09

### Added
- `run.py` and `start.bat` in the project root to start the app from anywhere (double-click or a shortcut).
- `VERSION` file.

### Changed
- **Duplicate detection rebuilt** on the VideoDuplicateFinder approach: frames sampled at fixed relative
  positions, 32x32 grayscale comparison (pHash and combined modes available), duration pre-filter.
- Black, white and flat frames are ignored; files with too few usable frames are marked "unusable"
  instead of being compared.
- Audio fingerprints veto visual matches whose soundtrack differs (same footage, different music).
- Duplicate groups only form when every pair in the group matched (no chaining through one similar video).
- New duplicate-scan settings: algorithm, percent, samples, pHash quorum, duration tolerance, audio, workers.

### Fixed
- Unrelated videos reported as 100% duplicates (flat frames hashed identically; scores were inflated by
  sliding alignment).
- `No module named 'backend'` when starting the app from inside the `backend` folder.

### Removed
- The previous sliding-window pHash duplicate data. It is discarded automatically on the first scan.

## [0.9.0] - 2026-11-09

Initial import of the existing application.

### Added
- Add videos by URL from pmvhaven.com and porntubeai.com; metadata, thumbnail, preview, funscript and
  metadata JSON downloaded alongside the video.
- Library with List/Grid/Table views, filters, field-prefixed search, sorting and pagination; Tasks view.
- Video page with Plyr player, HTTP range streaming and hover previews.
- HEVC re-encode queue (reorder, cancel, CRF/preset, downscale, CPU limit).
- Repair, sync-from-disk, rename, thumbnail picker.
- Share to Telegram, Gofile and Pixeldrain.
- Multi-user accounts with roles, per-user settings and an Admin Portal (Users, Tasks, Download,
  Re-encode, Connections, Library Maintenance, Issues, Scheduler, Stats).
- Scheduler for maintenance jobs; Stats with snapshots and trends.
- External API for the companion userscript.
