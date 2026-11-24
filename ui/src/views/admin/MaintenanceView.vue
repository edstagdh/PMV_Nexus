<script setup>
// Admin -> Maintenance: library-wide jobs (sync from the metadata files,
// sync with PMVHaven, repair all) and files in the download and cache folders
// that no library item points to.
import { t, formatNumber, tn, locale } from "../../lib/i18n.js";
import { hasPermission } from "../../stores/session.js";
import { ref, computed } from "vue";
import Icon from "../../components/Icon.vue";
import ModalDialog from "../../components/ModalDialog.vue";
import SiteSyncPanel from "../../components/admin/SiteSyncPanel.vue";
import PerformersPanel from "../../components/admin/PerformersPanel.vue";
import StoryboardsPanel from "../../components/admin/StoryboardsPanel.vue";
import FramesPanel from "../../components/admin/FramesPanel.vue";
import { api } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";

const scan = ref(null);
const scanning = ref(false);
const error = ref("");
const selected = ref(new Set());
const kindFilter = ref("");
const confirming = ref(false);
const deleting = ref(false);

const fileSync = ref({ busy: false, result: "" });
async function syncFromFiles() {
  fileSync.value = { busy: true, result: "" };
  try {
    const r = await api("/api/settings/sync-metadata", { method: "POST" });
    const parts = [t("{updated} of {checked} updated", { updated: r.updated, checked: r.checked })];
    if (r.missing_metadata) parts.push(t("{n} had no metadata file", { n: r.missing_metadata }));
    fileSync.value = { busy: false, result: parts.join(" · ") };
  } catch (err) {
    fileSync.value = { busy: false, result: err.message || t("Could not sync") };
  }
}
const repair = ref({ busy: false, concurrent: 1 });
async function repairAll() {
  repair.value.busy = true;
  try {
    const r = await api("/api/settings/repair", { method: "POST", body: { max_concurrent: repair.value.concurrent } });
    toast(r.queued ? tn("Queued {n} video — follow it on the Tasks page", "Queued {n} videos — follow them on the Tasks page", r.queued) : t("Nothing needed repairing"), { type: "success" });
  } catch (err) {
    toast(err.message || t("Could not start the repair"), { type: "error", timeout: 8000 });
  } finally {
    repair.value.busy = false;
  }
}

async function runScan() {
  scanning.value = true;
  error.value = "";
  try {
    scan.value = await api("/api/maintenance/untracked-files");
    selected.value = new Set();
  } catch (err) {
    error.value = err.message || t("Could not scan the folders");
  } finally {
    scanning.value = false;
  }
}

const kinds = computed(() => {
  const counts = {};
  for (const f of scan.value?.files || []) counts[f.kind] = (counts[f.kind] || 0) + 1;
  return Object.entries(counts).sort();
});
const shown = computed(() => (scan.value?.files || []).filter((f) => !kindFilter.value || f.kind === kindFilter.value));
const allShownSelected = computed(() => shown.value.length > 0 && shown.value.every((f) => selected.value.has(f.path)));
const selectedSize = computed(() => (scan.value?.files || []).filter((f) => selected.value.has(f.path)).reduce((n, f) => n + f.size, 0));

function toggle(path) {
  const s = new Set(selected.value);
  s.has(path) ? s.delete(path) : s.add(path);
  selected.value = s;
}
function toggleAll() {
  const s = new Set(selected.value);
  if (allShownSelected.value) shown.value.forEach((f) => s.delete(f.path));
  else shown.value.forEach((f) => s.add(f.path));
  selected.value = s;
}

async function doDelete() {
  deleting.value = true;
  try {
    const r = await api("/api/maintenance/untracked-files/delete", { method: "POST", body: { paths: [...selected.value] } });
    const parts = [t("Deleted {n}", { n: r.deleted.length })];
    if (r.failed.length) parts.push(t("{n} still in use", { n: r.failed.length }));
    if (r.refused.length) parts.push(t("{n} skipped (now part of the library)", { n: r.refused.length }));
    toast(parts.join(" · "), { type: r.failed.length ? "error" : "success", timeout: r.failed.length ? 8000 : 3500 });
    confirming.value = false;
    await runScan();
  } catch (err) {
    toast(err.message || t("Could not delete"), { type: "error" });
  } finally {
    deleting.value = false;
  }
}

