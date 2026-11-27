# Changelog

All notable changes to PMV Nexus are listed here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). The current version is in the `VERSION` file.

## [3.9.4] - 2026-11-28

### Changed
- **The Wall: "Pick for me" is now "Random".** Its way there is calmer: the picked video lights up with a pulsing glow,
  The Wall flies to it and zooms in, then the video opens — it no longer hops between other videos first. The settings
  are now "Random picks" (the first choice reads "Any of them, at random") and "Show the way there".
- **The Wall: the hover preview's title** is written on the video for small previews too, like on the big ones
  (My Settings → The Wall → Title: "Under the video" for the old look).

## [3.9.3] - 2026-11-27

### Added
- **The Wall: video details in the side panel** — a preview on pointing at the picture, creators, **Open the video** /
  **New tab**, length, quality, file size, codec, rating, views, likes, uploaded and added dates, your plays and how much
  you watched, tags, music and description (choose which in My Settings → The Wall).
- **The Wall: double-click** a video to open it in this tab or a new one (a performer: their profile) — My Settings →
  The Wall → Double-click; by default it does nothing more than a click.
- **The Wall: the hover preview grows with the picture** — a picture shown bigger than the preview size plays its
  preview right on it, at its size (can be turned off).
- **The Wall: sideways scrolling** (touchpads, tilt wheels) moves it sideways.

### Changed
- **The Wall: arrow keys fly** by default — ↑ ↓ forward and back (like the mouse wheel), ← → sideways. My Settings →
  The Wall → Arrow keys can set them to slide in all four directions (3.9.2's way) or to turn; Shift gives turning.
- **The Wall: "Moving speed (3D)"** is now in % of the pointer's way (100 % = the pictures follow the pointer).

### Fixed
- **The Wall (3D): dragging stepped** instead of flowing (the turning followed the mouse in whole-pixel jumps). The page
  now moves it itself: smoothly however slowly you drag, gliding on after you let go.

## [3.9.2] - 2026-11-27

### Added
- **The Wall in the sidebar** (under Performers).
- **The Wall: zoom you control** — the mouse wheel zooms 1 % a step by default (Shift: five times as much), smoothly
  and, in 2D, towards the mouse; **+** / **−** / **0** on the keyboard; and in the bar **−**, a zoom slider, **+**, the
  zoom in % (100 % = everything fits) and **Fit**.
- **The Wall: arrow keys** move it while held; in 3D, Shift + arrow keys turn it.
- **My Settings → The Wall** now sets nearly every part of it: what it opens in, starting zoom, side panel, starting
  "Shared videos at least" and "Also who they're with", videos around a performer (50–1000), zoom per wheel step,
  turning / moving speed, glide, arrow-key speed, size of dots and pictures, picture detail, line strength, video
  titles in 2D, background, hover previews (on/off, wait, size), and Pick for me.

### Changed
- "The wall" is now called **The Wall** everywhere.

### Fixed
- The Wall's mouse-wheel zoom jumped about a quarter at every step (the graph library's own zoom kept going after each
  step), so it went from everything too small to one picture larger than the screen.

## [3.9.1] - 2026-11-27

### Added
- **The wall → Pick for me** (when a performer is in the middle): opens one of their videos — random, one you haven't
  watched, one of your Favorites, the one you watch most, highest rated, most viewed / liked on the site, or newest
  (**My Settings → The wall**). Pressing it again gives another one. An animation looks through the videos, locks on,
  zooms in and grows the picture into the video page's player; it can be turned off, made 2–12 s long, and skipped
  with a click, Esc or **Skip**.
- **The wall:** pointing at a video plays its preview (muted, like the library); a **?** button explains how to use it
  (it opens by itself the first time).

### Changed
- **The wall:** 3D turns freely in every direction (also over the top) and glides to a stop; dragging always moves the
  view — a dot can no longer be dragged away, which pulled everything linked to it along. Pictures are drawn from small
  copies, so hundreds of thumbnails stay smooth. The panel no longer lists a performer's video titles (the library
  does); **Most often with** stays.

