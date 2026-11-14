// Who is signed in, their permissions and per-user interface preferences.
// Preferences are saved to the account (/api/me/settings: "theme", shared
// with the classic interface, plus one "ui" object for everything else), so
// they follow the user to any browser or device.
import { reactive } from "vue";
import { api } from "../lib/api.js";

const THEMES = ["midnight", "slate", "daylight"];
const VIEWS = ["grid", "list", "table"];
// Multiples of 30, so full rows fit both 5 per row (sidebar open) and 6 (collapsed).
const PAGE_SIZES = [30, 60, 90, 120];
const SORT_KEYS = ["download_datetime", "upload_date", "title", "creator", "ratings", "views", "tech_duration", "tech_filesize", "tech_resolution"];
export const OPTIONAL_BADGES = ["codec", "filesize", "orientation", "rating", "gone", "favorite", "status"];
// Badges on the small related-video thumbnails. The bottom row has room for
// RELATED_BADGE_MAX; rating and 💀 sit in the top corners and don't count.
export const RELATED_BADGES_BOTTOM = ["duration", "quality", "codec", "filesize"];
export const RELATED_BADGES_TOP = ["rating", "gone", "favorite"];
export const RELATED_BADGES = [...RELATED_BADGES_BOTTOM, ...RELATED_BADGES_TOP];
export const RELATED_BADGE_MAX = 3;
const HEX = /^#[0-9a-f]{6}$/i;
const CUSTOM_BASES = ["midnight", "daylight"];

export const DEFAULT_UI = Object.freeze({
  view: "grid",
  pageSize: 60,
  textScale: 110,
  badges: { codec: true, filesize: true, orientation: true, rating: true, gone: true, favorite: true, status: true },
  goneColor: "#dc2626",
  backMode: "library",          // "library" | "history"
  groupCodecs: true,            // Library filters: one entry per codec ("AV1"), not per version ("AV1 (Main@L4.0)")
  suggestKeepOpen: true,        // Edit details: suggestion list stays open after picking a value
  miniPlayer: true,             // keep playing in the floating mini-player after leaving a video page
  abRepeat: false,              // player: an A-B button to repeat one part of a video (2.18.0)
  resumePlayback: true,         // continue a video where I left off (2.24.0)
  videoFrames: false,           // the row of frames under the player, click to jump (3.2.0; off: the seekbar previews do it)
  frameSearch: null,            // Search this frame: [{name, url, field}]; null = Google Lens (lib/frameSearch.js)
  rememberVolume: true,         // each browser keeps the volume you last set (3.2.0)
  startVolume: 10,              // % -- a new browser, or every video when rememberVolume is off
  watchedBar: true,             // thin "how far I got" line on thumbnails (2.24.0)
  panicKeys: [],                // [{code, label}] (max 3) pressed together: stop and show an empty page (2.24.0)
  touchSides: "swipe",          // phones in fullscreen: "swipe" brightness/volume on the sides, or "seek" double-tap (2.19.0)
  // Pop-up toasts for background updates, by group (stores/notifications.js).
  toasts: { download: true, reencode: true, upload: true, job: true, duplicate: true },
  defaultSort: "download_datetime",
  defaultDir: "desc",
  tablePreviewScale: 200,
  relatedCount: 15,
  relatedBadges: ["quality", "duration", "rating", "gone"],
  chipColorsOn: true,
  chipColors: { performer: "#ec4899", tag: "#38bdf8", music: "#22c55e", creator: "#f97316" },
  // A custom theme starts from a dark or light base and overrides four colours;
  // the rest (borders, hover, dimmed text) are derived from them.
  customTheme: { enabled: false, base: "midnight", bg: "#0b0b0d", panel: "#17171b", text: "#f2f2f3", accent: "#f97316" },
});

export const PREF_OPTIONS = { THEMES, VIEWS, PAGE_SIZES, SORT_KEYS, CUSTOM_BASES };

const clampNum = (v, min, max, fallback) => (Number.isFinite(Number(v)) ? Math.min(max, Math.max(min, Number(v))) : fallback);

