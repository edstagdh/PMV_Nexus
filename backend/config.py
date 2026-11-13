from __future__ import annotations

import os
import re
from pathlib import Path
from functools import lru_cache
import json
import shutil

import yaml
from dotenv import load_dotenv

from . import secrets_store
from pydantic import BaseModel, Field, model_validator

ROOT_DIR = Path(__file__).resolve().parent.parent
# Legacy: .env is loaded only so secrets_store can import old values once on
# upgrade. Credentials are managed in Admin -> Credentials (secrets_store.py).
load_dotenv(ROOT_DIR / ".env")
OVERRIDES_FILE = ROOT_DIR / "data" / "settings_overrides.json"


def _load_overrides() -> dict:
    if OVERRIDES_FILE.exists():
        try:
            return json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_overrides(**sections: dict) -> None:
    """Persists user-editable settings as an overlay on top of config.yaml.
    Call as save_overrides(download={"output_dir": "/new/path"}). Merges
    shallowly per section and clears the settings cache so the next
    get_settings() call picks up the change."""
    OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)
    current = _load_overrides()
    for section, values in sections.items():
        current.setdefault(section, {}).update(values)
    OVERRIDES_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")
    get_settings.cache_clear()


class SiteConfig(BaseModel):
    """One PMVHaven-platform site's connection details -- PMVHaven itself,
    or a sister site like porntubeai.com. See backend/sites.py for how a
    video URL is matched to one of these by base_url's domain."""
    base_url: str
    cdn_base: str
    login_path: str = "/login"
    label: str = ""  # display name ("PMVHaven"); defaults to the key


# Sites on the PMVHaven platform the app knows out of the box. config.yaml's
# `sites:` block can override any of these or add more (same keys merge).
_PLATFORM_CDN = "https://pmvhavencloud.s3.eu-west-par.io.cloud.ovh.net/"
DEFAULT_SITES: dict[str, dict] = {
    "pmvhaven": {"base_url": "https://pmvhaven.com", "cdn_base": _PLATFORM_CDN, "label": "PMVHaven"},
    "porntubeai": {"base_url": "https://porntubeai.com", "cdn_base": _PLATFORM_CDN, "label": "PornTubeAI"},
    "hmvhaven": {"base_url": "https://hmvhaven.com", "cdn_base": _PLATFORM_CDN, "label": "HMVHaven"},
}
# Sister sites whose login can be the PMVHaven account or their own one
# (Credentials page). PMVHaven itself always uses the PMVHaven account.
SISTER_SITES = ("porntubeai", "hmvhaven")


class DownloadConfig(BaseModel):
    output_dir: str = "./downloads"
    cache_dir: str = "./data/cache"
    session_dir: str = "./data/pmv_session"
    library_file: str = "./data/library.json"
    library_db_file: str = "./data/library.db"
    max_concurrent_downloads: int = 1
    max_concurrent_metadata_fetches: int = 3
    generated_preview_num_clips: int = 5
    generated_preview_clip_duration_seconds: float = 2.0
    generated_preview_max_height: int = 480
    generated_preview_fps: int = 24
    max_retries: int = 3
    chunk_size_kb: int = 512
    download_funscripts: bool = True
    save_metadata_json: bool = True
    min_free_percent: int = 10  # disk-space guard: downloads stop below this much free space (0 = off)
    # 2.27.0: PMVHaven & sister sites are used through their API only (details + the
    # /api/videos/<id>/download endpoint). True = also read the video's web page when the
    # API can't help (the old way; mind the site's terms, which forbid scraping).
    website_fallback: bool = False


class AuthConfig(BaseModel):
    session_ttl_hours: int = 720


LOG_LEVELS = ("TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL")
# loguru's rotation ("<n> <time unit>" or "<n> KB/MB/GB") and retention ("<n> <time unit>" or "forever").
LOG_ROTATION_PATTERN = r"^(\d{1,4} (second|minute|hour|day|week|month|year)s?|\d{1,6} (KB|MB|GB))$"
LOG_RETENTION_PATTERN = r"^(forever|\d{1,4} (second|minute|hour|day|week|month|year)s?)$"