## [3.9.0] - 2026-11-27

### Added
- **Library → Select → Redo Frames:** takes the video frames under the player again for every picked video, one after
  another on Tasks (a video someone is watching still goes first). **Select all {n}** picks every result of the current
  search and filters, up to 5,000 — e.g. all vertical videos.
- **FansDB and StashDB**, searched by hand from a performer's page (**FansDB / StashDB**); ThePornDB stays the automatic
  match. Each site has its own API key and GraphQL address under **Server Settings → Credentials**. Linking a performer
  not on ThePornDB fills the details nobody typed in, adds their links, sets the placeholder's gender and saves up to 5
  of their photos; on a ThePornDB profile it adds their page, links and what ThePornDB leaves empty. **Refresh** and
  **Unlink** on the same panel.
- **Details typed in for performers not on ThePornDB** (**Edit details** on their page): other names, birthday,
  birthplace, nationality, ethnicity, hair, eyes, height, weight, measurements, cup size, enhanced, tattoos, piercings,
  active years, a bio and up to 20 links to their pages. They show like a profile's and count for the Performers
  page's filters.
- **More performer photos:** up to 30 for a performer not on ThePornDB, several picked or dropped at once, and **Add
  from link**: the server fetches the picture — public internet addresses only (never your own network or the server;
  checked again after each redirect, at most 3), usual ports, pictures only, 30 MB and 20 seconds at most.
