"""Where the app keeps its files -- checked when installing and when restoring
a backup (2.25.1), since another computer usually has other drives and folders.

The console (run.py --restore, a restore staged on the Backups page applied at
start, install.bat via Tools/first_install.py) shows every folder the settings
point at, whether it's there, and lets the user point each one somewhere else:
  * a folder that can't be reached (e.g. drive D: doesn't exist here) must be changed;
  * the restored files that belonged in a changed folder are written to the new one;
  * the new places are written to config.yaml (and dropped from
    data/settings_overrides.json, so config.yaml decides);
  * after every restore the app looks for videos, thumbnails and previews whose
    recorded place is gone in the folders now set (data/relink_pending.json,
    run by the library at start -- library_mixins/item_extras.py).

Deliberately light: no import of backend.config (that would create the
credential store); reads config.yaml / overrides itself.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG = ROOT_DIR / "config.yaml"
TEMPLATE = ROOT_DIR / "config.example.yaml"
OVERRIDES = ROOT_DIR / "data" / "settings_overrides.json"
RELINK_FILE = ROOT_DIR / "data" / "relink_pending.json"

# (section, key, label, default -- the settings models' own defaults, is_file)
FOLDERS = [
    ("download", "output_dir", "Videos", "./downloads", False),
    ("download", "cache_dir", "Thumbnails & previews", "./data/cache", False),
    ("download", "session_dir", "Site logins", "./data/pmv_session", False),
    ("download", "library_db_file", "Library database", "./data/library.db", True),
    ("backup", "dir", "Backups", "./data/backups", False),
    ("logging", "log_dir", "Logs", "./data/logs", False),
]
# What a moved folder means for the library's recorded file places.
RELINK_ATTRS = {
    "output_dir": ["video_path", "funscript_path", "metadata_path", "thumbnail_path", "preview_path"],  # older versions kept these next to the video
    "cache_dir": ["thumbnail_path", "preview_path", "metadata_path"],
}


@dataclass
class Plan:
    values: dict = field(default_factory=dict)    # (section, key) -> value used from now on
    old: dict = field(default_factory=dict)       # (section, key) -> value in the settings before
    old_root: Optional[str] = None                # the app folder on the computer the settings come from
    changed: set = field(default_factory=set)
    config: dict = field(default_factory=dict)    # the config.yaml these come from (installers read the port)

    def relocate(self) -> list[tuple[str, str]]:
        """(old place, new place) on this computer, for restored files."""
        return [(str(resolve(self.old[k])), str(resolve(self.values[k]))) for k in self.changed]


# ------------------------------------------------------------------ reading
def resolve(value: str) -> Path:
    return (ROOT_DIR / str(value)).resolve()


def _section(data: dict, section: str) -> dict:
    s = (data or {}).get(section)
    return s if isinstance(s, dict) else {}


def effective(config: dict, overrides: dict) -> dict:
    """(section, key) -> the value the app would use (overrides win, as in config.get_settings)."""
    out = {}
    for section, key, _label, default, _f in FOLDERS:
        value = _section(overrides, section).get(key) or _section(config, section).get(key) or default
        out[(section, key)] = str(value)
    return out


def current_settings() -> tuple[dict, dict]:
    path = CONFIG if CONFIG.is_file() else TEMPLATE
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        config = {}
    try:
        overrides = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        overrides = {}
    return config, overrides


def status(value: str, is_file: bool) -> tuple[str, bool]:
    """("found" | "will be created" | "NOT REACHABLE ...", usable)."""
    if other_os(value):
        return f"NOT REACHABLE (a {'Linux' if os.name == 'nt' else 'Windows'} path)", False
    p = resolve(value)
    folder = p.parent if is_file else p
    if (p.exists() if is_file else p.is_dir()):
        return "found", True
    if not is_file and p.exists():
        return "NOT A FOLDER", False
    probe = folder
    while not probe.exists():
        if probe.parent == probe:
            return f"NOT REACHABLE ({probe} doesn't exist here)", False
        probe = probe.parent
    if not probe.is_dir():
        return f"NOT REACHABLE ({probe} isn't a folder)", False
    return "will be created" if not is_file else "new (empty)", True


# ------------------------------------------------------------------ asking
def other_os(value: str) -> bool:
    """A Windows path on Linux / macOS, or a Linux path on Windows (e.g. a backup from the other system)."""
    if os.name != "nt":
        return bool(re.match(r"^[A-Za-z]:[\\/]", value))
    return value.startswith("/") and not value.startswith("//")


def shown(value: str) -> str:
    return value if other_os(value) else str(resolve(value))


_shown = shown


def _table(plan: Plan, out) -> list[bool]:
    ok = []
    width = max(len(f[2]) for f in FOLDERS)
    for i, (section, key, label, _d, is_file) in enumerate(FOLDERS, 1):
        text, usable = status(plan.values[(section, key)], is_file)
        ok.append(usable)
        out(f"  {i}. {label.ljust(width)}  {_shown(plan.values[(section, key)])}  [{text}]")
    return ok


def _clean(answer: str) -> str:
    return answer.strip().strip('"').strip("'").strip()


def ask(plan: Plan, *, heading: str, ask_fn: Callable[[str], str] = input, out=print) -> Plan:
    """Shows the folders and lets the user change them. Unreachable ones must be changed."""
    out("")
    out(heading)
    while True:
        ok = _table(plan, out)
        for i, usable in enumerate(ok):
            if usable:
                continue
            section, key, label, default, is_file = FOLDERS[i]
            while True:
                hint = "where did you copy them" if key == "output_dir" else "where should it be"
                answer = _clean(ask_fn(f"  {label} can't be reached - {hint}? (Enter = {default}): ")) or default
                if status(answer, is_file)[1]:
                    break
                out(f"    {status(answer, is_file)[0]} - try another place.")
            plan.values[(section, key)] = answer
            plan.changed.add((section, key))
        if not all(ok):
            out("")
            out("  Now:")
            continue
        answer = _clean(ask_fn("  Press Enter to use these folders, or type a number to change one: "))
        if not answer:
            return plan
        if not answer.isdigit() or not 1 <= int(answer) <= len(FOLDERS):
            out("  Type a number from the list, or just press Enter.")
            continue
        section, key, label, _default, is_file = FOLDERS[int(answer) - 1]
        new = _clean(ask_fn(f"  {label} - new place (Enter = keep {_shown(plan.values[(section, key)])}): "))
        if new:
            text, usable = status(new, is_file)
            if not usable:
                out(f"    {text} - not changed.")
            else:
                if key == "output_dir" and text != "found":
                    out("    That folder doesn't exist yet: it's created, empty. Copy the videos there.")
                plan.values[(section, key)] = new
                plan.changed.add((section, key))
        out("")


def interactive() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


# ------------------------------------------------------------------ writing
def set_yaml_value(text: str, section: str, key: str, value) -> str:
    """Sets `section: key:` in config.yaml text, keeping comments and layout."""
    rendered = json.dumps(value) if isinstance(value, str) else str(value)
    block = re.search(rf"(?m)^{re.escape(section)}:[ \t]*(?:#.*)?\n((?:[ \t]+.*\n|[ \t]*\n|#.*\n)*)", text)
    line = re.compile(rf"(?m)^([ \t]+{re.escape(key)}:[ \t]*)[^#\n]*?([ \t]*(?:#.*)?)$")
    if block and line.search(block.group(1)):
        body = line.sub(lambda m: f"{m.group(1)}{rendered}{m.group(2)}", block.group(1), count=1)
        return text[: block.start(1)] + body + text[block.end(1):]
    if block:
        return text[: block.start(1)] + f"  {key}: {rendered}\n" + text[block.start(1):]
    return text.rstrip("\n") + f"\n\n{section}:\n  {key}: {rendered}\n"


def save(plan: Plan) -> None:
    """Writes the changed folders to config.yaml (created from the template if
    needed) and removes them from the overrides, so config.yaml decides."""
    if not plan.changed:
        return
    if not CONFIG.is_file():
        CONFIG.write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    text = CONFIG.read_text(encoding="utf-8")
    for section, key in sorted(plan.changed):
        text = set_yaml_value(text, section, key, plan.values[(section, key)])
    CONFIG.write_text(text, encoding="utf-8")
    try:
        overrides = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    touched = False
    for section, key in plan.changed:
        if key in _section(overrides, section):
            del overrides[section][key]
            touched = True
    if touched:
        OVERRIDES.write_text(json.dumps(overrides, indent=2), encoding="utf-8")


def queue_relink(plan: Plan) -> None:
    """After a restore: the library looks for missing videos / thumbnails in
    the folders now set, the next time it starts."""
    jobs = []
    for (section, key), attrs in ((("download", k), a) for k, a in RELINK_ATTRS.items()):
        old = plan.old.get((section, key))
        if old is not None and plan.old_root and not Path(old).is_absolute() and not re.match(r"^[A-Za-z]:", old):
            old = str(Path(plan.old_root) / old)  # a relative folder, on the computer the backup came from
        jobs.append({"old": old, "new": str(resolve(plan.values[(section, key)])), "attrs": attrs})
    RELINK_FILE.parent.mkdir(parents=True, exist_ok=True)
    RELINK_FILE.write_text(json.dumps(jobs, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ entry points
def plan_from(config: dict, overrides: dict, old_root: Optional[str] = None) -> Plan:
    values = effective(config, overrides)
    return Plan(values=dict(values), old=dict(values), old_root=old_root, config=config or {})


def check_install(ask_fn=input, out=print) -> Plan:
    """install.bat (new setup or re-run): where this install keeps its files."""
    plan = plan_from(*current_settings())
    plan = ask(plan, heading="Where the app keeps its files (relative places are inside the app folder):",
               ask_fn=ask_fn, out=out)
    save(plan)
    return plan
