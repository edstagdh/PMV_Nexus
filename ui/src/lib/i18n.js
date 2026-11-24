// Translations (3.7.0). The English text is the key: $t("Clear filters") in templates,
// t("Clear filters") in scripts. A language file maps that English to the translation;
// anything it doesn't have (or a broken entry the server dropped) stays English.
//
//   t("Saved {name}", { name })              placeholders in {braces} are filled in as text
//   tn("{n} video", "{n} videos", count)     plurals: the language's own plural forms
//   N_("Videos")                             marks text kept in a constant; show it with t(label)
//
// languages/en.json is generated from these calls (python -m Tools.i18n extract), so every
// text must be a plain string literal: never t(variable) for new text, never a `template`.
// Translations are only ever shown as text (no v-html), so a language file can't add markup.
import { reactive } from "vue";

const state = reactive({ code: "en", name: "English", dir: "ltr", messages: {}, ready: false });  // i18n-ignore
export const i18n = state;

const PLACEHOLDER = /\{([a-zA-Z_][a-zA-Z0-9_]*)\}/g;

// In a right-to-left language each filled-in value is isolated (FSI … PDI, invisible), so a
// size, a time or an English name keeps its own order inside the sentence ("13 MB", not "MB 13").
const FSI = "\u2068", PDI = "\u2069";
function fill(text, params) {
  if (!params) return text;
  const wrap = state.dir === "rtl" ? (v) => FSI + v + PDI : (v) => v;
  return text.replace(PLACEHOLDER, (all, key) => (params[key] === undefined || params[key] === null ? all : wrap(String(params[key]))));
}

/** Translated text (or the English) with {placeholders} filled in. */
export function t(msg, params) {
  const v = state.messages[msg];
  return fill(typeof v === "string" && v ? v : msg, params);
}

const rules = new Map();
function pluralRule(code) {
  if (!rules.has(code)) {
    let r;
    try { r = new Intl.PluralRules(code); } catch { r = new Intl.PluralRules("en"); }
    rules.set(code, r);
  }
  return rules.get(code);
}

/** Plural text: English singular and plural forms in the code, the language's own forms from its file. */
export function tn(singular, plural, n, params) {
  const v = state.messages[plural];
  const num = Number(n) || 0;
  const all = { n: formatNumber(num), ...params };
  if (v && typeof v === "object") {
    const form = v[pluralRule(state.code).select(num)] || v.other;
    if (typeof form === "string" && form) return fill(form, all);
  }
  return fill(pluralRule("en").select(num) === "one" ? singular : plural, all);
}

/** The plural form's text, translated but with its {placeholders} left in (components/Tx.vue). */
export function tnRaw(singular, plural, n) {
  const v = state.messages[plural];
  const num = Number(n) || 0;
  if (v && typeof v === "object") {
    const form = v[pluralRule(state.code).select(num)] || v.other;
    if (typeof form === "string" && form) return form;
  }
  return pluralRule("en").select(num) === "one" ? singular : plural;
}

/** Marks text for the catalogue without translating it (constants made before a language loads). */
export const N_ = (msg) => msg;

/** The locale for dates and numbers (the browser's own when the language is English). */
export function locale() {
  return state.code === "en" ? undefined : state.code;
}
export function formatNumber(n, options) {
  try { return new Intl.NumberFormat(locale(), options).format(n); } catch { return String(n); }
}

function applyDocument() {
  const root = document.documentElement;
  root.lang = state.code;
  root.dir = state.dir;
}

/** Switches to `code` (installed on the server); "en" or a failure means English. */
export async function setLanguage(code) {
  if (!code || code === "en") {
    Object.assign(state, { code: "en", name: "English", dir: "ltr", messages: {}, ready: true });  // i18n-ignore
    applyDocument();
    remember("en");
    return true;
  }
  try {
    const resp = await fetch(`/api/i18n/lang/${encodeURIComponent(code)}.json`, { credentials: "same-origin" });
    if (!resp.ok) throw new Error(String(resp.status));
    const data = await resp.json();
    const lang = data.language || {};
    Object.assign(state, {
      code: lang.code || code, name: lang.native || lang.name || code,
      dir: lang.direction === "rtl" ? "rtl" : "ltr", messages: data.messages || {}, ready: true,
    });
    applyDocument();
    remember(state.code);
    return true;
  } catch {
    if (state.code !== "en") return false;  // keep the language that's showing
    await setLanguage("en");
    return false;
  }
}

const KEY = "pmv-lang";
function remember(code) { try { localStorage.setItem(KEY, code); } catch { /* storage off */ } }
export function rememberedLanguage() { try { return localStorage.getItem(KEY) || ""; } catch { return ""; } }

let boot = null;
/** Installed languages + the server's default (no sign-in needed: the sign-in page uses it). */
export async function loadBoot(force = false) {
  if (!boot || force) {
    boot = fetch("/api/i18n/boot", { credentials: "same-origin" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  }
  return boot;
}

/** The language to show: the account's choice, else the server default (the sign-in page
 *  also tries the browser's languages), else English. Unknown codes fall back the same way. */
export async function pickLanguage(accountChoice, { signIn = false } = {}) {
  const info = await loadBoot();
  const extra = new Set((info?.languages || []).map((l) => l.code));
  const installed = new Set(["en", ...extra]);
  const wanted = [accountChoice];
  if (signIn) {
    // The browser's languages count only when one is installed here: an English browser shouldn't
    // override the admin's default with English.
    wanted.push(rememberedLanguage(), ...(navigator.languages || []).flatMap((l) => [l, l.split("-")[0]]).filter((c) => extra.has(c)));
  }
  wanted.push(info?.default || "en");
  return wanted.find((c) => c && installed.has(c)) || "en";
}
