"""
Names (2.11.0, Management -> Names) and bulk edit.

Names: every tag / performer / music value in the library with how many
videos use it. A misspelled one is renamed -- or merged into an existing
one -- in every video ("aurora vael" -> "aurora vale"): the stored
values change (the site's value and any hand edit alike), unlike the
case-only spellings (spellings.py) which only change what's shown. With
"remember" (the default) the fix is also kept as a rule (spellings.add_rule),
so the same typo from a later download or sync shows fixed too.

possible_typos(): pairs of names one or two letters apart (not just upper/
lower case), found with a deletion index -- fast even for thousands of names.

bulk_edit(): add / remove values on many videos at once, as hand edits
(like Edit details), so later syncs don't undo them.
"""
from __future__ import annotations

import difflib
from collections import Counter, defaultdict
from typing import Optional

from loguru import logger

from ..models import EDITABLE_ITEM_FIELDS
from ..spellings import fold, spellings

MAX_VALUE = 120


def _clean(value: str) -> str:
    return " ".join(str(value or "").split())[:MAX_VALUE]


class NamesMixin:

    def _named_items(self):
        return [i for i in self._items.values() if i.status not in ("merged",)]

    def name_list(self, field: str) -> dict:
        """[{value, count, shown}] for every value as stored, most used first, plus the remembered fixes."""
        if field not in EDITABLE_ITEM_FIELDS:
            raise ValueError("Unknown field")
        counts: Counter = Counter()
        for item in self._named_items():
            for v in set(getattr(item, field) or []):
                counts[v] += 1
        names = [{"value": v, "count": n, "shown": spellings.canonical(field, v)} for v, n in counts.items()]
        names.sort(key=lambda r: (-r["count"], r["value"].casefold()))
        rules = [{"old": k, "new": v} for k, v in sorted(spellings.rules(field).items())]
        return {"names": names, "rules": rules}

    def possible_typos(self, field: str, limit: int = 200) -> list[dict]:
        """Pairs of names 1-2 letters apart: {a, b, a_count, b_count, score}; `a` is the more used one."""
        counts: Counter = Counter()
        for item in self._named_items():
            for v in set(getattr(item, field) or []):
                counts[spellings.canonical(field, v)] += 1
        by_key: dict[str, str] = {}
        for v in counts:  # one entry per case-insensitive name (case is the Spellings section's job)
            by_key.setdefault(fold(v), v)
        index: dict[str, set] = defaultdict(set)
        for key in by_key:
            if len(key) < 4:
                continue
            index[key].add(key)
            for i in range(len(key)):
                index[key[:i] + key[i + 1:]].add(key)
        pairs = set()
        for group in index.values():
            if 1 < len(group) <= 25:
                g = sorted(group)
                for i in range(len(g)):
                    for j in range(i + 1, len(g)):
                        pairs.add((g[i], g[j]))
        out = []
        for x, y in pairs:
            # Numbers that differ ("vol 2" / "vol 3") are different things, not typos.
            if [c for c in x if c.isdigit()] != [c for c in y if c.isdigit()]:
                continue
            score = difflib.SequenceMatcher(None, x, y).ratio()
            if score < 0.8:
                continue
            a, b = by_key[x], by_key[y]
            if counts[b] > counts[a] or (counts[b] == counts[a] and b < a):
                a, b = b, a
            out.append({"a": a, "b": b, "a_count": counts[a], "b_count": counts[b], "score": round(score, 3)})
        out.sort(key=lambda r: (-r["score"], -(r["a_count"] + r["b_count"])))
        return out[:limit]

    async def rename_name(self, field: str, old: str, new: str, remember: bool = True) -> dict:
        """Replaces `old` (any upper/lower case) with `new` in every video --
        merging when a video already has `new`. Returns {"videos": n}."""
        if field not in EDITABLE_ITEM_FIELDS:
            raise ValueError("Unknown field")
        old, new = _clean(old), _clean(new)
        if not old or not new:
            raise ValueError("Both names are needed")
        if old == new:
            raise ValueError("That's the same name")
        old_key = fold(old)

        def swap(values: Optional[list]) -> tuple[list, bool]:
            values = list(values or [])
            if not any(fold(v) == old_key for v in values):
                return values, False
            out, seen = [], set()
            for v in values:
                v = new if fold(v) == old_key else v
                if fold(v) not in seen:
                    seen.add(fold(v))
                    out.append(v)
            return out, True

        changed = []
        async with self._lock:
            for item in self._named_items():
                hit = False
                value, c = swap(getattr(item, field))
                if c:
                    setattr(item, field, value)
                    hit = True
                if field in item.user_edits:
                    value, c = swap(item.user_edits[field])
                    if c:
                        item.user_edits[field] = value
                        hit = True
                if field in item.site_values:
                    value, c = swap(item.site_values[field])
                    if c:
                        item.site_values[field] = value
                        hit = True
                if hit:
                    item.touch()
                    changed.append(item)
            if changed:
                self.db.save_items(changed)
        if field == "performers":
            self._carry_performer_link(old, new)
        if remember:
            spellings.add_rule(field, old, new)
        else:
            spellings.invalidate()
        self.db._note_change(["spellings", "names"])
        logger.info("Renamed {} '{}' -> '{}' in {} video(s){}", field, old, new, len(changed),
                    " (remembered for new videos)" if remember else "")
        return {"videos": len(changed)}

    def _carry_performer_link(self, old: str, new: str) -> None:
        """A performer's ThePornDB link moves with her name when the new name has none yet."""
        old_row, new_key = self._perf_names.get(fold(old)), fold(new)
        if old_row and new_key not in self._perf_names and old_row.get("status") in ("linked", "skipped"):
            self._save_name(new_key, new, status=old_row["status"], performer_id=old_row.get("performer_id"),
                            source=old_row.get("source"), candidates=old_row.get("candidates") or [])

    def remove_name_rule(self, field: str, old: str) -> None:
        if field not in EDITABLE_ITEM_FIELDS:
            raise ValueError("Unknown field")
        spellings.remove_rule(field, old)
        self.db._note_change(["spellings", "names"])

    async def bulk_edit(self, item_ids: list[str], changes: dict) -> dict:
        """changes = {field: {"add": [...], "remove": [...]}} for tags / performers / music.
        Applied as hand edits (they stick through syncs). Returns {"videos": n}."""
        plan = {}
        for field, ops in (changes or {}).items():
            if field not in EDITABLE_ITEM_FIELDS or not isinstance(ops, dict):
                continue
            add = [v for v in dict.fromkeys(_clean(x) for x in ops.get("add") or []) if v]
            remove = {fold(_clean(x)) for x in ops.get("remove") or [] if _clean(x)}
            if add or remove:
                plan[field] = (add, remove)
        if not plan:
            raise ValueError("Nothing to change")
        ids = list(dict.fromkeys(item_ids or []))[:5000]
        changed = []
        async with self._lock:
            for item_id in ids:
                item = self._items.get(item_id)
                if not item or item.status == "merged":
                    continue
                touched = False
                for field, (add, remove) in plan.items():
                    current = list(getattr(item, field) or [])
                    removed = {fold(spellings.canonical(field, v)) for v in remove} | remove
                    kept = [v for v in current if fold(v) not in removed and fold(spellings.canonical(field, v)) not in removed]
                    have = {fold(v) for v in kept} | {fold(spellings.canonical(field, v)) for v in kept}
                    result = kept + [v for v in add if fold(v) not in have]
                    if result == current:
                        continue
                    if field not in item.user_edits:
                        item.site_values[field] = current
                    item.user_edits[field] = result[:300]
                    setattr(item, field, result[:300])
                    touched = True
                if touched:
                    item.touch()
                    changed.append(item)
            if changed:
                self.db.save_items(changed)
        logger.info("Bulk edit: {} on {} of {} selected video(s)",
                    ", ".join(f"{f} +{len(a)}/-{len(r)}" for f, (a, r) in plan.items()), len(changed), len(ids))
        return {"videos": len(changed)}
