<script setup>
// Tasks page: library-wide jobs (GET /api/library/activity) -- what's
// running now with its stage and progress, the next Scheduler runs, and
// what finished in the last day. Videos' own downloads / re-encodes /
// repairs are listed by TasksView itself.
import { waitingFor } from "../lib/turns.js";
import { t, formatNumber } from "../lib/i18n.js";
import { askConfirm } from "../stores/confirm.js";
import { ref, computed, onBeforeUnmount } from "vue";
import Icon from "./Icon.vue";
import { formatDateTime } from "../lib/format.js";
import { hasPermission } from "../stores/session.js";
import { api } from "../lib/api.js";
import { toast } from "../stores/toast.js";
import ItemText from "./ItemText.vue";

const props = defineProps({ data: Object, part: { type: String, default: "running" } });
const emit = defineEmits(["changed"]);
const stopping = ref(new Set());

async function stop(a) {
  if (!(await askConfirm(t("Stop “{title}”?", { title: a.title }) + " " + (a.kind === "library_repair" ? t("Videos still waiting leave the queue; the ones being repaired now finish.") : t("What it has done so far is kept.")), { ok: t("Stop"), danger: true }))) return;
  stopping.value = new Set([...stopping.value, a.id]);
  try { await api(`/api/library/activity/${a.id}/stop`, { method: "POST" }); toast(t("Stopping “{name}”…", { name: a.title })); emit("changed"); }
  catch (err) { toast(err.message || t("Could not stop it"), { type: "error" }); }
}

const ICONS = {
  duplicate_scan: "copy", site_sync: "repeat", backup: "archive", update_check: "download", stats_snapshot: "chart",
  performer_sync: "user", library_repair: "wrench", metadata_sync: "tag", untracked_files: "search",
  move_folder: "drag", upload: "upload", upload_dates: "calendar",
};
const SOURCE = { schedule: t("Scheduled"), manual: t("Started by hand"), auto: t("Automatic"), extension: t("Extension") };
const STATUS = { success: t("Done"), failed: t("Failed"), blocked: t("Didn't start"), interrupted: t("Interrupted"), cancelled: t("Stopped"),
  running: t("Running"), waiting: t("Waiting") };

// Ticks once a second so "running for" stays current between reloads.
const now = ref(Date.now() / 1000);
const tick = setInterval(() => { now.value = Date.now() / 1000; }, 1000);
onBeforeUnmount(() => clearInterval(tick));

function duration(seconds) {
  if (seconds == null || seconds < 0) return "";
  const s = Math.round(seconds), m = Math.floor(s / 60), h = Math.floor(m / 60);
  return h ? t("{h}h {m}m", { h, m: m % 60 }) : m ? t("{m}m {s}s", { m, s: s % 60 }) : t("{s}s", { s });
}
function inTime(at) {
  const s = at - now.value;
  if (s <= 60) return t("in under a minute");
  return t("in {time}", { time: duration(s - (s % 60)) });
}
const running = computed(() => props.data?.running || []);
// 3.12.0: a step of this job waits for the CPU / disk (lib/turns.js)
const turn = (a) => waitingFor(props.data?.resources, { activity: a.id });
const finished = computed(() => props.data?.finished || []);
const upcoming = computed(() => props.data?.upcoming || []);
const showAll = ref(false);
const finishedShown = computed(() => (showAll.value ? finished.value : finished.value.slice(0, 5)));
const link = (a) => (a.kind === "upload" ? { name: "upload" } : a.job_id ? { name: "portal", params: { tab: "scheduler" } } : null);

// 3.4.0: a task that failed or says "see the log" links to Management -> Logs, showing just the time it
// ran (everything logged then, system entries too; only warnings and errors when it ran long).
function logLink(a) {
  if (!hasPermission("logs.view") || !a.started_at || !a.finished_at) return null;
  if (!(a.status === "failed" || a.status === "interrupted" || /see the log/i.test(a.summary || ""))) return null;
  const long = a.finished_at - a.started_at > 600;
  return { name: "portal", params: { tab: "logs" }, query: {
    cat: "all", ...(long ? { level: "WARNING" } : {}), task: a.title,
    from: Math.floor(a.started_at - 2), to: Math.ceil(a.finished_at + 2) } };
}
</script>

