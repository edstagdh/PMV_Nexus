# PMV Nexus Guide

PMV Nexus downloads, organizes, re-encodes, de-duplicates and shares videos from PMVHaven
(and its sister sites porntubeai.com and hmvhaven.com) through a web app you run on your own machine.

## Getting started

### Requirements
- **Python 3.11 or newer**
- **FFmpeg** (both `ffmpeg` and `ffprobe`) on your PATH
- **MediaInfo** (bundled with `pymediainfo` on Windows)
- **Playwright Firefox**, only needed to log the app into the sites

**Management → Tools** shows which of these are installed and how to fix anything missing.

### Install
**Windows:** run **`PMV-Nexus-Setup-<version>.exe`** from the project's Releases page. It installs
everything (Python too, if the PC has none — it says so first), asks whether to start with a new library or from a
backup, where the videos and cache go, and the port, and offers a Start-menu / desktop shortcut, starting with
Windows, or running as a Windows service. **Linux / macOS:** `./install.sh` does the same in the console (and can
set up a systemd user service).

**Windows without Setup:** double-click **`install.bat`** in the app folder. It installs everything the app needs (into `.venv`)
and asks for a **backup zip** to set up from (see *Backups → Scenario 3*) — or, for a new setup, where the
**folders** go (videos, thumbnails & previews, logins, database, backups, logs; Enter keeps the defaults inside the
app folder, a number changes one) and the **port** (Enter = 8676).
By hand, on any OS:
```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
python -m playwright install firefox
```

### Configure
The first start creates `config.yaml` from `config.example.yaml`. Set `download.output_dir` (videos) and
`download.cache_dir` (thumbnails, previews, metadata) in it — by default both are inside `data/`.
No `.env` file is needed: accounts and credentials are set up in the app.

### Start the app
- **Windows:** double-click `start.bat`. It uses `.venv` automatically if it exists.
- **Any OS:** `python run.py` from the project folder.

Open `http://localhost:8676`. Another port: set `server.port` in `config.yaml` and restart (`server.host:
"127.0.0.1"` makes the app reachable from this computer only). `run.py --port 9000` (or `start.bat --port 9000`)
uses another port for one run.

**First start:** the console window shows a **setup code**. Enter it in the browser together with a username and
password to create the admin account. Then:
1. Open **Server settings → Credentials** and enter your PMVHaven email and password (plus any API keys you use).
2. Optional: add your PMVHaven **API key** there too (from pmvhaven.com/api-keys). Downloads use the site's API
   only, so connecting the sites under **Connections** isn't needed any more (it's only used if
   `download.website_fallback` is turned on in `config.yaml`).

## Finding your way around

Everything is in the sidebar on the left (on a phone, behind the ☰ button): the **Add videos** and **Upload**
buttons, **Library**, **Discover**, **Playlists**, the **Server** section (admins only: **Tasks**, **Management** and
the server's **Settings**; click the *Server* heading to fold it away, and Tasks still shows while something is
running), and at the bottom **Help**, **My settings** and **Sign out**. The sidebar button next to the app name shrinks it to icons. The interface
updates live without refreshing.