function size(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = bytes / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}
const when = (ts) => new Date(ts * 1000).toLocaleString(locale(), { dateStyle: "short", timeStyle: "short" });
</script>

<template>
  <div class="maint">
    <header>
      <h1>{{ $t("Maintenance") }}</h1>
      <p class="muted small"><Tx msg="Library-wide jobs. Duplicate detection has its own {link1} page."><template #link1><RouterLink to="/admin/duplicates">{{ $t("Duplicates") }}</RouterLink></template></Tx></p>
    </header>

    <section v-if="hasPermission('settings.manage')" class="card block">
      <div class="block-head">
        <div>
          <h2>{{ $t("Sync from metadata files") }}</h2>
          <p class="muted small">{{ $t("Re-reads each video's metadata JSON on disk and fills in tags, performers and technical details — for videos downloaded before those fields existed, or while “Save a metadata JSON” was off. Safe to run any time.") }}</p>
        </div>
        <button class="btn btn-sm" :disabled="fileSync.busy" @click="syncFromFiles">{{ fileSync.busy ? $t("Syncing…") : $t("Sync from files") }}</button>
      </div>
      <p v-if="fileSync.result" class="small">{{ fileSync.result }}</p>
    </section>

    <SiteSyncPanel v-if="hasPermission('maintenance.run')" />

    <PerformersPanel v-if="hasPermission('performers.manage')" />


    <section v-if="hasPermission('videos.maintain')" class="card block">
      <div class="block-head">
        <div>
          <h2>{{ $t("Repair / update everything") }}</h2>
          <p class="muted small">{{ $t("Checks every video's thumbnail, preview clip and metadata, re-fetches anything missing or empty from PMVHaven (or makes it locally with ffmpeg when the site's copy is broken), refreshes tags, rating and description, and re-reads the technical details. The video file itself is never touched. One video can be repaired from its ⋮ menu instead.") }}</p>
        </div>
        <div class="bar">
          <label class="small">{{ $t("At once") }}
            <select v-model.number="repair.concurrent" class="select select-sm" :aria-label="$t('Videos at once')">
              <option v-for="n in [1, 2, 3, 4]" :key="n" :value="n">{{ n }}</option>
            </select>
          </label>
          <button class="btn btn-sm" :disabled="repair.busy" @click="repairAll"><Icon name="wrench" />{{ $t("Repair all") }}</button>
        </div>
      </div>
    </section>

    <StoryboardsPanel v-if="hasPermission('maintenance.run')" />
    <FramesPanel v-if="hasPermission('maintenance.run')" />

    <section v-if="hasPermission('maintenance.run')" class="card block">
      <div class="block-head">
        <div>
          <h2>{{ $t("Untracked files") }}</h2>
          <p class="muted small">{{ $t("Files in the download and cache folders that no video in the library points to — for example a removed duplicate whose file was in use at the time, or something copied there by hand. Nothing is deleted until you choose.") }}</p>
        </div>
        <button class="btn btn-primary btn-sm" :disabled="scanning" @click="runScan"><Icon name="search" />{{ scanning ? $t("Scanning…") : scan ? $t("Scan again") : $t("Scan folders") }}</button>
      </div>

      <p v-if="error" class="error">{{ error }}</p>
      <template v-if="scan">
        <p class="small">
          <Tx msg="{n} untracked file, {size} — in {downloads} and {cache}." plural="{n} untracked files, {size} — in {downloads} and {cache}."
              :n="scan.files.length" :params="{ size: size(scan.total_size) }">
            <template #n><strong>{{ formatNumber(scan.files.length) }}</strong></template>
            <template #downloads><code>{{ scan.roots.downloads }}</code></template><template #cache><code>{{ scan.roots.cache }}</code></template></Tx>
        </p>
        <p v-if="scan.errors.length" class="error small">{{ $t("Some folders couldn't be read: {folders}", { folders: scan.errors.join("; ") }) }}</p>
        <div v-if="scan.files.length" class="bar">
          <select v-model="kindFilter" class="select select-sm" :aria-label="$t('Kind')">
            <option value="">{{ $t("All kinds") }}</option>
            <option v-for="[k, n] in kinds" :key="k" :value="k">{{ k }} ({{ n }})</option>
          </select>
          <span class="spacer" />
          <span class="muted small">{{ $t("{n} selected", { n: selected.size }) }} · {{ size(selectedSize) }}</span>
          <button class="btn btn-danger btn-sm" :disabled="!selected.size" @click="confirming = true"><Icon name="trash" />{{ $t("Delete selected") }}</button>
        </div>
        <div v-if="scan.files.length" class="table-wrap">
          <table>
            <thead>
              <tr>
                <th class="cb"><input type="checkbox" :checked="allShownSelected" :aria-label="$t('Select all shown')" @change="toggleAll"></th>
                <th>{{ $t("Kind") }}</th><th>{{ $t("Folder") }}</th><th>{{ $t("File") }}</th><th class="num">{{ $t("Size") }}</th><th>{{ $t("Modified") }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="f in shown" :key="f.path" :class="{ on: selected.has(f.path) }" @click="toggle(f.path)">
                <td class="cb"><input type="checkbox" :checked="selected.has(f.path)" :aria-label="$t('Select {relative}', { relative: f.relative })" @click.stop @change="toggle(f.path)"></td>
                <td><span class="kind" :class="f.kind.replace(/\s/g, '-')">{{ f.kind }}</span></td>
                <td class="muted">{{ f.location }}</td>
                <td class="path" :title="f.path">{{ f.relative }}</td>
                <td class="num">{{ size(f.size) }}</td>
                <td class="muted nowrap">{{ when(f.modified) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="muted">{{ $t("✅ Everything in those folders belongs to a video in the library.") }}</p>
      </template>
    </section>

    <ModalDialog v-if="confirming" :title="$t('Delete these files?')" @close="confirming = false">
      <p>{{ $tn("Delete {n} file ({size}) from disk? This can't be undone.", "Delete {n} files ({size}) from disk? This can't be undone.", selected.size, { size: size(selectedSize) }) }}</p>
      <template #footer>
        <button class="btn" @click="confirming = false">{{ $t("Cancel") }}</button>
        <button class="btn btn-danger" :disabled="deleting" @click="doDelete">{{ deleting ? $t("Deleting…") : $t("Delete") }}</button>
      </template>
    </ModalDialog>
  </div>
</template>

<style scoped>
.maint { padding: 1rem 1.5rem 2rem; display: flex; flex-direction: column; gap: 1rem; }
h1 { font-size: 1.4rem; }
header p { margin: 0.25rem 0 0; }
.small { font-size: 0.8rem; }
.block { padding: 1rem 1.125rem; display: flex; flex-direction: column; gap: 0.75rem; }
.block-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; }
.block-head h2 { font-size: 1.05rem; }
.block-head p { margin: 0.25rem 0 0; max-width: 50rem; }
.bar { display: flex; align-items: center; gap: 0.625rem; flex-wrap: wrap; }
.spacer { flex: 1; }
.select-sm { height: 2rem; }
.table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: var(--radius); }
table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
th { text-align: start; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); padding: 0.5rem 0.625rem; border-bottom: 1px solid var(--border); background: var(--panel-2); }
td { padding: 0.4rem 0.625rem; border-bottom: 1px solid var(--border); }
tbody tr { cursor: pointer; }
tbody tr:hover, tbody tr.on { background: var(--panel-2); }
.cb { width: 2rem; }
.num { text-align: end; font-variant-numeric: tabular-nums; white-space: nowrap; }
.nowrap { white-space: nowrap; }
.path { font-family: var(--mono); font-size: 0.75rem; overflow-wrap: anywhere; }
.kind { font-size: 0.7rem; font-weight: 700; padding: 0.1rem 0.4rem; border-radius: 0.25rem; background: var(--panel-hover); color: var(--text-2); white-space: nowrap; }
.kind.video { background: var(--accent-soft); color: var(--accent); }
.kind.re-encode-backup, .kind.partial-download { background: color-mix(in srgb, var(--warning) 15%, transparent); color: var(--warning); }
.error { color: var(--danger); }
@media (max-width: 56rem) { .maint { padding: 0.75rem 1rem 1.5rem; } .block-head { flex-direction: column; } }
</style>
