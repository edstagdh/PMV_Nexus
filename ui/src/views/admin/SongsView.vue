<script setup>
// Management -> Songs (2.14.0): songs found in videos, waiting to be added to
// their Music or dismissed; "Find songs now" runs the Scheduler job once
// (videos without music); and the settings (backend/library_mixins/songs.py).
import { t, tn } from "../../lib/i18n.js";
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
const rows = ref({}); // suggestion id -> SongSuggestion (for t("Add all"))
const canSettings = hasPermission("settings.manage");

async function load() {
  try {
    data.value = await api("/api/songs");
    if (!cfg.value) cfg.value = { ...data.value.settings };
    error.value = "";
  } catch (err) { error.value = err.message || t("Could not load the songs"); }
}
let timer = null;
let stop = null;
onMounted(() => {
  load();
  stop = onLibraryChange((c) => { if (!c || c.includes("songs")) { clearTimeout(timer); timer = setTimeout(load, 400); } });
});
onBeforeUnmount(() => { stop?.(); clearTimeout(timer); });

const isSure = (s) => s.hits >= 2 && s.source !== "Gemini (AI)";  // i18n-ignore (the server's name)  // AI-only finds are never "sure"
const sureCount = computed(() => (data.value?.videos || []).reduce((n, v) => n + v.suggestions.filter(isSure).length, 0));

async function scanNow() {
  busy.value = "scan";
  try { await api("/api/songs/scan-now", { method: "POST" }); toast(t("Listening to videos without music — follow it on Tasks"), { type: "success" }); }
  catch (err) { toast(err.message || t("Could not start"), { type: "error" }); }
  finally { busy.value = ""; }
}
async function addAllSure() {
  const sure = data.value.videos.flatMap((v) => v.suggestions.filter(isSure));
  if (!(await askConfirm(tn("Add {n} song heard in two or more samples to its video?", "Add {n} songs heard in two or more samples to their videos?", sure.length), { ok: t("Add") }))) return;
  busy.value = "all";
  for (const s of sure) await rows.value[s.id]?.add();
  busy.value = "";
  load();
}
async function saveSettings() {
  busy.value = "settings";
  try { await api("/api/settings/songs", { method: "POST", body: cfg.value }); toast(t("Saved"), { type: "success" }); }
  catch (err) { toast(err.message || t("Could not save"), { type: "error" }); }
  finally { busy.value = ""; }
}
</script>

