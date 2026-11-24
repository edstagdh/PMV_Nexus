"""Translations on the server (3.7.0).

The English text is the key, as in the interface (ui/src/lib/i18n.js). Text people read --
error messages, notifications, Tasks entries, job summaries -- is written as

    L("Couldn't read the video's length")
    L("Downloaded: {title}", title=item.title)

which is a normal ``str`` (the English, so logs and existing code keep working) that also
remembers its English template and values. It's translated only where it leaves the server:
JSON responses (``TranslatingJSONResponse``) and error messages (``tr`` in the HTTP error
handler), into the language the browser asked for (``X-Language``, set by ``LanguageMiddleware``).
Logs stay English. There are no plurals here: server texts are worded without them
("Videos queued: {n}").

Language files: ``languages/en.json`` (generated: ``python -m Tools.i18n extract``) lists every
text; installed languages live in ``data/languages/<code>.json`` after ``validate`` cleaned them.
A language file is only ever data: values are shown as text, never as HTML, and an entry whose
{placeholders} don't match the English is dropped (English shows instead).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Optional

from fastapi.responses import JSONResponse
from loguru import logger

ROOT_DIR = Path(__file__).resolve().parent.parent
EN_FILE = ROOT_DIR / "languages" / "en.json"
LANG_DIR = ROOT_DIR / "data" / "languages"

CODE_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
PLURAL_FORMS = ("zero", "one", "two", "few", "many", "other")
MAX_FILE_BYTES = 1_000_000
MAX_TEXT = 2000
MAX_LABEL = 60


class LanguageError(ValueError):
    """A language file that can't be used at all (its entries are checked one by one instead)."""


# ---- texts ---------------------------------------------------------------------------

def _fill(text: str, params: dict) -> str:
    return PLACEHOLDER_RE.sub(lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), text)


_recent: "OrderedDict[str, Msg]" = OrderedDict()
_recent_lock = threading.Lock()
_RECENT_MAX = 4000


class Msg(str):
    """English text that can still be translated: ``msgid`` (the template) + ``params``."""

    def __new__(cls, msgid: str, params: Optional[dict] = None):
        params = params or {}
        obj = super().__new__(cls, _fill(msgid, params))
        obj.msgid = msgid
        obj.params = params
        # A Msg often passes through code that turns it into a plain str (str(e) of an
        # exception, a pydantic field): remembering recent ones lets tr() still find it.
        if params:
            with _recent_lock:
                _recent[str.__str__(obj)] = obj
                _recent.move_to_end(str.__str__(obj))
                if len(_recent) > _RECENT_MAX:
                    _recent.popitem(last=False)
        return obj

    def __reduce__(self):
        return (Msg, (self.msgid, self.params))


def L(msgid: str, **params: Any) -> Msg:
    """Text for people: English now, translated where it leaves the server."""
    return Msg(msgid, params)


# ---- language files ------------------------------------------------------------------

_lock = threading.RLock()
_en: Optional[dict] = None
_installed: dict[str, dict] = {}
_loaded = False


def english() -> dict:
    """msgid -> English (str, or {"one": .., "other": ..} for the interface's plurals)."""
    global _en
    if _en is None:
        try:
            _en = json.loads(EN_FILE.read_text(encoding="utf-8")).get("messages") or {}
        except (OSError, ValueError) as exc:
            logger.warning("Translations: couldn't read {}: {}", EN_FILE, exc)
            _en = {}
    return _en


def placeholders(value) -> set[str]:
    if isinstance(value, dict):
        out: set[str] = set()
        for form in value.values():
            out |= placeholders(form)
        return out
    return set(PLACEHOLDER_RE.findall(value or ""))


def _clean_text(text: str) -> str:
    # No control characters (newlines and tabs are fine); normalised so look-alike sequences compare equal.
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) != "Cc")
    return unicodedata.normalize("NFC", text)


def _label(info: dict, key: str, required: bool = True) -> str:
    value = info.get(key)
    if not required and (value is None or (isinstance(value, str) and not value.strip())):
        return ""
    if not isinstance(value, (str, int, float)) or not str(value).strip():
        raise LanguageError(L("The language file has no “{field}”", field=key))
    value = _clean_text(str(value)).strip()
    if len(value) > MAX_LABEL:
        raise LanguageError(L("The language file's “{field}” is too long", field=key))
    return value