/** Accepts whatever was saved (possibly by an older version) and returns a complete, valid object. */
export function normalizeUi(raw) {
  const r = raw && typeof raw === "object" ? raw : {};
  const badges = { ...DEFAULT_UI.badges };
  for (const k of OPTIONAL_BADGES) if (typeof r.badges?.[k] === "boolean") badges[k] = r.badges[k];
  return {
    view: VIEWS.includes(r.view) ? r.view : DEFAULT_UI.view,
    pageSize: nearestPageSize(r.pageSize),
    textScale: clampNum(r.textScale, 80, 150, DEFAULT_UI.textScale),
    badges,
    goneColor: HEX.test(r.goneColor || "") ? r.goneColor : DEFAULT_UI.goneColor,
    backMode: r.backMode === "history" ? "history" : "library",
    suggestKeepOpen: typeof r.suggestKeepOpen === "boolean" ? r.suggestKeepOpen : DEFAULT_UI.suggestKeepOpen,
    groupCodecs: typeof r.groupCodecs === "boolean" ? r.groupCodecs : DEFAULT_UI.groupCodecs,
    miniPlayer: typeof r.miniPlayer === "boolean" ? r.miniPlayer : DEFAULT_UI.miniPlayer,
    abRepeat: r.abRepeat === true,
    touchSides: r.touchSides === "seek" ? "seek" : "swipe",
    resumePlayback: r.resumePlayback !== false,
    videoFrames: r.videoFrames === true,
    frameSearch: Array.isArray(r.frameSearch) ? r.frameSearch : null,
    rememberVolume: r.rememberVolume !== false,
    startVolume: clampNum(r.startVolume, 0, 100, DEFAULT_UI.startVolume),
    watchedBar: r.watchedBar !== false,
    panicKeys: Array.isArray(r.panicKeys)
      ? r.panicKeys.filter((k) => k && typeof k.code === "string" && k.code.length <= 32).slice(0, 3)
        .map((k) => ({ code: k.code, label: String(k.label || k.code).slice(0, 16) }))
      : [],
    toasts: Object.fromEntries(Object.entries(DEFAULT_UI.toasts).map(([k, v]) => [k, typeof r.toasts?.[k] === "boolean" ? r.toasts[k] : v])),
    defaultSort: SORT_KEYS.includes(r.defaultSort) ? r.defaultSort : DEFAULT_UI.defaultSort, // an old "created_at" becomes Date acquired
    defaultDir: r.defaultDir === "asc" ? "asc" : "desc",
    tablePreviewScale: clampNum(r.tablePreviewScale, 100, 400, DEFAULT_UI.tablePreviewScale),
    relatedCount: Math.round(clampNum(r.relatedCount, 0, 40, DEFAULT_UI.relatedCount)),
    relatedBadges: Array.isArray(r.relatedBadges)
      ? [
        ...[...new Set(r.relatedBadges.filter((b) => RELATED_BADGES_BOTTOM.includes(b)))].slice(0, RELATED_BADGE_MAX),
        ...RELATED_BADGES_TOP.filter((b) => r.relatedBadges.includes(b)),
      ]
      : [...DEFAULT_UI.relatedBadges],
    chipColorsOn: typeof r.chipColorsOn === "boolean" ? r.chipColorsOn : DEFAULT_UI.chipColorsOn,
    chipColors: pickColors(r.chipColors, DEFAULT_UI.chipColors),
    customTheme: {
      ...pickColors(r.customTheme, DEFAULT_UI.customTheme),
      enabled: r.customTheme?.enabled === true,
      base: CUSTOM_BASES.includes(r.customTheme?.base) ? r.customTheme.base : DEFAULT_UI.customTheme.base,
    },
  };
}

/** Older versions offered 24/48/96/192: map those to the closest current size. */
function nearestPageSize(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_UI.pageSize;
  return PAGE_SIZES.reduce((best, s) => (Math.abs(s - n) < Math.abs(best - n) ? s : best), PAGE_SIZES[0]);
}

function pickColors(raw, defaults) {
  const out = {};
  for (const [k, v] of Object.entries(defaults)) {
    if (typeof v === "string" && v.startsWith("#")) out[k] = HEX.test(raw?.[k] || "") ? raw[k] : v;
  }
  return out;
}

function readCachedUi() {
  try { return normalizeUi(JSON.parse(localStorage.getItem("pmv-ui") || "null")); } catch { return normalizeUi(null); }
}

export const session = reactive({
  ready: false,
  user: null,
  setupRequired: false,
  prefs: {
    theme: localStorage.getItem("pmv-theme") || "midnight",
    sidebarCollapsed: localStorage.getItem("pmv-sidebar") === "collapsed",
    adminFolded: localStorage.getItem("pmv-admin-nav") === "folded",
    ...readCachedUi(),
  },
});

export function hasPermission(permission) {
  const perms = session.user?.permissions || [];
  return perms.includes("*") || perms.includes(permission);
}
export const isAdmin = () => hasPermission("*");
/** True when any one of the permissions is held (a string or a list). */
export const hasAny = (perms) => (Array.isArray(perms) ? perms : [perms]).some(hasPermission);

const CUSTOM_VARS = ["--bg", "--bg-elev", "--panel", "--panel-2", "--panel-hover", "--border", "--border-strong",
  "--text", "--text-2", "--text-dim", "--accent", "--accent-hover", "--accent-soft", "--thumb-bg"];

