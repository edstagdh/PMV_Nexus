from __future__ import annotations

import time
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


class RenameRequest(BaseModel):
    new_name: str


class DownloadDirRequest(BaseModel):
    new_dir: str
    move_existing: bool = True


# Fields of LibraryItem the user can edit by hand (flags live only in the
# metadata file, so they are kept in user_edits alone).
EDITABLE_ITEM_FIELDS = ("tags", "performers", "music")
EDITABLE_DETAIL_FIELDS = (*EDITABLE_ITEM_FIELDS, "flags")


class LibraryItem(BaseModel):
    # Fields this version doesn't know (e.g. saved by an extension, or by an
    # earlier version) are kept as they are, not dropped on the next save (2.30.0).
    model_config = ConfigDict(extra="allow")

    id: str
    url: str
    # Which configured site (see backend/sites.py) this video came from --
    # "pmvhaven", "porntubeai", etc. Determined once at add() time from the
    # URL's domain and never changes after that. Older items from before
    # multi-site support default to "pmvhaven" (the only site that existed
    # then) via this field default, so existing library.db rows load fine
    # without a migration step.
    site: str = "pmvhaven"
    status: str = "fetching"
    # If this item was resolved as a duplicate via the Issues tab's merge
    # action, this holds the id of the item it was merged INTO, and status
    # becomes "merged" -- everything else about this item is cleared (see
    # LibraryManager.merge_items()), except id/url/site/created_at/
    # updated_at, so that re-adding the same URL later is recognized as
    # already-handled instead of being silently re-downloaded (see
    # LibraryManager.add()).
    merged_into: Optional[str] = None
    # Id of the user who added it (2.10.0) -- who gets its notifications;
    # None (older videos, the external API) = the admins.
    added_by: Optional[str] = None
    title: str = ""
    creator: str = ""
    progress_percent: Optional[float] = None
    progress_label: str = ""
    speed: str = ""
    error: Optional[str] = None
    attempts: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    # ISO-8601 UTC timestamp set once the download completes. Backfilled with
    # the migration date (as a plain "YYYY-MM-DD") for items that existed
    # before this field was added, since their real download date wasn't tracked.
    download_datetime: Optional[str] = None
    video_path: Optional[str] = None
    funscript_path: Optional[str] = None
    metadata_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    # Separate from `updated_at` -- bumped ONLY when thumbnail_path is
    # actually set/replaced (initial download, thumbnail picker, or
    # repair), never by unrelated progress ticks (download %, re-encode
    # %) that also call item.touch(). The frontend uses
    # THIS field (not updated_at) for thumbnail cache-busting -- see
    # views/list.js, views/grid.js, views/table.js -- so a currently-
    # running item's thumbnail doesn't get needlessly torn down and
    # reloaded (flickering) on every 2s poll just because its progress
    # changed. None for items that predate this field; the frontend falls
    # back to 0 in that case, which is stable (no flicker) and simply
    # means no additional cache-busting happens until the next real
    # thumbnail change, when this field starts getting set.
    thumbnail_updated_at: Optional[float] = None
    # Set whenever the preview clip is replaced (repair, 10-bit conversion, ...),
    # so the interface asks for the new file instead of a cached one.
    preview_updated_at: Optional[float] = None
    preview_path: Optional[str] = None
    reencode_status: Optional[str] = None  # None | queued | encoding | done | failed
    reencode_progress_percent: Optional[float] = None
    reencode_eta_seconds: Optional[int] = None
    reencode_speed: Optional[str] = None
    reencode_error: Optional[str] = None
    # Consecutive re-encode FAILURES for this item (reset to 0 on a
    # successful encode) -- NOT total attempts ever made. Once this
    # reaches 2 (i.e. the upcoming attempt would be the 3rd in a row),
    # reencode_worker.py automatically escalates to a corruption-tolerant
    # recovery pipeline instead of a normal encode -- see
    # media_tools.reencode_video()'s tolerant= parameter and
    # ReencodeWorkerMixin._reencode_item()'s docstring for the full
    # escalation sequence.
    reencode_attempts: int = 0
    # Codec the queued/last re-encode targets ("hevc" | "av1"); None = the
    # configured default at the time it runs.
    reencode_target: Optional[str] = None
    # Settings for this one re-encode, chosen in the Re-encode dialog (2.22.0):
    # {crf, preset, copy_audio, downscale, remove_chapters, keep_original_file};
    # what's missing comes from Server Settings -> Re-encode.
    reencode_options: Optional[dict] = None
    repair_status: Optional[str] = None
    repair_progress_label: Optional[str] = None
    repair_error: Optional[str] = None
    # "original" (PMVHaven's own) | "custom" (picked via the thumbnail gallery)
    thumbnail_source: str = "original"
    # If a repair pauses waiting on a decision (e.g. custom thumbnail broken),
    # this holds what's being asked -- cleared once resolved.
    repair_pending_action: Optional[dict] = None
    # Alternate-thumbnail candidates from the site's "Video Timeline" (its API) -- small frame-grab JPEGs sampled at roughly
    # even intervals across the video, each {"url": str, "seconds": float},
    # sorted chronologically. These are just remote URLs (not files on our
    # own disk) until/unless the person picks one via the thumbnail picker,
    # at which point it gets cached and applied like any other thumbnail.
    # Populated at metadata-fetch time for new downloads; backfilled for
    # older items via repair.
    timeline_thumbnails: list[dict] = Field(default_factory=list)
    tech_codec: Optional[str] = None
    tech_resolution: Optional[str] = None
    tech_filesize: Optional[str] = None
    tech_duration: Optional[str] = None
    # Our OWN perceptual hash of the file currently on disk, computed with
    # the same method Stash uses for duplicate detection (see
    # media_tools.generate_stash_phash()) -- a 16-char hex string (64
    # bits). Deliberately separate from PMVHaven's own "phash" field
    # inside the metadata JSON sidecar: that one describes whatever file
    # PMVHaven itself hosts (their own algorithm, unknown), and goes
    # stale the moment this app re-encodes the file. This field is
    # instead recomputed from whatever's actually on disk right after
    # every download and every re-encode, so it's always accurate for
    # comparing against other items in THIS library.
    # NOTE: no longer read by duplicate detection as of the score-based
    # overhaul -- see dup_phash_sequence below, which is what
    # find_issues()/_find_duplicate_groups() actually use now. Still
    # computed on every download/re-encode and kept here rather than
    # removed outright, since dropping it would need a migration for
    # existing rows for no real benefit; safe to ignore.
    local_phash: Optional[str] = None
    local_phash_generated_at: Optional[float] = None
    # Fingerprint of the file's bytes at the moment local_phash was last
    # computed -- lets repair's phash step skip the expensive recompute
    # when the file is confirmed unchanged (see repair_worker.py), rather
    # than needing to regenerate the phash just to find out nothing
    # changed. size is checked first (cheap stat(), no disk read); the
    # SHA-256 is only computed if the size actually matches.
    local_phash_source_hash: Optional[str] = None
    local_phash_source_size: Optional[int] = None
    # Score-based duplicate detection (see backend/library_mixins/
    # duplicate_worker.py) -- a sequence of per-frame perceptual hashes
    # sampled evenly across the whole video, NOT a single hash like
    # local_phash above. This is what duplicate detection actually
    # compares now: two sequences are aligned (tolerating a trimmed
    # intro/outro between two encodes of the same content) and scored as
    # a 0-100% match, persisted per-pair in the duplicate_matches DB
    # table rather than recomputed live.
    dup_phash_sequence: list[str] = Field(default_factory=list)

    @field_validator("dup_phash_sequence", mode="before")
    @classmethod
    def _coerce_none_dup_phash_sequence(cls, value):
        """A handful of rows in existing databases ended up with this
        field stored as JSON `null` rather than an empty list -- from a
        state before default_factory=list was consistently applied to
        every write path, most likely for an item whose duplicate-scan
        hashing failed on every attempt (see media_tools.
        sample_frame_hashes()/sample_frame_hashes_ffmpeg_fallback()) before
        ever reaching a step that would have written a proper []. Without
        this, Pydantic rejects `null` outright (it requires an actual
        list), which made LibraryDB.load_all() skip loading that row
        entirely on every server start -- the item was invisible in the
        app, even though its files were still on disk, until a fresh
        re-download overwrote the row with a valid empty list (which is
        exactly why re-downloading "fixed" it, temporarily)."""
        return [] if value is None else value

    dup_phash_generated_at: Optional[float] = None
    # Fingerprint of the file's bytes at the moment dup_phash_sequence was
    # last computed -- same skip-recompute pattern as local_phash_source_*
    # above, so the (much more expensive, since it decodes 48 real frames
    # instead of one sprite) sequence is only ever regenerated when the
    # file has actually changed.
    dup_phash_source_hash: Optional[str] = None
    dup_phash_source_size: Optional[int] = None
    # File modification time at the moment dup_phash_sequence was last
    # computed -- used together with dup_phash_source_size for a CHEAP
    # staleness check (a plain os.stat() call, no file content read) on
    # every scan. dup_phash_source_hash above is still computed and
    # stored once per (re)hash -- it's what powers the identical-file
    # short-circuit in Phase 2 comparisons -- but it is NOT recomputed
    # just to check whether a file changed; that used to mean re-reading
    # every video file's full contents on every single scan, which is
    # what made a "delta-only" scan still take a very long time even
    # when nothing had actually changed.
    dup_phash_source_mtime: Optional[float] = None
    ratings: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    performers: list[str] = Field(default_factory=list)
    # Live site data from "Sync with PMVHaven" (library_mixins/site_sync_worker.py):
    # site_status "available" | "removed" (not returned by the API any more),
    # site_stats {"views", "likes", "dislikes", "favorites"}.
    site_status: Optional[str] = None
    site_synced_at: Optional[float] = None
    site_stats: dict = Field(default_factory=dict)
    # Full video description text, and music credit labels (already
    # formatted as display strings, e.g. "Song — Artist" -- see
    # _extract_tags_performers_music_from_meta_dict() in
    # library_manager.py for how these get parsed out of either metadata
    # JSON shape). Both exist mainly to support full-text search (see
    # query_items()'s field-prefix search support in library_manager.py).
    # Items downloaded before these fields existed need a "Sync database
    # from files" run (Settings -> Download tab) or an individual Repair
    # to get backfilled, same as any other field added after the fact.
    description: str = ""
    music: list[str] = Field(default_factory=list)
    # When the video was published on the site, "YYYY-MM-DD" (None = unknown).
    upload_date: Optional[str] = None
    # Extensions' own data on this video: extension name -> anything (2.30.0, backend/extensions.py).
    ext: dict[str, Any] = Field(default_factory=dict)
    # Details the user edited by hand (video page -> Edit details): field ->
    # list, for "tags", "performers", "music" and "flags". They always win
    # over whatever a download, sync, repair or "sync from files" writes --
    # see apply_user_edits(), which the database layer runs on every save.
    # site_values keeps the site's latest value of each edited field, so
    # "reset to site" can restore it and syncs don't report false changes.
    user_edits: dict[str, list[str]] = Field(default_factory=dict)
    # Place in the download queue (lower downloads first). None = in the
    # order it was added (created_at); set when moved on the Tasks page.
    queue_rank: Optional[float] = None
    site_values: dict[str, list[str]] = Field(default_factory=dict)

    @field_serializer("tags", "performers", "music")
    def _shown_spelling(self, values: list[str], info):
        """What the app shows: one spelling per case-insensitive value (see
        spellings.py). The database stores the raw values (context raw=True)."""
        if info.context and info.context.get("raw"):
            return values
        from .spellings import spellings
        return spellings.canon_list(info.field_name, values)

    def touch(self) -> None:
        self.updated_at = time.time()

    def apply_user_edits(self) -> None:
        for field in EDITABLE_ITEM_FIELDS:
            if field not in self.user_edits:
                continue
            current = getattr(self, field)
            if current != self.user_edits[field]:
                # Something (sync/repair/...) wrote the site's value: keep it
                # aside, then put the user's edit back.
                self.site_values[field] = list(current)
                setattr(self, field, list(self.user_edits[field]))

    @property
    def download_rank(self) -> float:
        return self.queue_rank if self.queue_rank is not None else self.created_at

    def site_value(self, field: str) -> list[str]:
        """The site's own value of an editable field, even while overridden."""
        if field in self.user_edits:
            return self.site_values.get(field, [])
        return getattr(self, field)


