<script setup>
// Tasks: everything the app is working on or waiting to do. Library-wide
// jobs first (Scheduler runs, scans, syncs, backups, performer matching,
// uploads... -- components/JobList.vue), then each video's downloads, detail
// fetches, re-encodes and repairs, grouped as Videos being worked on (3.12.0; was "Running now") /
// Waiting / Failed, with live progress; then the next scheduled runs and
// the jobs that finished in the last day. Queue positions:
// downloads run one at a time in the order they were added; re-encode and
// repair order come from the server (summary.*_queue_order).
import { t } from "../lib/i18n.js";
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import Icon from "../components/Icon.vue";
import VideoThumb from "../components/VideoThumb.vue";
import JobList from "../components/JobList.vue";
import { api } from "../lib/api.js";
import { itemUrlTitle } from "../lib/format.js";
import { openDialog } from "../stores/dialogs.js";
import { hasPermission } from "../stores/session.js";
import { onLibraryChange } from "../stores/live.js";
import { toast } from "../stores/toast.js";
import { creatorText } from "../lib/creators.js";
import { waitingFor } from "../lib/turns.js";

const canManage = computed(() => hasPermission("tasks.manage"));
const items = ref(null);
const jobs = ref(null);
const summary = ref({ reencode_queue_order: [], repair_queue_order: [] });
const error = ref("");
const busy = ref(new Set());
let pollTimer = null;
let reloadTimer = null;

async function load() {
  try {
    const [tasks, s, j] = await Promise.all([api("/api/library/tasks"), api("/api/library/summary"), api("/api/library/activity")]);
    items.value = tasks.items;
    summary.value = s;
    jobs.value = j;
    error.value = "";
  } catch (err) {
    error.value = err.message || t("Could not load tasks");
  }
  // Progress isn't always pushed live, so poll while something is running.
  clearTimeout(pollTimer);
  if (running.value.length || jobs.value?.running?.length || jobs.value?.resources?.waiting?.length) pollTimer = setTimeout(load, 2000);
}

function isRunning(it) {
  return it.status === "downloading" || it.status === "fetching" || it.reencode_status === "encoding"
    || it.repair_status === "running";
}
function isFailed(it) {
  if (it.status === "pending") return false; // queued to download: an older failed repair doesn't make it "failed"
  return it.status === "failed" || it.reencode_status === "failed" || it.repair_status === "failed";
}
const all = computed(() => items.value || []);
const running = computed(() => all.value.filter(isRunning));
// Waiting, in the order things will run (3.6.0): the download queue, then the re-encode queue,
// then the repair queue -- each by its own #n (before, only downloads were in order).
const waiting = computed(() => {
  const order = new Map();
  [pendingOrder.value, summary.value.reencode_queue_order || [], summary.value.repair_queue_order || []]
    .forEach((list, q) => list.forEach((id, n) => { if (!order.has(id)) order.set(id, q * 1e6 + n); }));
  return all.value.filter((it) => !isRunning(it) && !isFailed(it))
    .sort((a, b) => (order.get(a.id) ?? 9e6) - (order.get(b.id) ?? 9e6));
});
const failed = computed(() => all.value.filter((it) => !isRunning(it) && isFailed(it)));

// The server's own order (it can be changed with the arrows); falls back to
// the order videos were added.
const pendingOrder = computed(() => summary.value.download_queue_order
  || all.value.filter((i) => i.status === "pending").sort((a, b) => a.created_at - b.created_at).map((i) => i.id));