class LoggingConfig(BaseModel):
    """Admin -> Server settings -> Logging (applied at once, no restart).
    `enabled` False records nothing anywhere; otherwise each output can be
    switched on its own: the console, the rotated text file (backend.log)
    and the searchable copy behind Admin -> Logs."""
    log_dir: str = "./data/logs"  # config.yaml only
    enabled: bool = True
    console: bool = True
    file: bool = True
    app_log: bool = True
    rotation: str = Field("1 month", pattern=LOG_ROTATION_PATTERN)
    retention: str = Field("12 months", pattern=LOG_RETENTION_PATTERN)
    level: str = "INFO"
    # Admin -> Logs (searchable copy in data/logs/app_logs.db).
    app_log_retention_days: int = 90
    app_log_max_rows: int = 500_000
    # How client IP addresses are recorded in Admin -> Logs:
    # hashed (default) | masked | full | off -- see backend/ip_privacy.py.
    ip_mode: str = "hashed"

    @model_validator(mode="before")
    @classmethod
    def _clean(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            level = str(data.get("level", "INFO")).upper()
            data["level"] = level if level in LOG_LEVELS else "INFO"
            for key, default, pattern in (("rotation", "1 month", LOG_ROTATION_PATTERN),
                                          ("retention", "12 months", LOG_RETENTION_PATTERN)):
                value = data.get(key)
                if value is not None and not re.match(pattern, str(value).strip()):
                    data[key] = default  # a hand-edited config.yaml value this app doesn't offer
        return data


HEVC_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")


class PlaybackConfig(BaseModel):
    """Converting while watching (backend/transcode.py; Server settings -> Re-encode):
    a video is streamed as HLS made on the fly, at most `max_height` lines, when
    the viewer picks 720p in the player or the browser can't play the file."""
    live_convert: bool = True
    max_height: int = Field(720, ge=240, le=720)    # 720 or 480 in the interface
    cpu_percent: int = Field(50, ge=10, le=100)     # of the cores, per conversion
    max_sessions: int = Field(2, ge=1, le=8)        # conversions at the same time


class FeaturesConfig(BaseModel):
    """Whole features that can be switched off (2.22.0). Off = gone from every menu,
    button and page, and the server refuses new requests (what's already queued
    still finishes). Server settings -> Re-encode / Credentials."""
    reencode: bool = True


class SongsConfig(BaseModel):
    """Song recognition (backend/song_recognition.py, library_mixins/songs.py;
    Management -> Songs)."""
    interval: int = Field(30, ge=15, le=300)          # a sample every N seconds of the video
    max_samples: int = Field(12, ge=1, le=40)         # per video
    shazam_retry: bool = True                         # a sample Shazam missed is tried again 7 s later (2.18.0)
    audd_fallback: bool = True                        # try AudD (key in Credentials) on samples Shazam missed
    audd_max_samples: int = Field(4, ge=1, le=20)     # AudD lookups per video
    per_run: int = Field(20, ge=1, le=500)            # videos per Scheduler run
    rescan_days: int = Field(30, ge=0, le=3650)       # look at a video without music again after this (0 = never)


class ReencodeConfig(BaseModel):
    """Re-encode settings (Admin -> Server settings -> Re-encode). Each codec
    keeps its own quality/preset: HEVC uses x265 (CRF 0-51, named presets),
    AV1 uses SVT-AV1 (CRF 1-63, presets 0 = slowest/best ... 13 = fastest)."""
    codec: str = Field("hevc", pattern="^(hevc|av1)$")
    target_crf: int = Field(24, ge=0, le=51)  # HEVC
    preset: str = "medium"  # HEVC
    av1_crf: int = Field(32, ge=1, le=63)
    av1_preset: int = Field(8, ge=0, le=13)
    warn_crf: bool = True
    copy_audio: bool = True
    downscale: bool = True
    # CPU use while encoding: off, a number of cores, or a share of them.
    cpu_limit_mode: str = Field("percent", pattern="^(off|cores|percent)$")
    cpu_limit_cores: int = Field(4, ge=1, le=1024)
    cpu_limit_percent: int = Field(70, ge=5, le=100)
    remove_chapters: bool = False
    keep_original_file: bool = False
    # Re-encode… opens a window to pick the codec and change the settings (2.22.0).
    # Off (2.22.1): it queues straight away with the codec and settings above.
    ask_each_time: bool = True

    @model_validator(mode="before")
    @classmethod
    def _migrate(cls, data):
        # Before 2.1.0: limit_cpu_usage (bool) meant "about 70% of the cores".
        if isinstance(data, dict) and "cpu_limit_mode" not in data and "limit_cpu_usage" in data:
            data = {**data, "cpu_limit_mode": "percent" if data["limit_cpu_usage"] else "off", "cpu_limit_percent": 70}
        if isinstance(data, dict) and data.get("preset") not in (None, *HEVC_PRESETS):
            data = {**data, "preset": "medium"}
        return data

    def cpu_cores(self) -> int | None:
        """How many cores an encode may use, or None for no limit."""
        total = os.cpu_count() or 4
        if self.cpu_limit_mode == "cores":
            return max(1, min(total, self.cpu_limit_cores))
        if self.cpu_limit_mode == "percent":
            return max(1, min(total, round(total * self.cpu_limit_percent / 100)))
        return None

    def codec_params(self, codec: str | None = None) -> tuple[str, int, str]:
        """(codec, crf, preset) for `codec` (default: the configured one)."""
        codec = codec or self.codec
        if codec == "av1":
            return "av1", self.av1_crf, str(self.av1_preset)
        return "hevc", self.target_crf, self.preset


class UploadConfig(BaseModel):
    """Upload page (Admin -> Server settings -> Uploads): what happens to a
    file browsers can't play. Videos already in MP4 with a browser codec are
    kept as they are; others are first re-packed into MP4 without touching
    the picture (fast, lossless). Only when that's not possible is the video
    re-encoded -- to `convert_codec`, after asking, or straight away with
    `auto_convert`. HEVC/AV1 use the Re-encode tab's quality and preset;
    AVC (x264) has its own here."""
    convert_codec: str = Field("h264", pattern="^(h264|hevc|av1)$")
    h264_crf: int = Field(20, ge=0, le=51)
    h264_preset: str = "medium"
    auto_convert: bool = False
    # Antivirus check of each uploaded file (backend/upload_safety.py): "auto" =
    # when Microsoft Defender or ClamAV is on the server, "required" = refuse
    # uploads without one, "off".
    virus_scan: str = Field("auto", pattern="^(auto|required|off)$")
    # 2.12.0: look the file's SHA-256 up on VirusTotal (needs its API key in Credentials):
    # "off", "on" (known-bad files are refused; unknown ones pass), "required" (refuse when it can't be checked).
    hash_check: str = Field("off", pattern="^(off|on|required)$")
    max_size_gb: int = Field(50, ge=1, le=1000)  # largest file accepted
    max_waiting: int = Field(10, ge=1, le=100)  # unfinished uploads one person may have

    @model_validator(mode="before")
    @classmethod
    def _check_preset(cls, data):
        if isinstance(data, dict) and data.get("h264_preset") not in (None, *HEVC_PRESETS):
            data = {**data, "h264_preset": "medium"}
        return data

    def params(self, reencode: "ReencodeConfig", codec: str | None = None) -> tuple[str, int, str]:
        """(codec, crf, preset) defaults for converting an upload."""
        codec = codec or self.convert_codec
        if codec == "h264":
            return "h264", self.h264_crf, self.h264_preset
        return reencode.codec_params(codec)


class DuplicateScanConfig(BaseModel):
    """Score-based duplicate-detection background scan (see
    backend/library_mixins/duplicate_worker.py). auto_enabled gates BOTH
    the once-daily scheduled sweep AND the automatic trigger fired after
    every download/re-encode -- while False (the default), the whole
    pipeline only ever runs when explicitly requested via the Admin
    Portal Issues page's "Run scan now" button, never on its own.

    Since 2.1.0 the recurring scan is a Scheduler "system" job and the
    event triggers are separate flags (scan_after_*); the old auto_enabled
    is migrated to them.

    The remaining fields tune the VideoDuplicateFinder-style matcher (see
    backend/dupfinder.py). Changing any of them makes the next scan clear
    old matches and re-compare everything (changing `samples` or `use_audio`
    also re-samples the files)."""
    # Scan for duplicates shortly after these events (coalesced: several
    # events in a row start one scan, about a minute after the last one).
    scan_after_download: bool = True
    scan_after_reencode: bool = True
    scan_after_repair: bool = False
    # "grayscale" (VDF default) | "phash" | "combined" (either matches)
    algorithm: str = Field("grayscale", pattern="^(grayscale|phash|combined)$")
    # Required similarity, in percent (VDF default 96).
    percent: float = Field(96.0, ge=80.0, le=100.0)
    # Frames sampled per video, at relative positions i/(N+1).
    samples: int = Field(10, ge=4, le=30)
    # phash mode: fraction of positions that must individually pass.
    phash_quorum: float = Field(0.6, ge=0.1, le=1.0)
    # Max duration difference for two files to be compared at all.
    duration_tolerance_percent: float = Field(5.0, ge=0.0, le=50.0)
    # Veto visual matches whose (audible) audio does not match.
    use_audio: bool = True
    audio_min_similarity: float = Field(70.0, ge=55.0, le=100.0)
    # Files sampled in parallel (each runs a few short ffmpeg processes).
    workers: int = Field(2, ge=1, le=16)

    @model_validator(mode="before")
    @classmethod
    def _migrate(cls, data):
        # Before 2.1.0 one auto_enabled flag covered every automatic scan.
        triggers = ("scan_after_download", "scan_after_reencode", "scan_after_repair")
        if isinstance(data, dict) and "auto_enabled" in data and not any(t in data for t in triggers):
            data = {**data, **{t: bool(data["auto_enabled"]) for t in triggers}}
        return data


class TrayConfig(BaseModel):
    """Opening the app in a browser (the tray icon, the Windows shortcuts; 2.29.0).
      browser      -- the program "Open PMV Nexus" uses, with arguments -- e.g.
                      "C:/Program Files/Mozilla Firefox/firefox.exe" -P Videos --new-window {url} --
                      empty = the default browser. {url} is the app's address (added at the end
                      when missing).
      open_browser -- when a shortcut (or Setup's "Open PMV Nexus now") STARTS the app, also open it
                      in the browser (2.31.0). Off: it just starts (tray icon). Clicking a shortcut
                      while the app already runs always opens it.
    Set in Setup, config.yaml or Server settings -> Opening the app (the browser only from the
    computer the app runs on)."""
    browser: str = Field("", max_length=2000)
    open_browser: bool = True


class BackupConfig(BaseModel):
    """Backups of the database + settings (see backend/backup.py). `dir` is
    relative to the project root unless absolute; `keep` is how many backup
    zips to retain (oldest are pruned after each new backup)."""
    dir: str = "./data/backups"
    keep: int = Field(10, ge=1, le=500)
    include_cache: bool = False


class PerformersConfig(BaseModel):
    """Admin -> Server settings -> Performers (ThePornDB; API key in Credentials).
      merge_aliases -- names ThePornDB lists as aliases of the same performer
                       show as one (her main name) everywhere; a wrong merge
                       can be split on her profile page.
      (Matching new names and refreshing profiles is the Scheduler system job
      "Match & refresh performers".)
      refresh_days  -- re-read each matched profile this often."""
    merge_aliases: bool = True
    refresh_days: int = Field(30, ge=7, le=365)


class ProxyConfig(BaseModel):
    """Admin -> Server settings -> Proxy. The proxy's address and login are
    credentials (encrypted store: proxy_url / proxy_username / proxy_password);
    this is where it's used.
      site_mode  off   -- the sites are reached directly;
                 split -- downloads run on two kinds of connection at once:
                          the direct one plus `proxy_downloads` through the
                          proxy (more downloads at the same time);
                 all   -- every request to the sites (pages, metadata,
                          downloads, sign-in) goes through the proxy.
      uploads  -- extensions' uploads go through the proxy (2.30.0; was a
                 switch per destination: any of them on = on).
      check_before_use -- test the proxy (cached for a few minutes) before
                 it's used; while it fails, nothing is sent through it."""
    site_mode: str = Field("off", pattern="^(off|split|all)$")
    proxy_downloads: int = Field(1, ge=1, le=3)
    uploads: bool = False
    check_before_use: bool = True

    @model_validator(mode="before")
    @classmethod
    def _legacy_upload_switches(cls, data):
        """Before 2.30.0 there was a switch per upload destination; any of them on = uploads on."""
        if isinstance(data, dict) and "uploads" not in data:
            legacy = [data.get(k) for k in list(data) if k not in cls.model_fields]
            if any(v is True for v in legacy):
                data = {**data, "uploads": True}
        return data
    # From the encrypted credential store, not config.yaml / overrides.
    url: str | None = None
    username: str | None = None
    password: str | None = None

    def address(self) -> str | None:
        """The proxy as a URL with its login, e.g. socks5://user:pass@host:1080 (None if not set)."""
        from urllib.parse import quote, urlsplit, urlunsplit
        if not self.url:
            return None
        raw = self.url.strip()
        if "://" not in raw:
            raw = "http://" + raw
        parts = urlsplit(raw)
        if not parts.hostname:
            return None
        if self.username and not parts.username:
            login = quote(self.username, safe="") + (":" + quote(self.password or "", safe="") if self.password else "")
            netloc = f"{login}@{parts.hostname}" + (f":{parts.port}" if parts.port else "")
            parts = parts._replace(netloc=netloc)
        return urlunsplit(parts)


class Settings(BaseModel):
    # Every configured site (PMVHaven, porntubeai.com, ...), keyed by a
    # short id (e.g. "pmvhaven", "porntubeai") -- see backend/sites.py for
    # how a video URL is resolved to one of these keys. Was a single
    # `site: SiteConfig` field before multi-site support was added; if you
    # have an old config.yaml with a `site:` (singular) block, rename it to
    # `sites: {pmvhaven: {...}}` -- see config.yaml's own comments.
    sites: dict[str, SiteConfig]
    download: DownloadConfig
    auth: AuthConfig
    logging: LoggingConfig
    reencode: ReencodeConfig
    upload: UploadConfig
    playback: PlaybackConfig = PlaybackConfig()
    songs: SongsConfig = SongsConfig()
    features: FeaturesConfig = FeaturesConfig()
    proxy: ProxyConfig
    performers: PerformersConfig
    duplicate_scan: DuplicateScanConfig
    backup: BackupConfig
    tray: TrayConfig = TrayConfig()

    # --- credentials, from the encrypted store (secrets_store.py) ---
    #
    # NOTE: this app used to gate its own login on a single hardcoded
    # APP_USERNAME/APP_PASSWORD pair read straight from .env. That's been
    # replaced by a proper multi-user, DB-backed accounts system (see
    # backend/db.py's users/roles tables and backend/security.py) --
    # APP_USERNAME/APP_PASSWORD no longer exist as settings at all. If
    # you're upgrading from an older version, you can remove those two
    # lines from your .env; they're simply never read anymore.
    app_secret_key: str
    # Shared PMVHaven-account credentials -- confirmed to work identically
    # for logging into any site on this platform (pmvhaven.com,
    # porntubeai.com), so there's just one email/password pair rather than
    # one per site.
    pmv_email: str | None
    pmv_password: str | None
    # Sister sites: (email, password) each one logs in with -- the PMVHaven
    # account unless "Use the PMVHaven account" is off for that site.
    site_logins: dict[str, tuple[str | None, str | None]] = {}
    app_api_key: str | None
    # PMVHaven REST API key (Bearer pmvh_...), see pmvhaven.com/api-keys.
    pmvhaven_api_key: str | None = None
    tpdb_api_key: str | None = None  # ThePornDB (performer profiles)
    audd_api_key: str | None = None  # song recognition fallback (song_recognition.audd_lookup)
    virustotal_api_key: str | None = None  # upload fingerprint lookups (upload_safety.hash_lookup)

    # One-time bootstrap credentials for the FIRST admin account -- read
    # only by security.py's ensure_bootstrap_admin(), and only while the
    # `users` table is still completely empty (a fresh install). Once that
    # first admin exists, these are never consulted again and can be
    # deleted from .env. Deliberately optional here (not validated at
    # config-load time) -- whether they're actually REQUIRED depends on
    # whether any user already exists in the database, which config.py has
    # no way to know; that conditional check lives in
    # ensure_bootstrap_admin() instead, called once at server startup.
    initial_admin_username: str | None
    initial_admin_password: str | None

    def login_for(self, site_key: str) -> tuple[str | None, str | None]:
        return self.site_logins.get(site_key, (self.pmv_email, self.pmv_password))

    @property
    def output_dir(self) -> Path:
        p = (ROOT_DIR / self.download.output_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cache_dir(self) -> Path:
        p = (ROOT_DIR / self.download.cache_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def session_dir(self) -> Path:
        """Root folder for saved login sessions. Each site gets its own
        subfolder under this (e.g. data/pmv_session/pmvhaven/,
        data/pmv_session/porntubeai/) -- see PMVClient.storage_state_file
        in pmv_client.py, since different domains need independent cookie
        jars even when they share login credentials."""
        p = (ROOT_DIR / self.download.session_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def library_file(self) -> Path:
        p = (ROOT_DIR / self.download.library_file).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def library_db_file(self) -> Path:
        """Resolves to the new library.db path -- but if this is the first
        run after the queue->library rename and an old data/queue.db exists
        with no data/library.db yet, transparently rename it in place first
        so existing downloaded items aren't orphaned by the rename."""
        p = (ROOT_DIR / self.download.library_db_file).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        legacy_p = ROOT_DIR / "data" / "queue.db"
        if not p.exists() and legacy_p.exists():
            legacy_p.rename(p)
            # WAL/SHM sidecar files, if present, belong with the main file
            for suffix in ("-wal", "-shm"):
                legacy_side = legacy_p.with_name(legacy_p.name + suffix)
                if legacy_side.exists():
                    legacy_side.rename(p.with_name(p.name + suffix))
        return p

    @property
    def backup_dir(self) -> Path:
        p = (ROOT_DIR / self.backup.dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def log_dir(self) -> Path:
        p = (ROOT_DIR / self.logging.log_dir).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p


def _load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _ensure_config_file() -> Path:
    """config.yaml is the user's own copy and is not in git; the first start
    creates it from the committed template, config.example.yaml."""
    path = ROOT_DIR / "config.yaml"
    template = ROOT_DIR / "config.example.yaml"
    if not path.exists() and template.exists():
        shutil.copyfile(template, path)
    return path


@lru_cache
def get_settings() -> Settings:
    raw = _load_yaml(_ensure_config_file())
    overrides = _load_overrides()
    download_raw = {**raw.get("download", {}), **overrides.get("download", {})}

    # Credentials come from the encrypted store (Admin -> Credentials), not
    # the environment -- see backend/secrets_store.py.
    secrets_store.ensure_initialized()
    creds = secrets_store.values()

    # `sites` (plural, a mapping of site-key -> {base_url, cdn_base,
    # login_path}) replaces the old singular `site:` block. Back-compat:
    # if an old config.yaml still has `site:` and no `sites:` at all,
    # treat that single block as "pmvhaven" so existing installs don't
    # break on upgrade without editing config.yaml by hand.
    sites_raw = raw.get("sites")
    if sites_raw is None:
        legacy_site = raw.get("site")
        # Neither block: the built-in DEFAULT_SITES are used as they are.
        sites_raw = {"pmvhaven": legacy_site} if legacy_site else {}
    merged_sites = {key: dict(value) for key, value in DEFAULT_SITES.items()}
    for key, value in (sites_raw or {}).items():
        merged_sites[key] = {**merged_sites.get(key, {}), **(value or {})}
    sites = {key: SiteConfig(**value) for key, value in merged_sites.items()}

    site_logins = {}
    for key in SISTER_SITES:
        if (creds.get(f"{key}_same_account") or "1") == "1":
            site_logins[key] = (creds["pmvhaven_email"], creds["pmvhaven_password"])
        else:
            site_logins[key] = (creds.get(f"{key}_email"), creds.get(f"{key}_password"))

    duplicate_scan_raw = {**raw.get("duplicate_scan", {}), **overrides.get("duplicate_scan", {})}

    return Settings(
        sites=sites,
        download=DownloadConfig(**download_raw),
        auth=AuthConfig(**raw.get("auth", {})),
        logging=LoggingConfig(**{**raw.get("logging", {}), **overrides.get("logging", {})}),
        reencode=ReencodeConfig(**{**raw.get("reencode", {}), **overrides.get("reencode", {})}),
        upload=UploadConfig(**{**raw.get("upload", {}), **overrides.get("upload", {})}),
        playback=PlaybackConfig(**{**raw.get("playback", {}), **overrides.get("playback", {})}),
        songs=SongsConfig(**{**raw.get("songs", {}), **overrides.get("songs", {})}),
        features=FeaturesConfig(**{**raw.get("features", {}), **overrides.get("features", {})}),
        performers=PerformersConfig(**{**raw.get("performers", {}), **overrides.get("performers", {})}),
        proxy=ProxyConfig(**{**raw.get("proxy", {}), **overrides.get("proxy", {}),
                             "url": creds.get("proxy_url"), "username": creds.get("proxy_username"),
                             "password": creds.get("proxy_password")}),
        duplicate_scan=DuplicateScanConfig(**duplicate_scan_raw),
        backup=BackupConfig(**{**raw.get("backup", {}), **overrides.get("backup", {})}),
        tray=TrayConfig(**{**(raw.get("tray") or {}), **overrides.get("tray", {})}),
        app_secret_key=creds["app_secret_key"],
        app_api_key=creds["app_api_key"],
        pmv_email=creds["pmvhaven_email"],
        pmv_password=creds["pmvhaven_password"],
        site_logins=site_logins,
        pmvhaven_api_key=creds["pmvhaven_api_key"],
        tpdb_api_key=creds.get("tpdb_api_key"),
        virustotal_api_key=creds.get("virustotal_api_key"),
        audd_api_key=creds.get("audd_api_key"),
        initial_admin_username=os.getenv("INITIAL_ADMIN_USERNAME"),
        initial_admin_password=os.getenv("INITIAL_ADMIN_PASSWORD"),
    )
