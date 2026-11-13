<script setup>
// Management -> Songs (2.14.0): songs found in videos, waiting to be added to
// their Music or dismissed; "Find songs now" runs the Scheduler job once
// (videos without music); and the settings (backend/library_mixins/songs.py).
import { askConfirm } from "../../stores/confirm.js";
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { RouterLink } from "vue-router";
import Icon from "../../components/Icon.vue";
import SongSuggestion from "../../components/songs/SongSuggestion.vue";
import SongMethodPicker from "../../components/songs/SongMethodPicker.vue";
import { api } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";
import { hasPermission } from "../../stores/session.js";
import { onLibraryChange } from "../../stores/live.js";

const data = ref(null);
const error = ref("");
const cfg = ref(null);
const busy = ref("");
const rows = ref({}); // suggestion id -> SongSuggestion (for "Add all")
const canSettings = hasPermission("settings.manage");

async function load() {
  try {
    data.value = await api("/api/songs");
    if (!cfg.value) cfg.value = { ...data.value.settings };
    error.value = "";
  } catch (err) { error.value = err.message || "Could not load the songs"; }
}
let timer = null;
let stop = null;
onMounted(() => {
  load();
  stop = onLibraryChange((c) => { if (!c || c.includes("songs")) { clearTimeout(timer); timer = setTimeout(load, 400); } });
});
onBeforeUnmount(() => { stop?.(); clearTimeout(timer); });

const isSure = (s) => s.hits >= 2 && s.source !== "Gemini (AI)";  // AI-only finds are never "sure"
const sureCount = computed(() => (data.value?.videos || []).reduce((n, v) => n + v.suggestions.filter(isSure).length, 0));

async function scanNow() {
  busy.value = "scan";
  try { await api("/api/songs/scan-now", { method: "POST" }); toast("Listening to videos without music — follow it on Tasks", { type: "success" }); }
  catch (err) { toast(err.message || "Could not start", { type: "error" }); }
  finally { busy.value = ""; }
}
async function addAllSure() {
  const sure = data.value.videos.flatMap((v) => v.suggestions.filter(isSure));
  if (!(await askConfirm(`Add ${sure.length} songs heard in two or more samples to their videos?`, { ok: "Add" }))) return;
  busy.value = "all";
  for (const s of sure) await rows.value[s.id]?.add();
  busy.value = "";
  load();
}
async function saveSettings() {
  busy.value = "settings";
  try { await api("/api/settings/songs", { method: "POST", body: cfg.value }); toast("Saved", { type: "success" }); }
  catch (err) { toast(err.message || "Could not save", { type: "error" }); }
  finally { busy.value = ""; }
}
</script>