class AddRequest(BaseModel):
    url: str


class LoginRequest(BaseModel):
    username: str
    password: str


class DownloadSettingsRequest(BaseModel):
    max_concurrent_downloads: int
    max_retries: int
    chunk_size_kb: int
    download_funscripts: bool
    save_metadata_json: bool
    min_free_percent: int = Field(10, ge=0, le=50)


class ReencodeSettingsRequest(BaseModel):
    codec: str = Field("hevc", pattern="^(hevc|av1)$")
    target_crf: int = Field(ge=0, le=51)
    preset: str = Field(pattern="^(ultrafast|superfast|veryfast|faster|fast|medium|slow|slower|veryslow)$")
    av1_crf: int = Field(32, ge=1, le=63)
    av1_preset: int = Field(8, ge=0, le=13)
    warn_crf: bool = True
    copy_audio: bool
    downscale: bool
    cpu_limit_mode: str = Field("percent", pattern="^(off|cores|percent)$")
    cpu_limit_cores: int = Field(4, ge=1, le=1024)
    cpu_limit_percent: int = Field(70, ge=5, le=100)
    remove_chapters: bool
    keep_original_file: bool
    ask_each_time: bool = True


class PlaybackSettingsRequest(BaseModel):
    live_convert: bool
    max_height: int = Field(720, ge=240, le=720)
    cpu_percent: int = Field(50, ge=10, le=100)
    max_sessions: int = Field(2, ge=1, le=8)