<template>
  <!-- Running now -->
  <ul v-if="part === 'running' && running.length" class="card list">
    <li v-for="a in running" :key="a.id" class="job" :class="a.status">
      <span class="ico"><Icon :name="ICONS[a.kind] || 'tool'" :size="20" /></span>
      <div class="main">
        <div class="line">
          <router-link v-if="link(a)" :to="link(a)" class="title">{{ a.title }}</router-link>
          <span v-else class="title"><ItemText :text="a.title" :item="a.item" /></span>
          <span class="pill" :class="a.status === 'waiting' ? 'pos' : 'run'">{{ STATUS[a.status] || a.status }}</span>
          <span v-if="turn(a)" class="pill pos" :title="turn(a).detail">{{ turn(a).text }}</span>
          <span class="muted small">{{ SOURCE[a.source] || "" }}<template v-if="a.started_at"> · {{ $t("for {time}", { time: duration(now - a.started_at) }) }}</template></span>
          <button v-if="a.can_stop" class="btn btn-sm stop" :disabled="stopping.has(a.id)" @click="stop(a)"><Icon name="stop" :size="14" />{{ $t("Stop") }}</button>
          <span v-else-if="a.stopped_by" class="muted small">{{ $t("Stopping…") }}</span>
        </div>
        <!-- A job in steps (3.7.0, e.g. a backup): the whole job, then the step it's on with its own bar. -->
        <template v-if="a.steps">
          <div class="state">
            <strong>{{ $t("Overall") }}</strong>
            <span class="muted">{{ $t("step {step} of {steps}", { step: a.step || 1, steps: a.steps }) }}</span>
            <span v-if="a.percent != null" class="pct">{{ Math.round(a.percent) }}%</span>
          </div>
          <div class="progress" :class="{ indeterminate: a.percent == null && a.status === 'running' }" role="progressbar"
               :aria-valuenow="a.percent == null ? undefined : Math.round(a.percent)" aria-valuemin="0" aria-valuemax="100" :aria-label="a.title">
            <div :style="a.percent == null ? null : { width: `${Math.max(0, Math.min(100, a.percent))}%` }" />
          </div>
          <div class="state step">
            <span><ItemText :text="a.stage || $t('Working…')" :item="a.item" /></span>
            <span v-if="a.total" class="muted">{{ $t("{done} of {total} done", { done: formatNumber(a.done || 0), total: formatNumber(a.total) }) }}</span>
            <span v-if="a.step_percent != null" class="pct">{{ Math.round(a.step_percent) }}%</span>
            <span v-if="a.detail" class="detail"><ItemText :text="a.detail" :item="a.item" /></span>
          </div>
          <div class="progress thin" :class="{ indeterminate: a.step_percent == null && a.status === 'running' }" role="progressbar"
               :aria-valuenow="a.step_percent == null ? undefined : Math.round(a.step_percent)" aria-valuemin="0" aria-valuemax="100" :aria-label="a.stage">
            <div :style="a.step_percent == null ? null : { width: `${Math.max(0, Math.min(100, a.step_percent))}%` }" />
          </div>
        </template>
        <template v-else>
          <div class="state">
            <strong><ItemText :text="a.stage || $t('Working…')" :item="a.item" /></strong>
            <span v-if="a.total" class="muted">{{ $t("{done} of {total} done", { done: formatNumber(a.done || 0), total: formatNumber(a.total) }) }}</span>
            <span v-if="a.percent != null" class="pct">{{ Math.round(a.percent) }}%</span>
            <span v-if="a.detail" class="detail"><ItemText :text="a.detail" :item="a.item" /></span>
          </div>
          <div class="progress" :class="{ indeterminate: a.percent == null && a.status === 'running' }" role="progressbar"
               :aria-valuenow="a.percent == null ? undefined : Math.round(a.percent)" aria-valuemin="0" aria-valuemax="100" :aria-label="a.title">
            <div :style="a.percent == null ? null : { width: `${Math.max(0, Math.min(100, a.percent))}%` }" />
          </div>
        </template>
      </div>
    </li>
  </ul>

  <!-- Coming up -->
  <ul v-else-if="part === 'upcoming' && (upcoming.length || data?.auto_duplicate_scan)" class="card compact">
    <li v-if="data?.auto_duplicate_scan">
      <Icon name="copy" :size="16" /><span class="name">{{ $t("Duplicate scan") }}</span><span class="muted">{{ $t("starts shortly, after the latest downloads") }}</span>
    </li>
    <li v-for="u in upcoming" :key="u.job_id">
      <Icon :name="ICONS[u.kind] || 'calendar'" :size="16" />
      <router-link :to="{ name: 'portal', params: { tab: 'scheduler' } }" class="name">{{ u.name }}</router-link>
      <span class="muted">{{ inTime(u.next_run_at) }} · {{ formatDateTime(u.next_run_at) }}</span>
    </li>
  </ul>

  <!-- Finished in the last day -->
  <template v-else-if="part === 'finished' && finished.length">
    <ul class="card compact">
      <li v-for="a in finishedShown" :key="a.id" :class="a.status">
        <Icon :name="ICONS[a.kind] || 'tool'" :size="16" />
        <span class="name"><ItemText :text="a.title" :item="a.item" /></span>
        <span class="pill" :class="a.status === 'success' ? 'ok' : a.status === 'blocked' || a.status === 'cancelled' ? 'pos' : 'bad'">{{ STATUS[a.status] || a.status }}</span>
        <span class="muted">{{ formatDateTime(a.finished_at) }} · {{ $t("took {time}", { time: duration(a.finished_at - a.started_at) || "0s" }) }}</span>
        <span v-if="a.summary" class="summary" :class="{ bad: a.status === 'failed' || a.status === 'interrupted' }">{{ a.summary }}
          <RouterLink v-if="logLink(a)" :to="logLink(a)" class="log-link">{{ $t("See the log") }}</RouterLink></span>
      </li>
    </ul>
    <button v-if="finished.length > 5" class="btn btn-ghost btn-sm more" @click="showAll = !showAll">
      {{ showAll ? $t("Show fewer") : $t("Show all {n}", { n: finished.length }) }}</button>
  </template>