<template>
  <div class="page songs">
    <p class="muted small intro">Songs heard in videos, waiting for you. <strong>Add</strong> puts one in the video's Music (you can fix its
      name first); <strong>Dismiss</strong> hides it for that video for good. Songs heard in two or more samples are the surest.
      A single video: <strong>Find songs</strong> in its ⋮ menu.</p>
    <p v-if="error" class="error-text">{{ error }}</p>

    <section v-if="data" class="card block head">
      <div class="stats">
        <div><strong>{{ data.counts.without_music }}</strong><span>videos without music</span></div>
        <div><strong>{{ data.counts.to_scan }}</strong><span>to listen to</span></div>
        <div><strong>{{ data.counts.pending }}</strong><span>songs waiting ({{ data.counts.pending_videos }} videos)</span></div>
        <div><strong>{{ data.counts.added }}</strong><span>added so far</span></div>
      </div>
      <div class="acts">
        <button class="btn btn-sm btn-primary" :disabled="busy === 'scan' || !data.counts.to_scan" @click="scanNow">
          <Icon name="music" :size="14" />Find songs now ({{ Math.min(data.counts.to_scan, data.settings.per_run) }} videos)</button>
        <button v-if="sureCount" class="btn btn-sm" :disabled="busy === 'all'" @click="addAllSure">Add all sure ones ({{ sureCount }})</button>
      </div>
      <p class="muted small">To do this regularly: Management → Scheduler → new job <strong>Find songs in videos without music</strong>
        (it listens to {{ data.settings.per_run }} videos per run, newest first). Uses the way set below.</p>
      <p v-if="data.scanning.length" class="muted small">Listening now: {{ data.scanning.length }} video(s) — see Tasks.</p>
    </section>
    <div v-else class="skeleton" style="height: 6rem; border-radius: var(--radius)" />

    <section v-for="v in data?.videos || []" :key="v.item_id" class="card block video">
      <div class="vhead">
        <RouterLink :to="{ name: 'video', params: { id: v.item_id } }" class="thumb">
          <img :src="`/api/library/${encodeURIComponent(v.item_id)}/thumbnail`" alt="" loading="lazy">
        </RouterLink>
        <div>
          <RouterLink :to="{ name: 'video', params: { id: v.item_id } }" class="vtitle">{{ v.title }}</RouterLink>
          <div class="muted small">{{ v.creator }}<template v-if="v.music.length"> · Music now: {{ v.music.join(", ") }}</template></div>
        </div>
      </div>
      <SongSuggestion v-for="s in v.suggestions" :key="s.id" :ref="(el) => { if (el) rows[s.id] = el; else delete rows[s.id]; }" :s="s" @done="load" />
    </section>
    <p v-if="data && !data.videos.length" class="muted">No songs waiting.</p>

    <section v-if="canSettings && cfg" class="card block">
      <h2>Settings</h2>
      <div class="grid">
        <label>A sample every<span><input v-model.number="cfg.interval" class="input input-num" type="number" min="15" max="300"> s of the video</span></label>
        <label>At most<span><input v-model.number="cfg.max_samples" class="input input-num" type="number" min="1" max="40"> samples per video</span></label>
        <label>Each Scheduler run<span><input v-model.number="cfg.per_run" class="input input-num" type="number" min="1" max="500"> videos</span></label>
        <label>Listen again after<span><input v-model.number="cfg.rescan_days" class="input input-num" type="number" min="0" max="3650"> days (0 = never)</span></label>
        <label class="switch"><input v-model="cfg.shazam_retry" type="checkbox">Shazam: try a missed sample again 7 s later</label>
        <SongMethodPicker v-model="cfg.method" :gemini="!!data?.gemini" label="Find songs with (Scheduler, Find songs now; preselected elsewhere)" class="wide" />
        <label>Gemini, at most<span><input v-model.number="cfg.gemini_max_samples" class="input input-num" type="number" min="1" max="20"> clips per video</span></label>
      </div>
      <p class="muted small">Optional. Gemini is Google's AI: it gets a 20-second sound clip of a spot in the video and names the
        song only when it's sure. When finding songs by hand (a video's Find songs, or picked videos) the way can be
        changed each time; its finds are marked “Gemini (AI)” — check them before adding. It needs a free
        API key in Server settings → Credentials{{ data?.gemini ? " (saved)" : " (not saved yet)" }}; the free tier allows a
        limited number of requests a day, and Google may use free-tier requests to improve its products.</p>
      <div><button class="btn btn-primary btn-sm" :disabled="busy === 'settings'" @click="saveSettings">Save</button></div>
    </section>
  </div>
</template>

<style scoped>
.wide { grid-column: 1 / -1; }
.intro { margin: 0; max-width: 52rem; }
.small { font-size: 0.8rem; }
.block { padding: 1rem 1.125rem; display: flex; flex-direction: column; gap: 0.5rem; }
.block h2 { font-size: 1.05rem; margin: 0; }
.block p { margin: 0; }
.stats { display: flex; gap: 1.75rem; flex-wrap: wrap; }
.stats div { display: flex; flex-direction: column; }
.stats strong { font-size: 1.35rem; }
.stats span { font-size: 0.78rem; color: var(--text-2); }
.acts { display: flex; gap: 0.5rem; flex-wrap: wrap; }
.vhead { display: flex; gap: 0.75rem; align-items: center; }
.thumb img { width: 7.5rem; aspect-ratio: 16 / 9; object-fit: cover; border-radius: var(--radius); display: block; background: var(--panel-hover); }
.vtitle { font-weight: 700; color: var(--text); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(17rem, 1fr)); gap: 0.6rem 1.25rem; }
.grid label { display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.84rem; font-weight: 600; }
.grid label span { display: flex; align-items: center; gap: 0.4rem; font-weight: 400; color: var(--text-2); }
.grid .switch { flex-direction: row; align-items: center; }
</style>
