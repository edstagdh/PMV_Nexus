<script setup>
// Per-user preferences for this interface, in tabs. Every change applies at
// once and is saved to the account, so it follows the user to other browsers.
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
import { cleanEngines, GOOGLE_LENS } from "../lib/frameSearch.js";

const route = useRoute();
const router = useRouter();
const TABS = [
  { key: "appearance", label: "Appearance" },
  { key: "library", label: "Library" },
  { key: "video", label: "Video page" },
  { key: "notifications", label: "Notifications" },
  { key: "account", label: "Account", show: () => hasPermission("self.password_reset") || !isAdmin() },
];
const tabs = computed(() => TABS.filter((t) => !t.show || t.show()));
const tab = computed(() => (tabs.value.some((t) => t.key === route.query.tab) ? route.query.tab : "appearance"));
function openTab(key) { router.replace({ query: { ...route.query, tab: key } }); }

const THEME_LABELS = { midnight: "Midnight", slate: "Slate", daylight: "Daylight" };
const SORT_LABELS = {
  download_datetime: "Date acquired", upload_date: "Uploaded to PMVHaven", title: "Title", creator: "Creator",
  ratings: "Rating", views: "Views on PMVHaven", tech_duration: "Duration (length)", tech_filesize: "File size", tech_resolution: "Resolution",
};
const BADGE_LABELS = {
  duration: "Duration", quality: "Quality", codec: "Codec (H.264 / HEVC / AV1)", filesize: "File size",
  orientation: "Orientation (🖥️ / 📱)", rating: "Rating", gone: "💀 Removed from PMVHaven", favorite: "♥ In my Favorites",
  status: "Work in progress (re-encode, repair)",
};
const GONE_COLORS = ["#dc2626", "#b91c1c", "#e11d48", "#7c3aed", "#ea580c", "#111827"];
const CHIP_KINDS = [
  { key: "performer", label: "Performers", sample: ["Aurora Vale", "Luna Prism"] },
  { key: "tag", label: "Tags", sample: ["#pmv", "#compilation"] },
  { key: "music", label: "Music", sample: ["Track — Artist"] },
  { key: "creator", label: "Uploader", sample: ["CreatorName"], text: true },
];
const THEME_COLORS = [
  { key: "bg", label: "Background" }, { key: "panel", label: "Panels" },
  { key: "text", label: "Text" }, { key: "accent", label: "Accent" },
];
const THEME_STARTERS = {
  midnight: { base: "midnight", bg: "#0b0b0d", panel: "#17171b", text: "#f2f2f3", accent: "#f97316" },
  daylight: { base: "daylight", bg: "#f4f4f6", panel: "#ffffff", text: "#18181b", accent: "#ea580c" },
};

