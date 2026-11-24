"""Translation tools (3.7.0).

    python -m Tools.i18n extract            write languages/en.json + languages/context.json from the code
    python -m Tools.i18n check              fail on: en.json out of date, English written straight into
                                            the interface, a broken language file, an out-of-date manifest
    python -m Tools.i18n manifest           write languages/manifest.json (sizes + SHA-256 of every language)
    python -m Tools.i18n missing FILE       FILE.missing.json: only the texts FILE doesn't have (new in this
                                            version, or left in English) -- translate just those
    python -m Tools.i18n merge FILE PART    adds the translated PART back into FILE (texts no longer in
                                            the app are dropped)
    python -m Tools.i18n pseudo [FOLDER]    two test languages (stretched accents; right-to-left) to install
                                            from a file -- for finding missed texts and layout problems

Texts come from t("..."), $t("..."), tn("one", "many", n), N_("...") in ui/src and L("...")
in backend/. The English text is the key; see languages/TRANSLATING.md.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANG = ROOT / "languages"
EN = LANG / "en.json"
CONTEXT = LANG / "context.json"
MANIFEST = LANG / "manifest.json"
UI_SRC = ROOT / "ui" / "src"
BACKEND = ROOT / "backend"
GENERATED = {"en.json", "context.json", "manifest.json"}

_STR = r'"((?:[^"\\\n]|\\.)*)"|\'((?:[^\'\\\n]|\\.)*)\''
_T = re.compile(r"(?<![\w$.])\$?t\(\s*(?:" + _STR + r")\s*[,)]")
_TN = re.compile(r"(?<![\w$.])\$?tn\(\s*(?:" + _STR + r")\s*,\s*(?:" + _STR + r")")
_N = re.compile(r"(?<![\w$.])N_\(\s*(?:" + _STR + r")\s*\)")
_TX = re.compile(r'<Tx\b(?:[^>"]|"[^"]*")*>')  # <Tx msg="..." [plural="..."]> (components/Tx.vue)
_BAD_CALL = re.compile(r"(?<![\w$.])\$?(?:t|tn|N_)\(\s*`")


def _js_unescape(s: str) -> str:
    return re.sub(r"\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", lambda m: _esc(m.group(1)), s)


def _esc(e: str) -> str:
    if e.startswith("u{"):
        return chr(int(e[2:-1], 16))
    if e[0] in "ux" and len(e) > 1:
        return chr(int(e[1:], 16))
    return {"n": "\n", "t": "\t", "r": "\r", "0": "\0"}.get(e, e)


def _pick(m: re.Match, first: int) -> str:
    return _js_unescape(m.group(first) if m.group(first) is not None else m.group(first + 1))


def _ui_files():
    return sorted(p for p in UI_SRC.rglob("*") if p.suffix in (".vue", ".js") and "node_modules" not in p.parts)


def extract() -> tuple[dict, dict, list[str]]:
    """(messages, where-used, problems)."""
    messages: dict[str, object] = {}
    where: dict[str, set] = {}
    problems: list[str] = []

    def add(key, value, rel):
        old = messages.get(key)
        if old is not None and old != value:
            problems.append(f"{rel}: “{key}” is used both as plain text and as a plural (or with other forms)")
        messages[key] = value
        where.setdefault(key, set()).add(rel)

    for path in _ui_files():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()
        for m in _BAD_CALL.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            problems.append(f"{rel}:{line}: a `template string` can't be translated -- use \"text {{name}}\" + params")
        for m in _TN.finditer(text):
            one, other = _pick(m, 1), _pick(m, 3)
            add(other, {"one": one, "other": other}, rel)
        for m in _T.finditer(text):
            add(_pick(m, 1), _pick(m, 1), rel)
        for m in _N.finditer(text):
            add(_pick(m, 1), _pick(m, 1), rel)
        for m in _TX.finditer(text):
            attrs = dict(re.findall(r'\s(msg|plural)="([^"]*)"', m.group(0)))
            if "plural" in attrs:
                add(attrs["plural"], {"one": attrs["msg"], "other": attrs["plural"]}, rel)
            else:
                add(attrs["msg"], attrs["msg"], rel)

    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            problems.append(f"{rel}: {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
                if name == "L" and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        add(arg.value, arg.value, rel)
                    else:
                        problems.append(f"{rel}:{node.lineno}: L() needs the English text written out (no f-string or variable)")
    ordered = dict(sorted(messages.items(), key=lambda kv: kv[0].casefold()))
    return ordered, {k: sorted(v) for k, v in sorted(where.items(), key=lambda kv: kv[0].casefold())}, problems


def _app_version() -> str:
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _en_doc(messages: dict) -> dict:
    return {
        "language": {"code": "en", "name": "English", "native": "English", "direction": "ltr", "version": _app_version()},
        "messages": messages,
    }


def _dump(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


def cmd_extract() -> int:
    messages, where, problems = extract()
    for p in problems:
        print("problem:", p)
    LANG.mkdir(exist_ok=True)
    EN.write_text(_dump(_en_doc(messages)), encoding="utf-8")
    CONTEXT.write_text(_dump(where), encoding="utf-8")
    plurals = sum(isinstance(v, dict) for v in messages.values())
    print(f"languages/en.json: {len(messages)} texts ({plurals} with plural forms)")
    return 1 if problems else 0


# ---- English written straight into the interface -----------------------------------------

# Words that are the same in every language (names, formats, codecs). A text made only of these,
# numbers and symbols isn't a translation problem.
ALLOWED_WORDS = {
    "pmv", "nexus", "pmvhaven", "hmvhaven", "porntubeai", "theporndb", "tpdb", "shazam", "gemini", "virustotal",
    "telegram", "github", "discord", "google", "lens", "yandex", "ffmpeg", "ffprobe", "avc", "hevc", "av1", "h", "264",
    "265", "mp4", "mkv", "webp", "jpeg", "png", "json", "jsonl", "csv", "zip", "sqlite", "hd", "fhd", "uhd", "4k", "8k",
    "sd", "qhd", "crf", "fps", "kbps", "mbps", "kb", "mb", "gb", "tb", "ms", "url", "api", "id", "ip", "http", "https",
    "socks5", "utc", "cpu", "gpu", "ram", "aac", "opus", "x265", "x264", "svt", "nvenc", "qsv", "amf", "hls",
    "ctrl", "alt", "shift", "esc", "enter", "tab", "admin", "v", "s", "m", "x", "px", "rem", "en", "pmvs",
}
KEY_NAMES = {"Escape", "Enter", "Tab", "Backspace", "Delete", "Space", "Home", "End", "PageUp", "PageDown",
             "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Control", "Shift", "Alt", "Meta"}
_WORD = re.compile(r"[A-Za-z][A-Za-z']+")
_ATTRS = ("title", "placeholder", "aria-label", "alt", "label")
IGNORE_MARK = "i18n-ignore"
IGNORE_FILE = "i18n-ignore-file"  # in a file's first comment: nothing in it is for translating (e.g. the decoy page)


def _needs_translation(text: str) -> bool:
    words = [w.lower().strip("'") for w in _WORD.findall(text)]
    return any(w not in ALLOWED_WORDS for w in words)


def _template_part(src: str) -> tuple[str, int]:
    m = re.search(r"<template[^>]*>", src)
    if not m:
        return "", 0
    end = src.rfind("</template>")
    return src[m.end():end], src.count("\n", 0, m.end())


_CODE_TAGS = ("code", "kbd", "pre", "samp")


def _scan_template(tpl: str, spans: bool = False):
    """Yields (offset, kind, text) for static text and static attributes -- or, with ``spans``,
    (start, end, kind, raw) for rewriting. Text inside <code>/<kbd>/<pre> is syntax, not words."""
    i, n = 0, len(tpl)
    in_code = 0
    while i < n:
        if tpl.startswith("<!--", i):
            j = tpl.find("-->", i)
            i = n if j < 0 else j + 3
            continue
        if tpl[i] == "<" and i + 1 < n and (tpl[i + 1].isalpha() or tpl[i + 1] == "/"):
            j, quote = i + 1, None
            while j < n:
                ch = tpl[j]
                if quote:
                    if ch == quote:
                        quote = None
                elif ch in "\"'":
                    quote = ch
                elif ch == ">":
                    break
                j += 1
            tag = tpl[i:j + 1]
            name = re.match(r"</?\s*([\w-]+)", tag)
            if name and name.group(1).lower() in _CODE_TAGS:
                in_code += -1 if tag.startswith("</") else (0 if tag.endswith("/>") else 1)
            for am in re.finditer(r'(?<![:@#\w-])(' + "|".join(_ATTRS) + r')="([^"]*)"', tag):
                if spans:
                    yield i + am.start(), i + am.end(), f"attribute {am.group(1)}", am.group(0)
                else:
                    yield i + am.start(), f"attribute {am.group(1)}", am.group(2)
            i = j + 1
            continue
        j = i
        while j < n and tpl[j] != "<":
            if tpl.startswith("{{", j):
                e = tpl.find("}}", j)
                j = n if e < 0 else e + 2
            else:
                j += 1
        chunk = re.sub(r"\{\{.*?\}\}", " ", tpl[i:j], flags=re.S)
        if chunk.strip() and not in_code:
            if spans:
                yield i, j, "text", tpl[i:j]
            else:
                yield i, "text", chunk.strip()
        i = j if j > i else i + 1


_SCRIPT_STR = re.compile(_STR)
_HAND_PLURAL = re.compile(r"""===?\s*1\s*\?\s*(["'])[a-z][^"'\n]*\1\s*:\s*(["'])[a-z][^"'\n]*\2""")
_COMPARED = re.compile(r"(===|!==|==|!=|\bcase)\s*$")
_COMPARED_AFTER = re.compile(r"\s*(===|!==|==|!=)")
_IMPORTS_T = re.compile(r"import\s*\{[^}]*\bt\b[^}]*\}\s*from")
_LOCAL_T = re.compile(r"(?:\b(?:const|let|var)\s+t\b\s*=|[(,]\s*t\s*(?:=[^=>]|[,)]\s*=>)|(?<![\w$.])t\s*=>|for\s*\((?:const|let)\s+t\b"
                      r"|\[(?:[^\]\[]*,)?\s*t\s*(?:,[^\]\[]*)?\]\s*(?:=[^=>]|\)\s*=>))")  # also [t, e] = … / ([t, e]) =>
