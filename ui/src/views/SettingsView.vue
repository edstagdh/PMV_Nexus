<script setup>
// Per-user preferences for this interface, in tabs. Every change applies at
// once and is saved to the account, so it follows the user to other browsers.
import LanguageSettings from "../components/LanguageSettings.vue";
import { t } from "../lib/i18n.js";
import PanicKeysSetting from "../components/PanicKeysSetting.vue";
import { ref, computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import VideoThumb from "../components/VideoThumb.vue";
import PasswordChecklist from "../components/PasswordChecklist.vue";
import {
  session, savePref, hasPermission, isAdmin, DEFAULT_UI, PREF_OPTIONS, OPTIONAL_BADGES, RELATED_BADGES_BOTTOM, RELATED_BADGES_TOP, RELATED_BADGE_MAX,
} from "../stores/session.js";
import { api } from "../lib/api.js";
import { toast } from "../stores/toast.js";
import { cleanEngines, DEFAULT_ENGINES, PRESETS } from "../lib/frameSearch.js";
import { PICK_MODES } from "../lib/wallPick.js";
import { WALL_DETAILS } from "../stores/session.js";

const route = useRoute();
const router = useRouter();
const TABS = [
  { key: "appearance", label: t("Appearance") },
  { key: "library", label: t("Library") },
  { key: "video", label: t("Video page") },
  { key: "wall", label: t("The Wall") },
  { key: "faces", label: t("Faces"), show: () => hasPermission("performers.manage") },
  { key: "notifications", label: t("Notifications") },
  { key: "language", label: t("Language") },
  { key: "account", label: t("Account"), show: () => hasPermission("self.password_reset") || !isAdmin() },
];
const tabs = computed(() => TABS.filter((tb) => !tb.show || tb.show()));
const tab = computed(() => (tabs.value.some((tb) => tb.key === route.query.tab) ? route.query.tab : "appearance"));
function openTab(key) { router.replace({ query: { ...route.query, tab: key } }); }

const THEME_LABELS = { midnight: t("Midnight"), slate: t("Slate"), daylight: t("Daylight") };
const SORT_LABELS = {
  download_datetime: t("Date acquired"), upload_date: t("Uploaded to PMVHaven"), title: t("Title"), creator: t("Creator"),
  ratings: t("Rating"), views: t("Views on PMVHaven"), tech_duration: t("Duration (length)"), tech_filesize: t("File size"), tech_resolution: t("Resolution"),
  fav_performers: t("Favourite performers (theirs first or last)"),
  random: t("Random (a new order each time you open the library)"),
};
const BADGE_LABELS = {
  duration: t("Duration"), quality: t("Quality"), codec: t("Codec (H.264 / HEVC / AV1)"), filesize: t("File size"),
  orientation: t("Orientation (🖥️ / 📱)"), rating: t("Rating"), gone: t("💀 Removed from PMVHaven"), favorite: t("♥ In my Favorites"),
  status: t("Work in progress (re-encode, repair)"),
};
const GONE_COLORS = ["#dc2626", "#b91c1c", "#e11d48", "#7c3aed", "#ea580c", "#111827"];
const CHIP_KINDS = [
  // 3.5.0: performers by gender (from ThePornDB, or the silhouette set on their page)
  { key: "female", label: t("Performers: female"), sample: [t("Aurora Vale")], g: "female" },
  { key: "male", label: t("Performers: male"), sample: [t("Rex Steel")], g: "male" },
  { key: "trans", label: t("Performers: trans / non-binary"), sample: [t("Sky Rivera")], g: "trans" },
  { key: "unknown", label: t("Performers: not known"), sample: [t("Luna Prism")], g: "unknown" },
  { key: "tag", label: t("Tags"), sample: ["#pmv", "#compilation"] },
  { key: "music", label: t("Music"), sample: [t("Track — Artist")] },
  { key: "creator", label: t("Uploader"), sample: ["CreatorName"], text: true },
];
const THEME_COLORS = [
  { key: "bg", label: t("Background") }, { key: "panel", label: t("Panels") },
  { key: "text", label: t("Text") }, { key: "accent", label: t("Accent") },
];
const THEME_STARTERS = {
  midnight: { base: "midnight", bg: "#0b0b0d", panel: "#17171b", text: "#f2f2f3", accent: "#f97316" },
  daylight: { base: "daylight", bg: "#f4f4f6", panel: "#ffffff", text: "#18181b", accent: "#ea580c" },
};

const sample = {
  id: "sample", url: "", title: t("Sample"), status: "done", creator: t("Creator"), tech_duration: "3:45",
  tech_resolution: "1920x1080", tech_codec: "hevc", tech_filesize: "182.4 MB", ratings: "92%",
  site_status: "removed", reencode_status: "encoding", reencode_progress_percent: 42,
};

// ---- appearance ----
const customOn = computed(() => session.prefs.customTheme.enabled);
function pickTheme(t) {
  if (session.prefs.customTheme.enabled) savePref("customTheme", { ...session.prefs.customTheme, enabled: false });
  savePref("theme", t);
}
function pickCustom() { savePref("customTheme", { ...session.prefs.customTheme, enabled: true }); }
function setCustom(key, value) { savePref("customTheme", { ...session.prefs.customTheme, [key]: value, enabled: true }); }
function startCustomFrom(name) { savePref("customTheme", { ...THEME_STARTERS[name], enabled: true }); }

// The text-size slider only shows the value while dragging and applies it on
// release -- applying while dragging rescales the slider under the pointer.
const textScaleDraft = ref(null);
const textScaleShown = computed(() => textScaleDraft.value ?? session.prefs.textScale);
function commitTextScale(e) { savePref("textScale", Number(e.target.value)); textScaleDraft.value = null; }

function setChipColor(kind, value) { savePref("chipColors", { ...session.prefs.chipColors, [kind]: value }); }

// ---- library ----
function setBadge(key, on) { savePref("badges", { ...session.prefs.badges, [key]: on }); }

// ---- video page ----
function toggleRelatedBadge(key, on) {
  const cur = session.prefs.relatedBadges.filter((b) => b !== key);
  savePref("relatedBadges", on ? [...cur, key] : cur);
}
const relatedFull = computed(() => session.prefs.relatedBadges.filter((b) => RELATED_BADGES_BOTTOM.includes(b)).length >= RELATED_BADGE_MAX);

// My Settings -> The Wall (3.9.2: nearly every part of it); stores/session.js has the ranges
const pct = (v) => `${v}%`;
const WALL_DETAIL_LABELS = WALL_DETAILS.map((k) => [k, {
  length: t("Length"), quality: t("Quality"), size: t("File size"), codec: t("Codec"), rating: t("Rating"), views: t("Views"),
  likes: t("Likes"), uploaded: t("Uploaded"), added: t("Added to the library"), plays: t("Your plays"), tags: t("Tags"),
  music: t("Music"), description: t("Description"),
}[k]]);
function toggleDetail(k, on) {
  const now = new Set(session.prefs.wallDetails);
  if (on) now.add(k); else now.delete(k);
  savePref("wallDetails", WALL_DETAILS.filter((x) => now.has(x)));
}
// 3.13.0: Management -> Faces (people who manage performers)
const SHOWN = [["open", t("Open")], ["collapsed", t("Closed, open with a click")], ["hidden", t("Hidden")]];
const FACES_GROUPS = [
  { title: t("Management → Faces"), items: [
    { key: "facesPageSize", kind: "range", label: t("Matches per page"), min: 10, max: 500, step: 10 },
    { key: "facesPictureSize", kind: "select", number: true, label: t("Picture size"), help: t("The face found in the video, and the performer's photo next to it."),
      options: [[96, t("Small")], [160, t("Medium")], [240, t("Large")], [320, t("Very large")]] },
    { key: "facesMinScore", kind: "range", label: t("Hide matches below this score"), min: 0, max: 100, step: 1,
      help: t("Only hides them for you; Remove below… on the Faces page deletes waiting ones for everyone."), show: (v) => (v ? `${v}` : t("Show all")) },
    { key: "facesSort", kind: "select", label: t("Sorted by"), options: [["score", t("Score")], ["performer", t("Performer")], ["video", t("Video")],
      ["found", t("When found / decided")], ["faces", t("Faces that look like them")], ["time", t("Time in the video")]] },
    { key: "facesDesc", kind: "switch", label: t("Highest / newest / Z first") },
  ] },
  { title: t("Video page"), items: [
    { key: "videoFaces", kind: "select", label: t("Faces found (waiting for you)"), options: SHOWN },
    { key: "videoFacesPageSize", kind: "range", label: t("Faces shown at a time there"), min: 1, max: 50, step: 1 },
  ] },
];
const GROUPS = computed(() => (tab.value === "faces" ? FACES_GROUPS : WALL_GROUPS));
const WALL_GROUPS = [
  { title: t("Opening it"), items: [
    { key: "wallStartView", kind: "select", label: t("Opens in"), options: [["last", t("The view used last")], ["3d", "3D"], ["2d", "2D"]] },
    { key: "wallZoomStart", kind: "range", min: 25, max: 400, step: 5, show: pct, label: t("Starting zoom"), help: t("How close it starts. 100 % shows everything; more starts closer.") },
    { key: "wallPanel", kind: "switch", label: t("Side panel open"), help: t("On wide screens. On a phone it opens when you pick something.") },
    { key: "wallMinShared", kind: "select", number: true, label: t("Shared videos at least"), help: t("Lines shown to start with. Auto: 1, or 2 in a very large library."),
      options: [[0, t("Auto")], ...Array.from({ length: 10 }, (_, i) => [i + 1, String(i + 1)])] },
    { key: "wallWithCo", kind: "switch", label: t("Also who they’re with"), help: t("Ticked to start with: a performer comes with the other performers of their videos.") },
    { key: "wallMaxVideos", kind: "range", min: 50, max: 1000, step: 50, label: t("Videos around a performer"), help: t("At most. More shows everything but is heavier to turn.") },
  ] },
  { title: t("Moving around"), items: [
    { key: "wallZoomStep", kind: "range", min: 1, max: 20, step: 1, show: pct, label: t("Zoom per wheel step"), help: t("How much one mouse-wheel step zooms in or out. Hold Shift for five times as much; + and − on the keyboard zoom five steps.") },
    { key: "wallTurnSpeed", kind: "range", min: 0.5, max: 6, step: 0.5, label: t("Turning speed (3D)"), help: t("How far a drag turns it.") },
    { key: "wallMovePct", kind: "range", min: 25, max: 300, step: 5, show: pct, label: t("Moving speed (3D)"), help: t("How far a right-drag (two fingers) moves it: 100 % = the pictures follow the pointer exactly.") },
    { key: "wallGlide", kind: "range", min: 0, max: 10, step: 1, label: t("Glide (3D)"), help: t("How long it keeps going after you let go. 0 stops at once.") },
    { key: "wallArrows", kind: "select", label: t("Arrow keys"), help: t("Hold Shift for the other one: turning, or moving when this is set to turn (3D)."),
      options: [["fly", t("Fly: ↑ ↓ forward and back, ← → sideways")], ["move", t("Slide: up, down, left and right")], ["turn", t("Turn it (3D)")]] },
    { key: "wallKeySpeed", kind: "range", min: 1, max: 10, step: 1, label: t("Arrow key speed") },
  ] },
  { title: t("Looks"), items: [
    { key: "wallPictureSize", kind: "range", min: 50, max: 200, step: 10, show: pct, label: t("Size of dots and pictures") },
    { key: "wallDetail", kind: "select", number: true, label: t("Picture detail"), help: t("Sharper pictures use more memory and can make turning less smooth."),
      options: [[128, t("Low (128 px)")], [256, t("Normal (256 px)")], [512, t("High (512 px)")]] },
    { key: "wallLines", kind: "range", min: 10, max: 100, step: 5, show: pct, label: t("Line strength") },
    { key: "wallTitles", kind: "select", label: t("Video titles (2D)"), options: [["zoomed", t("When zoomed in")], ["always", t("Always")], ["never", t("Never")]] },
    { key: "wallBackground", kind: "select", label: t("Background"), options: [["theme", t("The theme's")], ["black", t("Black")]] },
  ] },
  { title: t("Previews"), items: [
    { key: "wallHover", kind: "switch", label: t("Play the preview on hover"), help: t("Pointing at a video with the mouse plays its preview, muted like in the library.") },
    { key: "wallHoverDelay", kind: "range", min: 0, max: 1000, step: 50, show: (v) => t("{ms} ms", { ms: v }), label: t("Wait before playing"), off: () => !session.prefs.wallHover },
    { key: "wallHoverSize", kind: "select", label: t("Preview size"), help: t("For pictures shown smaller than this."), options: [["s", t("Small")], ["m", t("Medium")], ["l", t("Large")]] },
    { key: "wallHoverTitle", kind: "select", label: t("Title"), options: [["over", t("On the video")], ["under", t("Under the video")]] },
    { key: "wallHoverGrow", kind: "switch", label: t("As big as the picture"), help: t("A picture shown bigger than the preview size plays its preview right on it, at its size.") },
  ] },
  { title: t("Clicking a video"), items: [
    { key: "wallDblClick", kind: "select", label: t("Double-click"), help: t("A click shows the video in the side panel. A double-click on a video (or a performer: their profile) can also open it."),
      options: [["none", t("Nothing more")], ["same", t("Open it in this tab")], ["new", t("Open it in a new tab")]] },
    { key: "wallDetails", kind: "checks", label: t("Details in the side panel"), options: WALL_DETAIL_LABELS },
  ] },
  { title: t("Random"), items: [
    { key: "wallPick", kind: "select", label: t("Random picks"), help: t("Which of a performer's videos The Wall's Random button opens. Pressing it again gives another one."),
      options: Object.entries(PICK_MODES).map(([k, l]) => [k, t(l)]) },
    { key: "wallPickAnimate", kind: "switch", label: t("Show the way there"), help: t("The picked video lights up, The Wall flies to it and zooms in, then the video opens. Off: the video opens right away. Click, Esc or Skip opens it at once.") },
    { key: "wallPickSeconds", kind: "range", min: 2, max: 12, step: 0.5, show: (v) => t("{s} s", { s: v }), label: t("How long it takes"), help: t("From the button to the video page."), off: () => !session.prefs.wallPickAnimate },
  ] },
];

function setToast(key, on) { savePref("toasts", { ...session.prefs.toasts, [key]: on }); }

function resetTab() {
  const keys = {
    appearance: ["textScale", "goneColor", "chipColorsOn", "chipColors", "customTheme"],
    library: ["defaultSort", "defaultDir", "badges", "tablePreviewScale", "groupCodecs"],
    video: ["sharedScenes", "relatedCount", "relatedOrder", "relatedBadges", "suggestKeepOpen", "miniPlayer", "abRepeat", "touchSides", "resumePlayback", "videoFrames", "frameSearch", "rememberVolume", "startVolume", "watchedBar", "panicKeys"],
    notifications: ["toasts"],
    wall: WALL_GROUPS.flatMap((g) => g.items.map((it) => it.key)),
    faces: FACES_GROUPS.flatMap((g) => g.items.map((it) => it.key)),
  }[tab.value] || [];
  for (const k of keys) {
    const v = DEFAULT_UI[k];
    savePref(k, Array.isArray(v) ? [...v] : typeof v === "object" ? { ...v } : v);
  }
  if (tab.value === "appearance") savePref("theme", "midnight");
  toast(t("Reset to defaults"));
}

// ---- account ----
const pw = ref({ current: "", next: "", confirm: "" });
const pwBusy = ref(false);
const pwError = ref("");
async function changePassword() {
  pwError.value = "";
  if (pw.value.next !== pw.value.confirm) { pwError.value = t("The new passwords don't match."); return; }
  pwBusy.value = true;
  try {
    await api("/api/me/password", { method: "POST", body: { current_password: pw.value.current, new_password: pw.value.next } });
    pw.value = { current: "", next: "", confirm: "" };
    toast(t("Password changed — other signed-in devices were signed out"), { type: "success" });
  } catch (err) {
    pwError.value = err.message || t("Could not change the password");
  } finally {
    pwBusy.value = false;
  }
}

const del = ref({ open: false, username: "", password: "", busy: false, error: "" });
async function deleteAccount() {
  del.value.busy = true;
  del.value.error = "";
  try {
    await api("/api/me/delete-account", { method: "POST", body: { confirm_username: del.value.username, current_password: del.value.password } });
    session.user = null; // back to the sign-in screen
  } catch (err) {
    del.value.error = err.message || t("Could not delete the account");
  } finally {
    del.value.busy = false;
  }
}

// ---- Video page -> Search this frame (3.2.0, lib/frameSearch.js) ----
const engines = ref(cleanEngines(session.prefs.frameSearch));
const engineDraft = ref({ name: "", url: "https://", field: "" });
function saveEngines(list) {
  savePref("frameSearch", list);
  engines.value = cleanEngines(list);
}
function addEngine() {
  const e = cleanEngines([engineDraft.value]);
  if (!e.length) {
    toast(t("Give it a name, an https:// upload address and the form's file field name (letters, numbers, _ - . [ ])"), { type: "error" });
    return;
  }
  saveEngines([...engines.value, e[0]]);
  engineDraft.value = { name: "", url: "https://", field: "" };
}
function removeEngine(n) { saveEngines(engines.value.filter((_, i) => i !== n)); }
function resetEngines() { saveEngines(DEFAULT_ENGINES.map((e) => ({ ...e }))); }
const isDefault = computed(() => engines.value.length === DEFAULT_ENGINES.length
  && DEFAULT_ENGINES.every((d, i) => engines.value[i].url === d.url && (engines.value[i].mode || "") === (d.mode || "")));
const missingPresets = computed(() => PRESETS.filter((p) => !engines.value.some((e) => e.url === p.url && (e.mode || "") === (p.mode || ""))));
function addPreset(p) { saveEngines([...engines.value, { ...p }]); }
const volumeDraft = ref(null);
const volumeShown = computed(() => volumeDraft.value ?? session.prefs.startVolume ?? 10);
function commitVolume() { if (volumeDraft.value !== null) { savePref("startVolume", volumeDraft.value); volumeDraft.value = null; } }
</script>

<template>
  <div class="settings">
    <header class="head">
      <h1>{{ $t("My Settings") }}</h1>
      <button v-if="tab !== 'account'" class="btn btn-ghost btn-sm" @click="resetTab">{{ $t("Reset this tab") }}</button>
    </header>

    <nav class="tabs" role="tablist">
      <button v-for="t in tabs" :key="t.key" role="tab" class="tab" :class="{ active: tab === t.key }"
              :aria-selected="tab === t.key" @click="openTab(t.key)">{{ t.label }}</button>
    </nav>

    <!-- ================= Appearance ================= -->
    <section v-if="tab === 'appearance'" class="card group">
      <div class="row top">
        <div class="label"><strong>{{ $t("Theme") }}</strong><span class="muted">{{ $t("Custom lets you pick the main colours yourself.") }}</span></div>
        <div class="stack">
          <div class="seg" role="group" :aria-label="$t('Theme')">
            <button v-for="t in PREF_OPTIONS.THEMES" :key="t" class="btn" :class="{ active: !customOn && session.prefs.theme === t }" @click="pickTheme(t)">{{ THEME_LABELS[t] }}</button>
            <button class="btn" :class="{ active: customOn }" @click="pickCustom">{{ $t("Custom") }}</button>
          </div>
          <div v-if="customOn" class="custom">
            <div class="colors">
              <label v-for="c in THEME_COLORS" :key="c.key" class="color-field">
                <input type="color" :value="session.prefs.customTheme[c.key]" @input="setCustom(c.key, $event.target.value)">
                <span>{{ c.label }}</span>
              </label>
            </div>
            <div class="inline small">
              <span class="muted">{{ $t("Base") }}</span>
              <select class="select select-sm" :value="session.prefs.customTheme.base" @change="setCustom('base', $event.target.value)">
                <option value="midnight">{{ $t("Dark") }}</option><option value="daylight">{{ $t("Light") }}</option>
              </select>
              <span class="muted">{{ $t("Start from") }}</span>
              <button class="btn btn-sm" @click="startCustomFrom('midnight')">{{ $t("Dark preset") }}</button>
              <button class="btn btn-sm" @click="startCustomFrom('daylight')">{{ $t("Light preset") }}</button>
            </div>
          </div>
        </div>
      </div>
      <div class="row">
        <div class="label"><strong>{{ $t("Text size") }}</strong><span class="muted">{{ $t("Scales all text, buttons and spacing. Applied when you let go.") }}</span></div>
        <div class="range">
          <input type="range" min="80" max="150" step="5" :value="textScaleShown" :aria-label="$t('Text size')"
                 @input="textScaleDraft = Number($event.target.value)" @change="commitTextScale">
          <output>{{ textScaleShown }}%</output>
        </div>
      </div>
      <div class="row top">
        <div class="label"><strong>{{ $t("Coloured chips") }}</strong><span class="muted">{{ $t("Performers, tags and music on the video page and in the list view; performer and uploader names in related videos.") }}</span></div>
        <div class="stack">
          <label class="switch"><input type="checkbox" :checked="session.prefs.chipColorsOn" @change="savePref('chipColorsOn', $event.target.checked)">{{ $t("Use colours") }}</label>
          <div v-for="k in CHIP_KINDS" :key="k.key" class="chip-row" :class="{ dim: !session.prefs.chipColorsOn }">
            <label class="color-field">
              <input type="color" :value="session.prefs.chipColors[k.key]" :disabled="!session.prefs.chipColorsOn" @input="setChipColor(k.key, $event.target.value)">
              <span>{{ k.label }}</span>
            </label>
            <template v-if="k.text"><span v-for="s in k.sample" :key="s" class="text-creator uploader-sample">{{ s }}</span></template>
            <template v-else><span v-for="s in k.sample" :key="s" class="chip" :class="k.g ? ['chip-performer', `g-${k.g}`] : `chip-${k.key}`">{{ s }}</span></template>
          </div>
        </div>
      </div>
      <div class="row">
        <div class="label"><strong>{{ $t("💀 badge colour") }}</strong><span class="muted">{{ $t("Background of the \"removed from PMVHaven\" badge.") }}</span></div>
        <div class="swatches">
          <button v-for="c in GONE_COLORS" :key="c" class="swatch" :class="{ on: session.prefs.goneColor === c }" :style="{ background: c }"
                  :aria-label="$t('Use {colour}', { colour: c })" @click="savePref('goneColor', c)" />
          <label class="swatch custom-swatch" :title="$t('Custom colour')">
            <input type="color" :value="session.prefs.goneColor" @input="savePref('goneColor', $event.target.value)">
          </label>
        </div>
      </div>
    </section>

    <!-- ================= Library ================= -->
    <section v-else-if="tab === 'library'" class="card group">
      <div class="row">
        <div class="label"><strong>{{ $t("Default sort") }}</strong><span class="muted">{{ $t("Used when no sort is chosen on the page.") }}</span></div>
        <div class="inline">
          <select class="select" :value="session.prefs.defaultSort" @change="savePref('defaultSort', $event.target.value)">
            <option v-for="k in PREF_OPTIONS.SORT_KEYS" :key="k" :value="k">{{ SORT_LABELS[k] }}</option>
          </select>
          <select v-if="session.prefs.defaultSort !== 'random'" class="select" :value="session.prefs.defaultDir" @change="savePref('defaultDir', $event.target.value)">
            <option value="desc">{{ $t("Descending") }}</option><option value="asc">{{ $t("Ascending") }}</option>
          </select>
        </div>
      </div>
      <div class="row top">
        <div class="label"><strong>{{ $t("Thumbnail badges") }}</strong><span class="muted">{{ $t("Duration and quality are always shown.") }}</span></div>
        <div class="badges">
          <div class="toggles">
            <label v-for="b in OPTIONAL_BADGES" :key="b" class="switch">
              <input type="checkbox" :checked="session.prefs.badges[b]" @change="setBadge(b, $event.target.checked)">{{ BADGE_LABELS[b] }}
            </label>
          </div>
          <div class="preview"><VideoThumb :item="sample" /></div>
        </div>
      </div>
      <div class="row">
        <div class="label"><strong>{{ $t("Group codec versions") }}</strong><span class="muted">{{ $t("In Filters, show each codec once — AV1, AVC, HEVC… — instead of every version (“AV1 (Main@L4.0)”, “AV1 (Main@L5.1)”…). Picking one finds all its versions.") }}</span></div>
        <label class="switch"><input type="checkbox" :checked="session.prefs.groupCodecs" @change="savePref('groupCodecs', $event.target.checked)">{{ $t("On") }}</label>
      </div>
      <div class="row">
        <div class="label"><strong>{{ $t("Table hover preview") }}</strong><span class="muted">{{ $t("Size of the preview shown when hovering a row in the table view.") }}</span></div>
        <div class="range">
          <input type="range" min="100" max="400" step="25" :value="session.prefs.tablePreviewScale" :aria-label="$t('Table preview size')"
                 @input="savePref('tablePreviewScale', Number($event.target.value))">
          <output>{{ session.prefs.tablePreviewScale }}%</output>
        </div>
      </div>
    </section>

    <!-- ================= Video page ================= -->
    <section v-else-if="tab === 'video'" class="card group">
      <div class="row">
        <div class="label">
          <strong>{{ $t("Related videos") }}</strong>
          <span class="muted">{{ $t("How many to show (0 hides the column). Ranked by shared performers (strongest), the same creator, then shared tags.") }}</span>
        </div>
        <div class="range">
          <input type="range" min="0" max="40" step="1" :value="session.prefs.relatedCount" :aria-label="$t('Number of related videos')"
                 @input="savePref('relatedCount', Number($event.target.value))">
          <output>{{ session.prefs.relatedCount }}</output>
        </div>
      </div>
      <div class="row">
        <div class="label">
          <strong>{{ $t("Which related videos") }}</strong>
          <span class="muted">{{ $t("The best matches, or a different random pick from all related videos on each visit (when there are more than you show).") }}</span>
        </div>
        <select class="select" :value="session.prefs.relatedOrder" :aria-label="$t('Which related videos')" @change="savePref('relatedOrder', $event.target.value)">
          <option value="best">{{ $t("Best matches") }}</option>
          <option value="random">{{ $t("Random from all related") }}</option>
        </select>
      </div>
      <div class="row top">
        <div class="label">
          <strong>{{ $t("Related-video badges") }}</strong>
          <span class="muted">{{ $t("Bottom row: up to {n} fit on the small thumbnails. Rating and 💀 sit in the top corners and don't count.", { n: RELATED_BADGE_MAX }) }}</span>
        </div>
        <div class="badges small-preview">
          <div class="toggles">
            <span class="sub">{{ $t("Bottom row") }}</span>
            <label v-for="b in RELATED_BADGES_BOTTOM" :key="b" class="switch" :class="{ dim: relatedFull && !session.prefs.relatedBadges.includes(b) }">
              <input type="checkbox" :checked="session.prefs.relatedBadges.includes(b)"
                     :disabled="relatedFull && !session.prefs.relatedBadges.includes(b)"
                     @change="toggleRelatedBadge(b, $event.target.checked)">{{ BADGE_LABELS[b] }}
            </label>
            <span class="sub">{{ $t("Top corners") }}</span>
            <label v-for="b in RELATED_BADGES_TOP" :key="b" class="switch">
              <input type="checkbox" :checked="session.prefs.relatedBadges.includes(b)" @change="toggleRelatedBadge(b, $event.target.checked)">{{ BADGE_LABELS[b] }}
            </label>
          </div>
          <div class="preview"><VideoThumb :item="sample" compact /></div>
        </div>
      </div>
      <div class="row">
        <div class="label">
          <strong>{{ $t("Mini-player") }}</strong>
          <span class="muted">{{ $t("Leaving a video page while it plays (or while a playlist plays) keeps it going in a small floating player. Off: leaving the page stops the video.") }}</span>
        </div>
        <label class="switch"><input type="checkbox" :checked="session.prefs.miniPlayer" @change="savePref('miniPlayer', $event.target.checked)">{{ $t("Use the mini-player") }}</label>
      </div>
      <div class="row">
        <div class="label">
          <strong>{{ $t("A-B repeat") }}</strong>
          <span class="muted">{{ $t("Adds an A-B button to the player for playing one part of a video over and over: click once to mark the start (A), again to mark the end (B), and a third time to stop.") }}</span>
        </div>
        <label class="switch"><input type="checkbox" :checked="session.prefs.abRepeat" @change="savePref('abRepeat', $event.target.checked)">{{ $t("Show the A-B button") }}</label>
      </div>
      <div class="row">
        <div class="label">
          <strong>{{ $t("Continue where I left off") }}</strong>
          <span class="muted"><Tx msg="Opening a video I stopped partway through starts there (with a {em1} button for a few seconds). Saved for my account, so it follows me to other devices. Videos watched to the end start from the beginning."><template #em1><em>{{ $t("Start over") }}</em></template></Tx></span>
        </div>
        <label class="switch"><input type="checkbox" :checked="session.prefs.resumePlayback !== false" @change="savePref('resumePlayback', $event.target.checked)">{{ $t("Continue") }}</label>
      </div>
      <div class="row">
        <div class="label">
          <strong>{{ $t("Shares scenes with") }}</strong>
          <span class="muted">{{ $t("Other videos that use the same footage, under the details.") }}</span>
        </div>
        <select class="select" :value="session.prefs.sharedScenes" :aria-label="$t('Shares scenes with')" @change="savePref('sharedScenes', $event.target.value)">
          <option v-for="[v, l] in SHOWN" :key="v" :value="v">{{ l }}</option>
        </select>
      </div>
      <div class="row">
        <div class="label">
          <strong>{{ $t("Frames under the player") }}</strong>
          <span class="muted">{{ $t("A row of pictures from across the video, with their time, under the player — click one to jump there. PMVHaven's own timeline frames when the video has them, otherwise 8 made from the file. Off by default: the seek bar's previews do the same.") }}</span>
        </div>
        <label class="switch"><input type="checkbox" :checked="session.prefs.videoFrames === true" @change="savePref('videoFrames', $event.target.checked)">{{ $t("Show them") }}</label>
      </div>
      <div class="row">
        <div class="label">
          <strong>{{ $t("Starting volume") }}</strong>
          <span class="muted">{{ $t("The volume a video starts at on a computer (phones use their own volume buttons).") }}</span>
        </div>
        <div class="stack">
          <div class="range">
            <input type="range" min="0" max="100" step="5" :value="volumeShown" :aria-label="$t('Starting volume')"
                   @input="volumeDraft = Number($event.target.value)" @change="commitVolume">
            <output>{{ volumeShown }}%</output>
          </div>
          <label class="switch"><input type="checkbox" :checked="session.prefs.rememberVolume !== false" @change="savePref('rememberVolume', $event.target.checked)">{{ $t("Keep the volume I last set in this browser instead") }}</label>
        </div>
      </div>
      <div class="row top">
        <div class="label">
          <strong>{{ $t("Search this frame") }}</strong>
          <span class="muted">{{ $t("⚙ → Search frame in the player pauses the video and looks the picture up on a reverse image search site, in a new tab. The picture is uploaded to that site straight from your browser. With several sites you pick one each time. Google Lens and Yandex are built in (for Yandex the app's server uploads the picture — Yandex refuses it straight from a browser); another site is added with the address its upload form sends the picture to and the form's file field name.") }}</span>
        </div>
        <div class="stack">
          <ul class="engines">
            <li v-for="(e, n) in engines" :key="n"><strong>{{ e.name }}</strong><code class="muted small">{{ e.url }} · {{ e.field }}</code>
              <button class="btn btn-ghost btn-sm" type="button" :title="$t('Remove {name}', { name: e.name })" @click="removeEngine(n)">{{ $t("Remove") }}</button></li>
            <li v-if="!engines.length" class="muted">{{ $t("None — Search frame is hidden in the player.") }}</li>
          </ul>
          <form class="engine-add" @submit.prevent="addEngine">
            <input v-model="engineDraft.name" class="input" :placeholder="$t('Name')" :aria-label="$t('Name')" maxlength="40">
            <input v-model="engineDraft.url" class="input" :placeholder="$t('https://… (upload address)')" :aria-label="$t('Upload address')">
            <input v-model="engineDraft.field" class="input" :placeholder="$t('File field')" :aria-label="$t('File field name')" maxlength="60">
            <button class="btn btn-sm" type="submit">{{ $t("Add") }}</button>
          </form>
          <div class="presets">
            <button v-for="p in missingPresets" :key="p.name" class="btn btn-sm" type="button" @click="addPreset(p)">{{ $t("Add {name}", { name: p.name }) }}</button>
            <button v-if="!isDefault" class="btn btn-ghost btn-sm" type="button" @click="resetEngines">{{ $t("Back to Google Lens + Yandex") }}</button>
          </div>
        </div>
      </div>
      <div class="row">
        <div class="label">
          <strong>{{ $t("Show how far I got on thumbnails") }}</strong>
          <span class="muted">{{ $t("A thin red line along the bottom of a video's picture, like YouTube.") }}</span>
        </div>
        <label class="switch"><input type="checkbox" :checked="session.prefs.watchedBar !== false" @change="savePref('watchedBar', $event.target.checked)">{{ $t("Show it") }}</label>
      </div>
      <div class="row">
        <div class="label">
          <strong>{{ $t("Panic keys") }}</strong>
          <span class="muted">{{ $t("Press these keys together anywhere in the app (on a keyboard) to stop playback at once and switch to an empty white page with no title. The browser's Back button returns. The browser's own shortcuts keep working; while you type in a text field, only keys including Ctrl, Alt or ⌘ / Win count.") }}</span>
        </div>
        <PanicKeysSetting />
      </div>
      <div class="row">
        <div class="label">
          <strong>{{ $t("Sides of the video in fullscreen (phones and tablets)") }}</strong>
          <span class="muted">{{ $t("Swipe up / down on the left for brightness and on the right for volume (iPhones and iPads don't let pages change the volume: use the buttons), or double-tap to skip back / forward. Outside fullscreen, double-tap always skips.") }}</span>
        </div>
        <select class="select" :value="session.prefs.touchSides" :aria-label="$t('Sides of the video in fullscreen')" @change="savePref('touchSides', $event.target.value)">
          <option value="swipe">{{ $t("Swipe: brightness / volume") }}</option>
          <option value="seek">{{ $t("Double-tap: skip 10 s") }}</option>
        </select>
      </div>
      <div class="row">
        <div class="label">
          <strong>{{ $t("Edit details: keep suggestions open") }}</strong>
          <span class="muted">{{ $t("After you pick a performer, tag or music value, the list of suggestions stays open so you can pick the next one. Off: it closes until you type again.") }}</span>
        </div>
        <label class="switch"><input type="checkbox" :checked="session.prefs.suggestKeepOpen" @change="savePref('suggestKeepOpen', $event.target.checked)">{{ $t("Keep open") }}</label>
      </div>
    </section>

    <!-- ================= Notifications ================= -->
    <!-- ================= The Wall (3.9.1; every option 3.9.2) ================= -->
    <section v-else-if="tab === 'wall' || tab === 'faces'" class="card group">
      <template v-for="g in GROUPS" :key="g.title">
        <h3 class="wall-group">{{ g.title }}</h3>
        <div v-for="it in g.items" :key="it.key" class="row">
          <div class="label"><strong>{{ it.label }}</strong><span v-if="it.help" class="muted">{{ it.help }}</span></div>
          <label v-if="it.kind === 'switch'" class="switch">
            <input type="checkbox" :checked="session.prefs[it.key]" @change="savePref(it.key, $event.target.checked)">{{ $t("On") }}
          </label>
          <div v-else-if="it.kind === 'checks'" class="checks">
            <label v-for="[v, l] in it.options" :key="v" class="switch">
              <input type="checkbox" :checked="session.prefs[it.key].includes(v)" @change="toggleDetail(v, $event.target.checked)">{{ l }}
            </label>
          </div>
          <select v-else-if="it.kind === 'select'" class="select" :value="session.prefs[it.key]" :aria-label="it.label"
                  @change="savePref(it.key, it.number ? Number($event.target.value) : $event.target.value)">
            <option v-for="[v, l] in it.options" :key="v" :value="v">{{ l }}</option>
          </select>
          <div v-else class="range">
            <input type="range" :min="it.min" :max="it.max" :step="it.step" :value="session.prefs[it.key]" :aria-label="it.label"
                   :disabled="it.off && it.off()" @input="savePref(it.key, Number($event.target.value))">
            <output>{{ it.show ? it.show(session.prefs[it.key]) : session.prefs[it.key] }}</output>
          </div>
        </div>
      </template>
    </section>

    <section v-else-if="tab === 'notifications'" class="card group">
      <div class="row">
        <div class="label">
          <strong>{{ $t("Pop-ups (toasts)") }}</strong>
          <span class="muted">{{ $t("A short pop-up in the corner when something finishes or fails in the background. Everything is also kept under the bell, whatever you pick here. Low disk space always pops up.") }}<template v-if="!isAdmin()"> {{ $t("You're told about the videos you added.") }}</template></span>
        </div>
        <div class="toggles">
          <label class="switch"><input type="checkbox" :checked="session.prefs.toasts.download" @change="setToast('download', $event.target.checked)">{{ $t("Downloads finished / failed") }}</label>
          <label class="switch"><input type="checkbox" :checked="session.prefs.toasts.reencode" @change="setToast('reencode', $event.target.checked)">{{ $t("Re-encodes and repairs finished / failed") }}</label>
          <label class="switch"><input type="checkbox" :checked="session.prefs.toasts.upload" @change="setToast('upload', $event.target.checked)">{{ $t("Uploads finished / failed") }}</label>
          <label v-if="isAdmin()" class="switch"><input type="checkbox" :checked="session.prefs.toasts.job" @change="setToast('job', $event.target.checked)">{{ $t("Jobs finished / failed (scans, syncs, backups, performer matching)") }}</label>
          <label v-if="isAdmin()" class="switch"><input type="checkbox" :checked="session.prefs.toasts.duplicate" @change="setToast('duplicate', $event.target.checked)">{{ $t("Possible duplicates found") }}</label>
        </div>
      </div>
    </section>

    <!-- ================= Account ================= -->
    <LanguageSettings v-else-if="tab === 'language'" />
    <section v-else-if="tab === 'account'" class="card group">
      <div class="row top">
        <div class="label"><strong>{{ $t("Change password") }}</strong><span class="muted">{{ $t("Signed in as {name}. Other devices are signed out.", { name: session.user?.username }) }}</span></div>
        <form class="pw" @submit.prevent="changePassword">
          <input v-model="pw.current" class="input" type="password" autocomplete="current-password" :placeholder="$t('Current password')" required>
          <input v-model="pw.next" class="input" type="password" autocomplete="new-password" :placeholder="$t('New password')" required>
          <input v-model="pw.confirm" class="input" type="password" autocomplete="new-password" :placeholder="$t('Repeat new password')" required>
          <PasswordChecklist :password="pw.next" :username="session.user?.username" />
          <div v-if="pw.confirm && pw.confirm !== pw.next" class="error">{{ $t("The new passwords don't match.") }}</div>
          <div v-if="pwError" class="error">{{ pwError }}</div>
          <button class="btn btn-primary" :disabled="pwBusy">{{ pwBusy ? $t("Saving…") : $t("Change password") }}</button>
        </form>
      </div>
      <div v-if="!isAdmin()" class="row top">
        <div class="label"><strong>{{ $t("Delete my account") }}</strong><span class="muted">{{ $t("Removes your account and settings for good and signs you out. The library is not affected.") }}</span></div>
        <div>
          <button v-if="!del.open" class="btn btn-danger" @click="del.open = true">{{ $t("Delete my account…") }}</button>
          <form v-else class="pw" @submit.prevent="deleteAccount">
            <input v-model="del.username" class="input" autocomplete="off" :placeholder="$t('Type your username ({name})', { name: session.user?.username })" required>
            <input v-model="del.password" class="input" type="password" autocomplete="current-password" :placeholder="$t('Current password')" required>
            <div v-if="del.error" class="error">{{ del.error }}</div>
            <div class="inline">
              <button class="btn" type="button" @click="del.open = false">{{ $t("Cancel") }}</button>
              <button class="btn btn-danger" :disabled="del.busy || del.username !== session.user?.username">{{ del.busy ? $t("Deleting…") : $t("Delete for good") }}</button>
            </div>
          </form>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.settings { padding: 1rem 1.5rem 2rem; display: flex; flex-direction: column; gap: 1rem; }
.head { display: flex; align-items: center; justify-content: space-between; }
.head h1 { font-size: 1.4rem; }
.group { padding: 0.25rem 1.25rem; }
.row { display: grid; grid-template-columns: minmax(12rem, 18rem) 1fr; gap: 1rem 1.5rem; align-items: center; padding: 0.875rem 0; border-top: 1px solid var(--border); }
.row:first-child { border-top: 0; }
.row.top { align-items: start; }
.wall-group { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); margin: 1.25rem 0 0; }
.wall-group:first-child { margin-top: 0.75rem; }
.wall-group + .row { border-top: 0; }
.checks { display: flex; flex-wrap: wrap; gap: 0.5rem 1.25rem; }
.label { display: flex; flex-direction: column; gap: 0.125rem; font-size: 0.875rem; }
.label .muted { font-size: 0.78rem; }
.stack { display: flex; flex-direction: column; gap: 0.75rem; }
.seg { display: flex; gap: 0.25rem; flex-wrap: wrap; }
.custom { display: flex; flex-direction: column; gap: 0.625rem; padding: 0.75rem; border: 1px solid var(--border); border-radius: var(--radius); background: var(--panel-2); }
.colors { display: flex; flex-wrap: wrap; gap: 0.75rem 1.25rem; }
.color-field { display: inline-flex; align-items: center; gap: 0.5rem; font-size: 0.84rem; cursor: pointer; min-width: 7.5rem; }
.color-field input { width: 2rem; height: 2rem; padding: 0; border: 1px solid var(--border-strong); border-radius: var(--radius-sm); background: none; cursor: pointer; }
.color-field input:disabled { cursor: not-allowed; }
.chip-row { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.dim { opacity: 0.5; }
.range { display: flex; align-items: center; gap: 0.75rem; }
.range input { flex: 1; max-width: 20rem; accent-color: var(--accent); }
.range output { min-width: 3rem; font-variant-numeric: tabular-nums; font-weight: 600; }
.inline { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
.small { font-size: 0.8rem; }
.swatches { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }
.swatch { width: 1.75rem; height: 1.75rem; border-radius: 50%; border: 2px solid transparent; outline: 1px solid var(--border-strong); cursor: pointer; padding: 0; }
.swatch.on { border-color: var(--bg); outline: 2px solid var(--accent); }
.custom-swatch { position: relative; overflow: hidden; background: conic-gradient(red, yellow, lime, cyan, blue, magenta, red); }
.custom-swatch input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }
.badges { display: grid; grid-template-columns: 1fr 15rem; gap: 1rem; align-items: start; }
.badges.small-preview { grid-template-columns: 1fr 10.5rem; }
.preview :deep(.thumb-empty) { color: transparent; background: linear-gradient(135deg, #3b2a1f, #1f2937 60%, #111827); }
.toggles { display: flex; flex-direction: column; gap: 0.625rem; }
.engines { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.375rem; }
.engines li { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.engines code { font-size: 0.75rem; overflow-wrap: anywhere; }
.engine-add { display: grid; grid-template-columns: 8rem minmax(0, 1fr) 8rem auto; gap: 0.375rem; }
.presets { display: flex; gap: 0.375rem; flex-wrap: wrap; }
@media (max-width: 40rem) { .engine-add { grid-template-columns: 1fr; } }
.sub { font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); margin-top: 0.25rem; }
.uploader-sample { font-weight: 600; font-size: 0.875rem; }
.radios { display: flex; flex-direction: column; gap: 0.75rem; }
.radio { display: flex; gap: 0.625rem; align-items: flex-start; cursor: pointer; font-size: 0.875rem; }
.radio input { margin-top: 0.2rem; accent-color: var(--accent); }
.radio > span { display: flex; flex-direction: column; gap: 0.125rem; }
.radio .muted { font-size: 0.78rem; }
.pw { display: flex; flex-direction: column; gap: 0.5rem; max-width: 20rem; }
.error { color: var(--danger); font-size: 0.84rem; }
@media (max-width: 56rem) {
  .settings { padding: 0.75rem 1rem 1.5rem; }
  .row, .badges, .badges.small-preview { grid-template-columns: 1fr; }
  .preview { max-width: 15rem; }
}
</style>
