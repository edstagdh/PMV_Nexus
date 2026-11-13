# PMV Nexus

<img src="ui/src/assets/logo.svg" alt="" width="72">

A self-hosted web app for building and managing your own library of PMVs (porn music videos): add a video by its
link, and PMV Nexus fetches its details through the site's official API, downloads it (where the site offers a
download), and keeps it in a searchable, filterable library with a built-in player, playlists, performers, songs,
duplicate detection, optional HEVC / AV1 re-encoding and backups. It runs on your own computer or home server.

Works with **PMVHaven** and its sister sites porntubeai.com and hmvhaven.com.

> **Independent, unofficial project.** PMV Nexus is not affiliated with, endorsed by or connected to PMVHaven or
> any other site it works with. **Adults only (18+).** You are responsible for following each site's terms
> (download only what a site offers for download, for your own personal use) and the law where you live; don't use
> it to redistribute content you have no rights to. It uses each site's official API, doesn't bypass any access
> control, and this project hosts no media. Read the full [Terms of use and disclaimer](DISCLAIMER.md) — installing
> or using PMV Nexus means you accept them.

---

## Contents

- [Features](#features)
- [Tech stack](#tech-stack)
- [Project layout](#project-layout)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the app](#running-the-app)
- [Using the app](#using-the-app)
- [Backups & restore](#backups--restore)
- [Data & storage layout](#data--storage-layout)
- [External API (userscript integration)](#external-api-userscript-integration)
- [Notes, limitations & fragility](#notes-limitations--fragility)
- [Troubleshooting](#troubleshooting)
- [Terms of use and disclaimer](DISCLAIMER.md)

---

## Features

### Adding & downloading
- Add a video by pasting its PMVHaven page URL — the video id is parsed straight out of it.
- Video details read from the site's API on add: title, creator (name/url/subscriber count), upload date, duration, views, rating %, description, tags, music credits, performer/model list, content-flag chips, and the site's own video details.
- Thumbnail, hover-preview clip, and funscript (if present) are downloaded alongside the video; all are optional/toggleable.
- Downloads use the site's API only: the video's details from `/api/videos/<id>`, the original-quality file from the site's own download endpoint (only videos the site offers for download). The app never reads the sites' web pages and never signs in to them.
- A per-video metadata JSON file can be saved alongside the download, enriched with real technical details (codec, resolution, bitrate, duration) read from the file itself via MediaInfo rather than trusting whatever the page displayed.
- Separate worker pools for metadata fetching vs. the actual download, each with its own concurrency limit, so a title/thumbnail shows up immediately instead of waiting behind whatever's currently downloading.
- Failed items retry automatically up to a configurable limit, then land in a `failed` state you can manually retry.

### Library management
- **Rename** — renames the video file and every sidecar (funscript, metadata JSON, thumbnail, preview, pre-reencode backup) to match, with filesystem-safe name sanitization.
- **Remove** — removes the library entry, optionally deleting the files from disk too; cleans up now-empty creator folders afterward. Deletion retries briefly on Windows to ride out a file lock left by the in-browser video player.
- **Change download folder** — optionally moves every already-downloaded file into the new folder in one operation; blocked while a re-encode is in progress.
- **Sync database from files** — re-reads every completed video's metadata JSON on disk and backfills tags/performers/technical details for items that predate those fields (or were downloaded with metadata saving off).
- **Repair broken thumbnails/previews/metadata** — sweeps every completed item, re-fetching anything missing or 0 bytes from PMVHaven and regenerating technical details locally from the file. Never touches the video file itself; a missing/corrupt video is reported as an error instead of silently re-downloaded. Runs in the background with live progress and is cancellable.

### Re-encoding (HEVC / AV1)
- Re-encodes any completed download to HEVC (`libx265`) or AV1 (`libsvtav1`) via ffmpeg, with:
  - The target codec chosen in Server settings, each codec with its own CRF and preset
  - Optional downscale to 1080p (vertical videos are scaled by height, not squashed)
  - Optional CPU limit: a number of cores or a percentage of them (the encoder is pinned to those cores)
  - Optional chapter removal
  - Audio copied as-is when it's already AAC, otherwise transcoded to AAC 128kbps stereo
  - Subtitle streams preserved as `mov_text`; all metadata stripped so stale tags don't survive the re-encode
- Refuses to re-encode a file into the codec it already has; a file already in the default codec is offered the other one (e.g. AV1 → HEVC for devices that can't play AV1).
- One FIFO queue, one encode running at a time, with move-up/move-down reordering for anything still waiting (the item currently encoding can't be reordered, since it's already running).
- Live progress %, ETA, and encode speed while a file is encoding.
- Cancel a queued or in-progress re-encode at any time (terminates the ffmpeg process cleanly).
- The pre-encode metadata JSON is preserved as a `_original.json` backup ("Original details" stays viewable from the UI); optionally the original video file itself is kept too, renamed with an `_old` suffix.
- Queue state survives a server restart — anything that was mid-encode when the server stopped is safely restarted from scratch.

### Extensions
Optional add-ons, each a Python package in an `extensions/` folder next to `run.py`. The folder isn't part of
the app and isn't created by it: with no extensions, nothing about them shows anywhere. An extension can add
actions to a video's menu and to the picked-videos bar, settings panels (Server settings → Extensions),
credentials, permissions, sources and API routes; it draws no interface of its own — the app shows the forms it
describes. See [Extensions](#extensions-1) below and the comments at the top of `backend/extensions.py`.

### Web UI
- **Library** tab (paginated, filterable, sortable — everything that's finished downloading) and a **Tasks** tab (unpaginated, everything still in progress: fetching, pending, downloading, or done-but-still-encoding/uploading).
- Three view modes for the Library — **List**, **Grid**, **Table** — with card/row DOM nodes reused in place across polls instead of being torn down, so nothing flickers and hover-preview playback is never interrupted.
- Filters modal: tags, performers, codec, a duration range slider, plus free-text search across title/creator/url/tags/performers.
- Sortable by title, creator, re-encode status, download date, duration, resolution, codec, size, or tags — from a toolbar control or by clicking table column headers.
- A dedicated video page (not a modal) with a Video.js 10 player supporting HTTP range-request streaming, a loop toggle, a download button, remembered volume, and a 720p button that converts the video while you watch (HLS via FFmpeg, also used automatically when the browser can't play a file); it moves into a floating mini-player while you browse.
- Hover-preview clips on thumbnails in List/Grid views.
- Three themes (Midnight, Slate, Daylight), remembered per browser.
- The whole library polls every 2 seconds; requests are sequence-guarded so a slower, older response can never overwrite fresher state.

### Settings (all live-editable from the UI, no restart needed)
- **Download** — folder (with an in-app folder browser), whether to move existing files when the folder changes, max concurrent downloads, max retries, chunk size, funscript toggle, metadata-JSON toggle.
- **Re-encode** — codec (HEVC / AV1), per-codec CRF and preset, copy-audio, downscale, CPU limit (cores or %), chapter removal, keep-original-file.
- **Interface** — theme, items per page.

### Reliability & design notes
- Single-process architecture: one background worker set, one SQLite database (WAL mode + busy-timeout tuned for a bursty single-process workload) — there's no need for anything heavier.
- Every write is a per-row upsert, not a full-table rewrite, so frequent progress ticks (download %, re-encode %, upload %) stay cheap even with a large library.
- Downloads, re-encodes, and queue state are rebuilt from the database on every restart rather than assumed to still be in memory — nothing gets silently orphaned by a crash or restart.
- The Tasks tab always sees *every* active item regardless of library size (it hits a dedicated uncapped endpoint rather than the paginated one).
- `/api/health` reports SQLite reachability, whether `ffmpeg` is on `PATH`, and free disk space on the output folder.

---

## Tech stack

| Layer | Tech |
|---|---|
| Backend | Python, FastAPI, Uvicorn |
| Persistence | SQLite (via a thin custom wrapper — see `backend/db.py`) |
| Site access | httpx (the sites' API and downloads) |
| Media | ffmpeg (re-encoding, HLS remuxing), PyMediaInfo, OpenCV (duration/fps probing) |
| Logging | loguru, with stdlib logging (uvicorn/fastapi/httpx) redirected into it |
| Song recognition | shazamio (Shazam); optional Google Gemini API (AI) |
| Images | Pillow (performer photos stored as WebP) |
| Frontend | Vue 3 + Vite, hand-written CSS, Video.js 10 for video playback, hls.js for converted (720p) streams |
| Auth | Signed session cookies (itsdangerous) for the web UI; a static API key for external tools |
| Encryption | cryptography (Fernet) for stored credentials, pyzipper (AES-256 zips) for password-protected backups |

---

## Project layout

```
project-root/
├── backend/
│   ├── __init__.py        # entry point — run this to start the server (see "Running the app")
│   ├── main.py             # FastAPI app: all routes, lifespan/startup hook
│   ├── config.py           # settings loader — merges config.yaml + data/settings_overrides.json + encrypted credentials
│   ├── db.py                # SQLite persistence layer for the library
│   ├── library_manager.py  # core orchestration: download/metadata/reencode workers
│   ├── logging_setup.py    # loguru configuration
│   ├── media_tools.py      # ffmpeg re-encoding + HLS download + MediaInfo probing
│   ├── models.py            # Pydantic request/response/domain models
│   ├── pmv_client.py        # site client: API requests + downloads, with your API key
│   ├── pmv_details.py       # site API JSON -> video details + download link (video_meta.py: the details' shape)
│   ├── security.py          # session-cookie auth + API-key auth
│   ├── sources/             # where videos come from (pmv_platform.py: PMVHaven and its sister sites)
│   └── extensions.py        # loads the optional add-ons in extensions/ (see "Extensions")
├── ui/                        # interface source (Vue 3 + Vite) — see ui/README.md
│   └── src/                   # views/ (pages), components/, stores/, lib/, styles/
├── frontend/                  # the built interface (generated from ui/ by `npm run build`, committed)
│   ├── index.html
│   └── assets/                # app.js, index.css, logo.svg, player-lang/ (player translations)
├── run.py                    # start the app (python run.py [--port N] [--restore backup.zip])
├── start.bat / install.bat   # Windows: start the app / console install (optionally from a backup)
├── install.sh                # Linux / macOS install (optionally from a backup, optional systemd user service)
├── installer/windows/        # Setup.exe (Inno Setup script, Windows service definition, icon)
├── .github/workflows/        # release.yml: builds Setup.exe and the GitHub Release for each vX.Y.Z tag
├── Tools/                    # helper scripts (first_install for install.bat, install_extensions, ...)
├── config.example.yaml       # settings template (committed); copied to config.yaml on first start
├── config.yaml               # your settings — created automatically, not committed
├── requirements.txt
├── extensions/               # optional add-ons -- not part of the app, you create it (see "Extensions")
└── data/                      # created automatically at first run
    ├── library.db              # SQLite database
    ├── backups/                 # backup zips (Management → Backups)
    ├── settings_overrides.json  # settings changed from the UI, layered on top of config.yaml
    ├── logs/                    # rotated backend.log + app_logs.db (Admin → Logs)
    ├── pmv_session/             # extensions' saved sign-ins (e.g. Telegram)
    └── cache/                   # thumbnails / previews / metadata JSON (path is configurable)
```

---

## Installation

### Prerequisites
- **Python 3.11–3.14** (3.10 reached its end of life in October 2026).
- **ffmpeg**, installed and available on your `PATH` — used for re-encoding and HLS remuxing.
- A **PMVHaven API key** (from pmvhaven.com/api-keys) is recommended, so the site knows the requests are yours.

### Windows: Setup.exe (recommended)
Download **`PMV-Nexus-Setup-<version>.exe`** from [Releases](https://github.com/edstagdh/PMV_Nexus/releases)
and run it. Windows may say it protected your PC — the installer isn't code-signed: **More info → Run anyway**.

- **Just me** (no admin) installs to `%LOCALAPPDATA%\Programs\PMV Nexus`; **all users** (admin) to Program Files
  and also offers the Windows service.
- **How the app runs:** only when you start it (Start-menu shortcut), also when you sign in to Windows, or as a
  **Windows service** (always on, before anyone signs in; Local System can't see mapped drives — use `\\server\share`
  paths or set an account under services.msc → Log On). Optional desktop shortcut. A firewall rule for the port is
  added for private networks (all-users installs; otherwise Windows asks the first time the app starts).
- **Python:** a Python 3.14, 3.13, 3.12 or 3.11 already on the PC is used (newest first; found through the entries python.org's
  installers make — conda / pyenv ones aren't seen). An app folder whose `.venv` was made with another version gets a new
  one on the next update. If there's none, Setup says so, downloads Python 3.12
  from python.org (checked against the file the release was built with) and installs it — its own progress window shows.
- It then creates `.venv`, installs the packages and, if ticked and
  not already on PATH, **FFmpeg** (in `runtime\`). Details: `data\logs\install.log`.
- **First install:** a new library or **from a backup** (the backup's password if it has one), then the **videos and
  cache folders** (from a backup: where you copied the videos), other folders only if one from the backup can't be
  reached here, and the **port**. Done with Tools/setup_core.py — the same checks as `run.py --restore`.
- **Updating:** run the newer Setup over it — it finds the install by itself (same folder, same options), so choose
  the same "only for me" / "all users" mode as the first time. `config.yaml`, `data`, `extensions` and the service
  setting are kept and the set-up pages skipped. If the app is running (tray, console window or service), Setup says
  so and closes it — work in progress stops safely and continues later — and starts it again afterwards. The app's
  code folders (`backend`, `frontend`, `Tools`, `userscript`, `docs`) are replaced as a whole, so nothing old stays
  behind. Going back to an older version isn't supported (restore a backup instead).
- **Uninstall** (Settings → Apps): removes the app; asks before deleting the library and settings (`data`,
  `config.yaml`) — videos outside the app folder are never touched.
- **New library:** Setup also creates your **admin account** (username + password) — no setup code needed.
- The Start-menu shortcut starts the app if it isn't running — with a **tray icon**, no console window — and opens
  it in the browser. Setup's **Opening the app** page sets which browser (e.g. a Firefox or Chrome profile) and
  whether starting also opens it (later: Server settings → Opening the app). "PMV Nexus (console)" starts it with its log window instead, for troubleshooting.

### Linux / macOS: `install.sh`
Get the code (download the release's source zip, or `git clone`), then run **`./install.sh`**: Python check (with the
command to install it if missing), `.venv` and packages, an ffmpeg check, then a new library
(folders + port) or one from a backup zip, and — on Linux with systemd — optionally a **user service**
(`systemctl --user status pmv-nexus`) that keeps it running and starts it with the computer.

### Windows without Setup: `install.bat`
Get the code (`git clone`, or download the zip and extract it), then double-click **`install.bat`**. It checks for
Python, creates `.venv`, installs the Python packages, checks for ffmpeg, and asks:
- **a backup zip** (drag the file into the window) — to set up from a backup, see
  [Setting up from a backup](#setting-up-from-a-backup-new-computer-or-reinstall); or, for a new setup,
- **the folders** — where the videos, thumbnails & previews, sign-ins, database, backups and logs go (Enter keeps
  the defaults inside the app folder; type a number to change one) — and **a port** (Enter = 8676), written to `config.yaml`.

It can be run again any time (e.g. after an update) to install missing packages. The manual steps below do the same
on any OS.

### 1. Get the code and set up a virtual environment
```bash
git clone <your-repo-url> pmv-nexus
cd pmv-nexus
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

### 2. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 3. Confirm ffmpeg is on your PATH
```bash
ffmpeg -version
```

### 4. Configure the app
The first start creates `config.yaml` from `config.example.yaml`; edit it to set where videos and the cache go
(see [Configuration](#configuration)), and `server.port` to use another port than 8676. `config.yaml` is your own file and is not committed to git. Credentials are
entered in the app after the first start — no `.env` file is needed.

### 5. (Optional) Extensions
If you have extensions, put each one's folder in `extensions/` (next to `run.py`), install their packages and
restart the app:
```bash
python -m Tools.install_extensions
```

---

## Configuration

Non-secret settings live in **`config.yaml`** (created from `config.example.yaml` on first start, not committed); credentials are managed in the app under **Admin → Server settings → Credentials** (stored encrypted). Anything changed under Admin → Server settings is stored separately in `data/settings_overrides.json` and layered on top of `config.yaml` at runtime — you don't need to hand-edit the YAML for day-to-day tweaks.

**Key names in `config.yaml` must match the field names on the corresponding Pydantic model in `backend/config.py` exactly.** Unrecognized keys are silently dropped (Pydantic ignores unknown fields on a plain `BaseModel` rather than erroring), and any field `config.yaml` doesn't set falls back to that model's own default — with no warning that your intended value never took effect. If a setting doesn't seem to be applying, check it against the table below and against `config.py` directly.

### `config.yaml`

| Section | Key | Default | Notes |
|---|---|---|---|
| `server` | `host`, `port` | `0.0.0.0`, `8676` | Where the app listens (read when it starts — restart after changing). `host: "127.0.0.1"` = this computer only. `run.py --host / --port` override them for one run. |
| `sites` | `<key>`: `base_url`, `label` | built in | PMVHaven, PornTubeAI and HMVHaven are built in; add a block only to change one or add another site on the same platform. |
| `download` | `output_dir` | `./downloads` | Where finished videos land, as `<output_dir>/<creator>/<file>`. |
| | `cache_dir` | `./data/cache` | Thumbnails, preview clips, and metadata JSON — kept separate from `output_dir` so the UI stays fast even if your video storage is slow/networked. |
| | `session_dir` | `./data/pmv_session` | Extensions' saved sign-ins. |
| | `library_file` | `./data/library.json` | Legacy flat-file store, only used for a one-time auto-migration into SQLite. |
| | `library_db_file` | `./data/library.db` | The active SQLite database. |
| | `max_concurrent_downloads` | `1` | Parallel video downloads. |
| | `max_concurrent_metadata_fetches` | `3` | Parallel video-details lookups (independent of downloads). |
| | `max_retries` | `3` | Attempts before an item is marked `failed`. |
| | `chunk_size_kb` | `512` | Download chunk size. |
| | `download_funscripts` | `true` | Also fetch the `.funscript` file when present. |
| | `save_metadata_json` | `true` | Write a `<name>.json` metadata file next to every video. |
| `auth` | `session_ttl_hours` | `720` (30 days) | How long the web UI's login cookie stays valid. |
| `logging` | `log_dir`, `rotation`, `retention`, `level` | `./data/logs`, `1 month`, `12 months`, `INFO` | loguru configuration. `rotation`/`retention` must be strings loguru understands (e.g. `"1 month"`, `"500 MB"`) — a plain word like `"monthly"` won't parse. |
| `reencode` | `codec`, `target_crf`, `preset`, `av1_crf`, `av1_preset`, `copy_audio`, `downscale`, `cpu_limit_mode`, `cpu_limit_cores`, `cpu_limit_percent`, `remove_chapters`, `keep_original_file` | `hevc`, `24`, `medium`, `32`, `8`, `true`, `true`, `percent`, `4`, `70`, `false`, `false` | Editable under Admin → Server settings → Re-encode (the old `limit_cpu_usage` is read as 70% / off). |

Windows paths like `D:/PH_DATA` work fine — forward slashes are fine cross-platform since everything is read through `pathlib`.

### Credentials (Admin → Server settings → Credentials)

All passwords, API keys and account settings are managed in the app and stored encrypted
(`data/secrets.db`, key in `data/secret.key` — keep the `data` folder private):

| Group | Fields | Purpose |
|---|---|---|
| PMVHaven API | API key | Key from https://pmvhaven.com/api-keys (`pmvh_...`); "Test key" checks it. The same key is used for all three sites. |
| External API | API key | `X-API-Key` for the userscript; Generate / Show / Clear. Empty = external API disabled. |

Extensions can add their own groups here. The session-signing key is generated automatically.

**Upgrading from `.env`:** on the first start of v1.2.0, any values still in `.env` are imported once into the
encrypted store; after that `.env` is ignored and can be deleted.

---

## Running the app

From the project root:

- **Windows:** double-click `start.bat` (uses `.venv\Scripts\python.exe` if present) — runs with its console window.
- **Windows, tray icon:** `.venv\Scripts\pythonw.exe run.py --tray` (what the installer's shortcuts do) — no console:
  - **click the icon** (or **Open PMV Nexus**) to open the app in the browser — the default one, or the program
    set in `config.yaml` → `tray.browser` (e.g. a Firefox profile: `"C:/Program Files/Mozilla Firefox/firefox.exe" -P
    Videos --new-window {url}`); `tray.open_browser: false` = a shortcut that starts the app doesn't open it (both
    also under Server settings → Opening the app; the browser only from the computer the app runs on);
  - **hover** to see what it's doing ("2 downloads, 1 re-encode, 1 playing");
  - **right-click**: Open, Copy the setup code (only before the first account exists), Open the log folder, Open the
    app folder, **Exit** (asks first when work is running, then stops cleanly — interrupted downloads and re-encodes
    continue at the next start).
  - If a folder can't be reached at start, a message box says which and offers the console window to choose it.
- **Any OS:** `python run.py`.

The port and address come from `config.yaml` (`server.port`, default **8676**; `server.host`, default `0.0.0.0` =
reachable from other devices on your network). For one run only: `python run.py --port 9000` (or
`start.bat --port 9000`). `--restore <backup.zip>` puts a backup back first — see
[Backups & restore](#backups--restore).

`run.py` changes to the project folder first, so it works from shortcuts, `.bat` files and other folders.
It also applies a restore staged on the Backups page before the app loads, and closes open browser tabs'
live-update connections when you stop it. Starting Uvicorn directly
(`python -m uvicorn backend.main:app --host 0.0.0.0 --port 8676`) still works, but ignores `server:` in
`config.yaml`, skips staged restores and may wait for open browser tabs when stopping.

### The interface

The interface's source is in `ui/` (Vue 3); the built files in `frontend/` are included, so nothing extra
is needed to run it. To change the interface itself, see `ui/README.md` (requires Node.js).

### First run
1. On a fresh install the console prints a **setup code**. Open `http://localhost:8676` (or the port you set), enter the code and create the admin account.
2. Enter your **PMVHaven API key** (and any other keys) in **Admin → Server settings → Credentials** (PMVHaven API, from pmvhaven.com/api-keys) so the site knows the requests are yours. The app uses the site's **API only** — video details and the site's own download endpoint; it never signs in to the site or reads its pages.
3. Click **+ Add videos**, paste a `pmvhaven.com/video/...` URL; it appears under **Tasks** while it downloads, then in the **Library**.

---

## Using the app

- **Library vs. Tasks** — Library only ever shows finished downloads; anything still fetching, pending, downloading, or done-but-still-encoding/uploading lives in **Tasks** instead.
- **Views** — admins can switch between Grid, List and Table from the toolbar; other users see the grid.
- **Filters & search** — the Filters panel has tags, performers, codec, quality, orientation, duration, rating and removed-from-site; the search box takes `title:`, `creator:`, `tag:`, `performer:`, `description:`, `music:` prefixes.
- **Per-item actions** — from the ⋮ menu or the video page: Edit details, Rename, Change thumbnail, Repair / update, Re-encode… (HEVC or AV1, with this time's settings), Add to playlist, Remove (plus any extensions' actions).
- **Playlists** — manual (ordered by hand) or smart (filled by a saved library filter), private or public to the app's other users; play in order or shuffled with next / previous, repeat and autoplay.
- **Mini-player** — leave a playing video's page and it keeps playing in a floating, draggable player while you browse; a tab reload brings it back paused at the same moment.
- **Upload** — add videos from your own computer with all their details; thumbnail, preview and technical details are made from the file.
- **Queues** — waiting downloads can be moved to the front/back or up/down on the Tasks page; waiting re-encodes up/down.
- **Settings** (sidebar) — your own interface preferences. App-wide settings (folders, downloads, re-encode, duplicate scan, credentials, opening the app) are under **Admin → Server settings**.
- **Maintenance** — **Admin → Maintenance** has sync from metadata files, sync with PMVHaven, repair everything and untracked files.

---

## Backups & restore

**Management → Backups** makes and restores backups; a weekly backup is a built-in job on the Scheduler page and
the newest 10 are kept (`backup.keep`). The user guide (Help → Guide, or `docs/GUIDE.md`, "Backups") has the full
details and step-by-step scenarios.

**What's in a backup** (one `.zip`, in `data/backups` by default):
- always: the library database (videos and their details, users, roles, playlists, watch history, stats),
  `config.yaml`, the settings changed in the app (`data/settings_overrides.json`), performer photos and playlist covers
  you added;
- optional: **secrets** — the encrypted credentials *with their key* and the saved site sign-ins;
  the thumbnail / preview **cache** (can be large);
- **never the video files.** Copy your download folder yourself (another drive, a NAS).

**Password (encryption).** Set a **backup password** on the Backups page and every new backup — manual or
scheduled — is an AES-256 encrypted zip. Without the password nobody can open it: not the app, not 7-Zip / WinRAR
(which open it *with* the password). Only a small `backup_info.json` (version, date, what's included) stays
readable, so the Backups page can list it. The password is kept in the app's encrypted credential store; it **can't
be recovered** — keep it in a password manager. Restoring or testing a protected backup asks for its password when
the saved one doesn't open it. Recommended for backups that include secrets.

**Restore test.** Each backup is checked right after it's made (and with **Test**): every file intact, the database
passes SQLite's integrity check, the settings can be read, and this app version can restore it.

### Restoring on the same install
**Restore** next to a backup, then restart the app (`start.bat` / `run.py`) — in a console window, since it shows
the backup's folders to check (as in step 2 below); without a console, a restore whose folders can't be reached
waits for one. Nothing changes until the restart;
**Cancel the restore** calls it off. Every file the restore replaces is kept as `<name>.pre-restore` (e.g.
`data/library.db.pre-restore`) — to undo, stop the app and rename them back. After a restore you sign in with the
accounts from the backup.

### Setting up from a backup (new computer or reinstall)
1. On the old install: **Create backup now** (tick **Include secrets** to keep credentials and extensions' sign-ins), then
   **Download** it, or copy it from `data/backups`. Copy the **download folder** with the videos too.
2. On the new computer, get the code, then either
   - **Windows:** double-click `install.bat` and drag the backup zip into its window when asked; or
   - **any OS:** install as above, then start once with the backup:
     ```bash
     python run.py --restore path/to/pmvnexus_backup_20261112_120000.zip
     ```
     (`start.bat --restore "D:\backups\pmvnexus_backup_….zip"` on Windows.)

   A password-protected backup asks for its password in the console. Then the console lists the **folders from
   the backup's settings** — videos, thumbnails & previews, sign-ins, database, backups, logs — and whether each
   is there on this computer. A folder that can't be reached (e.g. drive `D:` doesn't exist here) has to be given a
   new place; any other can be changed by typing its number (e.g. where you copied the videos); Enter keeps them.
   The restored files go to the new places, the new places are saved in `config.yaml`, and at the start the app
   finds the videos, thumbnails and previews in them (by `<creator>/<file>`, so the old paths don't have to match).
   The app then starts with the restored library, settings and accounts — on the port in the backup's
   `config.yaml`. No setup code or temporary account is needed.
3. Check **Management → Tools** (ffmpeg and packages), and **Server settings → Credentials** if the backup had no
   secrets.

### Backups from other versions
- **Older backups restore into newer versions.** Every backup since 1.2.0 can be restored as is: when the app
  starts after the restore it brings the database up to date (adds the tables and columns added since) and reads
  settings saved under old names. Tested with backups made by 1.2.0, 1.10.0, 2.0.0, 2.10.0 and 2.17.3.
- **A backup made by a newer version is refused** by an older app ("Update the app first, then restore it") — an
  older app can't know what the newer one changed. Update the code, then restore.
- Password-protected backups (2.25.0+) need the `pyzipper` package (in `requirements.txt`).

**Folders that can't be reached at a start** (e.g. the videos drive isn't plugged in): `run.py` stops before
loading the app and lists them; in a console window it asks for their new place (saved in `config.yaml`, and the
app looks for the files there), otherwise it says which setting to fix.

---

## Extensions

Extensions are optional add-ons kept outside the app's code. Each is a folder in `extensions/` (next to `run.py`)
with an `__init__.py` that has a `register(api)` function, and optionally a `requirements.txt`:

```
extensions/
└── my_extension/
    ├── __init__.py        # def register(api): ...
    └── requirements.txt   # its Python packages (optional)
```

- **Install:** copy (or `git clone`) the folders into `extensions/`, run `python -m Tools.install_extensions`
  with the app's Python (the Windows Setup does this itself when it finds the folder), restart the app.
- **Remove:** stop the app, delete the extension's folder, start it again. Its data on videos stays in the
  database, unused, and comes back if you reinstall it.
- **See them:** Server settings → **Extensions** (only there when at least one is installed): what's loaded or
  failed (the log says why), and their settings panels. Their actions appear in a video's menu and in the
  library's picked-videos bar; their credentials under Server settings → Credentials; their permissions under
  Users → roles.
- **Write one:** the API (`ExtensionAPI`) and the form format are described at the top of
  `backend/extensions.py`. A broken extension is skipped; the app runs without it.

## Data & storage layout

```
data/
├── library.db                 # SQLite database — the source of truth for the library
├── settings_overrides.json    # UI-changed settings, layered over config.yaml
├── logs/
│   └── backend.log            # rotated monthly, kept for 12 months, rotated files zipped
├── pmv_session/               # extensions' saved sign-ins (e.g. Telegram)
└── cache/                      # (path from config.download.cache_dir)
    └── <creator>/               # thumbnails, preview clips, metadata JSON
```

Downloaded videos themselves go to `<output_dir>/<creator>/<creator> - <upload date> - <title> [<id>].<ext>`, with matching sidecars (`.funscript`, `.json`) alongside. A re-encode backs up the pre-encode metadata as `<name>_original.json`, and — if "keep original file" is on — the pre-encode video as `<name>_old.<ext>`.


---

## External API (userscript integration)

These endpoints authenticate via a static `X-API-Key` header (generate the key in **Admin → Server settings → Credentials → External API** to enable them) instead of the cookie-based session, for tools like a browser userscript that can't hold a login session:

- `POST /api/external/library` — body `{"url": "..."}`, adds a video to the library.
- `GET /api/external/exists?url=...` — returns `{"exists": bool, "status": ..., "title": ..., "label": ...}` for dedupe checks before adding (an item that previously `failed` is reported as not existing, since it's re-addable). A duplicate removed on the Duplicates page has `status: "merged"` and `duplicate_of: {id, title, url}` (the copy that was kept).
- `GET /api/external/index` — `{"generated_at", "done": [ids], "merged": {id: {"of", "title"}}}`: every downloaded video's id (the 24-character id at the end of a PMVHaven video URL) and every removed duplicate, so a client can keep its own copy instead of asking per video.

The userscript itself is in [`userscript/pmv-nexus.user.js`](userscript/pmv-nexus.user.js) — install it in Tampermonkey/Violentmonkey; it asks for the app's URL and the API key on first use (change them later from its menu: **Change PMV Nexus Address / API Key**). The Windows installer puts the chosen port in its default address. It keeps a copy of the downloaded list (synced daily, or via its menu → **Sync Downloaded List Now**), marks downloaded videos and duplicates on every listing, and adds a download button on video pages.

---

## Notes, limitations & fragility

- The sites are used through their API only: if PMVHaven changes its API, `backend/pmv_details.py` (reading the details), `backend/pmv_api.py` (sync) and `backend/sources/pmv_platform.py` are the places to look.
- Video/re-encode progress is polled every 2 seconds; there's no push/websocket layer.
- This is designed around a single instance with one SQLite file — it isn't built for multiple concurrent installs sharing one database.

---

## Troubleshooting

- **A setting in `config.yaml` doesn't seem to apply** — the key name almost certainly doesn't match the corresponding Pydantic field in `backend/config.py`; unmatched keys are dropped silently. See the note in [Configuration](#configuration).
- **`ModuleNotFoundError: No module named 'cv2'`** — install OpenCV separately: `pip install opencv-python` (see the note in [Installation](#installation)).
- **Re-encode/HLS download fails immediately** — confirm `ffmpeg -version` works from the same environment the app runs in; `/api/health` also reports whether `ffmpeg` is on `PATH`.
- **A video won't download ("the site didn't give the file" / "the API didn't return this video")** — it's private, removed, or not offered for download on the site; check that your API key is saved (Credentials → PMVHaven API → Test key).
- **Can't delete a file on Windows** — the app already retries deletion a few times to ride out the video player holding a stream open; if it still fails, make sure the video isn't open in another program.

---

## Documentation, versions & support

- In-app **Help** menu: Guide (`docs/GUIDE.md`), Changelog (`CHANGELOG.md`) and About.
- The current version is in `VERSION`; click the version label in the header to check for updates.
- Report bugs at <https://github.com/edstagdh/PMV_Nexus/issues> (Help → Report an issue prefills one).

### Making a release (maintainers)
Releases are built by GitHub Actions (`.github/workflows/release.yml`) — nothing to build locally:
1. Update `VERSION` and the `CHANGELOG.md` section for it; commit and push.
2. Tag the commit and push the tag: `git tag v2.26.0 && git push origin v2.26.0`.
3. The **Release** workflow checks that the tag matches `VERSION`, compiles the Python code, downloads WinSW and checks
   the Python installer's signature, builds `Setup.exe` with Inno Setup, and publishes a GitHub Release with the
   `.exe`, its `.sha256` and the CHANGELOG section as release notes (the source zip is added by GitHub).

To try the installer without releasing: **Actions → Release → Run workflow** — the `.exe` is attached to that run
as an artifact. To build on a Windows PC instead: install Inno Setup 6, put WinSW-x64.exe at
`installer/windows/build/PMVNexusService.exe`, and run `iscc installer\windows\PMVNexus.iss`.

## License

MIT, see [LICENSE](LICENSE).