/** Applies theme, text size and colours to the page. */
export function applyPrefs() {
  const p = session.prefs;
  const root = document.documentElement;
  const custom = p.customTheme?.enabled ? p.customTheme : null;
  root.dataset.theme = custom ? custom.base : THEMES.includes(p.theme) ? p.theme : "midnight";
  CUSTOM_VARS.forEach((v) => root.style.removeProperty(v));
  if (custom) {
    const mix = (a, b, pct) => `color-mix(in srgb, ${a} ${100 - pct}%, ${b})`;
    const { bg, panel, text, accent } = custom;
    const vars = {
      "--bg": bg, "--bg-elev": mix(bg, panel, 50), "--panel": panel,
      "--panel-2": mix(panel, text, 5), "--panel-hover": mix(panel, text, 9),
      "--border": mix(panel, text, 12), "--border-strong": mix(panel, text, 20),
      "--text": text, "--text-2": mix(text, bg, 22), "--text-dim": mix(text, bg, 45),
      "--accent": accent, "--accent-hover": mix(accent, text, 18), "--accent-soft": `color-mix(in srgb, ${accent} 15%, transparent)`,
      "--thumb-bg": mix(bg, text, 6),
    };
    for (const [k, v] of Object.entries(vars)) root.style.setProperty(k, v);
  }
  // All sizes in the interface are in rem, so this one value scales everything.
  root.style.fontSize = `${(16 * p.textScale) / 100}px`;
  root.style.setProperty("--gone-bg", p.goneColor);
  for (const [k, v] of Object.entries(p.chipColors || {})) root.style.setProperty(`--chip-${k}`, v);
  root.classList.toggle("chips-colored", !!p.chipColorsOn);
}
applyPrefs();

function uiSnapshot() {
  const p = session.prefs;
  return normalizeUi({
    view: p.view, pageSize: p.pageSize, textScale: p.textScale, badges: p.badges, goneColor: p.goneColor,
    backMode: p.backMode, suggestKeepOpen: p.suggestKeepOpen, groupCodecs: p.groupCodecs, miniPlayer: p.miniPlayer, abRepeat: p.abRepeat, touchSides: p.touchSides, resumePlayback: p.resumePlayback, videoFrames: p.videoFrames, frameSearch: p.frameSearch, rememberVolume: p.rememberVolume, startVolume: p.startVolume, watchedBar: p.watchedBar, panicKeys: p.panicKeys, toasts: p.toasts, defaultSort: p.defaultSort, defaultDir: p.defaultDir, tablePreviewScale: p.tablePreviewScale,
    relatedCount: p.relatedCount, relatedBadges: p.relatedBadges, chipColorsOn: p.chipColorsOn, chipColors: p.chipColors,
    customTheme: p.customTheme,
  });
}

async function loadPrefs() {
  try {
    const s = (await api("/api/me/settings")) || {};
    if (THEMES.includes(s.theme)) session.prefs.theme = s.theme;
    if (s.ui) Object.assign(session.prefs, normalizeUi(s.ui));
  } catch { /* keep cached/defaults */ }
  applyPrefs();
  cacheLocally();
}

function cacheLocally() {
  try {
    localStorage.setItem("pmv-theme", session.prefs.theme);
    localStorage.setItem("pmv-ui", JSON.stringify(uiSnapshot()));
  } catch { /* storage unavailable */ }
}

let saveTimer = null;
/** Changes a preference, applies it immediately and saves it to the account. */
export function savePref(key, value) {
  session.prefs[key] = value;
  if (key === "sidebarCollapsed") {
    try { localStorage.setItem("pmv-sidebar", value ? "collapsed" : "expanded"); } catch { /* ignore */ }
    return;
  }
  if (key === "adminFolded") {
    try { localStorage.setItem("pmv-admin-nav", value ? "folded" : "open"); } catch { /* ignore */ }
    return;
  }
  applyPrefs();
  cacheLocally();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => flushPrefs(), 400);
}

/** Sends pending preference changes now. `keepalive` lets the request finish while the page closes. */
function flushPrefs(keepalive = false) {
  if (saveTimer === null) return;
  clearTimeout(saveTimer);
  saveTimer = null;
  fetch("/api/me/settings", {
    method: "PATCH", credentials: "same-origin", keepalive, headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: { theme: session.prefs.theme, ui: uiSnapshot() } }),
  }).catch(() => { /* not critical: kept locally */ });
}
// Closing or reloading the tab right after a change would otherwise drop it.
window.addEventListener("pagehide", () => flushPrefs(true));

/** After the admin changed roles / groups / someone's access: fetch my current
 *  permissions and update them in place (the page re-checks what it shows). */
export async function refreshPermissions() {
  if (!session.user) return;
  try {
    const status = await api("/api/auth/status");
    if (status?.user) Object.assign(session.user, { role: status.user.role, permissions: status.user.permissions });
  } catch { /* signed out: the next request handles it */ }
}

export async function loadSession() {
  try {
    const status = await api("/api/auth/status");
    if (!status?.user) throw new Error("not signed in");
    session.user = status.user;
    session.setupRequired = false;
    await loadPrefs();
  } catch {
    session.user = null;
    try { session.setupRequired = !!(await api("/api/setup/status")).setup_required; } catch { session.setupRequired = false; }
  }
  session.ready = true;
}

export async function login(username, password) {
  const result = await api("/api/auth/login", { method: "POST", body: { username, password } });
  session.user = result.user;
  await loadPrefs();
}

export async function completeSetup(setupCode, username, password) {
  const result = await api("/api/setup/admin", { method: "POST", body: { setup_code: setupCode, username, password } });
  session.user = result.user;
  session.setupRequired = false;
  await loadPrefs();
}

export async function logout() {
  try { await api("/api/auth/logout", { method: "POST" }); } catch { /* signed out anyway */ }
  session.user = null;
}