_TEMPLATE_LIT = re.compile(r"`((?:[^`\\]|\\.)*)`")
_SKIP_LINE = re.compile(r"^\s*(import |export \* |//|\*|/\*)|console\.|logger\.|throw new Error\(|"
                        r"localStorage|sessionStorage|matchMedia|new RegExp|router\.(push|replace)")


_EXPR = re.compile(r'\{\{(.*?)\}\}|(?<=\s)(?::|v-bind:|v-if=|v-else-if=|v-show=)[\w.-]*="([^"]*)"', re.S)
_BOUND_ATTR = re.compile(r'(?<=\s)(?::|v-bind:|v-(?=for=))([\w.-]+)="([^"]*)"')


def template_expression_strings(tpl: str):
    """(offset, text) of quoted text inside {{ }} and :bound="..." attributes that isn't translated."""
    for m in re.finditer(r"\{\{(.*?)\}\}", tpl, re.S):
        yield from _expr_strings(m.group(1), m.start(1))
    for m in _BOUND_ATTR.finditer(tpl):
        if not m.group(0).startswith("v-for") and m.group(1) in ("key", "class", "style", "to", "src", "href", "id", "for", "value", "name", "type", "icon"):
            continue
        yield from _expr_strings(m.group(2), m.start(2))


def _expr_strings(expr: str, base: int):
    cleaned = _T.sub(lambda m: " " * len(m.group(0)), _TN.sub(lambda m: " " * len(m.group(0)), expr))
    for m in _SCRIPT_STR.finditer(cleaned):
        s = m.group(1) if m.group(1) is not None else m.group(2)
        before = cleaned[:m.start()]
        if re.search(r"(===|!==|==)\s*$", before) or re.match(r"\s*(===|!==|==)", cleaned[m.end():]):
            continue
        if _looks_like_text(s) or (_PROSE_LOWER.fullmatch(s) and _needs_translation(s)):
            yield base + m.start(), s
    for m in _TEMPLATE_LIT.finditer(cleaned):
        if _template_is_text(_template_static(m.group(1))):
            yield base + m.start(), m.group(1)