class ProxySettingsRequest(BaseModel):
    site_mode: str = Field("off", pattern="^(off|split|all)$")
    proxy_downloads: int = Field(1, ge=1, le=3)
    uploads: bool = False  # extensions' uploads (2.30.0)
    check_before_use: bool = True


class LoggingSettingsRequest(BaseModel):
    enabled: bool = True
    console: bool = True
    file: bool = True
    app_log: bool = True
    level: str = Field("INFO", pattern="^(TRACE|DEBUG|INFO|SUCCESS|WARNING|ERROR|CRITICAL)$")
    rotation: str = Field("1 month", pattern=r"^\d{1,4} (second|minute|hour|day|week|month|year)s?$|^\d{1,6} (KB|MB|GB)$")
    retention: str = Field("12 months", pattern=r"^forever$|^\d{1,4} (second|minute|hour|day|week|month|year)s?$")
    app_log_retention_days: int = Field(90, ge=1, le=3650)
    app_log_max_rows: int = Field(500_000, ge=1000, le=50_000_000)
    ip_mode: str = Field("hashed", pattern="^(hashed|masked|full|off)$")


class UploadSettingsRequest(BaseModel):
    convert_codec: str = Field("h264", pattern="^(h264|hevc|av1)$")
    h264_crf: int = Field(20, ge=0, le=51)
    h264_preset: str = Field("medium", pattern="^(ultrafast|superfast|veryfast|faster|fast|medium|slow|slower|veryslow)$")
    auto_convert: bool = False
    virus_scan: str = Field("auto", pattern="^(auto|required|off)$")
    hash_check: str = Field("off", pattern="^(off|on|required)$")
    max_size_gb: int = Field(50, ge=1, le=1000)
    max_waiting: int = Field(10, ge=1, le=100)