Tips:
- Press **/** to jump to the search box; the **?** inside it shows search examples.
- Click a performer, tag or creator anywhere to filter the library by it.
- The address bar holds the whole view (filters, sorting, page), so you can bookmark it or use Back.
- **My settings** has five tabs:
  - **Appearance:** theme (or a custom theme with your own colours), text size, coloured performer/tag/music
    chips and the 💀 badge colour.
  - **Library:** default sort, which thumbnail badges show, and the table hover-preview size.
  - **Video page:** how many related videos to show and which badges they get, what **Back** does (return
    to the library — the default, keeps your filters and scroll position — or step back through the videos you
    opened), and whether Edit details' suggestion lists stay open after you pick a value.
  - **Notifications:** which kinds of background updates pop up (see Notifications below).
  - **Account:** change your password; regular users can also delete their own account here.
- **Related videos** are other downloaded videos ranked by what they share with the one you're watching:
  each shared performer counts most, the same creator next, then each shared tag.
- **Edit details** (on a video's page or its ⋮ menu) lets you change its performers, tags, music and content
  flags, side by side — click a box to see every value already in your library, type to narrow it down, and pick. Your edits are kept when you sync,
  update or repair; **Use the site's** goes back to PMVHaven's.
- **Change thumbnail** can use one of the site's timeline frames or **any frame of the video itself**.
- The library's **Filters → Quality** picks exact qualities (480p … 8K) or "1080p or better", and
  horizontal, vertical or square videos.

Addresses from older versions (`/next/…`, `/admin/download`, `/admin/issues`, …) still work and lead to the
matching page.

## Everyday use

### Adding videos
Click **+ Add videos** and paste one or more video page URLs. Each shows up under **Tasks** while its
details are fetched and the file downloads, then moves to the **Library**. As soon as the details are
fetched, **Details** on the task opens the full video page (description, tags, performers, music, rating)
with the download status, before the file has finished downloading.

### Tasks
**Tasks** shows everything the app is working on, with live progress:
- **Jobs running now:** work that isn't about one video. That covers Scheduler jobs, duplicate scans, syncs with
  PMVHaven, backups (including ones made with **Create backup**), performer matching, repair-all, metadata syncs,
  folder moves and uploads being converted. Each shows what it's doing, how far it got (e.g. "12 of 61 done ·
  20%"), how long it has been running, and whether it was scheduled, started by hand or started automatically.
- **Running now / Waiting / Failed:** each video's download, re-encode, repair or share upload. Failed downloads
  can be retried.
- **Converting for watching:** every video being converted while someone watches it (**⚙ → 720p** in the player), with
  who's watching, the size and how far it got. **Stop** ends it; the viewer's player switches to the original file.
- **Coming up:** the next Scheduler runs, and a duplicate scan about to start after downloads.
- **Finished in the last day:** jobs that ended, with how they went (e.g. "matched 7 · refreshed 0").

The Scheduler page shows the same progress under a running job.

**Stop** ends a running duplicate scan, performer matching or sync with PMVHaven; what it did so far is kept. A
real sync can only be stopped while it's still reading, before it changes anything. Stopping **Repair/Update all**
takes the videos still waiting out of the queue and lets the ones being repaired finish. Backups, folder moves and
metadata syncs can't be stopped halfway, because stopping them midway could leave files half-written.

Waiting downloads can be moved with **First / Up / Down / Last**. That's handy when a long download is running and
something else should go next. Waiting re-encodes can be moved **Up / Down** too.
Removing a video that hasn't finished downloading also deletes its thumbnail, preview clip and details file.

### Notifications
The **bell** (top of the sidebar; top right on a phone) keeps what happened in the background:
- downloads, re-encodes, repairs, uploads and shares that finished or failed;
- for admins, also jobs (scans, syncs, backups, performer matching) and low disk space.

Its badge counts unread **problems**. **Problems** shows only failures and warnings; **All** shows everything.
Clicking one opens the video or page it's about. Each person is told about the videos they added; videos from
before 2.10.0, and ones added through the API, notify the admins.

New ones also pop up in the corner. **My settings → Notifications** turns the pop-ups off per kind: downloads,
re-encodes & repairs, uploads & shares, jobs. They're still kept under the bell. If the app loses the connection
to the server, a banner says so until it's back, and every page (except a video's, whose player keeps playing what it
already has) shows "Can't reach the server" instead of its content; the page comes back, refreshed, by itself.

### Low disk space
When the drive with the videos (or the app's data) has less than **10%** free, downloads stop. Any download in
progress goes back to the queue, and its partial file is deleted to give the space back. A red banner tells
everyone, and the admins get a notification. Re-encodes and everything else carry on. Once there's more than
11% free, downloads start again by themselves. Change the limit (or set 0 to turn it off) in
**Server settings → Download → Stop downloads below (% free)**; the current free space is shown there.

### Re-encoding
**Re-encode…** in a video's ⋮ menu (or on its page) converts it to a more efficient codec to save space. The default
codec, each codec's quality (CRF) and speed preset, the options (copy AAC audio, downscale to 1080p, remove chapters,
keep the original as "_old") and how many CPU cores an encode may use are set under **Server settings → Re-encode**:
- **HEVC (H.265)** plays almost everywhere — the usual choice.
- **AV1** makes smaller files but encodes slower, and some older devices can't play it.

**Re-encode…** opens a window to pick the codec (the one the video is already in can't be picked, so AV1 videos can be
turned into HEVC for devices that struggle with AV1). It shows that codec's settings from Server settings, which you
can change for **this re-encode only** (switching codec shows the other codec's settings; **Back to the server's
settings** undoes your changes). Many at once: **Select** in the library → **Re-encode…**, the same window.
**Without the window:** untick **Ask for the codec and settings each time** (Server settings → Re-encode) and
**Re-encode** queues the video straight away in the default codec with the settings there (Library → Select just
confirms); a video already in that codec still gets the window, to pick the other codec.
**Switching it off:** the **Re-encoding** switch at the top of **Server settings → Re-encode** removes "Re-encode"
everywhere for everyone and refuses new re-encodes (queued ones still finish and can be cancelled); converting
uploads isn't affected. AV1 needs an FFmpeg build with SVT-AV1 (the Re-encode tab warns if it's missing).

### Uploading your own videos
**Upload** (sidebar, under Add videos) adds a video from your computer:
1. Choose or drop the file and fill in the title, creator, upload date, description, performers, tags, music and
   content flags (the lists show every existing value and stay open while you pick). Press **Upload** (with
   progress; you can cancel).
2. The file is checked — uploads are handled **one at a time, in the order they arrived** (the page shows your
   place in line; you can cancel while waiting). Only **.mp4 .m4v .mkv .webm .mov .avi .wmv .ts** files can be
   picked; anything else is refused before it's sent. The server then makes sure the file really is that kind of
   video (its first bytes), scans it with the server's antivirus when there is one (Microsoft Defender or ClamAV —
   **Server settings → Uploads → Virus scan**; **Test the scanner** there shows what the antivirus answers), optionally
   looks the file's fingerprint up on VirusTotal (below), and checks FFmpeg finds a video track in it. Every upload is then
   re-written, so only its picture and sound are kept: one browsers can play is re-packed into a clean MP4 — quick,
   and the picture isn't touched. Anything else has to be
   **re-encoded**: pick **AVC (H.264)** (plays everywhere), **HEVC** or **AV1**, its quality (CRF) and preset, and
   press **Convert and add** (with progress). The admin sets the suggested codec in **Server settings → Uploads**,
   and can make this happen automatically without asking. The video joins the library only once this is done.
3. **Thumbnail:** click one of the frames spread over the video, pick any moment with the slider (**Use this
   frame**), or **Upload an image**.
4. **Preview clip:** the hover preview is 5 short clips. Move each with its slider (they stay inside the video
   and never overlap), ▶ to watch one or **Play all clips** — quietly, at 5% volume, with a volume slider — add
   or remove clips, then **Make the preview**. Or keep the automatic one.

It's saved like a download — `<download folder>/<creator>/<creator> - <date> - <title> [<id>].mp4`. Uploaded videos
aren't linked to PMVHaven: no "Open on PMVHaven", they're skipped by Sync with PMVHaven, and Repair just remakes
the thumbnail, preview and details from the file.

**Fingerprint check (VirusTotal):** with a free VirusTotal API key in **Server settings → Credentials** (**Test key**
there checks it with VirusTotal's harmless test file), the
upload's SHA-256 fingerprint (not the file) is looked up there. A file that antivirus engines on VirusTotal flag is
refused. Most personal videos are simply unknown to VirusTotal, which is fine — this catches *known* bad files, it
doesn't scan new ones. **Off** (default), **On** (lookup errors are ignored), or **Required** (an upload is refused
when the lookup fails, e.g. after the free 4-a-minute / 500-a-day limit). Every lookup and VirusTotal's answer is in
**Management → Logs** (search "VirusTotal"), and the Upload page lists the checks that ran when it's done.

Limits (Server settings → Uploads): the largest file (50 GB), how many unfinished uploads one person may have (10),
and enough free disk space for the file twice. Files left half-uploaded by a crash or restart are deleted when the
app starts.

### Library
- Admins switch between **Grid**, **List** and **Table** views; everyone else always gets the grid.
- Sort by **date acquired** (when the file arrived — downloaded or uploaded), **uploaded to PMVHaven**, title,
  creator, rating, views, duration, size or resolution (videos with no known upload date come last).
- **Filters** opens over the videos and stays at the top while you scroll, until you close it.
- **Filters → Clear all filters** removes every filter but keeps the search and sort.
- Filter by tags, performers, codec, quality, orientation, duration, rating and 💀 removed-from-site, and sort by any column.
- Search finds videos with **every word** you type, in any order (`alyx star natasha` finds "… Natasha Nice and
  Alyx Star"); put words in "quotes" to need them together as written. Field prefixes search one field: `title:`,
  `creator:`, `tag:`, `performer:`, `description:`, `music:`, `codec:` — the **?** in the search box lists them with examples.
- A word matches **any part** of a word: `tag:avc` finds tags "AVC", "H264-AVC", "avc1". `*` stands for any letters
  inside one tag or name: `tag:4k*` tags starting with 4k, `tag:*remix` ending with remix, `tag:h*vc` "hevc" and
  "h264 avc" (a `*` never runs from one tag into the next). `codec:hevc` finds every HEVC version, `codec:*l5.1*`
  a level.
- **Performer details** (bottom of Filters): cup size, age (from–to), hair, eyes and enhanced / natural breasts —
  from the ThePornDB profiles of performers matched to it. A video matches when **one** of its performers has all of
  the details you picked (cup DD *and* age 25–30 = the same person); performers not on ThePornDB never match. The
  choices list only what your performers have. Smart playlists keep these too.
- **Codecs in Filters** are grouped by default: one AV1, AVC, HEVC… entry that finds all its versions ("AV1
  (Main@L4.0)", "AV1 (Main@L5.1)"…). To pick exact versions, turn off **My settings → Library → Group codec
  versions**.
- Tags, performers and music that differ only in upper/lower case count as one value (see **Spellings** below).
- Click a video to open its page with the player, details and actions (rename, thumbnail, re-encode,
  share, repair, remove).
- The video page's details list its **Shares**: a badge for each destination it was shared to (click a ✓ badge to
  open it there; a failed one shows why when you point at it).

### The player
Video.js with the app's colours. Hover the speaker for the **volume slider** (a browser starts at 10 % until you change it; phones play at full
volume and use their own buttons); the ⚙ menu has **720p** (below) and the
playback speed, and **Download** (saves the video file); **Loop** sits next to it. While a playlist plays, **⏭** next to play/pause skips to the
next video. In fullscreen the video's title shows at the top along with the controls — while a playlist plays as
`Playlist | Title` (at most two lines; longer ones end in "…").

**Seekbar previews:** point at the seek bar (or drag it on a phone) to see the picture at that moment. Each video gets
small frames every 2 seconds (less often in videos over ~13 minutes, at most 400 per video), made by the server at low
priority: new videos right after they're downloaded or uploaded, any other video the first time it's played (it's
there from the next time), or the whole library at once from **Management → Maintenance → Seekbar previews → Make the
… missing** (one job on Tasks, can be stopped). They're kept in the cache folder (`.storyboards`, roughly 0.2–1 MB per
video), deleted with the video, moved with the cache folder, and not included in backups (they can be made again).
Videos with a key frame every few seconds (most PMVs) take a few seconds; others are decoded fully and take longer. After the seek bar is the video's **length**; click it to see the time left
instead (and again to go back). On the video page the keys (Space, ←/→, M, F, …) work anywhere on the page.
On phones and tablets tap the video to show the controls — a big play/pause button sits in the middle while they
show — and double-tap the left or right side to skip 10 s.
In fullscreen the sides work differently by default: **swipe up / down on the left for brightness and on the right
for volume** (brightness only changes the video's picture, from 20% to 150% — pages can't change the screen's own
brightness; iPhones and iPads don't let pages change the volume, so use the buttons there). To keep double-tap to
skip in fullscreen: **My settings → Video page → Sides of the video in fullscreen → Double-tap**. On a phone the
screen turns to suit each video in fullscreen and stays turned while a playlist moves on to a video of the same shape.

**720p (converting while watching):** **⚙ → 720p** (shows On / Off) plays a copy the server converts while you
watch — H.264 at 720p or less, much less data, for watching over a slow connection or VPN. Seeking works as usual
(after a jump the picture needs a few seconds). Your choice is remembered in this browser until you switch it off again.
A video your browser can't play (e.g. HEVC or AV1 in a browser without them) switches to this by itself, with a
message. Nothing is saved on the server: the pieces are deleted when playback stops. The admin sets it up in
**Server settings → Re-encode → Converting while watching**: on/off, the largest size (720p or 480p), how much of the
CPU each conversion may use (default 50% of the cores, at low priority) and how many videos may be converted at once
(default 2, one per person). FFmpeg pauses itself when it's about a minute ahead of the viewer, so a paused video
uses no CPU; a conversion nobody watches for 20 minutes ends. Each conversion shows on **Tasks** while it runs.

**Continue where you left off:** the app remembers where you stopped in each video (for your account, so it follows
you to other devices) and starts there next time, with a **Start over** button at the top of the player for a few
seconds. Videos watched to the end (95 %+) start from the beginning. Thumbnails show how far you got as a thin red
line along the bottom. Both can be switched off in **My settings → Video page**. Nobody else sees your places.

**Panic keys** (keyboard; off until set): in **My settings → Video page → Panic keys**, press **Set keys**, then press
the keys you want together (up to 3, e.g. **A + D**) and let go. The app shows the keys it detected — **Use these keys**
saves them, **Try again** records again (some keyboards don't report certain keys pressed together). From then on,
pressing them **all at once** (not one after the other) anywhere in the app stops playback at
once (the mini-player too, fullscreen ends) and switches to an ordinary-looking article page — one of five (baking,
houseplants, hiking, home desk, groceries) from invented lifestyle sites, with a few made-up everyday ads, the
article's own tab title and icon, a casual address (`/read/…`), dark or light to match your theme, and a different
one each time. Nothing on it does anything; the browser's **Back** button returns to the app. The chosen keys **belong to this site**, like YouTube's shortcuts: here, outside
text fields, the browser doesn't act on them (e.g. Firefox's "search for text when you start typing" can't swallow
the second key any more); with another Ctrl / Alt / Shift / ⌘ held (Ctrl + F…) they still reach the browser, and
other sites aren't affected. A key that is also a player shortcut (Space, K, J, L, F, M, C, I, arrows, 0–9…) stops
working as that shortcut — the window says so before you confirm. While you type in a text field only keys that
include Ctrl, Alt or ⌘ / Win count, so typing can't set it off.

**A-B repeat** (off by default — turn it on in **My settings → Video page → A-B repeat**): an **A-B** button in the
player plays one part of a video over and over. Click it at the start of the part (A), again at the end (B); it then
jumps back to A every time it reaches B. A third click stops it. It also stops when another video opens; while it
repeats, a playlist doesn't move on.

### Keep watching while you browse (mini-player)
Leave a video page while the video plays and it carries on in a small player in the bottom-right corner, so you
can browse the library, playlists or settings. Click its title or ⤢ to go back to the video page (it keeps
playing), ✕ to stop it. Opening another video replaces it. Leaving a page whose video is paused just stops it.
While the mini-player is open, keys like Space and the arrows work for the page, not the video (click the player
first to use them there). It has its own volume slider (hover the speaker). On a phone held upright it's about
half the screen wide. Don't want it? **Settings → Video page → Mini-player** off: leaving a video page then stops the
video.
- **Move it:** drag its bar anywhere on the screen; the spot is remembered in this browser. Double-click the empty
  part of the bar to put it back in the corner.
- **Reloading the tab** brings it back (with its playlist), paused at the same moment. Reloading a video page
  continues from where you were, paused.

### Playlists
**Playlists** (sidebar) are for everyone, not just the admin.
- **Favorites:** everyone has a built-in **Favorites** playlist, always listed first. Press **♥ Add to Favorites**
  on a video's page (again to remove it). It can't be deleted or renamed, and it's always private: nobody else sees
  it, the admin included.
- **Add a video:** ⋮ menu or the video page → **Add to playlist…** — the playlists it's in show as chips (× takes
  it out); type to find another of yours and pick it. Below that, **New playlist with this video**.
- **Add from the playlist page:** **Add videos** opens a search over the library (the same search, with
  `performer:` etc.); **Add** puts a result at the end of the playlist.
- **Add a whole view:** in the library, set up the search / filters you want and press **Playlist** next to the
  filters. **Add** puts every matching video (all pages, not just the one you see) into a playlist.
- **Smart playlists:** in the same dialog, **Smart playlist → Save** keeps the *filter* instead of a fixed list —
  new videos that match (e.g. a performer's new downloads) appear by themselves. To change its filter, set up the
  library view again and choose **Replace the filter of …**.
- **Private or public:** private playlists are only yours; public ones can be seen and played by every user of
  the app, but only you (or the admin) can change them.
- **Playing:** **Play all** or **Shuffle** on a playlist, or click any video in it to start there. The video page
  then shows the playlist beside the player with **Previous / Next** (the player itself has **⏭** too), **Shuffle**, **Repeat** (start over after
  the last video) and **Autoplay** (play the next one when a video ends — in fullscreen it stays fullscreen). It
  keeps going in the mini-player too.
  While a playlist plays, the player's own Loop button is off — use Repeat. Opening a video that isn't in the
  playlist ends the playlist.
- **Views:** list or grid (the buttons on the right, remembered per browser). Each video has the full ⋮ menu
  plus **Play from here** and **Remove from this playlist** (it stays in the library).
- **Cover:** a playlist's picture is a collage of its first 4 videos. On the playlist page, **Cover…** (if you can
  change the playlist) picks one of its videos' covers instead, or your own picture (converted to WebP, kept in
  `data/playlist_covers` and in every backup); **Collage** goes back.
- **Order:** drag the ⠿ handle (or a grid tile), or use ↑ / ↓ in the list. Removing a video from the library takes it out of every playlist; a duplicate removed on the
  Duplicates page is replaced by the copy you kept.

### Performers
Click a performer on a video's page to open her profile. If ThePornDB is set up (API key in **Server settings →
Credentials → ThePornDB**), the profile shows:
- photo, age, details, bio and aliases;
- similar performers you have, and all her videos (**Her videos in the library** opens them in the library).

How it works:
- **Matching:** each performer name is matched once to a ThePornDB profile, and that link is kept.
  - Clear matches link by themselves when the **Match & refresh performers** job runs (a System job on the
    Scheduler page, daily at 05:00 by default). **Match now** in Management → Maintenance → Performers runs it
    straight away.
  - Names that could be several people are listed in **Management → Maintenance → Performers**. Pick the right
    person there (or on her profile), search ThePornDB yourself, or **Skip** the name.
- **Aliases:** names ThePornDB lists as aliases of the same person (e.g. "Riley R" for Riley Reid) are treated as
  her. With **Server settings → Performers → merge aliases** on, they're shown under her main name everywhere.
  - If a name was merged wrongly, open the profile and use **Not her — split off** next to it. It's never merged
    into her again.
  - **Wrong person?** picks a different profile.
- **Not too fast:** the app sends ThePornDB at most one request a second. If ThePornDB still says "too many
  requests", the app waits as long as it asks, retries, and stays slower until it restarts.
- **Refreshing:** profiles and photos are kept locally. The same job refreshes each profile every 30 days (Server
  settings → Performers).
- **Photos as WebP:** photos are saved as WebP (quality 80, at most 1600 pixels on the long side), usually a
  third to two thirds smaller than the JPEG/PNG ThePornDB sends. Photos saved before 2.15.0: **Maintenance →
  Performers → Convert photos to WebP** converts them all once (in the background, on Tasks; it can be stopped
  and run again later — finished ones stay converted). Its result says how much space it freed.
- **Her photos** on the profile change by themselves every few seconds; to look through them yourself use the
  arrows on the sides, drag or swipe sideways, the dots, or ←/→ after clicking the photo. "2 / 5" shows where you are.
- **Performers page** (sidebar → Performers): everyone in your videos, one card per person (names linked to the same
  ThePornDB profile together). Performers matched to ThePornDB always come first, then the others under "Not on
  ThePornDB" with a grey placeholder (female by default; on their page **Placeholder: Female / Male** switches it,
  for people with the Performers permission). Find by name, sort by most videos / name / fewest videos, or show only
  one group; the choice is remembered in this browser.
  **Filters** narrows it by what ThePornDB says about them: gender, nationality, ethnicity, hair, eyes, cup size
  (several values each), age, height (cm) and weight (kg) ranges, and still active / enhanced breasts / tattoos /
  piercings (yes or no). Only details someone has are offered; while a detail is filtered on, performers without it are
  hidden. The number on the button counts the filters in use; **Clear filters** removes them.
- **Photos per performer:** up to 10 from ThePornDB, plus one of your own (11 at most).
- **Your own photo:** on a performer's profile, **Add your photo** (people with the Performers permission) adds a picture
  from your computer next to ThePornDB's — pick it or drop it on the panel; **Replace photo** swaps it (after asking). It's converted to WebP like the
  others (JPEG, PNG, WebP, GIF, BMP, TIFF; 30 MB at most; iPhone HEIC isn't readable — export it as JPEG). **Show
  first** makes one her main photo (also on similar-performer cards); **Unset first** puts ThePornDB's first again.
  They're kept when her profile refreshes, saved in the app's `data/performer_photos` folder, and included in every
  backup (they can't be downloaded again). Performers not on ThePornDB can have photos too (the first one replaces
  their placeholder); if they're matched to ThePornDB later, their photos move to the profile.

### Currently Playing
**Currently Playing** (sidebar) shows the videos being played right now, by anyone — a way to find what others are
enjoying. It updates by itself: a video appears when someone starts it and goes when they pause, finish or close the
tab (or after about 90 seconds without word from their player). It shows **only the videos**: never who is watching,
where they are in the video or since when; the same video played by two people shows once. Click one to watch it too.
It needs the **Currently Playing** permission (under *Watching*). New installs give it to the *user* role; on an
existing install the admin ticks it in **Users → Roles → user** (or for a group / person).

### Discover
**🎲 Discover** in the sidebar (the dice in the top bar on a phone) opens a random downloaded video; on a video's page,
**Discover again** (top right) opens another one. You won't get the same video twice until
you've been shown every video in the library; after that a new round starts, still skipping the 20% you saw
most recently. Each user has their own history.

### Help
**Help** (sidebar) has this guide, the changelog and **About**: the version, **Check now** for updates, a
prefilled bug-report link, server details and the license.

## Management

The **Server** section of the sidebar has **Tasks**, **Management** and **Settings** (the server's settings, called
Server settings below). Management's tabs are the pages below; each one shows for people with its permission (see *Who may do what*), the admin sees all:

| Page | What it does |
|---|---|
| Server settings | Tabs: **Download** (folders, downloads at once, retries), **Proxy** (where to use a proxy, speed test), **Re-encode** (codec, quality, CPU use), **Uploads** (what to convert files browsers can't play to, and whether to do it without asking), **Performers** (alias merging, how often profiles refresh), **Duplicate scan** (when it runs and how videos are compared), **Logging** (on/off, where to record, level, how long to keep, how addresses show), **Credentials**, **Connections** |
| Users | Create, edit, disable and remove accounts; roles, groups and per-person permissions |
| Duplicates | Duplicate groups found by the scan, side by side |
| Names | Every tag, performer and music name: rename, merge misspellings, likely typos, remembered fixes, upper/lower case spellings |
| Songs | Songs heard in videos, waiting to be added to their Music; find songs in videos without music; settings |
| Maintenance | Sync from metadata files, sync with PMVHaven, repair everything, untracked files |
| Scheduler | Run jobs on a schedule or immediately, with run history and live progress. Four **System** jobs are built in — Weekly backup, Check for updates (daily), Daily duplicate scan and Match & refresh performers (daily); they can be changed or disabled, not deleted |
| Backups | Back up and restore the database and settings |
| Stats | Live CPU / memory (this app and the whole computer) / network / disks with % free; library growth, activity, failures and watching over 7 days … all; the library's make-up |
| Logs | Everything the app does and every change made by a user |
| Tools | Status of FFmpeg, Python packages and other requirements |

**Users:** editing a user can rename, disable or set a new password (which signs them out everywhere).

**Who may do what (permissions, roles and groups).** Each feature is a permission — e.g. *Upload videos*, *Edit
details*, *Names*, *Remove videos*, *Stats*, *See Tasks*. Someone's permissions are everything they get from:
- their **role** — every account has one: **admin** (everything), **user** (browse, play, search, own settings),
  or a role you make (e.g. *moderator* with *Edit details*);
- the **groups** they're in — e.g. a group *Info helpers* with *Names*, so its members can fix tag and performer
  names;
- permissions given **to them directly** (Users → **Access** next to the person).

Nothing takes a permission away: they add up. Manage them under **Users → Roles** and **Groups** (create, edit,
delete; a role still in use can't be deleted) and **Access** per person (role, groups, own permissions, and what
they end up with). Changes apply on the person's next click — no need to sign in again; their menus update by
themselves. Only the admin manages users, roles, groups and permissions, and there is one admin. Pages and buttons
someone may not use aren't shown to them, and the server refuses them anyway.

**Logs** has search and filters (category, event, user, level, text). Click an entry for its full JSON
record; **Export** downloads the matching entries as JSON Lines. Client addresses are hashed by default.

**Server settings → Logging** switches logging off altogether (nothing is recorded anywhere) or chooses where it
goes — Management → Logs, the log file (`data/logs/backend.log`) and the console — the level (Debug, Info, Warning,
Error), when a new log file starts and how long old ones are kept, how long Management → Logs keeps entries, and how
client addresses are shown (pseudonym, last part only, full or not at all). Changes apply right away.

**Songs (finding the music in a video).** A video's ⋮ menu → **Find songs** listens to it: every 30 seconds a
12-second sample of its sound is recognised by **Shazam**; samples Shazam doesn't know go to **AudD** when its API
token is saved (Server settings → Credentials → AudD; **Test key** checks it — 300 free requests, then $5 per 1,000).
Only these short sound samples leave the server. What's heard is **suggested**, never added by itself:
- in the Find songs window, and in **Management → Songs** (all videos, newest first);
- each suggestion shows in how many samples it was heard and where (e.g. "3 of 8 samples at 0:35, 1:05, 1:35") —
  heard twice or more is the surest; a single hit is worth checking (**Open** goes to Shazam / AudD's page);
- **Add** puts it in the video's Music as "Song — Artist" (fix the name first if you like; it counts as your edit, so
  syncs keep it); **Dismiss** hides it for good for that video. Listening again doesn't bring back songs you added or
  dismissed, nor songs the video already lists. **Add all sure ones** adds every song heard twice or more.
- **Find songs now** listens to videos that have no music (and weren't listened to in the last 30 days), up to 20 per
  run, newest first. To do it regularly: **Scheduler → new job → Find songs in videos without music**. For picked
  videos: **Select** in the library → **Find songs**.
- **Try again a bit later:** a sample Shazam doesn't know is tried once more 7 seconds further on (often past a sound
  effect or a transition); the result says how many were retried. Turn it off in the settings to use fewer requests.
- Settings (Songs page, bottom): how often to sample, the most samples per video, videos per run, when to listen
  again, and AudD's use. One video is listened to at a time; Tasks shows it, with **Stop**. Starting one while another
scan runs puts it on Tasks as "Waiting for the song scan that's running"; it starts by itself after.

Shazam is used through *shazamio*, an unofficial client of Shazam's app — free and without a key, but Shazam can
limit it (the scan then stops with "Shazam is limiting requests"; try later) or change it (an app update with a newer
shazamio fixes that). Mash-ups, sped-up or pitched songs and songs under loud sound effects are found less often.

**Names** lists every tag, performer and music name with how many videos use it. **Rename / merge** changes a
name in every video that has it; typing an existing name merges into it (e.g. "abella danguer" → "abella danger").
With **Remember** (on by default) the misspelling is also kept as a fix, so it shows corrected when it turns up again
on a new download or a sync (**Remembered fixes** lists them; **Forget** stops that without changing any video).
**Possible typos** suggests names only a letter or two apart (numbers that differ, like "Vol 2" / "Vol 3", aren't
suggested) — merge either way, or mark them as different. A performer's ThePornDB link moves with her name.

**Bulk edit:** in the library, **Select** (next to the layout buttons), click videos to pick them (or **Select this
page**; picks are kept across pages), then **Add to playlists…** (tick one or more playlists, or make a new one with
the picked videos) or **Edit tags, performers, music…**: what you add goes on every picked video,
what you remove comes off the ones that have it. These count as your edits, so syncs don't undo them.
The same bar also has (depending on your permissions) **Find songs**, **Repair / update**, **Re-encode…** (pick the
codec and its settings) and **Share** (tick the destinations): they queue every picked video and show which were skipped and why
(already in that codec, already queued, file missing…).

**Names → Spellings** lists tags, performers and music written in more than one way (e.g. "Natural Tits"
and "Natural tits"). The app treats them as one and shows the spelling most videos use (on a tie, the newest one), so
it follows your library as new videos arrive; pick a spelling to always use that one, or **Automatic** to go back.
The videos themselves keep what PMVHaven wrote.

**Maintenance → Untracked files** finds files in your download and cache folders that no video in the
library uses, so you can delete them. Performer photos in use are not listed (before 2.15.0 they were); a photo
no performer uses any more is listed as "performer photo". A re-encode's work file (`…_temp.mp4`) isn't listed
while it's being written; one left over from an interrupted re-encode is listed as "unfinished re-encode". Seekbar
previews of videos no longer in the library are listed as "seekbar preview".

## Credentials

**Server settings → Credentials** holds everything the app needs to sign in to other services:

- **PMVHaven account:** email and password used by Connections.
- **PornTubeAI account / HMVHaven account:** "Use the PMVHaven account" is on by default. Turn it off to enter a
  different email and password for that site.
- **PMVHaven API:** your API key from pmvhaven.com/api-keys. **Test key** checks it with PMVHaven. The same key is
  used for porntubeai.com and hmvhaven.com.
- **External API:** the key the userscript sends. Use **Generate**, then **Show** to copy it. The userscript
  (`userscript/pmv-nexus.user.js`, install it in Tampermonkey or Violentmonkey) asks for the
  app's address and this key the first time. It keeps a list of what you've downloaded, refreshed once a day
  or from its menu (**Sync Downloaded List Now**), and marks videos on PMVHaven as Downloaded or Duplicate.
- **Telegram, Gofile, Pixeldrain:** share destinations. The **Sharing** switch at the top of the Credentials tab
  removes "Share" everywhere for everyone (whatever credentials are set) and refuses new shares; queued ones finish.
- **AudD:** optional API token for finding songs (see *Songs*).
- **VirusTotal:** optional API key (free account at virustotal.com) for the uploads' fingerprint check.

Passwords and keys are never shown again after saving; type a new value to replace one, or **Clear** to remove it.
Everything is stored encrypted in `data/secrets.db` with the key in `data/secret.key`, so keep the `data` folder
private.

**Telegram** additionally needs a sign-in: after saving the API ID and hash, open **Server settings → Connections →
Telegram → Sign in**, enter the phone number, the code Telegram sends, and the cloud password if two-step
verification is on. Telegram ends logins that aren't used for a long time; sharing then says so — **Sign in again**
there. (`python -m Tools.generate_telegram_session` in a terminal still works too.)

## Proxy

**Server settings → Proxy** sends some of the app's connections through a proxy (`socks5://host:port` or
`http://host:port`, with a username and password if it needs one):
- **PMVHaven, HMVHaven, PornTubeAI:**
  - **Not used**
  - **Split downloads:** downloads also run through the proxy, at the same time as the direct ones. With
    "Downloads at the same time" at 1 and one through the proxy, two videos download at once. More than 2 in total
    most likely gets downloads refused or slowed by the site.
  - **Everything:** pages, details, downloads and sign-in all go through the proxy.
- **Share uploads:** Telegram, Gofile and/or Pixeldrain through the proxy.
- **Test the proxy** before relying on it. It compares the direct connection with the proxy: does it work, which
  address the sites see, latency, whether PMVHaven answers, and download speed. It also tells you if the proxy is
  slow enough to hold things up.
- **Check the proxy before using it** (on by default) tests it at most every 5 minutes. While it fails, nothing is
  sent through it: downloads carry on over the direct connection, and uploads fail with a message rather than going
  out without the proxy.
- HLS videos can only go through an HTTP proxy. SOCKS proxies need the `socksio` package (in requirements.txt).

## Sync with PMVHaven

**Management → Maintenance → Sync with PMVHaven** compares your downloaded PMVHaven videos with the site's
current data:

- **Preview** lists what would change, without saving anything.
- **Sync now** applies the site's tags, performers, music, rating and description, and stores the site stats
  (views, likes, dislikes, favorites).
- Videos the site no longer returns are listed as *not found on the site* and flagged as removed.
- Video files, file names and titles are never changed. Title changes on the site are only reported.
- Your own edits (Edit details) are kept.
- Videos flagged as removed show a 💀 badge in the library and a notice on their page.
- The result lists changes per field. Tags marked *reordered* kept the same entries in a new vote ranking.

To run it regularly, add a **Sync library with PMVHaven** job on the Scheduler page. Videos from
porntubeai.com and hmvhaven.com are checked against their own site, with the same API key; if a site's API
can't be reached its videos are skipped (never marked removed) and the report says so. The sync works without
an API key, but uses your key from **Credentials** when one is saved.

## Duplicate detection

Run it from **Management → Duplicates → Scan now**. It also runs on its own:
- every day, from the **Daily duplicate scan** system job on the Scheduler page (change its time or disable it there);
- about a minute after a download, a re-encode or a repair finishes — each switched on or off under
  **Server settings → Duplicate scan → Scan automatically**. A batch (e.g. ten downloads) starts one scan after
  the last one, and only new or changed files are read.

Each group shows the copies side by side:
**Keep this one** removes the others (their files are deleted, the links stay known so they aren't
downloaded again), **Compare** plays them side by side, **Not duplicates** hides the group for good.

How a scan works:
1. Each video gets 10 small grayscale frames taken at fixed points (1/11, 2/11 … 10/11 of its length)
   plus an audio fingerprint. Only new or changed files are processed on later scans.
2. Videos are compared only with videos of a similar length (±5% by default).
3. Frames are compared position by position. Black, white or flat frames are ignored, so dark intros or
   fades cannot make unrelated videos look identical.
4. If the picture matches but both videos have audio that does not match, the pair is rejected:
   PMVs often reuse the same footage with different music.

| Verdict | Meaning |
|---|---|
| identical | The files are the same (same size and content) |
| duplicate | Same video, e.g. a re-encode or a different resolution |

Videos with too few usable frames (for example almost entirely black, or unreadable) are listed as
"unusable" in the scan status and are not compared.

The settings are on **Server settings → Duplicate scan** (or under `duplicate_scan` in `config.yaml`):

| Setting | Default | Meaning |
|---|---|---|
| `algorithm` | `grayscale` | `grayscale`, `phash` or `combined` (either one matching) |
| `percent` | `96` | Required similarity |
| `samples` | `10` | Frames per video |
| `phash_quorum` | `0.6` | pHash: share of frames that must match |
| `duration_tolerance_percent` | `5` | Maximum length difference |
| `use_audio` | `true` | Reject matches whose audio differs |
| `audio_min_similarity` | `70` | Audio similarity needed (unrelated audio is ~50) |
| `workers` | `2` | Videos processed in parallel |

Changing any of them makes the next scan compare everything again.

## Backups

**Management → Backups → Create backup now** saves the library database and your settings into a zip file
(default folder `data/backups`). You can also include:
- the **thumbnail / preview cache** (can be large)

Performer photos and playlist covers are always included (they can't be downloaded again).
- **secrets**: the encrypted credentials (with their key) and the saved site logins. Keep such backups private.

A weekly backup is a built-in System job on the Scheduler page; only the newest 10 backups are kept
(`backup.keep` in `config.yaml`).

**Backup password:** with a password set under **Backup password** on the same page, every new backup (manual or
scheduled) is an encrypted zip (AES-256) — marked 🔒 in the list. Nobody can open it without the password, the app
included; 7-Zip or WinRAR open it with it. The password can't be recovered, so keep it in a password manager.
Restore and **Test** use the saved password, and ask for it when it doesn't open a backup (e.g. one made with an
earlier password). Changing or removing the password doesn't change backups already made. Recommended when backups
include secrets or are copied off the computer.

**Restore test:** right after each backup is made, and with **Test** next to any backup, the app opens it like a
restore would — without changing anything — and checks that every file in the zip is intact, everything listed is
there and restores inside the app's folders, the database passes SQLite's integrity check (with its number of
videos, users and playlists), the settings can be read, and this version of the app can restore it. The result shows next to the backup; a failure is also
a notification. A **Restore test of the newest backup** job can be added on the Scheduler page.

### Restoring

**What a backup holds:** the library database (videos, their details and your edits, users, roles and groups,
playlists, watch history, stats, notifications), `config.yaml`, the settings changed in the app
(`data/settings_overrides.json`) and the performer photos you added. Optionally the **secrets** (credentials, their key, site and Telegram sign-ins)
and the **cache** (thumbnails and previews). **Never the video files themselves** — keep a copy of the download
folder yourself (another drive, a NAS).

**How a restore works:** click **Restore** next to a backup, then restart the app (`start.bat` or `run.py`). Until
the restart nothing changes, and **Cancel the restore** on the Backups page calls it off. At the start the backup is
put back before the database opens; every file it replaces is kept next to the original with a `.pre-restore`
suffix, so a restore can itself be undone by renaming those back. Files from inside the app folder go back there
(wherever the app now lives); files that were outside it — e.g. a cache folder on another drive — go back to the
same full path. Press **Test** first if you're unsure about a backup: it checks everything without changing
anything.

After a restore you sign in with the accounts and passwords **from the backup**.

**Backups from other versions:** a backup made by an older version (back to 1.2.0) restores as is — at the start
after the restore the app adds what's new to the database and reads settings saved under old names. A backup made
by a **newer** version is refused ("Update the app first") — update the app, then restore.

#### Scenario 1 — undo a mistake on the same computer
A bad bulk edit, a wrong merge in Names, deleted playlists…
1. **Create backup now** first (so today's state can be got back too).
2. **Restore** the newest backup made before the mistake, restart.
3. Anything done after that backup (new downloads, edits) is gone from the library — the video files of new
   downloads stay in the folder: **Maintenance → Untracked files** lists them, and adding them again by link
   picks up the existing file.

#### Scenario 2 — the database is damaged
Errors like "database disk image is malformed", or the app won't start after a crash or power cut.
1. Start the app if it starts at all (or skip to step 3).
2. **Management → Backups → Test** on the newest backup, then **Restore** it and restart.
3. If the app won't start: a restore can't be picked in the app, so copy the backup zip somewhere safe, rename the
   broken `data/library.db` to `library.db.broken`, start the app (it makes an empty library; create an admin account with
   the setup code, see *Start the app*), then restore from **Backups** as above — your old accounts come back with it.

#### Scenario 3 — a new computer, or reinstalling
1. On the old computer: **Create backup now** with **secrets** ticked (otherwise you'll type the credentials in
   again). Copy the zip from `data/backups` and the whole **download folder** to the new computer (or its drive).
2. Install the app on the new computer from the backup:
   - **Windows:** double-click `install.bat` and drag the backup zip into its window when it asks.
   - **Any OS:** install (see *Install*), then start once with `python run.py --restore path/to/the-backup.zip`.

   A backup with a password asks for it. Then the console shows the **folders from the backup's settings** and
   whether each exists here. One that can't be reached (another drive letter, a Windows path on Linux) must get a
   new place; type a number to change any other — e.g. the folder you copied the videos to. Enter keeps the list.
   The restored files go to the new places, `config.yaml` gets them, and at the start the app finds the videos,
   thumbnails and previews there. The app starts with everything from the backup — sign in with your old account
   (no setup code needed), on the port in the backup's `config.yaml`.

   Or: start the new install once, create a temporary admin with the setup code, put the zip into `data/backups`,
   then **Test** and **Restore** it on **Management → Backups** and restart.
4. **Folders:** done in step 2 for the `install.bat` / `--restore` way. With the other way, the restart shows the
   same folder list in the console. Changing them later in the app also works: open **Server settings → Download**, set the download folder (and the cache folder) to the new place
   with **Move the videos already downloaded** turned **off**: the app finds the videos there by the same names
   and links them back (the message says how many). Same for the cache folder (**Move the files already cached**
   off), or let the thumbnails and previews be made
   again (Maintenance → Repair everything).
5. Check **Tools** (FFmpeg and the Python packages on the new computer) and, if you didn't include secrets,
   **Server settings → Credentials**. Telegram sign-ins may have to be done again.

Moving between Windows and Linux works the same way; only the folder paths in step 4 change.

#### Scenario 4 — only the settings or credentials
A backup always restores everything it has, so there's no partial restore. To move only credentials, use a backup
with secrets on a fresh install, or type them in again. To go back to the default settings, delete
`data/settings_overrides.json` while the app is stopped.

#### Good habits
- Keep the weekly backup job on, and add **Restore test of the newest backup** to the Scheduler.
- Copy backups off the computer now and then — a backup on the same disk doesn't survive that disk failing.
- Backups with secrets hold the key to your credentials: set a backup password, or keep them private.

## Security

Made for a home network or VPN. What the app does to stay safe there:
- **Sign-in:** after 5 wrong passwords for an account (or 20 from one address) within 15 minutes, signing in waits a
  minute, doubling each time up to 30 minutes. A wrong username takes as long as a wrong password, so names can't be
  guessed from the timing.
- **Requests from other sites** (including other web apps on the same computer) can't change anything here.
- **Headers:** a strict Content-Security-Policy (only this app's own scripts), no framing, no file-type guessing,
  no referrer to other sites; the server doesn't say what software it runs; API answers aren't cached.
- **Regular users** see file names, not the server's folders, and not who added a video; the exact Python / OS /
  FFmpeg versions, Tasks and the duplicate scan are for admins. `/api/health` (no sign-in) says only what's ok.
- **Uploads:** see *Uploading your own videos* (type check, contents check, virus scan, re-writing, one at a time).
- **API documentation** (`/docs`, `/redoc`, `/openapi.json`) is for the admin only; anyone else gets a "no
  permission" page that takes them back to the library after 5 seconds.
- **Favorites** are private to their owner. Request bodies (which can hold passwords) are never logged.

## Updates

The version is shown under the app name in the sidebar. **Help → About → Check now** looks for a newer
version on GitHub; the daily "Check for updates" system job also runs automatically.

To update, download the new version (or `git pull`), then run `pip install -r requirements.txt` and
restart the app. Browser tabs that were open during the update show a **Reload** notice.

## Troubleshooting

- **The app does not start:** run it from a terminal (`python run.py`) to see the error, and check the
  **Management → Tools** page.
- **Forgot the admin password:** stop the app and run
  `python -m backend.create_admin --reset-password --username <name> --password <new>`.
- **Downloads fail with "The site didn't give the file (HTTP 403)":** the site doesn't offer that video for download
  (or it's private or removed). Check the video on the site; with your API key saved, private videos you can see
  may work.
- **"The site's API didn't return this video":** the link's video isn't known to the API (removed, private, a
  link from another site) or the site is down.
- **Logs:** `data/logs`.
- **Still stuck?** Use the bug-report link under **Help → About**.