def _script_parts(path: Path, src: str):
    if path.suffix == ".js":
        yield src, 0
        return
    for m in re.finditer(r"<script[^>]*>(.*?)</script>", src, re.S):
        yield m.group(1), src.count("\n", 0, m.start(1))


def _strip_comment(line: str) -> str:
    """The line without a trailing // comment (one outside quotes)."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == "\\":
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif line.startswith("//", i) and (i == 0 or line[i - 1] in " \t;,){"):
            return line[:i]
    return line


# "♥ Favourites", "videos playing", "· for 2 min", " (at death)": text that doesn't start with a capital letter.
_PROSE_LOWER = re.compile(r"\s?[^\w\s\"'`]?\s?[a-z][a-z’']+(?: [a-z’'(),.:;…—+/&-]+)+[.…!?]?|\s?[^\w\s\"'`.,:;/\\()\[\]{}<>=+*&|!?#$%@^~-]+\s?[A-Za-z][\w ’'(),.:;…—+/&-]*")


def _template_static(lit: str) -> str:
    """A `template`'s text with every ${…} as X -- also the pieces a nested `…` cuts it into
    (`${n} updates${bad ? ` (${bad} failed)` : ""} — see…`)."""
    s = re.sub(r"\$\{[^}]*\}", " X ", lit)
    s = re.sub(r"\$\{[^`]*$", " X ", s)          # ends inside ${ (a nested template follows)
    return re.sub(r"^[^`{]*?\}", " X ", s)          # starts inside ${ (after a nested template)


def _template_is_text(s: str) -> bool:
    """A `template ${string}` with words for people in it (not a path, a CSS value or a key)."""
    t = s.strip()
    if not t or t.startswith(("/", "#", ".", "--", "http", "?", "&", "[", "=")) or re.search(r"[<>{};=]|\bpx\b|rem\b|url\(|/api/", t):
        return False
    if re.match(r"[\w-]+\(", t) or re.search(r"\.(jpe?g|png|webp|json|mp4|js|css)$", t):  # CSS functions, file names
        return False
    # a phrase: a word next to other text with a space between (ids and keys like "ext:x" have none)
    return bool(re.search(r"[A-Za-z]{2,}\s+\S|\S\s+[A-Za-z]{2,}", t)) and _needs_translation(t)


def _looks_like_text(s: str) -> bool:
    # A sentence or label for people: starts with a capital letter and has a real word.
    if len(s) < 2 or not re.match(r"[A-Z]", s) or re.match(r"^[A-Z0-9_]+$", s):
        return False
    if re.search(r"[<>{}=]|;\S|^[a-z_][\w-]*[.:/]|\.(vue|js|css|png|webp|json)$|^[A-Z][a-z]+[A-Z]", s):
        return False
    if re.fullmatch(r"[A-Z][a-zA-Z]*(-[A-Z][a-zA-Z]*)+", s) or re.fullmatch(r"[A-Z]+[a-z]+[A-Z]\w*", s):  # header names, identifiers
        return False
    return _needs_translation(s)


def find_untranslated() -> list[str]:
    found = []
    for path in _ui_files():
        src = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()
        if IGNORE_FILE in src:
            continue
        if path.suffix == ".vue":
            tpl, base = _template_part(src)
            for off, kind, text in _scan_template(tpl):
                if _needs_translation(text):
                    line = base + tpl.count("\n", 0, off) + 1
                    if IGNORE_MARK not in src.splitlines()[line - 1]:
                        found.append(f"{rel}:{line}: {kind}: {text[:70]!r}")
            for off, text in template_expression_strings(tpl):
                line = base + tpl.count("\n", 0, off) + 1
                if IGNORE_MARK not in src.splitlines()[line - 1]:
                    found.append(f"{rel}:{line}: text in an expression: {text[:70]!r}")
        for pm in _HAND_PLURAL.finditer(src):
            line = src.count("\n", 0, pm.start()) + 1
            if IGNORE_MARK not in src.splitlines()[line - 1]:
                found.append(f"{rel}:{line}: plural made by hand: {pm.group(0)[:70]!r} -- use tn()/$tn()")
        imports_t = bool(_IMPORTS_T.search(src))
        for part, base in _script_parts(path, src):
            for lineno, line in enumerate(part.splitlines(), base + 1):
                if imports_t and _LOCAL_T.search(line):
                    found.append(f"{rel}:{lineno}: a local “t” hides the translate function -- give it another name")
                if IGNORE_MARK in line or _SKIP_LINE.search(line):
                    continue
                stripped = _T.sub("", _TN.sub("", _N.sub("", _strip_comment(line))))
                for m in _SCRIPT_STR.finditer(stripped):
                    s = m.group(1) if m.group(1) is not None else m.group(2)
                    if _COMPARED.search(stripped[:m.start()]) or _COMPARED_AFTER.match(stripped[m.end():]):
                        continue  # e.key === "Escape", status === "Done": a value, not text for people
                    if _looks_like_text(s):
                        found.append(f"{rel}:{lineno}: string: {s[:70]!r}")
                for m in _TEMPLATE_LIT.finditer(stripped):
                    s = _template_static(m.group(1))
                    if _template_is_text(s):
                        found.append(f"{rel}:{lineno}: template string: {m.group(1)[:70]!r}")
                for m in _SCRIPT_STR.finditer(stripped):
                    s = m.group(1) if m.group(1) is not None else m.group(2)
                    if _PROSE_LOWER.fullmatch(s) and _needs_translation(s) and not _COMPARED.search(stripped[:m.start()]):
                        found.append(f"{rel}:{lineno}: string: {s[:70]!r}")
    return found


# ---- manifest + checks --------------------------------------------------------------------

def _language_files():
    return sorted(p for p in LANG.glob("*.json") if p.name not in GENERATED)


def build_manifest() -> tuple[dict, list[str]]:
    sys.path.insert(0, str(ROOT))
    from backend import i18n  # needs the English as written right now
    i18n._en = None
    entries, problems = [], []
    for path in _language_files():
        raw = path.read_bytes()
        try:
            clean, report = i18n.validate(raw)
        except i18n.LanguageError as exc:
            problems.append(f"languages/{path.name}: {exc}")
            continue
        info = clean["language"]
        if path.stem != info["code"]:
            problems.append(f"languages/{path.name}: the file name must be its code ({info['code']}.json)")
            continue
        if report["rejected_count"]:
            for r in report["rejected"][:10]:
                problems.append(f"languages/{path.name}: “{r['text'][:60]}”: {r['reason']}")
        entries.append({
            "code": info["code"], "name": info["name"], "native": info["native"], "direction": info["direction"],
            "version": info["version"], "file": path.name, "size": len(raw), "sha256": i18n.sha256(raw),
            "translated": report["translated"], "total": report["total"],
        })
    return {"format": 1, "app_version": _app_version(), "languages": entries}, problems


def cmd_manifest() -> int:
    manifest, problems = build_manifest()
    for p in problems:
        print("problem:", p)
    MANIFEST.write_text(_dump(manifest), encoding="utf-8")
    print(f"languages/manifest.json: {len(manifest['languages'])} language(s)")
    return 1 if problems else 0


def cmd_check() -> int:
    failed = False
    messages, where, problems = extract()
    for p in problems:
        print("problem:", p)
        failed = True
    current = EN.read_text(encoding="utf-8") if EN.exists() else ""
    if current != _dump(_en_doc(messages)) or not CONTEXT.exists() or CONTEXT.read_text(encoding="utf-8") != _dump(where):
        print("languages/en.json is out of date: run  python -m Tools.i18n extract")
        failed = True
    untranslated = find_untranslated()
    for u in untranslated:
        print("not translatable:", u)
    if untranslated:
        print(f"{len(untranslated)} text(s) written straight into the interface: wrap them in $t()/t()/tn()/N_() "
              f"(or add the comment “{IGNORE_MARK}” on that line when it's not for people)")
        failed = True
    manifest, mproblems = build_manifest()
    for p in mproblems:
        print("problem:", p)
        failed = True
    if MANIFEST.exists() or manifest["languages"]:
        on_disk = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else None
        strip = lambda m: {**m, "app_version": None} if m else m  # noqa: E731
        if strip(on_disk) != strip(manifest):
            print("languages/manifest.json is out of date: run  python -m Tools.i18n manifest")
            failed = True
    if not failed:
        print(f"Translations OK: {len(messages)} texts, {len(manifest['languages'])} other language(s)")
    return 1 if failed else 0


# ---- test languages -------------------------------------------------------------------------

_ACCENTS = str.maketrans("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
                         "àƀçđéƒĝĥîĵķļɱñöþǫŕšţûṽŵẋýžÀƁÇĐÉƑĜĤÎĴĶĻṀÑÖÞǪŔŠŢÛṼŴẊÝŽ")


def _pseudo(text: str) -> str:
    parts = re.split(r"(\{[a-zA-Z_][a-zA-Z0-9_]*\})", text)
    body = "".join(p if p.startswith("{") else p.translate(_ACCENTS) for p in parts)
    pad = "~" * max(1, len(text) // 3)
    return f"[{body} {pad}]"


def cmd_pseudo(folder: str) -> int:
    messages, _, _ = extract()
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    stretched = {k: ({f: _pseudo(t) for f, t in v.items()} if isinstance(v, dict) else _pseudo(v)) for k, v in messages.items()}
    files = {
        "qps-ploc.json": {"language": {"code": "qps-ploc", "name": "Test: stretched", "native": "Ţéšţ ~~~",
                                       "direction": "ltr", "version": "test"}, "messages": stretched},
        "qps-plocm.json": {"language": {"code": "qps-plocm", "name": "Test: right to left", "native": "Test (RTL)",
                                        "direction": "rtl", "version": "test"}, "messages": messages},
    }
    for name, doc in files.items():
        (out / name).write_text(_dump(doc), encoding="utf-8")
        print(out / name)
    return 0


def _read(path: str) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(doc, dict) or not isinstance(doc.get("messages"), dict):
        raise SystemExit(f"{path}: not a language file")
    return doc


def cmd_missing(path: str) -> int:
    messages, _, _ = extract()
    doc = _read(path)
    have = doc["messages"]
    todo = {k: v for k, v in messages.items() if not have.get(k) or have.get(k) == k and not isinstance(v, dict)}
    out = Path(path).with_suffix(".missing.json")
    out.write_text(_dump({"language": doc.get("language", {}), "messages": todo}), encoding="utf-8")
    print(f"{out}: {len(todo)} of {len(messages)} texts to translate")
    return 0


def cmd_merge(path: str, part: str) -> int:
    messages, _, _ = extract()
    doc, new = _read(path), _read(part)
    merged = {**doc["messages"], **{k: v for k, v in new["messages"].items() if v}}
    doc["messages"] = {k: merged[k] for k in messages if k in merged}
    Path(path).write_text(_dump(doc), encoding="utf-8")
    print(f"{path}: {len(doc['messages'])} of {len(messages)} texts translated")
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "check"
    if cmd == "extract":
        return cmd_extract()
    if cmd == "check":
        return cmd_check()
    if cmd == "manifest":
        return cmd_manifest()
    if cmd == "missing" and len(argv) > 1:
        return cmd_missing(argv[1])
    if cmd == "merge" and len(argv) > 2:
        return cmd_merge(argv[1], argv[2])
    if cmd == "pseudo":
        return cmd_pseudo(argv[1] if len(argv) > 1 else str(ROOT / "data" / "test-languages"))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