function position(it) {
  const at = (list, label) => { const i = list.indexOf(it.id); return i === -1 ? null : { label, n: i + 1, of: list.length }; };
  if (it.status === "pending") return at(pendingOrder.value, t("Download queue"));
  if (it.reencode_status === "queued") return at(summary.value.reencode_queue_order || [], t("Re-encode queue"));
  if (it.repair_status === "queued") return at(summary.value.repair_queue_order || [], t("Repair queue"));
  return null;
}
function eta(seconds) {
  if (seconds == null) return "";
  const m = Math.floor(seconds / 60), s = Math.round(seconds % 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : m ? `${m}m ${s}s` : `${s}s`;
}
// { text, percent, detail } describing what's happening to an item.
function state(it) {
  const turn = waitingFor(jobs.value?.resources, { item: it.id });   // 3.12.0: waits for the CPU / disk
  if (turn) return { text: turn.text, detail: turn.detail };
  if (it.status === "downloading") {
    return { text: t("Downloading"), percent: it.progress_percent, detail: [it.speed, it.progress_label].filter(Boolean).join(" · ") };
  }
  if (it.status === "fetching") return { text: t("Fetching details from PMVHaven…") };
  if (it.status === "pending") {
    return summary.value?.disk?.low ? { text: t("Waiting for disk space"), detail: t("Downloads are stopped until there's more free space") } : { text: t("Waiting to download") };
  }
  if (it.status === "failed") return { text: t("Download failed"), detail: it.error, failed: true };
  if (it.reencode_status === "encoding" && it.reencode_stage === "checking") return { text: t("Checking the file before re-encoding") };
  if (it.reencode_status === "encoding") {
    return { text: t("Re-encoding"), percent: it.reencode_progress_percent, detail: it.reencode_eta_seconds != null ? t("ETA {time}", { time: eta(it.reencode_eta_seconds) }) : "" };
  }
  if (it.reencode_status === "queued") return { text: t("Waiting to re-encode") };
  if (it.reencode_status === "failed") return { text: t("Re-encode failed"), detail: it.reencode_error, failed: true };
  if (it.repair_status === "running") return { text: t("Repairing"), detail: it.repair_progress_label || t("Working…") };
  if (it.repair_status === "queued") return { text: t("Waiting to repair") };
  if (it.repair_status === "failed") return { text: t("Repair failed"), detail: it.repair_error, failed: true };
  return { text: it.status };
}

async function act(it, key, run) {
  busy.value = new Set([...busy.value, it.id + key]);
  try { await run(); await load(); }
  catch (err) { toast(err.message || t("That didn't work"), { type: "error" }); }
  finally { const b = new Set(busy.value); b.delete(it.id + key); busy.value = b; }
}
const isBusy = (it, key) => busy.value.has(it.id + key);
const retry = (it) => act(it, "retry", () => api(`/api/library/${encodeURIComponent(it.id)}/retry`, { method: "POST" }).then(() => toast(t("Queued again"))));
const cancelReencode = (it) => act(it, "reenc", () => api(`/api/library/${encodeURIComponent(it.id)}/reencode/cancel`, { method: "POST" }));
const cancelRepair = (it) => act(it, "repair", () => api(`/api/library/${encodeURIComponent(it.id)}/repair/cancel`, { method: "POST" }));
const move = (it, direction) => act(it, direction, () => api(`/api/library/${encodeURIComponent(it.id)}/reencode/move`, { method: "POST", body: { direction } }));
const moveDownload = (it, direction) => act(it, direction, () => api(`/api/library/${encodeURIComponent(it.id)}/download/move`, { method: "POST", body: { direction } }));
const reencodeOrder = computed(() => summary.value.reencode_queue_order || []);
const res = computed(() => jobs.value?.resources || null);
const holders = (kind) => (res.value?.holding || []).filter((h) => h.needs.includes(kind));
const canSettings = computed(() => hasPermission("settings.manage"));

const sections = computed(() => [
  { key: "running", title: t("Videos being worked on"), list: running.value },
  { key: "waiting", title: t("Waiting"), list: waiting.value },
  { key: "failed", title: t("Failed"), list: failed.value },
].filter((s) => s.list.length));

const stopLive = onLibraryChange(() => { clearTimeout(reloadTimer); reloadTimer = setTimeout(load, 300); });
onMounted(load);
onBeforeUnmount(() => { clearTimeout(pollTimer); clearTimeout(reloadTimer); stopLive(); });
</script>

<template>
  <div class="tasks">
    <header class="head">
      <h1>{{ $t("Tasks") }}</h1>
      <p class="muted small">{{ $t("Everything the app is working on or waiting to do — jobs like scans, syncs, backups and performer matching, and each video's download, re-encode, repair or upload. Updates live.") }}</p>
    </header>

    <div v-if="error" class="empty"><div class="empty-icon">⚠️</div>{{ error }}<button class="btn" @click="load">{{ $t("Try again") }}</button></div>
    <div v-else-if="!items" class="skeleton list-skel" />
    <template v-else>
    <!-- 3.12.0: heavy work takes turns for the CPU and the disk (Server Settings -> Background work) -->
    <section v-if="res && (res.holding.length || res.waiting.length || res.live)" class="card turns" aria-live="polite">
      <div class="turns-head">
        <strong>{{ $t("Sharing the computer") }}</strong>
        <span class="muted small">{{ $t("Background FFmpeg uses up to {n} of {total} cores", { n: res.cpu_cores, total: res.total_cores }) }}</span>
        <router-link v-if="canSettings" :to="{ name: 'server', query: { tab: 'work' } }" class="small">{{ $t("Settings") }}</router-link>
      </div>
      <div class="turns-grid">
        <div v-for="kind in ['cpu', 'disk']" :key="kind" class="turn">
          <span class="turn-k">{{ kind === "cpu" ? $t("CPU") : $t("Disk") }}</span>
          <template v-if="holders(kind).length">
            <span v-for="(h, i) in holders(kind)" :key="i" class="turn-who">{{ h.label }}</span>
          </template>
          <span v-else-if="kind === 'cpu' && res.live" class="turn-who">{{ $t("Kept free while a video is converted for watching") }}</span>
          <span v-else class="muted">{{ $t("free") }}</span>
        </div>
      </div>
      <p v-if="res.waiting.length" class="muted small turns-wait">
        {{ $tn("{n} step waits for its turn:", "{n} steps wait for their turn:", res.waiting.length) }}
        {{ res.waiting.slice(0, 6).map((w) => w.label).join(" · ") }}<template v-if="res.waiting.length > 6"> …</template>
      </p>
    </section>
    <section v-if="jobs?.running?.length" class="section">
      <h2>{{ $t("Library jobs") }} <span class="count">{{ jobs.running.length }}</span></h2>
      <JobList :data="jobs" part="running" @changed="load" />
    </section>
    <div v-if="!sections.length && !jobs?.running?.length" class="empty"><div class="empty-icon">✅</div>{{ $t("Nothing running — no jobs, downloads, re-encodes, repairs or uploads.") }}</div>
    <section v-for="s in sections" :key="s.key" class="section">
      <h2>{{ s.title }} <span class="count">{{ s.list.length }}</span></h2>
      <ul class="card list">
        <li v-for="it in s.list" :key="it.id" class="task" :class="{ running: s.key === 'running', failed: s.key === 'failed' }">
          <router-link v-if="it.status === 'done' || it.metadata_path" :to="{ name: 'video', params: { id: it.id } }" class="thumb-link">
            <VideoThumb :item="it" compact />
          </router-link>
          <div v-else class="thumb-link"><VideoThumb :item="it" compact /></div>
          <div class="task-main">
            <router-link v-if="it.status === 'done' || it.metadata_path" :to="{ name: 'video', params: { id: it.id } }" class="title">{{ itemUrlTitle(it) }}</router-link>
            <span v-else class="title">{{ itemUrlTitle(it) }}</span>
            <div class="sub muted">
              <span v-if="creatorText(it)" class="text-creator">{{ creatorText(it) }}</span>
              <span v-if="it.tech_filesize">{{ it.tech_filesize }}</span>
              <span v-if="it.tech_resolution">{{ it.tech_resolution }}</span>
            </div>
            <div class="state" :class="{ bad: state(it).failed }">
              <span v-if="s.key === 'running'" class="pill run">{{ $t("Running") }}</span>
              <span v-else-if="position(it)" class="pill pos">{{ $t("#{n} of {total} · {queue}", { n: position(it).n, total: position(it).of, queue: position(it).label }) }}</span>
              <strong>{{ state(it).text }}</strong>
              <span v-if="state(it).percent != null" class="pct">{{ Math.round(state(it).percent) }}%</span>
              <span v-if="state(it).detail" class="detail">{{ state(it).detail }}</span>
            </div>
            <div v-if="state(it).percent != null" class="progress" role="progressbar" :aria-valuenow="Math.round(state(it).percent)" aria-valuemin="0" aria-valuemax="100">
              <div :style="{ width: `${Math.max(0, Math.min(100, state(it).percent))}%` }" />
            </div>
          </div>
          <div v-if="canManage" class="actions">
            <!-- Queue position: wide buttons with clear arrows and a word each. -->
            <div v-if="it.status === 'pending' && pendingOrder.length > 1" class="qgroup" role="group" :aria-label="$t('Download queue position')">
              <button class="qbtn" :title="$t('Download next')" :disabled="pendingOrder[0] === it.id || isBusy(it, 'top')" @click="moveDownload(it, 'top')"><Icon name="to-top" :size="20" /><span>{{ $t("First") }}</span></button>
              <button class="qbtn" :title="$t('Move up in the download queue')" :disabled="pendingOrder[0] === it.id || isBusy(it, 'up')" @click="moveDownload(it, 'up')"><Icon name="arrow-up" :size="20" /><span>{{ $t("Up") }}</span></button>
              <button class="qbtn" :title="$t('Move down in the download queue')" :disabled="pendingOrder.at(-1) === it.id || isBusy(it, 'down')" @click="moveDownload(it, 'down')"><Icon name="arrow-down" :size="20" /><span>{{ $t("Down") }}</span></button>
              <button class="qbtn" :title="$t('Download last')" :disabled="pendingOrder.at(-1) === it.id || isBusy(it, 'bottom')" @click="moveDownload(it, 'bottom')"><Icon name="to-bottom" :size="20" /><span>{{ $t("Last") }}</span></button>
            </div>
            <div v-if="it.reencode_status === 'queued'" class="qgroup" role="group" :aria-label="$t('Re-encode queue position')">
              <button class="qbtn" :title="$t('Re-encode next (right after the one encoding now)')" :disabled="reencodeOrder[0] === it.id || isBusy(it, 'top')" @click="move(it, 'top')"><Icon name="to-top" :size="20" /><span>{{ $t("First") }}</span></button>
              <button class="qbtn" :title="$t('Move up in the re-encode queue')" :disabled="reencodeOrder[0] === it.id || isBusy(it, 'up')" @click="move(it, 'up')"><Icon name="arrow-up" :size="20" /><span>{{ $t("Up") }}</span></button>
              <button class="qbtn" :title="$t('Move down in the re-encode queue')" :disabled="reencodeOrder.at(-1) === it.id || isBusy(it, 'down')" @click="move(it, 'down')"><Icon name="arrow-down" :size="20" /><span>{{ $t("Down") }}</span></button>
              <button class="qbtn" :title="$t('Re-encode last')" :disabled="reencodeOrder.at(-1) === it.id || isBusy(it, 'bottom')" @click="move(it, 'bottom')"><Icon name="to-bottom" :size="20" /><span>{{ $t("Last") }}</span></button>
            </div>
            <button v-if="it.status === 'failed'" class="btn btn-sm btn-primary" :disabled="isBusy(it, 'retry')" @click="retry(it)"><Icon name="repeat" />{{ $t("Retry") }}</button>
            <button v-if="it.reencode_status === 'queued' || it.reencode_status === 'encoding'" class="btn btn-sm" :disabled="isBusy(it, 'reenc')" @click="cancelReencode(it)">{{ $t("Cancel re-encode") }}</button>
            <button v-if="it.repair_status === 'queued' || it.repair_status === 'running'" class="btn btn-sm" :disabled="isBusy(it, 'repair')" @click="cancelRepair(it)">{{ $t("Cancel repair") }}</button>
            <button v-if="it.status !== 'done'" class="btn btn-ghost btn-sm btn-danger" :title="$t('Stop and remove this video from the library')" @click="openDialog('remove', it)"><Icon name="trash" />{{ $t("Remove") }}</button>
          </div>
        </li>
      </ul>
    </section>
    <section v-if="jobs?.upcoming?.length || jobs?.auto_duplicate_scan" class="section">
      <h2>{{ $t("Coming up") }}</h2>
      <JobList :data="jobs" part="upcoming" />
    </section>
    <section v-if="jobs?.finished?.length" class="section">
      <h2>{{ $t("Finished in the last day") }} <span class="count">{{ jobs.finished.length }}</span></h2>
      <JobList :data="jobs" part="finished" />
    </section>
    </template>
  </div>
</template>

<style scoped>
/* 3.12.0: who has the CPU / disk */
.turns { padding: 0.75rem 1rem; display: flex; flex-direction: column; gap: 0.5rem; margin-bottom: 1rem; }
.turns-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem 0.875rem; }
.turns-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); gap: 0.5rem; }
.turn { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.25rem 0.5rem; min-width: 0; font-size: 0.85rem; }
.turn-k { font-size: 0.68rem; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase; color: var(--text-dim); min-width: 2.5rem; }
.turn-who { overflow-wrap: anywhere; }
.turns-wait { margin: 0; overflow-wrap: anywhere; }
.small { font-size: 0.8rem; }
.tasks { padding: 1rem 1.5rem 2rem; display: flex; flex-direction: column; gap: 1rem; }
.head h1 { font-size: 1.4rem; }
.head p { margin: 0.25rem 0 0; }
.small { font-size: 0.8rem; }
.section h2 { font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); margin: 0 0 0.5rem; display: flex; align-items: center; gap: 0.5rem; }
.count { font-size: 0.7rem; background: var(--panel-hover); color: var(--text-2); border-radius: 999px; padding: 0.05rem 0.45rem; }
.list { list-style: none; margin: 0; padding: 0; overflow: hidden; }
.task { display: grid; grid-template-columns: 9rem minmax(0, 1fr) auto; gap: 1rem; align-items: center; padding: 0.75rem 1rem; border-top: 1px solid var(--border); }
.task:first-child { border-top: 0; }
.task.running { background: color-mix(in srgb, var(--accent) 5%, transparent); }
.thumb-link { display: block; }
.task-main { min-width: 0; display: flex; flex-direction: column; gap: 0.25rem; }
.title { font-weight: 650; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
a.title:hover { color: var(--accent); text-decoration: none; }
.sub { display: flex; flex-wrap: wrap; gap: 0 0.75rem; font-size: 0.78rem; }
.state { display: flex; flex-wrap: wrap; align-items: center; gap: 0.25rem 0.5rem; font-size: 0.84rem; }
.state.bad strong { color: var(--danger); }
.detail { color: var(--text-2); overflow-wrap: anywhere; }
.state.bad .detail { color: var(--text-2); font-family: var(--mono); font-size: 0.75rem; }
.pct { font-variant-numeric: tabular-nums; font-weight: 700; color: var(--accent); }
.pill { font-size: 0.68rem; font-weight: 700; border-radius: 0.25rem; padding: 0.1rem 0.4rem; }
.pill.run { background: var(--accent); color: #fff; }
.pill.pos { background: var(--panel-hover); color: var(--text-2); }
.progress { height: 0.375rem; border-radius: 999px; background: var(--panel-hover); overflow: hidden; } /* the full width of the row (2.23.0) */
.progress div { height: 100%; background: var(--accent); transition: width 0.5s ease; }
.actions { display: flex; gap: 0.375rem; flex-wrap: wrap; justify-content: flex-end; }
.list-skel { height: 16rem; border-radius: var(--radius-lg); }
@media (max-width: 56rem) {
  .tasks { padding: 0.75rem 1rem 1.5rem; }
  .task { grid-template-columns: 6.5rem minmax(0, 1fr); }
  .actions { grid-column: 1 / -1; justify-content: flex-start; }
}
.qgroup { display: inline-flex; border: 1px solid var(--border-strong, var(--border)); border-radius: var(--radius); overflow: hidden; }
.qbtn { display: inline-flex; flex-direction: column; align-items: center; justify-content: center; gap: 0.1rem; min-width: 3.25rem;
  height: 2.75rem; padding: 0 0.5rem; border: 0; border-inline-start: 1px solid var(--border); background: var(--panel-2); color: var(--text);
  font: inherit; font-size: 0.68rem; font-weight: 650; cursor: pointer; }
.qbtn:first-child { border-inline-start: 0; }
.qbtn:hover:not(:disabled) { background: var(--panel-hover); color: var(--accent); }
.qbtn:disabled { opacity: 0.35; cursor: default; }
.qbtn :deep(svg) { stroke-width: 2.4; }
</style>