- **The wall** (Performers → **The wall**, or **On the wall** on a performer's page): who appears with whom as a 3D
  graph (2D too); click a performer to put them in the middle with all their videos as thumbnails, optionally with the
  people they share them with. Only performers matched to ThePornDB / FansDB / StashDB or filled in by hand.
- **Shared scenes:** videos using the same footage are found by comparing the frames of their seekbar previews
  (black, white and flat frames, the first and last seconds, and intros found in many videos are left out) and listed
  under the video (**Shares scenes with**, with times to jump to). They also rank higher among related videos. Runs
  after new seekbar previews and from **Management → Maintenance → Shared scenes**.
- **Re-encode: check the file first** (Server Settings → Re-encode, on by default): every packet is read and a few
  seconds at 10/50/90 % are decoded before re-encoding; a damaged file is marked failed with what was found instead of
  being re-encoded, and you get a notification.
- **MediaInfo…** in a video's Technical Details: MediaInfo's full report of the file in a window (with **Every field**
  and **Copy**). The video's **pHash** is shown under General.
- **Network logging:** every request the app sends out (ThePornDB, FansDB/StashDB, PMVHaven, Gemini, VirusTotal,
  Filester, update checks, photos, extensions) and every request it receives is logged under the category *network* —
  failures, timeouts and errors as warnings, slow ones as info, the rest at Debug. Keys and tokens in addresses are left
  out; headers and bodies are never logged.
- **ThePornDB: how long to wait and how often to try again** (Server Settings → Performers → **Wait for ThePornDB**,
  60 s, and **Tries after no answer**, 2). Each try without an answer is logged.
- **My Settings → Video → Which related videos:** best matches, or a random pick among all related videos.

### Changed
- Management → Logs' **User actions** view leaves out the new network entries.

### Fixed
- **A performer linked by hand to a ThePornDB profile spelled differently** (e.g. "Vana Rose" in your videos →
  ThePornDB's "Vanna Rose"): with merge aliases on, the videos show the profile's spelling, but that badge opened an
  empty page ("No videos with this performer"), the badge stayed grey, and a ♥ set before linking didn't show. The
  profile's own name now opens the profile (also from similar performers), and colour and ♥ follow it.
- Adding a photo to a performer who already had the most allowed could leave the new file and its row behind; the
  limit is now checked before anything is saved. A ThePornDB profile that received several photos when its name got
  linked no longer has all but one deleted by the next photo added — you're asked to remove them yourself first.
- Performer page: a missing space between "Not looked up on ThePornDB yet." and the next sentence.
- Filester messages pointed to "Admin → Credentials"; the credentials are under **Server Settings → Credentials**.

## [3.8.0] - 2026-11-25

### Added
- **Backups in more than one place.** Each Scheduler backup job has its own options (**Edit**): what goes in, how
  many of its backups to keep, up to 5 more folders each backup is copied to (an external drive, a NAS share), and
  an upload to a private Filester folder. So a failing or wiped disk doesn't take the backups with it.
  - **Copies** are read back and checked byte for byte before they get their name; a folder that can't be reached
    is reported and the other copies are still made. The copy folders' backups are listed on the Backups page,
    ready to test or restore. In a copy folder only the job's own backups are deleted when pruning.
  - **Filester** (API key under **Admin → Credentials → Filester**): a private folder “PMV Nexus backups” is made
    and used. What may leave the computer is enforced on saving the job and again before every upload: only
    encrypted backups (a backup password must be set), never the credentials (**Secrets**) or the videos, and in
    parts of at most 9 GB (Filester takes files up to 10 GB). Every upload's size is checked; a failed one
    leaves no parts behind. Only uploads this app made (recorded in `data/cloud_backups.json`) are ever deleted
    there, beyond the job's **Keep**.
  - **Create backup now** can upload too (**Also upload to Filester**, same rules).
  - Copies and the upload happen only after the backup passed its restore test. If one fails, the job's run is
    *failed* with the reason, and a notification says so; the backup and the other copies stay.
  - **Backups → Copies elsewhere** lists each job's copy folders and upload, the backups on Filester, and
    **Check Filester** (says whether it connected, and as whom).
- **Daily backup**, a new built-in Scheduler job: the database and settings every day at 02:30, keeping 7.
  **Weekly backup** on a new install also takes the thumbnails, previews and details files, video frames and seekbar
  previews, keeping 4.
- **Backup names say when, which version and what's in it:**
  `pmvnexus_backup_<date>_<time>_v<version>_<flags>.zip`, e.g. `pmvnexus_backup_20261125_183250_v3.8.0_a0b1c1d1e1f0.zip`.
  The date comes first, so names sort by date. The flags are a letter per part followed by 1 (in it) or 0: a
  credentials, b ThePornDB photos, c thumbnails/previews/details files, d video frames, e seekbar previews, f videos
  (the database and settings are always in). Older names keep working everywhere.
- **`backup_report.md` inside every backup:** for a person reading it — what started it (the Backups page and who,
  or which Scheduler job), when (local and UTC), the app version, a checklist of the parts with file counts and
  sizes, the split size, whether it's password-protected, where it was saved, where it gets copied/uploaded, and the
  steps with their times. In a password-protected backup it's encrypted like the rest.

### Changed
- **Password-protected backups show less without the password:** the part readable without it (`backup_info.json`)
  now holds only the format, the date and the job's id. The Backups page reads the rest with the saved password; a
  backup the saved password doesn't open is described from its name.
- **Each backup job keeps its own number of backups** (its **Keep**); backups made by hand on the Backups page, and
  older ones, still keep `backup.keep` (10). Backup jobs from before 3.8.0 get their options written in once —
  doing exactly what they did (Server Settings' "include thumbnails", keep 10) — so they show in the job dialog.
- The Scheduler's job type is now just **Backup**; the job card says what each backup job takes, keeps and copies.

### Fixed
- Scheduler job dialog: the cron field showed under the simple schedule too.
- Scheduler on a phone: a long job-type label made the page wider than the screen.

## [3.7.0] - 2026-11-25

### Added
- **Languages.** The whole interface — and the messages the server sends: errors, notifications, Tasks entries,
  job results — can be shown in other languages. Each person picks theirs in **My Settings → Language**; the
  admin chooses the default (also used on the sign-in page, which has its own small language list). Texts a
  language doesn't have yet show in English; video titles, names and tags stay as they are.
- **Getting languages:** admins install them from the app's GitHub repository (**Get languages from GitHub**,
  with **Update** when a newer file is published) or from a file. Every download is checked against GitHub's list
  of sizes and SHA-256 hashes, and every text is checked before it's used: a language file can only change words
  (no markup), and an entry that doesn't fit the English is ignored. Installed languages are kept in
  `data/languages` and go into backups.
- **Right-to-left languages** mirror the layout (sidebar on the right, text and buttons right to left); the player,
  its seekbar and frames, charts and the duration slider keep running left to right. Titles and names in another
  script keep their own direction, and sizes, times and names inside a sentence keep their own order ("13 MB").
- **For translators:** `languages/en.json` lists every text (generated from the code), with `TRANSLATING.md`
  (including a ready-made prompt for AI translation) and `python -m Tools.i18n missing / merge` to translate only
  what's new after an update. A **Translations** GitHub workflow checks every push: no English typed straight into
  a page, every language file valid, the list of hashes up to date. The release build runs the same check.
- **Player on touch screens:** **Loop** (left) and **Next video** (right, while a playlist plays) sit beside the big
  play/pause button in the middle, in the same round style, instead of in the control bar.
- **Backups, part by part:** besides the database and settings, a backup can hold the secrets, the ThePornDB
  performer photos, the thumbnails and previews, the video frames, the seekbar previews — and now **the videos
  themselves** (the whole library; asked a second time). The Backups page shows each one's files and size, the
  estimated size of the backup and the free space where backups go (updated every few seconds), and lists exactly
  what will go in before it starts. A backup that wouldn't fit isn't started (scheduled ones too), and one that runs
  out of space while being written stops and removes its unfinished file.
- **Split backups:** a backup bigger than a size you choose (1.9 to 50 GB) is written as numbered parts —
  `name.zip.001`, `.002`, … — one normal zip cut into pieces, which 7-Zip opens from the first part. The app lists,
  downloads (part by part), tests, restores and deletes the parts together; Setup restores one when you pick its
  `.001` file. Scheduled backups use it too.
- **Choose where a backup goes:** a backup made on the Backups page can be saved to any folder (e.g. a bigger or
  external drive) — the free space shown is that folder's. Folders used this way are remembered: their backups are
  listed (with their folder), tested, restored and deleted like the others, and skipped while the drive isn't there.
  Only the backups folder keeps just the newest ones.
- **Uninstall asks what else to delete**, one tick each with its size and folder: the library database and settings,
  the thumbnails/previews/frames, the backups, and the videos (backups and videos asked again). Each is deleted only
  in the folder shown for it, and only the files the app made.

### Changed
- **Performers aren't assumed to be women any more.** Every text about a performer is gender-neutral ("Find on
  ThePornDB", "Which profile is it?", "Someone else — split off", "Videos in the library"…), and someone whose
  gender isn't known (or is neither female nor male) gets a neutral placeholder picture instead of the female one.
- **Python 3.12 to 3.14.** `requirements.txt` now pins the exact package versions the app is tested with (it used
  to give minimum versions), and those need Python 3.12 or newer, so 3.11 is no longer supported. Setup, install.bat
  and install.sh look for 3.14, 3.13 or 3.12; an app folder whose `.venv` was made with 3.11 gets a new one on the
  next update (Setup installs Python 3.12 when the PC has none of them). **Management → Tools** flags a package
  whose version differs from the pinned one.
- **Task Manager shows PMV Nexus**, not pythonw.exe: Setup puts a copy of `PMVNexus.exe` in the app's Python
  environment, and that copy runs Python inside itself (the app, the tray icon and what they start). A Windows
  service still runs as python.exe.
- **Backups run in the background** (followed on Tasks, with a notification at the end), and Tasks shows both the
  whole job and the step it's on, each with its own bar. Pictures and videos are stored in the zip as they are
  instead of being compressed again (faster, same size). The restore test checks a backup's videos by their size
  instead of reading them all back; a restore skips videos that are already in place. Restoring a password-protected
  backup that the saved password opens no longer writes a decrypted copy of it first.
- Dates and numbers follow the chosen language. Scheduler results read as words in that language, without
  timestamps and addresses. The built-in jobs' names are translated while they keep their original name.
- The interface's styles use start / end instead of left / right throughout (no change in English).
- **A failed Windows install leaves its details in `C:\Logs`** (made when it isn't there):
  `PMVNexus-install-<date>-<time>.log` with what failed, the install log and Setup's own log. Its message says where.
- **Windows Setup: Cancel at every step, and it undoes what Setup did.** Cancel used to be gone once the files were
  copied (while the Python environment and packages, FFmpeg and the settings were set up). Now there's a Cancel
  button for those steps and on the pages after installing too. It asks first, listing what it will undo, stops the
  running step, then undoes this run: a new install is removed again (app folder, shortcuts, Apps entry, folders the
  set-up pages created, service, firewall rule, the Python Setup installed); an update puts the previous version
  back — its code (copied before the update), its Python environment (a changed one is made new, the old one kept
  aside until the update is done) and FFmpeg — and starts the app or service again. Only what this run created is
  deleted. Setup ending with an error is undone the same way. When installing the packages fails, Setup asks whether
  to undo the install or keep it to continue later.
- **Antivirus hints (the app isn't code-signed):** when a failed install looks like an antivirus's doing (a program
  of ours removed, a step that couldn't start, "access is denied" and the like), Setup says so and what to do: check
  the quarantine, allow the PMV Nexus folder, run Setup again. After installing, Setup checks that `PMVNexus.exe` is
  still there — without it the shortcuts don't work, so it says so; without its copy in `.venv` the app runs as
  pythonw.exe, and it says that too. The launcher's own error messages mention it as well. README and the guide
  explain the SmartScreen warning and how to check a download against its `.sha256` file.

### Fixed
- **Uninstall deleted backups outside the backups folder (data loss, earlier 3.7.0 builds only).** With **Backups**
  ticked it also deleted the backups in every other folder backups had been saved to (Backups → Save to), while the
  window showed only the backups folder. The library tick could also take backup files left in the `data` folder
  and every file in a logs or sign-ins folder chosen outside it. Now each tick deletes only in the folder it shows;
  backups in other folders are never deleted (listed as kept); backup files are only ever deleted by the Backups
  tick and video files only by the Videos tick; a logs folder you chose loses only the app's log files, a sign-ins
  folder you chose is kept; and deleting backups is confirmed once more, with the folder named.
- **Windows Setup, "Start with an empty library" on a new PC:** it could stop at once with "The app's settings can't
  be read" and no reason. Each of Setup's steps waited for a file saying it was done, and the previous step's file
  could still be there (an antivirus still reading it), so Setup went on before the step had even run. Every step now
  has its own file. Setup's helper also always says what went wrong (e.g. a package that isn't installed), and when it
  can't, the message gives its exit code and the last line of its log.

## [3.6.0] - 2026-11-20

### Added
- **Creators**, separate from the uploader: PMVHaven's "creator" list (who made the video, sometimes several people)
  is now read, next to the account that uploaded it. A video is listed under its creators and its uploader alike
  (filter, `creator:` search, sort, "More from…", related videos), and the video page shows "uploaded by …" when
  that's someone else. Videos already in the library get their creators from the details saved when they were
  downloaded (also for videos gone from the site), once, in the background; Sync with PMVHaven keeps them current.
- **Edit details → Creators**: add or change who made a video (suggestions from existing creators and uploaders);
  kept through syncs like tags. With none, the uploader counts as the creator.
- **Notifications menu**: clicking a notification opens a small menu instead of jumping straight away — open the
  video or page it's about, open Tasks (for downloads, re-encodes, repairs, uploads and jobs), see the log from
  around that moment (warnings and errors for a problem), or copy it.
- **Performers page: favourites** — a **♥ Favourites** button shows only your favourite performers, and the new
  **Favourites** sort puts them first (or last, with the order button), then by most videos. Favourites have a ♥
  before their name.
- **Re-encode queue: First and Last** on Tasks, as downloads have — First puts a video next, right after the one
  encoding now.

### Changed
- A finished or failed job about one video (e.g. finding songs) now links that video; Tasks is in its menu.
- **Library: ✕ next to the search box also clears the search text** (it cleared only the filters). The sort stays.

### Fixed
- **Tasks → Waiting wasn't in queue order for re-encodes** (e.g. after picking several videos to re-encode): only
  downloads were sorted. Everything waiting is now listed in the order it will run — downloads, then re-encodes,
  then repairs, each by its #.

## [3.5.0] - 2026-11-17

### Added
- **Performer badges coloured by gender**: female pink, male blue, trans / non-binary / intersex light purple, not
  known grey (from the ThePornDB profile, or set by hand on the performer's page — now also "Trans"). Each colour
  is in My Settings → Appearance; the tooltip says the gender.
- **Favourite performers**: ♥ on a performer's page (yours only, all her names count); her badges get a ♥.
  New library sort **Favourite performers**: their videos first, or last with the order button.
- **Saved searches** (🔖 next to Filters): save the whole view (search, filters, exclude, sort) under a name and open
  it again later; saved with your account.
- **Exclude** (Filters): leave out videos by word, or by field — `tag:avc title:downbad music:remix`.
- **Filters: Only without music / Only without performers.**
- **Tasks: the video a task is about is a link** to its page, e.g. "Finding songs: “After Party”", and the video
  being worked on in jobs that go through many (seekbar previews, video frames, Find songs for many, Make audio
  playable, Make previews H.264, converting while watching), also after the task finished.
- **Duration filter as a slider** with two handles snapping to 0–10, 12, 15, 20, 25, 30, 45, 60 minutes and no limit,
  plus exact m:ss boxes (seconds now count; older links in minutes still work).

### Changed
- **Clear filters** is now the ✕ next to the search box (it was at the bottom of the Filters panel).
- **Tags are yellow** by default (blue is now male performers'); a custom tag colour you chose is kept.
- **Logs: Copy** an entry (as JSON) or only its error details, and the traceback is shown readably under the
  JSON. Clicking or selecting text inside an open entry no longer closes it. Copy buttons also work when the app
  is opened over plain http on the home network.
- **Release notes** cover every version since the previous release (a version that wasn't released on its own is
  included instead of left out).
- **Release workflow:** renames the Discord server's version channel to the new version when a release is
  published (optional; see README → Making a release).
- **README** brought up to date with everything the app does (proxy, phones, converting while watching, Search this
  frame, ThePornDB performers, songs, multi-user, Scheduler, …).
- **Discord community:** link in the README (badge), on GitHub's "New issue" page and in the support section.

### Fixed
- **"Response content longer than Content-Length" errors in the log** when a browser asked for part of a video or
  preview beyond its end (e.g. the file got smaller after a preview conversion or a re-encode while a tab still had
  its old size). Such a request now gets the proper "range not satisfiable" answer, and a full download never sends
  more than the size it announced.

## [3.4.2] - 2026-11-16

### Fixed
- **Video frames: "Redo Frames" brought back the same placeholder pictures** for videos PMVHaven has no real
  timeline frames for. Redo Frames now makes them from the video file. Site frames that are all one placeholder
  picture are no longer used (the app makes them from the file), and videos that already have such frames get
  theirs made from the file once, in the background, after updating.

## [3.4.1] - 2026-11-16

### Added
- **Library: sort Random.** The order button becomes "Shuffle again": a new order each click. The order is kept
  in the address (next page, Back and reload keep it). Can also be your default sort (a new order each visit).

## [3.4.0] - 2026-11-15

### Added
- **Your own tab order in Management and Server Settings**: drag a tab, or the ⠿ button → up / down (phones,
  keyboard) → "Back to the usual order". Saved with your account; the first tab opens when you come to the page.
- **"See the log" on Tasks**: a finished task that failed or says "see the log" links to Management → Logs showing
  only the time it ran (the error and what led to it). The Logs page (and its export) can be limited to a time window.
- **Re-encode to the same codec to downscale** a video larger than 1080p (e.g. 8K HEVC → 1080p HEVC), from the
  Re-encode window with "Downscale to 1080p if larger" on.

### Changed
- **Find songs shows each song as soon as it's heard** (it can be added while the scan goes on), and its "song(s) so
  far" count keeps up, through Gemini's clips too.
- **Video frames under the player, vertical videos**: 24 frames made from the file instead of 8 (the site has only
  8), so they fill the row at about the height of a horizontal video's 8 instead of being about three times taller;
  every third shows its time. Vertical videos that already have frames get the 24 once, in the background, after
  updating. If they can't be made, the usual 8 are kept, shown as before.
- Server Settings' link to your own settings says **My Settings**.

### Fixed
- **Seekbar previews / video frames failed with "Couldn't read the video's length"** for files whose name or title
  has characters like "＊（）" on some Windows setups: FFmpeg's answers were read in Windows' code page instead of
  UTF-8. All FFmpeg/ffprobe output is now read as UTF-8, a probe that finds nothing says
  why in the log, and an MKV/WebM track's DURATION tag is used when the file gives no length.
- **Stats → Most watched**: a long title wraps in its column instead of pushing the time and plays onto a line of
  their own.

## [3.3.1] - 2026-11-15

### Added
- **Re-encode to AVC (H.264)**, next to HEVC and AV1 — for an HEVC/AV1 video an older device can't play. Its own
  quality and preset in Server Settings → Re-encode (default CRF 20, medium: the source was already compressed, so
  a low CRF keeps the extra loss invisible; files get bigger). Every codec can now go to every other one, except the
  one the video is already in.

## [3.3.0] - 2026-11-15

### Changed
- **Tags and performers are sorted A→Z** on the video page, in Edit details (sorted again as soon as you add or edit
  one) and everywhere else; numbers sort in order ("Part 2" before "Part 10").
- **Every word of a tag or performer name is capitalized** ("Alpha vs beta" shows as "Alpha Vs Beta"; "PMV" stays
  "PMV"). Music keeps its own writing.
- **Hover previews are always 8-bit H.264**: AV1, VP9 and 10-bit previews are converted when they first play, right
  after a download, and by the new Scheduler job "Make previews H.264" (added once, for the first night after
  updating). A graphics card's H.264 encoder (NVENC, Quick Sync, AMF, VideoToolbox) is used when it works and a short
  test encode shows it's faster than x264. The videos themselves are not changed.

### Fixed
- **Names → Rename / merge couldn't change only the capitals** ("PMV" shown as "Pmv" → "PMV"): the new writing wasn't
  seen as a change. It now rewrites every way the name is written in every video and becomes the spelling shown.

## [3.2.1] - 2026-11-14

### Changed
- **Search frame: Yandex is built in by default**, next to Google Lens (for anyone who hasn't changed the sites;
  otherwise My Settings → Video page → Add Yandex, or "Back to Google Lens + Yandex").

### Fixed
- **Confirmations asked from the notifications panel couldn't be answered** ("Delete all notifications?"): the panel
  lay over them, so any click there — even on Delete or Cancel — closed the panel instead. Confirmations now sit above
  every panel; Esc answers the confirmation first.
- **Search frame on Yandex didn't work** ("Yandex didn't answer as expected"): the app's server now uploads the frame
  the way Yandex's own page does ("post, redirect, get") and the tab opens the results Yandex sends back. Any Yandex
  entry you added switches over by itself (Yandex refuses the upload straight from a browser). When it fails, the log
  says what Yandex answered.
- **"Cancel 1 running task(s), timeout graceful shutdown exceeded"** in the log when the app stops didn't say what was
  cancelled: it's now a warning naming the requests that were still open (nearly always a video still playing in a
  browser tab — nothing of the app's own work is lost), and the traceback that followed it is gone.

## [3.2.0] - 2026-11-14

### Added
- **Search frame** (⚙ in the player): pauses the video and looks up the picture on screen on a reverse image search
  site in a new tab — Google Lens by default, Yandex built in too, more sites in My Settings → Video page → Search
  this frame. The picture goes from your browser to that site (through the app's small /frame-search page, the only
  page allowed to send a form to another site); only for Yandex, whose upload answers with the address of its results,
  the app's server passes it on.
- **Video frames under the player** (like PMVHaven's video timeline; **off by default** — the seek bar's previews do
  the same; switch them on in My Settings → Video page): a row of pictures from across the video with their time —
  click one to jump there; the one you're in is highlighted. PMVHaven's own timeline frames when the video has them,
  otherwise 8 made from the file (uploads, videos gone from the site), evenly spread. Small WebP files (at most
  320 px) in the cache folder's `.frames`; new videos get them by themselves, others when their page is opened, or all
  at once from Management → Maintenance → Video frames. **Redo Frames** (with the video's buttons) makes them again.
- **Notifications for possible duplicates:** when a duplicate scan (after downloads, re-encodes or repairs, the daily
  one or Scan now) finds a pair it hadn't found before: "Possible duplicate: “New video” — looks like “Older video”
  (94% match)", opening Management → Duplicates. More than 5 new pairs at once give one summary instead. Pairs you
  marked "not duplicates" stay quiet, and pairs found before 3.2.0 aren't announced. For admins; pop-ups can be
  switched off in My Settings → Notifications → "Possible duplicates found".
- **Audio browsers can't play is converted to AAC** (PCM, AC-3, DTS, TrueHD, ALAC, MP2 played silent in some or all
  browsers): right after a download, and for the videos already in the library by the new Scheduler job **Make audio
  playable** — added once, for the first night after updating (02:00), or start it with Run now. The picture isn't
  touched.
- **One-time Scheduler jobs:** a job can run once at a date and time, then switches itself off.
- **Starting volume** (My Settings → Video page): the volume videos start at (default 10 %), or keep the volume you
  last set in each browser (on by default, as before).
- **♥ badge on videos in your Favorites** (like the 💀 one for videos gone from the site): on thumbnails (also the
  small ones) and in the table; it updates as soon as you add or remove a favorite. Can be turned off in
  My Settings → Library → badges ("♥ In my Favorites").
- **Screenshots** in the README and the Guide (also shown in Help → Guide, which can now show pictures) — a demo
  library with invented titles, names and numbers and abstract generated videos. Examples in the app (My Settings,
  Server Settings, search help) use invented names too.

### Changed
- **Video page:** the title and **🎲 Random** (was "Discover again") are above the player, in the video's own column —
  the related videos start at the top on the right, and go below everything else in a narrow window; the uploader
  and details come right under the player. The Library / Back link is gone: **Library** in the sidebar takes
  you back to the library as you left it (filters, page) from any page, so the "Back button" setting is gone too.
  The description has a **Description** heading; **Technical Details** are split into General / Video / Audio / Other,
  side by side when there's room; the videos in **Related videos** and **More from …** have the ⋮ menu;
  **Repair/Update** is spelled that way.
- **"My Settings" and "Server Settings"** are written that way everywhere.
- **Volume slider:** 2.5× taller, in 1 % steps (dragging, mouse wheel, arrow keys — was 5 %, so anything below
  about 3 % jumped to 0), with the value shown above it.
- **Sort:** "Duration" is "Duration (length)".
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
