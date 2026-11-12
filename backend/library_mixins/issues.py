from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from loguru import logger

from ..models import LibraryItem
from ._helpers import delete_with_retry


class IssuesMixin:

    def find_issues(self, min_percentage: float = 75.0) -> list[dict]:
        """Returns every currently-open maintenance issue in the library.
        Currently just duplicate-video groups, matched by the background
        duplicate-scan worker's persisted per-pair scores (see
        backend/library_mixins/duplicate_worker.py) rather than a live
        scan -- this method is now a cheap read of already-computed data.
        Each issue is {"type": "duplicate", "items": [minimal item
        summaries], "pairs": [{"item_id_a", "item_id_b",
        "match_percentage", "verdict"}, ...]} -- `pairs` carries every
        persisted edge that actually connects members of that group (not
        necessarily every possible pair within it -- some pairs may never
        have been directly compared if their similarity came through a
        third item in the chain), so the frontend can show real scores,
        not just a bare grouping. Groups the person has explicitly marked
        ignored (see ignore_issue()) are excluded."""
        issues: list[dict] = []
        for group in self._find_duplicate_groups(min_percentage):
            issues.append({
                "type": "duplicate",
                "items": [self._issue_item_summary(self._items[i]) for i in group["item_ids"]],
                "pairs": group["pairs"],
            })
        return issues

    def _find_duplicate_groups(self, min_percentage: float) -> list[dict]:
        """Groups completed items connected by a persisted
        duplicate_matches edge scoring >= min_percentage, using
        union-find so a chain of pairwise matches (A-B, B-C) still ends
        up in one group even if A and C were never directly compared to
        each other -- deliberate, since two encodes several steps removed
        from the same original source can each be a strong match to their
        immediate neighbor without necessarily scoring high against each
        other directly.

        Only items still "done" today are considered -- a match computed
        against an item since removed/merged/re-downloading is stale;
        this filters those out defensively (the row itself is also
        actively cleaned up in remove()/merge_items(), so this is a
        backstop, not the primary cleanup mechanism).

        Returns groups of 2+ items as {"item_ids": [...], "pairs":
        [...]}, largest group first; within each group, item_ids are
        sorted oldest-first (by created_at) so the likely "original"
        naturally sorts to the front."""
        matches = self.db.list_duplicate_matches(min_percentage=min_percentage)
        if not matches:
            return []

        involved_ids = {m["item_id_a"] for m in matches} | {m["item_id_b"] for m in matches}
        id_to_item = {
            i.id: i for i in self._items.values()
            if i.id in involved_ids and i.status == "done"
        }
        if not id_to_item:
            return []

        valid_matches = [m for m in matches if m["item_id_a"] in id_to_item and m["item_id_b"] in id_to_item]

        # Complete-linkage clustering: two clusters merge only if EVERY
        # cross pair has a persisted match edge. Plain union-find let one
        # bridging pair (A~B, B~C) pull unrelated videos into a group; the
        # scan compares every duration-compatible pair, so a missing edge
        # means "compared and rejected" (VideoDuplicateFinder guards against
        # the same chaining problem).
        edge_set = {frozenset((m["item_id_a"], m["item_id_b"])) for m in valid_matches}
        cluster_of: dict[str, set[str]] = {i: {i} for i in id_to_item}
        for m in sorted(valid_matches, key=lambda m: m["match_percentage"], reverse=True):
            ca, cb = cluster_of[m["item_id_a"]], cluster_of[m["item_id_b"]]
            if ca is cb:
                continue
            if all(frozenset((x, y)) in edge_set for x in ca for y in cb):
                ca |= cb
                for member in cb:
                    cluster_of[member] = ca

        seen_clusters: dict[int, list[str]] = {}
        for item_id, cluster in cluster_of.items():
            if len(cluster) > 1:
                seen_clusters.setdefault(id(cluster), sorted(cluster))
        multi_groups = dict(enumerate(seen_clusters.values()))

        ignored = self.db.list_ignored_issues(issue_type="duplicate")
        ignored_sets = {tuple(sorted(entry["item_ids"])) for entry in ignored}

        result: list[dict] = []
        for ids in multi_groups.values():
            if tuple(sorted(ids)) in ignored_sets:
                continue
            id_set = set(ids)
            pairs = [
                {
                    "item_id_a": m["item_id_a"],
                    "item_id_b": m["item_id_b"],
                    "match_percentage": m["match_percentage"],
                    "verdict": m["verdict"],
                    "audio_score": m.get("audio_score"),
                }
                for m in valid_matches
                if m["item_id_a"] in id_set and m["item_id_b"] in id_set
            ]
            sorted_ids = sorted(ids, key=lambda i: id_to_item[i].created_at)
            result.append({"item_ids": sorted_ids, "pairs": pairs})

        result.sort(key=lambda g: len(g["item_ids"]), reverse=True)
        return result

    @staticmethod
    def _issue_item_summary(item: LibraryItem) -> dict:
        """Each item of a duplicate group, as the library list returns it."""
        # Full item (thumbnail, preview, tech details, rating...) so the
        # Duplicates page can show the videos side by side.
        return item.model_dump()

    def ignore_issue(self, issue_type: str, item_ids: list[str]) -> None:
        """Records that this exact set of items, for this exact issue
        type, should stop being reported by find_issues() -- e.g. "these
        two videos really aren't duplicates, stop flagging them". See
        _find_duplicate_groups()'s docstring for how the exact-set match
        (rather than a looser "any of these ids") is applied."""
        self.db.ignore_issue(issue_type, item_ids)
        logger.info("Ignored {} issue for item(s) {}", issue_type, item_ids)

    def unignore_issue(self, issue_type: str, item_ids: list[str]) -> None:
        """Reverses ignore_issue() -- not currently wired to any frontend
        button, but kept here as the natural counterpart in case an
        "un-ignore" action gets added to the Issues page later."""
        self.db.unignore_issue(issue_type, item_ids)
        logger.info("Un-ignored {} issue for item(s) {}", issue_type, item_ids)

    async def merge_items(self, keep_id: str, merge_ids: list[str]) -> dict:
        """Resolves a duplicate-items issue: `keep_id` is left completely
        untouched, and every item in `merge_ids` is discarded -- its files
        (video/thumbnail/preview/funscript/metadata JSON) are deleted from
        disk, and its LibraryItem row is stripped down to just enough to
        remember it existed: id, url, site, status="merged",
        merged_into=keep_id, and timestamps. Everything else (title,
        creator, tags, performers, tech_*, thumbnails, local_phash,
        dup_phash_sequence, share history) is cleared, and any persisted
        duplicate_matches rows referencing the discarded item are deleted
        outright (see db.delete_duplicate_matches_for_item()) -- a merged
        item should never keep appearing inside a duplicate group.

        The row is deliberately NOT deleted outright (unlike remove()) --
        keeping it, with status "merged", means re-adding the same URL
        later is recognized by add()'s existing "any non-failed item
        already exists" check and skipped rather than silently
        re-downloaded.

        Raises ValueError if keep_id doesn't exist, or if keep_id appears
        in merge_ids (merging an item into itself makes no sense). Items
        in merge_ids that no longer exist (e.g. already removed by some
        other action) are silently skipped rather than raising, since
        there's nothing left to merge."""
        if keep_id in merge_ids:
            raise ValueError("Can't merge an item into itself")

        async with self._lock:
            keep_item = self._items.get(keep_id)
            if not keep_item:
                raise ValueError(f"Item {keep_id} not found")

        merged_count = 0
        undeleted: list[str] = []
        for merge_id in merge_ids:
            async with self._lock:
                item = self._items.get(merge_id)
            if not item:
                continue  # already gone -- nothing to merge

            for attr in ("video_path", "funscript_path", "metadata_path", "thumbnail_path", "preview_path"):
                file_path_str = getattr(item, attr, None)
                if not file_path_str:
                    continue
                if not await asyncio.to_thread(delete_with_retry, Path(file_path_str), merge_id) \
                        and Path(file_path_str).exists():
                    undeleted.append(file_path_str)

            # Copies that were merged into this one now point at the kept video.
            async with self._lock:
                for child in self._items.values():
                    if child.status == "merged" and child.merged_into == merge_id:
                        child.merged_into = keep_id
                        self.db.save_item(child)

            if item.video_path:
                creator_dir = Path(item.video_path).parent
                try:
                    if creator_dir.exists() and not any(creator_dir.iterdir()):
                        creator_dir.rmdir()
                except Exception:
                    pass

            self.db.delete_duplicate_matches_for_item(merge_id)
            self.db.delete_dup_samples_for_item(merge_id)
            # Playlists that held the removed copy now hold the kept one.
            self.db.repoint_playlist_items(merge_id, keep_id)

            async with self._lock:
                item.status = "merged"
                item.merged_into = keep_id
                item.title = ""
                item.creator = ""
                item.progress_percent = None
                item.progress_label = ""
                item.speed = ""
                item.error = None
                item.download_datetime = None
                item.video_path = None
                item.funscript_path = None
                item.metadata_path = None
                item.thumbnail_path = None
                item.preview_path = None
                item.reencode_status = None
                item.reencode_progress_percent = None
                item.reencode_eta_seconds = None
                item.reencode_speed = None
                item.reencode_error = None
                item.repair_status = None
                item.repair_progress_label = None
                item.repair_error = None
                item.repair_pending_action = None
                item.timeline_thumbnails = []
                item.tech_codec = None
                item.tech_resolution = None
                item.tech_filesize = None
                item.tech_duration = None
                item.ratings = None
                item.tags = []
                item.performers = []
                item.user_edits = {}
                item.site_values = {}
                item.shares = {}
                item.local_phash = None
                item.local_phash_generated_at = None
                item.dup_phash_sequence = []
                item.dup_phash_generated_at = None
                item.dup_phash_source_hash = None
                item.dup_phash_source_size = None
                item.touch()
                self.db.save_item(item)
            merged_count += 1

        logger.info("Merged {} item(s) into {} via the Issues page", merged_count, keep_id)
        if undeleted:
            logger.warning("Merge into {}: {} file(s) are in use and were left on disk -- "
                           "see Maintenance -> Untracked files: {}", keep_id, len(undeleted), undeleted)
        return {"kept_id": keep_id, "merged_ids": merge_ids, "merged_count": merged_count,
                "undeleted_files": undeleted}
