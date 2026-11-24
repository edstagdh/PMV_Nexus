# Translating PMV Nexus

PMV Nexus shows its interface — and the messages its server sends (errors, notifications, Tasks) — in any language
that has a file here. Everyone picks theirs in **My Settings → Language**; admins install languages there too, from
this folder on GitHub or from a file.

## The files

| File | What it is |
|---|---|
| `en.json` | Every text of the app, in English. **Generated** from the code (`python -m Tools.i18n extract`) — never edit it by hand. |
| `context.json` | Where each text is used (file names), to help with ambiguous words. Generated too. |
| `<code>.json` | One language, e.g. `fr.json`, `pt-BR.json`. The file name is its code. |
| `manifest.json` | Size and SHA-256 of every language file. The app downloads only files that match it. Generated: `python -m Tools.i18n manifest`. |

A language file looks like this:

```json
{
 "language": {
  "code": "fr",
  "name": "French",
  "native": "Français",
  "direction": "ltr",
  "version": "1",
  "translator": "optional — who made it"
 },
 "messages": {
  "Clear filters": "Effacer les filtres",
  "Saved “{name}”": "« {name} » enregistré",
  "{n} videos": { "one": "{n} vidéo", "other": "{n} vidéos" }
 }
}
```

- **The key is the English text.** Only change the values.
- `direction` is `"rtl"` for languages written right to left; the whole interface then mirrors.
- Bump `version` whenever you change the file, so people see “Update available”.
- `{placeholders}` are filled in by the app (a name, a number, a date). Keep every one, spelled exactly the same; you
  may move them. An entry whose placeholders don't match the English is ignored and the English shows instead.
- **Plurals** (entries whose English value is an object) use the language's own forms: `zero`, `one`, `two`, `few`,
  `many`, `other` — whichever your language has (see the [CLDR plural rules](https://www.unicode.org/cldr/charts/latest/supplemental/language_plural_rules.html)). `other` is always required.
- Texts are shown as plain text: HTML does nothing. Keep symbols and emoji (`·`, `—`, `→`, `♥`, `💀`, `★`) as they are.
- A text you leave out stays English — a half-finished language works.
- Leave these as they are: **PMV Nexus, PMVHaven, HMVHaven, PornTubeAI, ThePornDB, Shazam, Gemini, VirusTotal,
  FFmpeg, AVC / HEVC / AV1, H.264, CRF, MB / GB**, and search words like `tag:` or `performer:`.
- Video titles, names, tags and descriptions are never translated — they aren't in these files.

## Making a language with an AI model

1. Copy `en.json` to `<code>.json` and fill in the `language` part.
2. Give the AI the file (in parts if it's too long for it) with this prompt:

   > Translate the values of the `"messages"` object in this JSON from English to **[language]**. It's the interface
   > of a self-hosted video library app (an admin and a few people at home use it in a browser). Rules:
   > keep every key exactly as it is (the keys are the English text); translate only the values; keep every
   > `{placeholder}` exactly as written (you may move it within the sentence); for values that are objects
   > (`"one"`, `"other"`), give the plural forms **[language]** uses, always including `"other"`; keep symbols, emoji
   > and these names untranslated: PMV Nexus, PMVHaven, HMVHaven, PornTubeAI, ThePornDB, Shazam, Gemini,
   > VirusTotal, FFmpeg, AVC, HEVC, AV1, H.264, CRF; keep `tag:`, `performer:`, `title:` and similar search words in
   > English; use the same word for the same thing everywhere (Library, Playlist, Performer, Creator, Uploader, Tag,
   > Re-encode, Repair, Tasks, Management, Server Settings, My Settings); buttons are short commands; answer with
   > the JSON only.

3. Check it in the app before publishing: **My Settings → Language → Install from a file…** shows how many texts
   were used and which ones were left in English, and why.

## After an app update

New versions add and change texts. To translate only what's new:

```
python -m Tools.i18n missing languages/fr.json          # writes languages/fr.missing.json
#   ... translate fr.missing.json (same prompt) ...
python -m Tools.i18n merge languages/fr.json languages/fr.missing.json
```

`merge` also drops texts the app no longer has.

## Publishing a language

1. Put `<code>.json` in this folder.
2. `python -m Tools.i18n manifest` (updates the sizes and SHA-256 hashes)
3. `python -m Tools.i18n check` (the same check GitHub runs: every file valid, manifest up to date)
4. Commit `<code>.json` and `manifest.json` together. The *Translations* workflow checks them on every push.

The app downloads from this repository's default branch, checks each file's size and SHA-256 against
`manifest.json`, then checks every entry before installing it.

## For developers

Every text people read goes through the translation functions — the *Translations* workflow fails otherwise:

- Interface templates: `{{ $t("Clear filters") }}`, `$t("Saved “{name}”", { name })`, `$tn("{n} video", "{n} videos", count)`.
  A sentence with markup inside: `<Tx msg="Keep {video} and remove the rest"><template #video><strong>…</strong></template></Tx>`.
- Interface scripts: `t(...)`, `tn(...)` from `lib/i18n.js`; text in a constant made when a file loads: `N_("…")`, shown with `t(label)`.
- Server: `L("Couldn't read {file}", file=name)` from `backend/i18n.py` — an ordinary `str` (logs stay English) that is
  translated where it leaves the server. Server texts avoid plurals ("Videos queued: {n}").
- Always write the English out in the call (no f-strings or variables), then run `python -m Tools.i18n extract`.
- A line that isn't for people (a key, a CSS value) can carry the comment `i18n-ignore`.
- Layout: use logical CSS (`margin-inline-start`, `inset-inline-end`, `text-align: start`) so right-to-left
  languages mirror by themselves. `python -m Tools.i18n pseudo` makes two test languages (stretched accents, and
  right to left) to install from a file.