const sample = {
  id: "sample", url: "", title: "Sample", status: "done", creator: "Creator", tech_duration: "3:45",
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

function setToast(key, on) { savePref("toasts", { ...session.prefs.toasts, [key]: on }); }

function resetTab() {
  const keys = {
    appearance: ["textScale", "goneColor", "chipColorsOn", "chipColors", "customTheme"],
    library: ["defaultSort", "defaultDir", "badges", "tablePreviewScale", "groupCodecs"],
    video: ["relatedCount", "relatedBadges", "suggestKeepOpen", "miniPlayer", "abRepeat", "touchSides", "resumePlayback", "videoFrames", "frameSearch", "rememberVolume", "startVolume", "watchedBar", "panicKeys"],
    notifications: ["toasts"],
  }[tab.value] || [];
  for (const k of keys) {
    const v = DEFAULT_UI[k];
    savePref(k, Array.isArray(v) ? [...v] : typeof v === "object" ? { ...v } : v);
  }
  if (tab.value === "appearance") savePref("theme", "midnight");
  toast("Reset to defaults");
}

// ---- account ----
const pw = ref({ current: "", next: "", confirm: "" });
const pwBusy = ref(false);
const pwError = ref("");
async function changePassword() {
  pwError.value = "";
  if (pw.value.next !== pw.value.confirm) { pwError.value = "The new passwords don't match."; return; }
  pwBusy.value = true;
  try {
    await api("/api/me/password", { method: "POST", body: { current_password: pw.value.current, new_password: pw.value.next } });
    pw.value = { current: "", next: "", confirm: "" };
    toast("Password changed — other signed-in devices were signed out", { type: "success" });
  } catch (err) {
    pwError.value = err.message || "Could not change the password";
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
    del.value.error = err.message || "Could not delete the account";
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
    toast("Give it a name, an https:// upload address and the form's file field name (letters, numbers, _ - . [ ])", { type: "error" });
    return;
  }
  saveEngines([...engines.value, e[0]]);
  engineDraft.value = { name: "", url: "https://", field: "" };
}
function removeEngine(n) { saveEngines(engines.value.filter((_, i) => i !== n)); }
function resetEngines() { saveEngines([{ ...GOOGLE_LENS }]); }
const volumeDraft = ref(null);
const volumeShown = computed(() => volumeDraft.value ?? session.prefs.startVolume ?? 10);
function commitVolume() { if (volumeDraft.value !== null) { savePref("startVolume", volumeDraft.value); volumeDraft.value = null; } }
</script>

<template>
  <div class="settings">
    <header class="head">
      <h1>My Settings</h1>
      <button v-if="tab !== 'account'" class="btn btn-ghost btn-sm" @click="resetTab">Reset this tab</button>
    </header>

    <nav class="tabs" role="tablist">
      <button v-for="t in tabs" :key="t.key" role="tab" class="tab" :class="{ active: tab === t.key }"
              :aria-selected="tab === t.key" @click="openTab(t.key)">{{ t.label }}</button>
    </nav>

    <!-- ================= Appearance ================= -->
    <section v-if="tab === 'appearance'" class="card group">
      <div class="row top">
        <div class="label"><strong>Theme</strong><span class="muted">Custom lets you pick the main colours yourself.</span></div>
        <div class="stack">
          <div class="seg" role="group" aria-label="Theme">
            <button v-for="t in PREF_OPTIONS.THEMES" :key="t" class="btn" :class="{ active: !customOn && session.prefs.theme === t }" @click="pickTheme(t)">{{ THEME_LABELS[t] }}</button>
            <button class="btn" :class="{ active: customOn }" @click="pickCustom">Custom</button>
          </div>
          <div v-if="customOn" class="custom">
            <div class="colors">
              <label v-for="c in THEME_COLORS" :key="c.key" class="color-field">
                <input type="color" :value="session.prefs.customTheme[c.key]" @input="setCustom(c.key, $event.target.value)">
                <span>{{ c.label }}</span>
              </label>
            </div>
            <div class="inline small">
              <span class="muted">Base</span>
              <select class="select select-sm" :value="session.prefs.customTheme.base" @change="setCustom('base', $event.target.value)">
                <option value="midnight">Dark</option><option value="daylight">Light</option>
              </select>
              <span class="muted">Start from</span>
              <button class="btn btn-sm" @click="startCustomFrom('midnight')">Dark preset</button>
              <button class="btn btn-sm" @click="startCustomFrom('daylight')">Light preset</button>
            </div>
          </div>
        </div>
      </div>
      <div class="row">
        <div class="label"><strong>Text size</strong><span class="muted">Scales all text, buttons and spacing. Applied when you let go.</span></div>
        <div class="range">
          <input type="range" min="80" max="150" step="5" :value="textScaleShown" aria-label="Text size"
                 @input="textScaleDraft = Number($event.target.value)" @change="commitTextScale">
          <output>{{ textScaleShown }}%</output>
        </div>
      </div>
      <div class="row top">
        <div class="label"><strong>Coloured chips</strong><span class="muted">Performers, tags and music on the video page and in the list view; performer and uploader names in related videos.</span></div>
        <div class="stack">
          <label class="switch"><input type="checkbox" :checked="session.prefs.chipColorsOn" @change="savePref('chipColorsOn', $event.target.checked)">Use colours</label>
          <div v-for="k in CHIP_KINDS" :key="k.key" class="chip-row" :class="{ dim: !session.prefs.chipColorsOn }">
            <label class="color-field">
              <input type="color" :value="session.prefs.chipColors[k.key]" :disabled="!session.prefs.chipColorsOn" @input="setChipColor(k.key, $event.target.value)">
              <span>{{ k.label }}</span>
            </label>
            <template v-if="k.text"><span v-for="s in k.sample" :key="s" class="text-creator uploader-sample">{{ s }}</span></template>
            <template v-else><span v-for="s in k.sample" :key="s" class="chip" :class="`chip-${k.key}`">{{ s }}</span></template>
          </div>
        </div>
      </div>
      <div class="row">
        <div class="label"><strong>💀 badge colour</strong><span class="muted">Background of the "removed from PMVHaven" badge.</span></div>
        <div class="swatches">
          <button v-for="c in GONE_COLORS" :key="c" class="swatch" :class="{ on: session.prefs.goneColor === c }" :style="{ background: c }"
                  :aria-label="`Use ${c}`" @click="savePref('goneColor', c)" />
          <label class="swatch custom-swatch" title="Custom colour">
            <input type="color" :value="session.prefs.goneColor" @input="savePref('goneColor', $event.target.value)">
          </label>
        </div>
      </div>
    </section>

    <!-- ================= Library ================= -->
    <section v-else-if="tab === 'library'" class="card group">
      <div class="row">
        <div class="label"><strong>Default sort</strong><span class="muted">Used when no sort is chosen on the page.</span></div>
        <div class="inline">
          <select class="select" :value="session.prefs.defaultSort" @change="savePref('defaultSort', $event.target.value)">
            <option v-for="k in PREF_OPTIONS.SORT_KEYS" :key="k" :value="k">{{ SORT_LABELS[k] }}</option>
          </select>
          <select class="select" :value="session.prefs.defaultDir" @change="savePref('defaultDir', $event.target.value)">
            <option value="desc">Descending</option><option value="asc">Ascending</option>
          </select>
        </div>
      </div>
      <div class="row top">
        <div class="label"><strong>Thumbnail badges</strong><span class="muted">Duration and quality are always shown.</span></div>
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
        <div class="label"><strong>Group codec versions</strong><span class="muted">In Filters, show each codec once — AV1, AVC, HEVC… —
          instead of every version (“AV1 (Main@L4.0)”, “AV1 (Main@L5.1)”…). Picking one finds all its versions.</span></div>
        <label class="switch"><input type="checkbox" :checked="session.prefs.groupCodecs" @change="savePref('groupCodecs', $event.target.checked)">On</label>
      </div>
      <div class="row">
        <div class="label"><strong>Table hover preview</strong><span class="muted">Size of the preview shown when hovering a row in the table view.</span></div>
        <div class="range">
          <input type="range" min="100" max="400" step="25" :value="session.prefs.tablePreviewScale" aria-label="Table preview size"
                 @input="savePref('tablePreviewScale', Number($event.target.value))">
          <output>{{ session.prefs.tablePreviewScale }}%</output>
        </div>
      </div>
    </section>

    <!-- ================= Video page ================= -->
    <section v-else-if="tab === 'video'" class="card group">
      <div class="row">
        <div class="label">
          <strong>Related videos</strong>
          <span class="muted">How many to show (0 hides the column). Ranked by shared performers (strongest), the same creator, then shared tags.</span>
        </div>
        <div class="range">
          <input type="range" min="0" max="40" step="1" :value="session.prefs.relatedCount" aria-label="Number of related videos"
                 @input="savePref('relatedCount', Number($event.target.value))">
          <output>{{ session.prefs.relatedCount }}</output>
        </div>
      </div>
      <div class="row top">
        <div class="label">
          <strong>Related-video badges</strong>
          <span class="muted">Bottom row: up to {{ RELATED_BADGE_MAX }} fit on the small thumbnails. Rating and 💀 sit in the top corners and don't count.</span>
        </div>
        <div class="badges small-preview">
          <div class="toggles">
            <span class="sub">Bottom row</span>
            <label v-for="b in RELATED_BADGES_BOTTOM" :key="b" class="switch" :class="{ dim: relatedFull && !session.prefs.relatedBadges.includes(b) }">
              <input type="checkbox" :checked="session.prefs.relatedBadges.includes(b)"
                     :disabled="relatedFull && !session.prefs.relatedBadges.includes(b)"
                     @change="toggleRelatedBadge(b, $event.target.checked)">{{ BADGE_LABELS[b] }}
            </label>
            <span class="sub">Top corners</span>
            <label v-for="b in RELATED_BADGES_TOP" :key="b" class="switch">
              <input type="checkbox" :checked="session.prefs.relatedBadges.includes(b)" @change="toggleRelatedBadge(b, $event.target.checked)">{{ BADGE_LABELS[b] }}
            </label>
          </div>
          <div class="preview"><VideoThumb :item="sample" compact /></div>
        </div>
      </div>
      <div class="row">
        <div class="label">
          <strong>Mini-player</strong>
          <span class="muted">Leaving a video page while it plays (or while a playlist plays) keeps it going in a small floating player. Off: leaving the page stops the video.</span>
        </div>
        <label class="switch"><input type="checkbox" :checked="session.prefs.miniPlayer" @change="savePref('miniPlayer', $event.target.checked)">Use the mini-player</label>
      </div>
      <div class="row">
        <div class="label">
          <strong>A-B repeat</strong>
          <span class="muted">Adds an A-B button to the player for playing one part of a video over and over: click once to mark the start (A), again to mark the end (B), and a third time to stop.</span>
        </div>
        <label class="switch"><input type="checkbox" :checked="session.prefs.abRepeat" @change="savePref('abRepeat', $event.target.checked)">Show the A-B button</label>
      </div>
      <div class="row">
        <div class="label">
          <strong>Continue where I left off</strong>
          <span class="muted">Opening a video I stopped partway through starts there (with a <em>Start over</em> button for a few seconds). Saved for my account, so it follows me to other devices. Videos watched to the end start from the beginning.</span>
        </div>
        <label class="switch"><input type="checkbox" :checked="session.prefs.resumePlayback !== false" @change="savePref('resumePlayback', $event.target.checked)">Continue</label>
      </div>
      <div class="row">
        <div class="label">
          <strong>Frames under the player</strong>
          <span class="muted">A row of pictures from across the video, with their time, under the player — click one to jump there. PMVHaven's own timeline frames when the video has them, otherwise 8 made from the file. Off by default: the seek bar's previews do the same.</span>
        </div>
        <label class="switch"><input type="checkbox" :checked="session.prefs.videoFrames === true" @change="savePref('videoFrames', $event.target.checked)">Show them</label>
      </div>
      <div class="row">
        <div class="label">
          <strong>Starting volume</strong>
          <span class="muted">The volume a video starts at on a computer (phones use their own volume buttons).</span>
        </div>
        <div class="stack">
          <div class="range">
            <input type="range" min="0" max="100" step="5" :value="volumeShown" aria-label="Starting volume"
                   @input="volumeDraft = Number($event.target.value)" @change="commitVolume">
            <output>{{ volumeShown }}%</output>
          </div>
          <label class="switch"><input type="checkbox" :checked="session.prefs.rememberVolume !== false" @change="savePref('rememberVolume', $event.target.checked)">Keep the volume I last set in this browser instead</label>
        </div>
      </div>
      <div class="row top">
        <div class="label">
          <strong>Search this frame</strong>
          <span class="muted">⚙ → Search frame in the player pauses the video and looks the picture up on a reverse image search site, in a
            new tab. The picture is uploaded to that site straight from your browser. With several sites you pick one each time.
            A site is added with the address its upload form sends the picture to and the form's file field name.</span>
        </div>
        <div class="stack">
          <ul class="engines">
            <li v-for="(e, n) in engines" :key="n"><strong>{{ e.name }}</strong><code class="muted small">{{ e.url }} · {{ e.field }}</code>
              <button class="btn btn-ghost btn-sm" type="button" :title="`Remove ${e.name}`" @click="removeEngine(n)">Remove</button></li>
            <li v-if="!engines.length" class="muted">None — Search frame is hidden in the player.</li>
          </ul>
          <form class="engine-add" @submit.prevent="addEngine">
            <input v-model="engineDraft.name" class="input" placeholder="Name" aria-label="Name" maxlength="40">
            <input v-model="engineDraft.url" class="input" placeholder="https://… (upload address)" aria-label="Upload address">
            <input v-model="engineDraft.field" class="input" placeholder="File field" aria-label="File field name" maxlength="60">
            <button class="btn btn-sm" type="submit">Add</button>
          </form>
          <button v-if="engines.length !== 1 || engines[0].url !== GOOGLE_LENS.url" class="btn btn-ghost btn-sm reset-engines" type="button" @click="resetEngines">Only Google Lens</button>
        </div>
      </div>
      <div class="row">
        <div class="label">
          <strong>Show how far I got on thumbnails</strong>
          <span class="muted">A thin red line along the bottom of a video's picture, like YouTube.</span>
        </div>
        <label class="switch"><input type="checkbox" :checked="session.prefs.watchedBar !== false" @change="savePref('watchedBar', $event.target.checked)">Show it</label>
      </div>
      <div class="row">
        <div class="label">
          <strong>Panic keys</strong>
          <span class="muted">Press these keys together anywhere in the app (on a keyboard) to stop playback at once and switch to an empty white page with no title. The browser's Back button returns. The browser's own shortcuts keep working; while you type in a text field, only keys including Ctrl, Alt or ⌘ / Win count.</span>
        </div>
        <PanicKeysSetting />
      </div>
      <div class="row">
        <div class="label">
          <strong>Sides of the video in fullscreen (phones and tablets)</strong>
          <span class="muted">Swipe up / down on the left for brightness and on the right for volume (iPhones and iPads don't let pages change the volume: use the buttons), or double-tap to skip back / forward. Outside fullscreen, double-tap always skips.</span>
        </div>
        <select class="select" :value="session.prefs.touchSides" aria-label="Sides of the video in fullscreen" @change="savePref('touchSides', $event.target.value)">
          <option value="swipe">Swipe: brightness / volume</option>
          <option value="seek">Double-tap: skip 10 s</option>
        </select>
      </div>
      <div class="row">
        <div class="label">
          <strong>Edit details: keep suggestions open</strong>
          <span class="muted">After you pick a performer, tag or music value, the list of suggestions stays open so you can pick the next one. Off: it closes until you type again.</span>
        </div>
        <label class="switch"><input type="checkbox" :checked="session.prefs.suggestKeepOpen" @change="savePref('suggestKeepOpen', $event.target.checked)">Keep open</label>
      </div>
    </section>

    <!-- ================= Notifications ================= -->
    <section v-else-if="tab === 'notifications'" class="card group">
      <div class="row">
        <div class="label">
          <strong>Pop-ups (toasts)</strong>
          <span class="muted">A short pop-up in the corner when something finishes or fails in the background. Everything is also kept under
            the bell, whatever you pick here. Low disk space always pops up.<template v-if="!isAdmin()"> You're told about the videos you added.</template></span>
        </div>
        <div class="toggles">
          <label class="switch"><input type="checkbox" :checked="session.prefs.toasts.download" @change="setToast('download', $event.target.checked)">Downloads finished / failed</label>
          <label class="switch"><input type="checkbox" :checked="session.prefs.toasts.reencode" @change="setToast('reencode', $event.target.checked)">Re-encodes and repairs finished / failed</label>
          <label class="switch"><input type="checkbox" :checked="session.prefs.toasts.upload" @change="setToast('upload', $event.target.checked)">Uploads finished / failed</label>
          <label v-if="isAdmin()" class="switch"><input type="checkbox" :checked="session.prefs.toasts.job" @change="setToast('job', $event.target.checked)">Jobs finished / failed (scans, syncs, backups, performer matching)</label>
          <label v-if="isAdmin()" class="switch"><input type="checkbox" :checked="session.prefs.toasts.duplicate" @change="setToast('duplicate', $event.target.checked)">Possible duplicates found</label>
        </div>
      </div>
    </section>

    <!-- ================= Account ================= -->
    <section v-else-if="tab === 'account'" class="card group">
      <div class="row top">
        <div class="label"><strong>Change password</strong><span class="muted">Signed in as {{ session.user?.username }}. Other devices are signed out.</span></div>
        <form class="pw" @submit.prevent="changePassword">
          <input v-model="pw.current" class="input" type="password" autocomplete="current-password" placeholder="Current password" required>
          <input v-model="pw.next" class="input" type="password" autocomplete="new-password" placeholder="New password" required>
          <input v-model="pw.confirm" class="input" type="password" autocomplete="new-password" placeholder="Repeat new password" required>
          <PasswordChecklist :password="pw.next" :username="session.user?.username" />
          <div v-if="pw.confirm && pw.confirm !== pw.next" class="error">The new passwords don't match.</div>
          <div v-if="pwError" class="error">{{ pwError }}</div>
          <button class="btn btn-primary" :disabled="pwBusy">{{ pwBusy ? "Saving…" : "Change password" }}</button>
        </form>
      </div>
      <div v-if="!isAdmin()" class="row top">
        <div class="label"><strong>Delete my account</strong><span class="muted">Removes your account and settings for good and signs you out. The library is not affected.</span></div>
        <div>
          <button v-if="!del.open" class="btn btn-danger" @click="del.open = true">Delete my account…</button>
          <form v-else class="pw" @submit.prevent="deleteAccount">
            <input v-model="del.username" class="input" autocomplete="off" :placeholder="`Type your username (${session.user?.username})`" required>
            <input v-model="del.password" class="input" type="password" autocomplete="current-password" placeholder="Current password" required>
            <div v-if="del.error" class="error">{{ del.error }}</div>
            <div class="inline">
              <button class="btn" type="button" @click="del.open = false">Cancel</button>
              <button class="btn btn-danger" :disabled="del.busy || del.username !== session.user?.username">{{ del.busy ? "Deleting…" : "Delete for good" }}</button>
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
.reset-engines { align-self: flex-start; }
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
