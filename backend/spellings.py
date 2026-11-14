"""
One spelling per tag / performer / music value.

The same value often arrives written differently ("Natural Tits" and
"Natural tits"). Items keep exactly what their source gave them (the
database is never rewritten), but everything the app shows, filters and
suggests uses one spelling per case-insensitive group:

  - the spelling the admin picked (Admin -> Maintenance -> Spellings), or
  - otherwise the one most videos use; on a tie, the one on the most
    recently added video.

Because the stored values stay as they came, the automatic choice follows
the library: if newer videos mostly say "Natural Tits", that becomes the
shown spelling once it's the more used one. A pick by the admin always wins.

Fixes (2.11.0, Management -> Names): a misspelled value renamed or merged
into another ("aurora vael" -> "aurora vale") is rewritten in every
video, and -- when "remember" is on -- kept as a rule, so the same typo
arriving later (a new download, a sync) shows as the fixed name too.
Rules are applied first, before the performer alias hook and the spelling.

LibraryItem serializes its tags/performers/music through canon_list();
LibraryDB stores the raw values (model_dump(context={"raw": True})).
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Iterable, Optional

FIELDS = ("tags", "performers", "music")
_PINS_KEY = "spelling_pins"
_RULES_KEY = "name_rules"
_REFRESH_SECONDS = 2.0  # the counts are rebuilt at most this often while the library changes


def fold(value: str) -> str:
    return " ".join(str(value).split()).casefold()


class Spellings:
    def __init__(self) -> None:
        self._items: Optional[Callable[[], Iterable]] = None
        self._db = None
        self._lock = threading.Lock()
        self._rev = None
        self._built_at = 0.0
        self._pins: dict[str, dict[str, str]] = {f: {} for f in FIELDS}
        # field -> folded -> {spelling: [uses, newest created_at]}
        self._counts: dict[str, dict[str, dict[str, list]]] = {f: {} for f in FIELDS}
        self._chosen: dict[str, dict[str, str]] = {f: {} for f in FIELDS}
        # field -> fn(value) -> another name to show instead, or None. Performers use
        # it to show ThePornDB aliases under her main name (library_mixins/performers.py).
        self._resolvers: dict[str, Callable[[str], Optional[str]]] = {}
        self._rules: dict[str, dict[str, str]] = {f: {} for f in FIELDS}  # field -> folded old -> new

    def set_resolver(self, field: str, fn: Callable[[str], Optional[str]]) -> None:
        self._resolvers[field] = fn

    def bind(self, items: Callable[[], Iterable], db) -> None:
        """items() -> every LibraryItem (the in-memory library); db for the picks."""
        self._items, self._db = items, db
        saved = db.get_app_state(_PINS_KEY, {}) or {}
        self._pins = {f: {fold(k): v for k, v in (saved.get(f) or {}).items()} for f in FIELDS}
        rules = db.get_app_state(_RULES_KEY, {}) or {}
        self._rules = {f: {fold(k): v for k, v in (rules.get(f) or {}).items()} for f in FIELDS}
        self._rev = None

    # ---------------------------------------------------------------- build
    def _fresh(self) -> None:
        if self._items is None:
            return
        rev = getattr(self._db, "revision", None)
        if rev == self._rev or (self._rev is not None and time.monotonic() - self._built_at < _REFRESH_SECONDS):
            return
        with self._lock:
            counts: dict[str, dict[str, dict[str, list]]] = {f: {} for f in FIELDS}
            for item in list(self._items()):
                if getattr(item, "status", None) == "merged":
                    continue
                created = getattr(item, "created_at", 0) or 0
                for field in FIELDS:
                    for value in getattr(item, field, None) or []:
                        spot = counts[field].setdefault(fold(value), {}).setdefault(value, [0, 0.0])
                        spot[0] += 1
                        spot[1] = max(spot[1], created)
            chosen = {f: {} for f in FIELDS}
            for field in FIELDS:
                for key, variants in counts[field].items():
                    pin = self._pins[field].get(key)
                    chosen[field][key] = pin or max(variants.items(), key=lambda kv: (kv[1][0], kv[1][1]))[0]
            self._counts, self._chosen = counts, chosen
            self._rev, self._built_at = rev, time.monotonic()

    def invalidate(self) -> None:
        self._rev = None

    # ---------------------------------------------------------------- use
    def canonical(self, field: str, value: str) -> str:
        if field not in FIELDS or not isinstance(value, str):
            return value
        value = self._rules[field].get(fold(value), value)
        resolver = self._resolvers.get(field)
        if resolver is not None:
            merged = resolver(value)
            if merged:
                return merged
        self._fresh()
        key = fold(value)
        return self._pins.get(field, {}).get(key) or self._chosen.get(field, {}).get(key) or value

    def key(self, field: str, value: str) -> str:
        """The folded shown value -- what filters and comparisons use."""
        return fold(self.canonical(field, value))

    def canon_list(self, field: str, values: Optional[list]) -> list:
        """The values in their shown spelling, without case-only duplicates."""
        out: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            shown = self.canonical(field, value)
            key = fold(shown)
            if key in seen:
                continue
            seen.add(key)
            out.append(shown)
        return out

    # ---------------------------------------------------------------- admin
    def groups(self, field: str, only_conflicts: bool = True) -> list[dict]:
        """[{key, chosen, pinned, total, variants: [{value, count}]}] -- by default only
        values written in more than one way, most used first."""
        self._fresh()
        out = []
        for key, variants in self._counts.get(field, {}).items():
            if only_conflicts and len(variants) < 2:
                continue
            items = sorted(variants.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))
            out.append({
                "key": key,
                "chosen": self._chosen[field].get(key, items[0][0]),
                "pinned": key in self._pins[field],
                "total": sum(v[0] for _, v in items),
                "variants": [{"value": v, "count": n[0]} for v, n in items],
            })
        out.sort(key=lambda g: (-g["total"], g["key"]))
        return out

    # ---------------------------------------------------------------- fixes
    def rules(self, field: str) -> dict[str, str]:
        return dict(self._rules.get(field, {}))

    def add_rule(self, field: str, old: str, new: str) -> None:
        """old (any case) shows as new from now on. Rules pointing at old now
        point at new; a rule for new itself is dropped (no loops)."""
        r = self._rules[field]
        old_key, new_key = fold(old), fold(new)
        for k, v in list(r.items()):
            if fold(v) == old_key:
                r[k] = new
        r.pop(new_key, None)
        if old_key != new_key:
            r[old_key] = new
        self._save_rules()

    def remove_rule(self, field: str, old: str) -> None:
        self._rules[field].pop(fold(old), None)
        self._save_rules()

    def _save_rules(self) -> None:
        if self._db is not None:
            self._db.set_app_state(_RULES_KEY, self._rules)
        self.invalidate()

    def pin(self, field: str, value: str, spelling: Optional[str]) -> None:
        """Use `spelling` for every value written like `value`; None goes back to automatic."""
        if field not in FIELDS:
            raise ValueError("Unknown field")
        key = fold(value)
        if spelling is None:
            self._pins[field].pop(key, None)
        else:
            spelling = " ".join(str(spelling).split())
            if fold(spelling) != key:
                raise ValueError("A spelling can only change upper/lower case")
            self._pins[field][key] = spelling
        if self._db is not None:
            self._db.set_app_state(_PINS_KEY, self._pins)
        self.invalidate()


spellings = Spellings()
