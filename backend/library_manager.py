"""
LibraryManager -- the central in-memory + SQLite-backed manager for the
video library: item CRUD, search/filter/sort, and six independent
background workers (download, metadata fetch, re-encode, repair,
duplicate detection), plus duplicate-issue detection and item-level
extras (rename, thumbnail management, folder moves, metadata sync).

This class's own body is now pure composition -- every actual method
lives in a mixin under backend/library_mixins/, split by concern (see
that package's own files for the full breakdown). This file stays the
one import path everything else in the app already uses
(`from .library_manager import library_manager`), and instantiates the
single singleton every route and every mixin operates on.
"""
from __future__ import annotations

from .library_mixins.disk_guard import DiskGuardMixin
from .library_mixins.names import NamesMixin
from .library_mixins.songs import SongsMixin
from .library_mixins.storyboards import StoryboardsMixin
from .library_mixins.frames import FramesMixin
from .library_mixins.audio_fix import AudioFixMixin
from .library_mixins.watch_positions import WatchPositionsMixin
from .library_mixins.core import CoreMixin
from .library_mixins.performers import PerformersMixin
from .library_mixins.download_worker import DownloadWorkerMixin
from .library_mixins.metadata_worker import MetadataWorkerMixin
from .library_mixins.reencode_worker import ReencodeWorkerMixin
from .library_mixins.repair_worker import RepairWorkerMixin
from .library_mixins.duplicate_worker import DuplicateWorkerMixin
from .library_mixins.scheduler_worker import SchedulerWorkerMixin
from .library_mixins.stats_worker import StatsWorkerMixin
from .library_mixins.issues import IssuesMixin
from .library_mixins.item_extras import ItemExtrasMixin
from .library_mixins.system_worker import SystemWorkerMixin
from .library_mixins.site_sync_worker import SiteSyncWorkerMixin
from .library_mixins.discover import DiscoverMixin
from .library_mixins.untracked_files import UntrackedFilesMixin
from .library_mixins.playlists import PlaylistsMixin
from .library_mixins.local_upload import LocalUploadMixin


class LibraryManager(
    CoreMixin,
    DownloadWorkerMixin,
    MetadataWorkerMixin,
    ReencodeWorkerMixin,
    RepairWorkerMixin,
    DuplicateWorkerMixin,
    UntrackedFilesMixin,
    SchedulerWorkerMixin,
    StatsWorkerMixin,
    IssuesMixin,
    ItemExtrasMixin,
    SystemWorkerMixin,
    SiteSyncWorkerMixin,
    DiscoverMixin,
    PlaylistsMixin,
    LocalUploadMixin,
    PerformersMixin,
    DiskGuardMixin,
    NamesMixin,
    SongsMixin,
    StoryboardsMixin,
    FramesMixin,
    AudioFixMixin,
    WatchPositionsMixin,
):
    """Composes every mixin above into the one class the rest of the app
    uses. Method resolution order (MRO) only matters if two mixins define
    a method with the same name -- they don't here, since each mixin owns
    a distinct, non-overlapping set of methods -- so the order above is
    for readability/grouping only, not functional correctness."""
    pass


# Singleton instance -- constructed once at import time (before any event
# loop exists), same as always. See CoreMixin.__init__ for what gets set
# up here, and CoreMixin.start() for the part that must wait until
# main.py's lifespan() calls it once uvicorn's event loop is actually
# running.
library_manager = LibraryManager()