class UploadConvertRequest(BaseModel):
    """Convert an uploaded file browsers can't play (Upload page)."""
    codec: str = Field(pattern="^(h264|hevc|av1)$")
    crf: int = Field(ge=0, le=63)
    preset: str = Field(max_length=20)


class PreviewFromTimestampsRequest(BaseModel):
    """Make the hover preview from these start times (seconds)."""
    timestamps: list[float] = Field(min_length=1, max_length=12)
    clip_seconds: float = Field(2.0, ge=0.5, le=10)


class ReencodeOptions(BaseModel):
    """This one re-encode's settings (Re-encode dialog, 2.22.0); left out = Server Settings."""
    crf: Optional[int] = Field(None, ge=0, le=63)            # HEVC 0-51, AV1 1-63 (checked per codec)
    preset: Optional[str] = Field(None, max_length=12)       # HEVC x265 name / AV1 0-13
    copy_audio: Optional[bool] = None
    downscale: Optional[bool] = None
    remove_chapters: Optional[bool] = None
    keep_original_file: Optional[bool] = None


class ReencodeRequest(BaseModel):
    codec: Optional[str] = Field(None, pattern="^(hevc|av1)$")  # None = the configured codec
    options: Optional[ReencodeOptions] = None


class TraySettingsRequest(BaseModel):
    """Server Settings -> Opening the app (2.31.0). browser None = unchanged."""
    open_browser: bool = True
    browser: Optional[str] = Field(None, max_length=2000)


class FeaturesSettingsRequest(BaseModel):
    reencode: Optional[bool] = None


class ReencodeMoveRequest(BaseModel):
    direction: str  # "up" | "down" (re-encode queue); downloads also take "top" | "bottom"


