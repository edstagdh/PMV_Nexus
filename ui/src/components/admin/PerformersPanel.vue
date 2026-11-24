<script setup>
// Management -> Maintenance -> Performers: how many library performer names
// are linked to ThePornDB, the ones waiting for a pick, and "match now" (runs
// the Scheduler job "Match & refresh performers").
import { t } from "../../lib/i18n.js";
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
  if (!(await askConfirm(`Convert ${ov.value.photos_to_convert} performer photos to WebP? The originals are replaced; it runs in the background (see Tasks) and can be stopped.`, { ok: t("Convert") }))) return;
  converting.value = true;
  try { await api("/api/performers/photos/convert", { method: "POST" }); toast(t("Converting performer photos — follow it on Tasks"), { type: "success" }); }
  catch (err) { toast(err.message || t("Could not start"), { type: "error" }); }
  finally { setTimeout(() => { converting.value = false; load(); }, 1500); }
}

async function match(retryNone = false) {
  try { await api(`/api/performers/match-now${retryNone ? "?retry_none=true" : ""}`, { method: "POST" }); toast(t("Match & refresh performers started")); setTimeout(load, 800); }
  catch (err) { toast(err.message || t("Could not start"), { type: "error" }); }
}
</script>

<template>
  <section class="card block">
    <div class="block-head">
      <div>
        <h2>{{ $t("Performers (ThePornDB)") }}</h2>
        <p class="muted small">{{ $t("Each performer name in your library is matched once to a ThePornDB profile (kept by its id), for the performer's profile page: photo, details, bio, aliases and similar performers. Clear matches link by themselves; names that could be several people wait here for you to pick. Options:") }} <RouterLink :to="{ name: 'server', query: { tab: 'performers' } }">{{ $t("Server Settings → Performers") }}</RouterLink>.</p>
      </div>
      <div class="bar">
        <button class="btn btn-sm" :disabled="!ov?.configured || ov?.job?.running" @click="match(false)">{{ ov?.job?.running ? $t("Running…") : $t("Match now") }}</button>
        <button class="btn btn-ghost btn-sm" :disabled="!ov?.configured || ov?.job?.running" :title="$t('Look up the names not found before again')" @click="match(true)">{{ $t("Retry not found") }}</button>
      </div>
    </div>
    <p v-if="ov && !ov.configured" class="error-text small"><Tx msg="Add the ThePornDB API key in {link1}."><template #link1><RouterLink :to="{ name: 'server', query: { tab: 'credentials' } }">{{ $t("Server Settings → Credentials") }}</RouterLink></template></Tx></p>
    <template v-if="ov">
      <p class="small">
        <Tx msg="{linked} linked · {review} to review · {none} not on ThePornDB · {skipped} skipped · {unchecked} not looked up yet — {names} names, {profiles} profiles"
            :params="{ none: ov.counts.none, skipped: ov.counts.skipped, unchecked: ov.counts.unchecked, names: ov.total, profiles: ov.profiles }">
          <template #linked><strong>{{ ov.counts.linked }}</strong></template><template #review><strong>{{ ov.counts.review }}</strong></template></Tx>
      </p>
      <p v-if="ov.photos_to_convert" class="small convert">
        <span>{{ $tn("{n} photo saved before 2.15.0 isn't WebP yet — converting saves space (new photos already are).", "{n} photos saved before 2.15.0 aren't WebP yet — converting saves space (new photos already are).", ov.photos_to_convert) }}</span>
        <button class="btn btn-sm" :disabled="converting" @click="convertPhotos">{{ $t("Convert photos to WebP") }}</button>
      </p>
      <p class="muted small">
        <template v-if="!ov.job">{{ $t("The Scheduler job is missing — restart the app.") }}</template>
        <template v-else-if="!ov.job.enabled"><Tx msg="The job {job} is turned off: new names are only matched with Match now."><template #job><strong>{{ $t("Match & refresh performers") }}</strong></template></Tx></template>
        <template v-else>{{ ov.job.next_run_at ? $t("Runs by itself, next on {time}", { time: formatDateTime(ov.job.next_run_at) }) : $t("Runs by itself") }}</template><template v-if="ov.job?.last_run_at"> · {{ $t("last run {time} ({status})", { time: formatDateTime(ov.job.last_run_at), status: ov.job.last_run_status }) }}</template>.
        <Tx msg="Change when: {link}."><template #link><RouterLink :to="{ name: 'portal', params: { tab: 'scheduler' } }">{{ $t("Scheduler") }}</RouterLink></template></Tx>
      </p>
      <p v-if="ov.busy" class="small accent">{{ ov.busy }}</p>
      <p v-if="ov.error" class="error-text small">{{ ov.error }}</p>
      <ul v-if="ov.review.length" class="review">
        <li v-for="r in ov.review" :key="r.name">
          <div class="r-head">
            <RouterLink :to="{ name: 'performer', params: { name: r.name } }"><strong>{{ r.name }}</strong></RouterLink>
            <span class="muted small">{{ $tn("{n} video", "{n} videos", r.videos) }} · {{ $t("{n} possible", { n: r.candidates.length }) }}</span>
            <button class="btn btn-ghost btn-sm" @click="open = open === r.name ? '' : r.name">{{ open === r.name ? $t("Close") : $t("Pick") }}</button>
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