def validate(raw: bytes) -> tuple[dict, dict]:
    """Checks a language file and returns (clean file, report). Raises LanguageError when it
    can't be used at all; single bad entries are dropped and counted (English shows for them)."""
    if len(raw) > MAX_FILE_BYTES:
        raise LanguageError(L("The language file is too big (over {mb} MB)", mb=MAX_FILE_BYTES // 1_000_000))
    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise LanguageError(L("That isn't a language file (it isn't valid JSON)"))
    if not isinstance(doc, dict) or not isinstance(doc.get("language"), dict) or not isinstance(doc.get("messages"), dict):
        raise LanguageError(L("That isn't a language file (it needs “language” and “messages”)"))
    info = doc["language"]
    code = str(info.get("code") or "").strip()
    if not CODE_RE.match(code):
        raise LanguageError(L("The language file's code isn't valid (e.g. “fr” or “pt-BR”)"))
    if code == "en":
        raise LanguageError(L("English is built in and can't be replaced"))
    direction = str(info.get("direction") or "ltr").lower()
    if direction not in ("ltr", "rtl"):
        raise LanguageError(L("The language file's direction must be “ltr” or “rtl”"))
    language = {
        "code": code,
        "name": _label(info, "name"),
        "native": _label(info, "native", required=False) or _label(info, "name"),
        "direction": direction,
        "version": _label(info, "version", required=False) or "1",
        "translator": _label(info, "translator", required=False),
    }

    en = english()
    messages: dict[str, Any] = {}
    unknown = 0
    rejected: list[dict] = []
    for msgid, value in doc["messages"].items():
        if msgid not in en:
            unknown += 1
            continue
        reason = None
        english_value = en[msgid]
        if isinstance(english_value, dict):
            if not isinstance(value, dict) or not isinstance(value.get("other"), str):
                reason = L("needs plural forms with “other”")
            else:
                forms = {}
                for form, text in value.items():
                    if form not in PLURAL_FORMS or not isinstance(text, str):
                        reason = L("unknown plural form “{form}”", form=form)
                        break
                    forms[form] = _clean_text(text)
                if not reason:
                    extra = placeholders(forms) - placeholders(english_value) - {"n"}
                    if extra:
                        reason = L("unknown placeholder {names}", names=", ".join("{" + p + "}" for p in sorted(extra)))
                    elif any(len(t) > MAX_TEXT for t in forms.values()):
                        reason = L("too long")
                    elif any(not t.strip() for t in forms.values()):
                        reason = L("empty")
                    else:
                        messages[msgid] = forms
        else:
            if not isinstance(value, str):
                reason = L("not text")
            else:
                text = _clean_text(value)
                if not text.strip():
                    reason = L("empty")
                elif len(text) > MAX_TEXT:
                    reason = L("too long")
                elif placeholders(text) != placeholders(msgid):
                    reason = L("placeholders differ from the English")
                else:
                    messages[msgid] = text
        if reason:
            rejected.append({"text": msgid[:120], "reason": reason})
    report = {
        "translated": len(messages), "total": len(en), "unknown": unknown,
        "rejected": rejected[:30], "rejected_count": len(rejected),
    }
    return {"language": language, "messages": messages}, report


def _load_installed() -> None:
    global _loaded
    with _lock:
        if _loaded:
            return
        _installed.clear()
        if LANG_DIR.is_dir():
            for path in sorted(LANG_DIR.glob("*.json")):
                try:
                    stored = json.loads(path.read_text(encoding="utf-8"))
                    # Checked again against this version's English (texts change between versions).
                    clean, report = validate(json.dumps({"language": stored["language"], "messages": stored["messages"]}).encode())
                    clean["installed"] = {**(stored.get("installed") or {}), "report": report}
                    _installed[clean["language"]["code"]] = clean
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    logger.warning("Translations: skipped {}: {}", path.name, exc)
        _loaded = True


def installed() -> dict[str, dict]:
    _load_installed()
    return _installed


def save(clean: dict, report: dict, source: str, sha256: str) -> dict:
    """Stores a checked language file (replacing an older one of the same code)."""
    code = clean["language"]["code"]
    LANG_DIR.mkdir(parents=True, exist_ok=True)
    stored = {**clean, "installed": {"source": source, "sha256": sha256, "at": time.time(), "report": report}}
    tmp = LANG_DIR / f"{code}.json.part"
    tmp.write_text(json.dumps(stored, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(LANG_DIR / f"{code}.json")
    with _lock:
        _installed[code] = stored
    logger.info("Translations: installed {} ({}) from {}: {} of {} texts", clean["language"]["name"], code,
                source, report["translated"], report["total"])
    return stored


def remove(code: str) -> bool:
    with _lock:
        gone = _installed.pop(code, None)
    path = LANG_DIR / f"{code}.json"
    if path.exists():
        path.unlink()
    return gone is not None


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# ---- translating at the edge ---------------------------------------------------------

_lang: ContextVar[str] = ContextVar("pmv_language", default="en")


def current() -> str:
    return _lang.get()


def tr(value: Any, code: Optional[str] = None) -> Any:
    """``value`` in the language (default: the request's). Translates Msg objects, and plain
    strings that are a known text or a Msg that lost its type on the way; others stay as they are."""
    if not isinstance(value, str):
        return value
    code = code or _lang.get()
    if code == "en":
        return str.__str__(value)
    lang = installed().get(code)
    if not lang:
        return str.__str__(value)
    msg = value if isinstance(value, Msg) else _recent.get(value)
    msgs = lang["messages"]
    if msg is None:
        found = msgs.get(value)
        return found if isinstance(found, str) else str.__str__(value)
    params = {k: tr(v, code) if isinstance(v, str) else v for k, v in msg.params.items()}
    found = msgs.get(msg.msgid)
    if not isinstance(found, str):
        return _fill(msg.msgid, params)
    if (lang.get("language") or {}).get("direction") == "rtl":
        # Each value isolated (FSI ... PDI, invisible): "13 MB" or an English name keeps its own order.
        params = {k: f"\u2068{v}\u2069" for k, v in params.items()}
    return _fill(found, params)


def known(text: Optional[str]) -> Any:
    """``text`` as a translatable text when it's exactly one of the app's own English texts (e.g. the
    name a built-in job was created with, kept in the database); anything else is left as it is."""
    return Msg(text) if isinstance(text, str) and not isinstance(text, Msg) and text in english() else text


def localize(data: Any, code: Optional[str] = None) -> Any:
    """Translates every Msg inside a JSON-like structure (plain strings are left alone:
    a video called "Remove" stays "Remove")."""
    if isinstance(data, Msg):
        return tr(data, code)
    if isinstance(data, dict):
        return {k: localize(v, code) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [localize(v, code) for v in data]
    return data


class TranslatingJSONResponse(JSONResponse):
    """The app's default response: Msg texts come out in the request's language."""

    def render(self, content: Any) -> bytes:
        if _lang.get() != "en":
            content = localize(content)
        return super().render(content)


class LanguageMiddleware:
    """Takes the language from ``X-Language`` (sent by the interface) for this request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            for key, value in scope.get("headers", []):
                if key == b"x-language":
                    code = value.decode("latin-1").strip()
                    if CODE_RE.match(code) and code in installed():
                        token = _lang.set(code)
                        try:
                            return await self.app(scope, receive, send)
                        finally:
                            _lang.reset(token)
                    break
        return await self.app(scope, receive, send)


# ---- stored texts (notifications, job summaries) -------------------------------------

def to_stored(value: Any) -> Optional[dict]:
    """A Msg as JSON ({"id", "p"}) so it can be translated when it's read back; None for plain text."""
    if not isinstance(value, Msg):
        value = _recent.get(value) if isinstance(value, str) else None
        if value is None:
            return None
    params = {}
    for k, v in value.params.items():
        nested = to_stored(v) if isinstance(v, str) else None
        params[k] = nested if nested else (v if isinstance(v, (int, float, bool)) or v is None else str(v))
    return {"id": value.msgid, "p": params}


def from_stored(data: Any, fallback: str = "") -> Any:
    """Back from to_stored(): a Msg (or ``fallback`` when there's nothing usable)."""
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        return fallback
    params = {}
    for k, v in (data.get("p") or {}).items():
        params[k] = from_stored(v, "") if isinstance(v, dict) else v
    return Msg(data["id"], params)
