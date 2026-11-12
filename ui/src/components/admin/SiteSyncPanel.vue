<script setup>
// Maintenance -> Sync with PMVHaven: compares the library with the site's
// current data (GET/POST /api/library/site-sync), as a preview or for real.
// Polls while a run is going; the last result stays on the server.
import { askConfirm } from "../../stores/confirm.js";
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import Icon from "../Icon.vue";
import { api } from "../../lib/api.js";
import { formatDateTime } from "../../lib/format.js";
import { toast } from "../../stores/toast.js";

const status = ref(null);
const error = ref("");
let timer = null;

async function load() {
  clearTimeout(timer);
  try {
    status.value = await api("/api/library/site-sync");
    error.value = "";
    if (status.value.running) timer = setTimeout(load, 1500);
  } catch (err) {
    error.value = err.message || "Could not read the sync status";
  }
}
onMounted(load);
onBeforeUnmount(() => clearTimeout(timer));

async function start(dryRun) {
  if (!dryRun && !(await askConfirm("Apply the site's current tags, performers, music, rating and description to the library?\n\nYour own edits to a video are kept.", { ok: "Apply" }))) return;
  try {
    await api("/api/library/site-sync", { method: "POST", body: { dry_run: dryRun } });
    setTimeout(load, 300);
  } catch (err) {
    toast(err.message || "Could not start the sync", { type: "error" });
  }
}

const r = computed(() => status.value?.last_result || null);
const progress = computed(() => {
  const p = status.value?.progress || {};
  return p.phase === "applying" ? "Applying the changes…" : `Fetching from the sites… ${p.done || 0} / ${p.total || "?"}`;
});
const headline = computed(() => {
  const x = r.value;
  if (!x || x.error) return "";
  return x.dry_run
    ? `Preview from ${formatDateTime(x.finished_at)}: ${x.with_changes} of ${x.checked} videos would change, ${x.removed} not found on the site.`
    : `Synced ${formatDateTime(x.finished_at)}: ${x.updated} of ${x.checked} videos updated, ${x.removed} not found on the site.`;
});
const notes = computed(() => {
  const x = r.value;
  if (!x || x.error) return [];
  const out = [];
  const counts = Object.entries(x.field_counts || {}).filter(([k]) => k !== "title");
  if (counts.length) out.push(`By field: ${counts.map(([k, v]) => `${k.replace("_reordered", " (order)")} ${v}`).join(", ")}`);
  if (x.title_differences) out.push(`${x.title_differences} titles differ on the site (titles are never changed)`);
  for (const [site, msg] of Object.entries(x.site_errors || {})) out.push(`${site} skipped: ${msg}`);
  if (x.skipped_other_sites) out.push(`${x.skipped_other_sites} videos skipped in total`);
  if (!x.tag_votes_available && x.found) out.push("The site didn't return tag votes, so tags were left as they are");
  if (x.truncated) out.push(`Only the first ${x.changes.length} entries are listed`);
  return out;
});
const LIST_FIELDS = ["tags", "performers", "music"];
</script>

<template>
  <section class="card panel">
    <div class="panel-head">
      <div>
        <h2>Sync with PMVHaven</h2>
        <p class="help-text">Compares every downloaded video with its site's current data (PMVHaven, PornTubeAI and HMVHaven, with the
          same API key) and updates tags, performers, music, rating,
          description and site stats. Videos the site no longer returns are marked 💀 removed. Files and file names are never changed, and title
          changes on the site are only reported. Your own edits are kept. Preview first; to run it regularly, add a “Sync library with PMVHaven” job on the
          <RouterLink to="/admin/scheduler">Scheduler</RouterLink>.</p>
      </div>
      <div class="page-actions">
        <button class="btn btn-sm" :disabled="status?.running" @click="start(true)"><Icon name="search" />Preview</button>
        <button class="btn btn-primary btn-sm" :disabled="status?.running" @click="start(false)">Sync now</button>
      </div>
    </div>

    <p v-if="error" class="error-text">{{ error }}</p>
    <p v-else-if="status?.running" class="help-text running"><span class="dot" />{{ progress }}</p>
    <p v-else-if="r?.error" class="error-text">The last sync failed: {{ r.error }}</p>
    <template v-else-if="r">
      <p class="help-text"><strong>{{ headline }}</strong></p>
      <ul v-if="notes.length" class="notes"><li v-for="n in notes" :key="n">{{ n }}</li></ul>
      <details v-if="r.changes.length" :open="r.dry_run" class="report">
        <summary>{{ r.dry_run ? "Differences found" : "Changes applied" }} ({{ r.changes.length }})</summary>
        <div v-for="c in r.changes" :key="c.id" class="change">
          <RouterLink :to="{ name: 'video', params: { id: c.id } }" class="title">{{ c.title || c.id }}</RouterLink>
          <template v-for="f in LIST_FIELDS" :key="f">
            <div v-if="c.changes[f]" class="line">
              <span class="field">{{ f }}</span>
              <span v-if="c.changes[f].reordered && !c.changes[f].added?.length && !c.changes[f].removed?.length" class="muted">order changed</span>
              <span v-for="t in c.changes[f].added || []" :key="`a${t}`" class="chip add">+ {{ t }}</span>
              <span v-for="t in c.changes[f].removed || []" :key="`r${t}`" class="chip del">− {{ t }}</span>
            </div>
          </template>
          <div v-if="c.changes.ratings" class="line"><span class="field">rating</span>{{ c.changes.ratings.old || "none" }} → {{ c.changes.ratings.new }}</div>
          <div v-if="c.changes.description" class="line"><span class="field">description</span>updated</div>
          <div v-if="c.changes.title" class="line"><span class="field">title on site</span>{{ c.changes.title.new }} <span class="muted">(not applied)</span></div>
        </div>
      </details>
      <details v-if="r.removed_items?.length" class="report">
        <summary>Not found on the site ({{ r.removed }})</summary>
        <div v-for="i in r.removed_items" :key="i.id" class="change">
          <RouterLink :to="{ name: 'video', params: { id: i.id } }" class="title">{{ i.title || i.id }}</RouterLink>
        </div>
      </details>
    </template>
  </section>
</template>

<style scoped>
.running { display: flex; align-items: center; gap: 0.5rem; }
.dot { width: 0.5rem; height: 0.5rem; border-radius: 50%; background: var(--accent); animation: pulse 1s infinite alternate; }
@keyframes pulse { to { opacity: 0.3; } }
.notes { margin: 0; padding-left: 1.25rem; font-size: 0.8rem; color: var(--text-2); }
.report { border: 1px solid var(--border); border-radius: var(--radius); }
.report summary { cursor: pointer; padding: 0.5rem 0.75rem; font-size: 0.84rem; font-weight: 600; }
.report[open] summary { border-bottom: 1px solid var(--border); }
.change { padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.8rem; }
.change:last-child { border-bottom: 0; }
.title { font-weight: 600; color: var(--text); }
.line { display: flex; flex-wrap: wrap; gap: 0.25rem 0.375rem; align-items: center; }
.field { min-width: 6.5rem; color: var(--text-dim); text-transform: capitalize; }
.chip { font-size: 0.72rem; padding: 0.05rem 0.4rem; border-radius: 0.25rem; }
.chip.add { background: var(--success-soft); color: var(--success); }
.chip.del { background: var(--danger-soft); color: var(--danger); text-decoration: line-through; }
</style>