<template>
  <div class="page songs">
    <p class="muted small intro"><Tx msg="Songs heard in videos, waiting for you. {b1} puts one in the video's Music (you can fix its name first); {b2} hides it for that video for good. Songs heard in two or more samples are the surest. A single video: {b3} in its ⋮ menu."><template #b1><strong>{{ $t("Add") }}</strong></template><template #b2><strong>{{ $t("Dismiss") }}</strong></template><template #b3><strong>{{ $t("Find songs") }}</strong></template></Tx></p>
    <p v-if="error" class="error-text">{{ error }}</p>

    <section v-if="data" class="card block head">
      <div class="stats">
        <div><strong>{{ data.counts.without_music }}</strong><span>{{ $t("videos without music") }}</span></div>
        <div><strong>{{ data.counts.to_scan }}</strong><span>{{ $t("to listen to") }}</span></div>
        <div><strong>{{ data.counts.pending }}</strong><span>{{ $t("songs waiting ({n} videos)", { n: data.counts.pending_videos }) }}</span></div>
        <div><strong>{{ data.counts.added }}</strong><span>{{ $t("added so far") }}</span></div>
      </div>
      <div class="acts">
        <button class="btn btn-sm btn-primary" :disabled="busy === 'scan' || !data.counts.to_scan" @click="scanNow">
          <Icon name="music" :size="14" />{{ $t("Find songs now ({n} videos)", { n: Math.min(data.counts.to_scan, data.settings.per_run) }) }}</button>
        <button v-if="sureCount" class="btn btn-sm" :disabled="busy === 'all'" @click="addAllSure">{{ $t("Add all sure ones ({n})", { n: sureCount }) }}</button>
      </div>
      <Tx tag="p" class="muted small" msg="To do this regularly: Management → Scheduler → new job {job} (it listens to {n} videos per run, newest first). Uses the way set below." :params="{ n: data.settings.per_run }">
        <template #job><strong>{{ $t("Find songs in videos without music") }}</strong></template></Tx>
      <p v-if="data.scanning.length" class="muted small">{{ $tn("Listening now: {n} video — see Tasks.", "Listening now: {n} videos — see Tasks.", data.scanning.length) }}</p>
    </section>
    <div v-else class="skeleton" style="height: 6rem; border-radius: var(--radius)" />

    <section v-for="v in data?.videos || []" :key="v.item_id" class="card block video">
      <div class="vhead">
        <RouterLink :to="{ name: 'video', params: { id: v.item_id } }" class="thumb">
          <img :src="`/api/library/${encodeURIComponent(v.item_id)}/thumbnail`" alt="" loading="lazy">
        </RouterLink>
        <div>
          <RouterLink :to="{ name: 'video', params: { id: v.item_id } }" class="vtitle">{{ v.title }}</RouterLink>
          <div class="muted small">{{ v.creator }}<template v-if="v.music.length"> · {{ $t("Music now: {songs}", { songs: v.music.join(", ") }) }}</template></div>
        </div>
      </div>
      <SongSuggestion v-for="s in v.suggestions" :key="s.id" :ref="(el) => { if (el) rows[s.id] = el; else delete rows[s.id]; }" :s="s" @done="load" />
    </section>
    <p v-if="data && !data.videos.length" class="muted">{{ $t("No songs waiting.") }}</p>

    <section v-if="canSettings && cfg" class="card block">
      <h2>{{ $t("Settings") }}</h2>
      <div class="grid">
        <label>{{ $t("A sample every") }}<span><input v-model.number="cfg.interval" class="input input-num" type="number" min="15" max="300"> {{ $t("s of the video") }}</span></label>
        <label>{{ $t("At most") }}<span><input v-model.number="cfg.max_samples" class="input input-num" type="number" min="1" max="40"> {{ $t("samples per video") }}</span></label>
        <label>{{ $t("Each Scheduler run") }}<span><input v-model.number="cfg.per_run" class="input input-num" type="number" min="1" max="500"> {{ $t("videos") }}</span></label>
        <label>{{ $t("Listen again after") }}<span><input v-model.number="cfg.rescan_days" class="input input-num" type="number" min="0" max="3650"> {{ $t("days (0 = never)") }}</span></label>
        <label class="switch"><input v-model="cfg.shazam_retry" type="checkbox">{{ $t("Shazam: try a missed sample again 7 s later") }}</label>
        <SongMethodPicker v-model="cfg.method" :gemini="!!data?.gemini" :label="$t('Find songs with (Scheduler, Find songs now; preselected elsewhere)')" class="wide" />
        <label>{{ $t("Gemini, at most") }}<span><input v-model.number="cfg.gemini_max_samples" class="input input-num" type="number" min="1" max="20"> {{ $t("clips per video") }}</span></label>
      </div>
      <p class="muted small">{{ $t("Optional. Gemini is Google's AI: it gets a 20-second sound clip of a spot in the video and names the song only when it's sure. When finding songs by hand (a video's Find songs, or picked videos) the way can be changed each time; its finds are marked “Gemini (AI)” — check them before adding.") }}
        {{ data?.gemini ? $t("It needs a free API key in Server Settings → Credentials (saved); the free tier allows a limited number of requests a day, and Google may use free-tier requests to improve its products.")
          : $t("It needs a free API key in Server Settings → Credentials (not saved yet); the free tier allows a limited number of requests a day, and Google may use free-tier requests to improve its products.") }}</p>
      <div><button class="btn btn-primary btn-sm" :disabled="busy === 'settings'" @click="saveSettings">{{ $t("Save") }}</button></div>
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