</template>

<style scoped>
.list, .compact { list-style: none; margin: 0; padding: 0; overflow: hidden; }
.job { display: grid; grid-template-columns: 2.5rem minmax(0, 1fr); gap: 0.75rem; align-items: center; padding: 0.75rem 1rem;
  border-top: 1px solid var(--border); background: color-mix(in srgb, var(--accent) 5%, transparent); }
.job:first-child { border-top: 0; }
.job.waiting { background: none; }
.ico { width: 2.5rem; height: 2.5rem; border-radius: 0.625rem; display: grid; place-items: center; background: var(--panel-hover); color: var(--accent); }
.main { min-width: 0; display: flex; flex-direction: column; gap: 0.3rem; }
.line { display: flex; flex-wrap: wrap; align-items: center; gap: 0.25rem 0.5rem; }
.title { font-weight: 650; color: var(--text); }
a.title:hover { color: var(--accent); text-decoration: none; }
.small { font-size: 0.78rem; }
.state { display: flex; flex-wrap: wrap; align-items: center; gap: 0.25rem 0.5rem; font-size: 0.84rem; }
.detail { color: var(--text-2); overflow-wrap: anywhere; }
.pct { font-variant-numeric: tabular-nums; font-weight: 700; color: var(--accent); }
.pill { font-size: 0.68rem; font-weight: 700; border-radius: 0.25rem; padding: 0.1rem 0.4rem; white-space: nowrap; }
.pill.run { background: var(--accent); color: #fff; }
.pill.pos { background: var(--panel-hover); color: var(--text-2); }
.pill.ok { background: color-mix(in srgb, var(--success, #22c55e) 18%, transparent); color: var(--success, #22c55e); }
.pill.bad { background: color-mix(in srgb, var(--danger) 18%, transparent); color: var(--danger); }
.progress { height: 0.375rem; border-radius: 999px; background: var(--panel-hover); overflow: hidden; position: relative; } /* the full width of the row (2.23.0) */
.progress div { height: 100%; background: var(--accent); transition: width 0.5s ease; }
.progress.thin { height: 0.25rem; opacity: 0.8; }
.state.step { margin-top: 0.375rem; font-size: 0.9em; }
/* No percentage known: a sliding bar shows it's alive. */
.progress.indeterminate div { position: absolute; width: 30%; animation: slide 1.4s ease-in-out infinite; }
@keyframes slide { from { inset-inline-start: -30%; } to { inset-inline-start: 100%; } }
@media (prefers-reduced-motion: reduce) { .progress.indeterminate div { animation: none; width: 100%; opacity: 0.35; } }
.compact li { display: flex; flex-wrap: wrap; align-items: center; gap: 0.25rem 0.625rem; padding: 0.5rem 1rem; border-top: 1px solid var(--border); font-size: 0.84rem; }
.compact li:first-child { border-top: 0; }
.compact .name { font-weight: 600; color: var(--text); }
.compact :deep(svg) { color: var(--text-dim); flex-shrink: 0; }
.summary { flex-basis: 100%; padding-inline-start: 1.6rem; color: var(--text-2); font-size: 0.78rem; overflow-wrap: anywhere; }
.summary.bad { color: var(--danger); }
.log-link { margin-inline-start: 0.4rem; white-space: nowrap; }
.more { margin-top: 0.375rem; }
.stop { margin-inline-start: auto; }
</style>