class ScheduledJobCreateRequest(BaseModel):
    """Payload for POST /api/scheduler/jobs -- see backend/library_mixins/
    scheduler_worker.py's JOB_REGISTRY for valid job_type values, and
    that module's _compute_next_run_at() for the schedule_config shapes
    schedule_mode "simple" vs "advanced" each expect. job_config holds
    JOB-TYPE-SPECIFIC options (e.g. duplicate_scan's
    {"verbose_logging": bool}) -- separate from schedule_config, which is
    purely about WHEN the job runs, not how it behaves once it does."""
    job_type: str
    name: str
    enabled: bool = True
    schedule_mode: str  # "simple" | "advanced"
    schedule_config: dict
    job_config: dict = {}


class ScheduledJobUpdateRequest(BaseModel):
    """Payload for PATCH /api/scheduler/jobs/{id} -- job_type is
    deliberately NOT editable here (create a new job instead of
    repurposing one -- keeps each job's run history unambiguous about
    what it was actually running)."""
    name: Optional[str] = None
    enabled: Optional[bool] = None
    schedule_mode: Optional[str] = None
    schedule_config: Optional[dict] = None
    job_config: Optional[dict] = None


class DuplicateScanSettingsRequest(BaseModel):
    """Payload for POST /api/settings/duplicate-scan -- toggles automatic
    duplicate scanning (the once-daily scheduled sweep, plus the trigger
    fired after every download/re-encode) on or off. Off by default; the
    manual "Run scan now" action (POST /api/library/issues/scan) always
    works regardless of this setting. Every other field is optional; only
    the ones actually sent are saved (see DuplicateScanConfig)."""
    auto_enabled: Optional[bool] = None  # legacy: sets all three scan_after_* flags
    scan_after_download: Optional[bool] = None
    scan_after_reencode: Optional[bool] = None
    scan_after_repair: Optional[bool] = None
    algorithm: Optional[str] = Field(None, pattern="^(grayscale|phash|combined)$")
    percent: Optional[float] = Field(None, ge=80.0, le=100.0)
    samples: Optional[int] = Field(None, ge=4, le=30)
    phash_quorum: Optional[float] = Field(None, ge=0.1, le=1.0)
    duration_tolerance_percent: Optional[float] = Field(None, ge=0.0, le=50.0)
    use_audio: Optional[bool] = None
    audio_min_similarity: Optional[float] = Field(None, ge=55.0, le=100.0)
    workers: Optional[int] = Field(None, ge=1, le=16)


class RepairSyncRequest(BaseModel):
    max_concurrent: int = 1


class SetThumbnailRequest(BaseModel):
    source: str  # "original" | "timeline" | "frame"
    url: Optional[str] = None  # required when source == "timeline"
    seconds: Optional[float] = None  # required when source == "frame" (position in the video)


class DeleteFilesRequest(BaseModel):
    paths: list[str]


class ItemDetailsRequest(BaseModel):
    """PATCH /api/library/{id}/details. A field left out is unchanged; a list
    replaces it (kept through syncs/repairs); null resets it to the site's value."""
    tags: Optional[list[str]] = None
    performers: Optional[list[str]] = None
    music: Optional[list[str]] = None
    flags: Optional[list[str]] = None


class ResolveRepairRequest(BaseModel):
    choice: str  # "reset_default" | "keep_custom"


class MergeItemsRequest(BaseModel):
    """Payload for POST /api/library/issues/merge -- keep_id survives
    untouched; every id in merge_ids is discarded (files deleted, row
    stripped down to a "merged" marker). See
    LibraryManager.merge_items()."""
    keep_id: str
    merge_ids: list[str]


class IgnoreIssueRequest(BaseModel):
    """Payload for POST /api/library/issues/ignore. issue_type is
    currently always "duplicate"; item_ids is the exact set of items this
    issue involves -- see LibraryManager._find_duplicate_groups()'s
    docstring for how exact-set matching works."""
    issue_type: str
    item_ids: list[str]


class BrowseMkdirRequest(BaseModel):
    """Payload for POST /api/settings/browse-dirs/mkdir -- creates a new
    subfolder inside parent_path. Backs the "+ New folder" button in the
    folder-browser modal (browse.js)."""
    parent_path: str
    name: str


# ============================================================
# USERS / ROLES / PER-USER SETTINGS
#
# Backs the DB-based multi-user auth system (replacing the old single
# hardcoded APP_USERNAME/APP_PASSWORD login). See backend/db.py's module
# docstring for the persistence-layer side of this; these are just the
# Pydantic shapes moving between that layer, security.py, and the API.
# ============================================================

