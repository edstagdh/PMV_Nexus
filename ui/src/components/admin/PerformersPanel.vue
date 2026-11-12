<script setup>
// Management -> Maintenance -> Performers: how many library performer names
// are linked to ThePornDB, the ones waiting for a pick, and "match now" (runs
// the Scheduler job "Match & refresh performers").
import { askConfirm } from "../../stores/confirm.js";
import { ref, onMounted, onBeforeUnmount } from "vue";
import { RouterLink } from "vue-router";
import PerformerPicker from "../PerformerPicker.vue";
import { api } from "../../lib/api.js";
import { formatDateTime } from "../../lib/format.js";
import { toast } from "../../stores/toast.js";

const ov = ref(null);
const open = ref("");
let timer = null;
async function load() { try { ov.value = await api("/api/performers/overview"); } catch { /* keep */ } }
onMounted(() => { load(); timer = setInterval(load, 4000); });
onBeforeUnmount(() => clearInterval(timer));

// 2.15.0: photos saved before are converted to WebP once (new ones already are).
const converting = ref(false);
async function convertPhotos() {
  if (!(await askConfirm(`Convert ${ov.value.photos_to_convert} performer photos to WebP? The originals are replaced; it runs in the background (see Tasks) and can be stopped.`, { ok: "Convert" }))) return;
  converting.value = true;
  try { await api("/api/performers/photos/convert", { method: "POST" }); toast("Converting performer photos — follow it on Tasks", { type: "success" }); }
  catch (err) { toast(err.message || "Could not start", { type: "error" }); }
  finally { setTimeout(() => { converting.value = false; load(); }, 1500); }
}

async function match(retryNone = false) {
  try { await api(`/api/performers/match-now${retryNone ? "?retry_none=true" : ""}`, { method: "POST" }); toast("Match & refresh performers started"); setTimeout(load, 800); }
  catch (err) { toast(err.message || "Could not start", { type: "error" }); }
}
</script>

<template>
  <section class="card block">
    <div class="block-head">
      <div>
        <h2>Performers (ThePornDB)</h2>
        <p class="muted small">Each performer name in your library is matched once to a ThePornDB profile (kept by its id), for her
          profile page: photo, details, bio, aliases and similar performers. Clear matches link by themselves; names that could be several
          people wait here for you to pick. Options: <RouterLink :to="{ name: 'server', query: { tab: 'performers' } }">Server settings → Performers</RouterLink>.</p>
      </div>
      <div class="bar">
        <button class="btn btn-sm" :disabled="!ov?.configured || ov?.job?.running" @click="match(false)">{{ ov?.job?.running ? "Running…" : "Match now" }}</button>
        <button class="btn btn-ghost btn-sm" :disabled="!ov?.configured || ov?.job?.running" title="Look up the names not found before again" @click="match(true)">Retry not found</button>
      </div>
    </div>
    <p v-if="ov && !ov.configured" class="error-text small">Add the ThePornDB API key in <RouterLink :to="{ name: 'server', query: { tab: 'credentials' } }">Server settings → Credentials</RouterLink>.</p>
    <template v-if="ov">
      <p class="small">
        <strong>{{ ov.counts.linked }}</strong> linked · <strong>{{ ov.counts.review }}</strong> to review · {{ ov.counts.none }} not on ThePornDB ·
        {{ ov.counts.skipped }} skipped · {{ ov.counts.unchecked }} not looked up yet — {{ ov.total }} names, {{ ov.profiles }} profiles
      </p>
      <p v-if="ov.photos_to_convert" class="small convert">
        <span><strong>{{ ov.photos_to_convert }}</strong> {{ ov.photos_to_convert === 1 ? "photo" : "photos" }} saved before 2.15.0 {{ ov.photos_to_convert === 1 ? "isn't" : "aren't" }} WebP yet — converting saves space (new photos already are).</span>
        <button class="btn btn-sm" :disabled="converting" @click="convertPhotos">Convert photos to WebP</button>
      </p>
      <p class="muted small">
        <template v-if="!ov.job">The Scheduler job is missing — restart the app.</template>
        <template v-else-if="!ov.job.enabled">The job <strong>Match &amp; refresh performers</strong> is turned off: new names are only matched with Match now.</template>
        <template v-else>Runs by itself<template v-if="ov.job.next_run_at">, next on {{ formatDateTime(ov.job.next_run_at) }}</template></template><template v-if="ov.job?.last_run_at"> · last run {{ formatDateTime(ov.job.last_run_at) }} ({{ ov.job.last_run_status }})</template>.
        Change when: <RouterLink :to="{ name: 'portal', params: { tab: 'scheduler' } }">Scheduler</RouterLink>.
      </p>
      <p v-if="ov.busy" class="small accent">{{ ov.busy }}</p>
      <p v-if="ov.error" class="error-text small">{{ ov.error }}</p>
      <ul v-if="ov.review.length" class="review">
        <li v-for="r in ov.review" :key="r.name">
          <div class="r-head">
            <RouterLink :to="{ name: 'performer', params: { name: r.name } }"><strong>{{ r.name }}</strong></RouterLink>
            <span class="muted small">{{ r.videos }} {{ r.videos === 1 ? "video" : "videos" }} · {{ r.candidates.length }} possible</span>
            <button class="btn btn-ghost btn-sm" @click="open = open === r.name ? '' : r.name">{{ open === r.name ? "Close" : "Pick" }}</button>
          </div>
          <PerformerPicker v-if="open === r.name" :name="r.name" :candidates="r.candidates" @done="open = ''; load()" />
        </li>
      </ul>
    </template>
  </section>
</template>

<style scoped>
.block { padding: 1rem 1.125rem; display: flex; flex-direction: column; gap: 0.75rem; }
.convert { display: flex; align-items: center; justify-content: space-between; gap: 0.75rem; flex-wrap: wrap; padding: 0.5rem 0.75rem; border-radius: var(--radius); background: var(--panel-hover); }
.block-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; }
.block-head h2 { font-size: 1.05rem; }
.block-head p { margin: 0.25rem 0 0; max-width: 50rem; }
.bar { display: flex; gap: 0.5rem; flex-wrap: wrap; }
.small { font-size: 0.8rem; margin: 0; }
.accent { color: var(--accent); }
.review { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.5rem; max-height: 36rem; overflow-y: auto; }
.review li { border: 1px solid var(--border); border-radius: var(--radius); padding: 0.5rem 0.625rem; display: flex; flex-direction: column; gap: 0.5rem; }
.r-head { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
@media (max-width: 56rem) { .block-head { flex-direction: column; } }
</style>