class Role(BaseModel):
    """One user class -- e.g. "admin" or "user". `permissions` is a flat
    list of permission strings (e.g. "library.view", "self.settings"), or
    the single wildcard string "*" meaning every permission -- used by the
    admin role so a newly-added permission automatically applies to admins
    without needing to update every admin role row. Seeded by
    db.py's _seed_default_roles_if_needed(); not yet exposed via any API
    route for editing (only the two defaults exist today) -- that's
    planned for when more user classes are added later."""
    name: str
    permissions: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)

    def has_permission(self, permission: str) -> bool:
        return "*" in self.permissions or permission in self.permissions


class User(BaseModel):
    """Full internal user record, INCLUDING password_hash -- this model is
    for backend/db.py <-> backend/security.py use only. NEVER return this
    directly from an API route; use UserPublic instead, which omits the
    hash. `token_version` is the session-invalidation counter -- see
    db.py's bump_token_version()/module docstring for how it's used."""
    id: str
    username: str
    password_hash: str
    role: str = "user"
    display_name: str = ""
    is_active: bool = True
    token_version: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    last_login_at: Optional[float] = None

    def touch(self) -> None:
        self.updated_at = time.time()

    def to_public(self) -> "UserPublic":
        return UserPublic(
            id=self.id,
            username=self.username,
            role=self.role,
            display_name=self.display_name,
            is_active=self.is_active,
            created_at=self.created_at,
            updated_at=self.updated_at,
            last_login_at=self.last_login_at,
        )


class UserPublic(BaseModel):
    """Safe subset of User for API responses -- no password_hash, no
    token_version (nothing sensitive, just not needed by any current
    frontend consumer). Returned by GET/POST/PATCH /api/users routes and
    by /api/auth/status."""
    id: str
    username: str
    role: str
    display_name: str = ""
    is_active: bool = True
    created_at: float
    updated_at: float
    last_login_at: Optional[float] = None


class CreateUserRequest(BaseModel):
    """Payload for POST /api/users (admin-only) -- backs the "+ Create
    user" modal on the Users management page. Only "admin" or "user" is
    currently a valid role (checked server-side in main.py's create_user
    route); creating a second "admin"-role account is rejected there too
    -- see that route for the single-master-admin invariant."""
    username: str
    password: str
    role: str = "user"


class UpdateUserRequest(BaseModel):
    """Payload for PATCH /api/users/{id} (admin-only) -- backs the Edit
    User popup's username field and "Account enabled" checkbox. Both
    optional so the popup can send just whichever actually changed. Does
    NOT include password -- that's the separate
    POST /api/users/{id}/reset-password route (AdminResetPasswordRequest
    below), since a password change always needs to also invalidate that
    user's existing sessions, which this generic partial-update route
    should not do implicitly just because some field happened to be
    present. role is also deliberately not editable here -- there is no
    supported way to change a user's role after creation, since the
    single-admin invariant means promoting/demoting to or from admin is
    not offered anywhere in this app."""
    username: Optional[str] = None
    is_active: Optional[bool] = None


class AdminResetPasswordRequest(BaseModel):
    """Payload for POST /api/users/{id}/reset-password (admin-only) --
    admin sets a new password directly for another user, no current-
    password confirmation needed (that's what distinguishes this from
    ChangeOwnPasswordRequest below). Always bumps that user's
    token_version, logging out any of their existing sessions."""
    new_password: str


class ChangeOwnPasswordRequest(BaseModel):
    """Payload for POST /api/me/password (any authenticated user,
    including regular users -- see the agreed permission matrix: self
    password reset is allowed for everyone, self username/display-name
    change is admin-only). Requires the current password as confirmation
    since this is self-service and not gated by an admin permission
    check. Bumps the caller's own token_version; the route re-issues a
    fresh session cookie in the same response so the person isn't
    immediately logged out by their own change."""
    current_password: str
    new_password: str


class SelfDeleteAccountRequest(BaseModel):
    """Payload for POST /api/me/delete-account -- the double-confirmation
    self-service account deletion flow. `confirm_username` must exactly
    match the caller's own username (step 1 of the confirmation), and
    `current_password` must be correct (step 2) -- both checked
    server-side in main.py's route, not just enforced by the frontend
    modal's two-step UI."""
    confirm_username: str
    current_password: str


class UserSettingsUpdateRequest(BaseModel):
    """Payload for PATCH /api/me/settings -- a free-form {key: value} map
    of preferences to upsert (e.g. {"page_size": 100, "theme": "slate"}).
    Deliberately not a fixed-field model, mirroring user_settings' own
    key-value storage in db.py, so a new preference added later needs no
    change here."""
    settings: dict[str, Any]
