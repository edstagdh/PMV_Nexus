<script setup>
// Admin -> Scheduler: background jobs (duplicate scan, repair, site sync,
// backups, ...) on a schedule or on demand. The job types come from the
// server's JOB_REGISTRY (GET /api/scheduler/job-types).
import { t, tn, formatNumber } from "../../lib/i18n.js";
import { askConfirm } from "../../stores/confirm.js";
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import Icon from "../../components/Icon.vue";
import ModalDialog from "../../components/ModalDialog.vue";
import { api } from "../../lib/api.js";
import { formatDateTime } from "../../lib/format.js";
import { toast } from "../../stores/toast.js";

const DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : "");
const DAY_NAMES = { monday: t("Monday"), tuesday: t("Tuesday"), wednesday: t("Wednesday"), thursday: t("Thursday"),
  friday: t("Friday"), saturday: t("Saturday"), sunday: t("Sunday") };

const types = ref([]);
const jobs = ref(null);
const error = ref("");
const history = ref({}); // job id -> runs (array) | "loading" | error text
const openDetails = ref(new Set()); // run ids whose detail lines are shown
const dialog = ref(null); // { job? }
const form = ref({});
const formError = ref("");
const busy = ref(false);
let pollTimer = null;

const typeOf = (key) => types.value.find((ty) => ty.job_type === key);

async function load() {
  clearTimeout(pollTimer);
  try {
    jobs.value = await api("/api/scheduler/jobs");
    error.value = "";
    for (const id of Object.keys(history.value)) loadHistory(id);
  } catch (err) {
    error.value = err.message || t("Could not load the jobs");
  }
  // Keep the list fresh while something runs, so finishing shows up.
  if (jobs.value?.some((j) => j.running)) pollTimer = setTimeout(load, 2000);
}
onMounted(async () => {
  try { types.value = await api("/api/scheduler/job-types"); } catch { types.value = []; }
  load();
});
onBeforeUnmount(() => clearTimeout(pollTimer));

// One time (3.2.0): "2026-11-14T02:00", the server's local time.
function onceText(at) {
  const d = at ? new Date(at) : null;
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "?";
}
function tomorrowAt(hhmm) {
  const d = new Date(); d.setDate(d.getDate() + 1);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${hhmm}`;
}
function describe(job) {
  if (job.schedule_mode === "advanced") return t("Cron: {expression}", { expression: job.schedule_config?.expression || "?" });
  const c = job.schedule_config || {};
  if (c.kind === "interval_hours") return tn("Every hour", "Every {n} hours", c.hours || 0);
  if (c.kind === "weekly") return t("Every {day} at {time}", { day: DAY_NAMES[c.day] || cap(c.day), time: c.time || "?" });
  if (c.kind === "once") return t("Once, {when}", { when: onceText(c.at) });
  return t("Daily at {time}", { time: c.time || "?" });
}
const RUN_STATUS = { success: t("success"), failed: t("failed"), interrupted: t("interrupted"), blocked: t("blocked"),
  cancelled: t("cancelled"), running: t("running") };
const STATUS_PILL = { success: "pill-ok", failed: "pill-bad", interrupted: "pill-warn", blocked: "pill-warn", cancelled: "pill-warn", running: "pill-accent" };
function duration(r) {
  if (!r.finished_at) return "";
  const s = Math.max(0, Math.round(r.finished_at - r.started_at));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}
/** result_summary is JSON for a normal run, or plain text (blocked/interrupted). */
function summary(r) {
  if (!r.result_summary) return { text: "", lines: [] };
  try {
    const obj = JSON.parse(r.result_summary);
    if (!obj || typeof obj !== "object") return { text: r.result_text || String(obj), lines: [] };
    const lines = Array.isArray(obj.verbose_log) ? obj.verbose_log : [];
    // 3.7.0: the server's wording in the reader's language (result_text), else the raw keys.
    const text = r.result_text || Object.entries(obj).filter(([k]) => k !== "verbose_log")
      .map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ");
    return { text, lines };
  } catch {
    return { text: r.result_text || r.result_summary, lines: [] };
  }
}

async function stopJob(job) {
  if (!(await askConfirm(t("Stop “{title}”?", { title: job.name }) + " " + t("What it has done so far is kept."), { ok: t("Stop"), danger: true }))) return;
  try { await api(`/api/library/activity/${job.progress.id}/stop`, { method: "POST" }); toast(t("Stopping “{name}”…", { name: job.name })); load(); }
  catch (err) { toast(err.message || t("Could not stop it"), { type: "error" }); }
}

async function loadHistory(id) {
  if (!Array.isArray(history.value[id])) history.value = { ...history.value, [id]: "loading" };
  try {
    const runs = await api(`/api/scheduler/jobs/${id}/runs`);
    history.value = { ...history.value, [id]: runs };
  } catch (err) {
    history.value = { ...history.value, [id]: err.message || t("Could not load the history") };
  }
}
function toggleHistory(id) {
  if (id in history.value) { const h = { ...history.value }; delete h[id]; history.value = h; }
  else loadHistory(id);
}
function toggleDetails(runId) {
  const s = new Set(openDetails.value);
  s.has(runId) ? s.delete(runId) : s.add(runId);
  openDetails.value = s;
}

async function runNow(job) {
  try { await api(`/api/scheduler/jobs/${job.id}/run-now`, { method: "POST" }); toast(t("Started {name}", { name: job.name })); }
  catch (err) { toast(err.message || t("Could not start the job"), { type: "error" }); }
  load();
}
async function setEnabled(job, on) {
  try { await api(`/api/scheduler/jobs/${job.id}`, { method: "PATCH", body: { enabled: on } }); }
  catch (err) { toast(err.message || t("Could not change the job"), { type: "error" }); }
  load();
}
async function remove(job) {
  if (!(await askConfirm(t("Delete the job “{name}” and its history?", { name: job.name }), { ok: t("Delete"), danger: true }))) return;
  try { await api(`/api/scheduler/jobs/${job.id}`, { method: "DELETE" }); toast(t("Job deleted")); }
  catch (err) { toast(err.message || t("Could not delete the job"), { type: "error" }); }
  load();
}

function openDialog(job = null) {
  const c = job?.schedule_config || {};
  form.value = {
    job_type: job?.job_type || types.value[0]?.job_type || "",
    name: job?.name || "",
    enabled: job ? job.enabled : true,
    mode: job?.schedule_mode || "simple",
    kind: c.kind || "daily",
    time: c.time || "03:00",
    hours: c.hours || 6,
    day: c.day || "monday",
    expression: c.expression || "0 3 * * *",
    at: c.at || tomorrowAt("03:00"),
    verbose: !!job?.job_config?.verbose_logging,
  };
  formError.value = "";
  dialog.value = { job };
}
const nameTouched = ref(false);
const defaultName = computed(() => typeOf(form.value.job_type)?.label || "");

async function save() {
  const f = form.value;
  const schedule_config = f.mode === "advanced" ? { expression: f.expression.trim() }
    : f.kind === "interval_hours" ? { kind: f.kind, hours: Math.max(1, Math.round(Number(f.hours) || 6)) }
    : f.kind === "weekly" ? { kind: f.kind, day: f.day, time: f.time || "03:00" }
    : f.kind === "once" ? { kind: "once", at: f.at || tomorrowAt("03:00") }
    : { kind: "daily", time: f.time || "03:00" };
  const body = {
    name: f.name.trim() || defaultName.value,
    enabled: f.enabled,
    schedule_mode: f.mode,
    schedule_config,
    job_config: f.job_type === "duplicate_scan" ? { verbose_logging: f.verbose } : {},
  };
  busy.value = true;
  formError.value = "";
  try {
    const job = dialog.value.job;
    if (job) await api(`/api/scheduler/jobs/${job.id}`, { method: "PATCH", body });
    else await api("/api/scheduler/jobs", { method: "POST", body: { job_type: f.job_type, ...body } });
    toast(job ? t("Job saved") : t("Job created"), { type: "success" });
    dialog.value = null;
    load();
  } catch (err) {
    formError.value = err.message || t("Could not save the job");
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>{{ $t("Scheduler") }}</h1>
        <p><Tx msg="Runs background jobs on a schedule — or right away with “Run now”. Jobs that change files never run at the same time as each other. {b1} jobs are built in: change or disable them, they can't be deleted."><template #b1><strong>{{ $t("System") }}</strong></template></Tx></p>
      </div>
      <button class="btn btn-primary btn-sm" :disabled="!types.length" @click="openDialog()"><Icon name="plus" />{{ $t("Add job") }}</button>
    </header>

    <p v-if="error" class="error-text">{{ error }}</p>
    <div v-else-if="!jobs" class="skeleton" style="height: 10rem; border-radius: var(--radius-lg)" />
    <div v-else-if="!jobs.length" class="card empty"><div class="empty-icon">🗓️</div><p>{{ $t("No jobs yet. Add one to run a task regularly.") }}</p></div>

    <section v-for="job in jobs || []" :key="job.id" class="card panel job">
      <div class="panel-head">
        <div class="job-title">
          <h2>{{ job.name }}</h2>
          <span class="pill" :title="typeOf(job.job_type)?.category === 'invasive' ? $t('Changes files: never runs alongside other such jobs') : $t('Read-only')">{{ typeOf(job.job_type)?.label || job.job_type }}</span>
          <span v-if="job.system" class="pill" :title="$t('Built in: it can be changed or disabled, not deleted')">{{ $t("System") }}</span>
          <span v-if="job.running" class="pill pill-accent">{{ $t("Running…") }}</span>
        </div>
        <label class="switch"><input type="checkbox" :checked="job.enabled" @change="setEnabled(job, $event.target.checked)">{{ $t("Enabled") }}</label>
      </div>
      <div v-if="job.running && job.progress" class="live">
        <span><strong>{{ job.progress.stage || $t("Working…") }}</strong>
          <template v-if="job.progress.total"> · {{ $t("{done} of {total} done", { done: formatNumber(job.progress.done || 0), total: formatNumber(job.progress.total) }) }}</template>
          <template v-if="job.progress.percent != null"> · {{ Math.round(job.progress.percent) }}%</template>
          <span v-if="job.progress.detail" class="muted"> · {{ job.progress.detail }}</span></span>
        <div v-if="job.progress.percent != null" class="bar" role="progressbar" :aria-valuenow="Math.round(job.progress.percent)" aria-valuemin="0" aria-valuemax="100">
          <div :style="{ width: `${job.progress.percent}%` }" /></div>
        <span class="live-actions">
          <button v-if="job.progress.can_stop" class="btn btn-sm" @click="stopJob(job)"><Icon name="stop" :size="14" />{{ $t("Stop") }}</button>
          <span v-else-if="job.progress.stopped_by" class="muted small">{{ $t("Stopping…") }}</span>
          <router-link to="/tasks" class="small">{{ $t("All running work in Tasks") }}</router-link>
        </span>
      </div>
      <p class="meta">
        <span>{{ job.enabled ? describe(job) : job.schedule_config?.kind === "once" && job.last_run_at ? $t("Ran once (switch it on to run it again)") : $t("Disabled") }}</span>
        <span v-if="job.enabled && job.next_run_at">{{ $t("Next: {time}", { time: formatDateTime(job.next_run_at) }) }}</span>
        <span v-if="job.last_run_at">{{ $t("Last: {time}", { time: formatDateTime(job.last_run_at) }) }}
          <span class="pill" :class="STATUS_PILL[job.last_run_status]">{{ RUN_STATUS[job.last_run_status] || job.last_run_status }}</span></span>
      </p>
      <div class="form-actions">
        <button class="btn btn-sm" :disabled="job.running" @click="runNow(job)"><Icon name="play" />{{ $t("Run now") }}</button>
        <button class="btn btn-ghost btn-sm" @click="openDialog(job)"><Icon name="edit" />{{ $t("Edit") }}</button>
        <button v-if="!job.system" class="btn btn-ghost btn-sm btn-danger" :disabled="job.running" @click="remove(job)"><Icon name="trash" />{{ $t("Delete") }}</button>
        <span style="flex: 1" />
        <button class="btn btn-ghost btn-sm" @click="toggleHistory(job.id)">{{ job.id in history ? $t("Hide history") : $t("History") }}</button>
      </div>
      <div v-if="job.id in history" class="history">
        <p v-if="history[job.id] === 'loading'" class="muted small">{{ $t("Loading…") }}</p>
        <p v-else-if="typeof history[job.id] === 'string'" class="error-text">{{ history[job.id] }}</p>
        <p v-else-if="!history[job.id].length" class="muted small">{{ $t("No runs yet.") }}</p>
        <template v-else>
          <div v-for="r in history[job.id]" :key="r.id" class="run">
            <div class="run-head">
              <span class="pill" :class="STATUS_PILL[r.status]">{{ r.status }}</span>
              <span>{{ formatDateTime(r.started_at) }}</span>
              <span class="muted">{{ r.trigger }}<template v-if="duration(r)"> · {{ duration(r) }}</template></span>
            </div>
            <template v-if="summary(r).text || summary(r).lines.length">
              <p v-if="summary(r).text" class="run-sum">{{ summary(r).text }}</p>
              <button v-if="summary(r).lines.length" class="btn btn-ghost btn-sm" @click="toggleDetails(r.id)">
                {{ openDetails.has(r.id) ? $t("Hide details") : $t("Show {n} detail lines", { n: summary(r).lines.length }) }}</button>
              <pre v-if="openDetails.has(r.id)" class="log mono scroll-thin">{{ summary(r).lines.join("\n") }}</pre>
            </template>
          </div>
        </template>
      </div>
    </section>

    <ModalDialog v-if="dialog" :title="dialog.job ? $t('Edit job') : $t('Add job')" @close="dialog = null">
      <form id="job-form" class="form" @submit.prevent="save">
        <label class="field">{{ $t("Task") }}
          <select v-model="form.job_type" class="select" :disabled="!!dialog.job">
            <option v-for="t in types" :key="t.job_type" :value="t.job_type">{{ t.label }}</option>
          </select>
          <small v-if="dialog.job" class="muted">{{ $t("The task can't be changed; add a new job instead.") }}</small>
        </label>
        <label class="field">{{ $t("Name") }}
          <input v-model="form.name" class="input" :placeholder="defaultName" @input="nameTouched = true">
        </label>
        <div class="field">{{ $t("Schedule") }}
          <div class="seg">
            <button type="button" class="btn btn-sm" :class="{ 'btn-primary': form.mode === 'simple' }" @click="form.mode = 'simple'">{{ $t("Simple") }}</button>
            <button type="button" class="btn btn-sm" :class="{ 'btn-primary': form.mode === 'advanced' }" @click="form.mode = 'advanced'">{{ $t("Cron") }}</button>
          </div>
        </div>
        <div v-if="form.mode === 'simple'" class="sched">
          <select v-model="form.kind" class="select" :aria-label="$t('How often')">
            <option value="daily">{{ $t("Every day") }}</option>
            <option value="weekly">{{ $t("Every week") }}</option>
            <option value="interval_hours">{{ $t("Every few hours") }}</option>
            <option value="once">{{ $t("One time") }}</option>
          </select>
          <select v-if="form.kind === 'weekly'" v-model="form.day" class="select" :aria-label="$t('Day')">
            <option v-for="d in DAYS" :key="d" :value="d">{{ cap(d) }}</option>
          </select>
          <input v-if="form.kind === 'once'" v-model="form.at" class="input" type="datetime-local" :aria-label="$t('Date and time')" required>
          <input v-else-if="form.kind !== 'interval_hours'" v-model="form.time" class="input" type="time" :aria-label="$t('Time')" required>
          <label v-else class="inline">{{ $t("every") }} <input v-model.number="form.hours" class="input input-num" type="number" min="1" max="168" :aria-label="$t('Hours')"> {{ $t("hours") }}</label>
        </div>
        <small v-if="form.mode === 'simple' && form.kind === 'once'" class="muted">{{ $t("Runs once at this time (the server's clock) and then switches itself off. A time that has passed runs within a minute.") }}</small>
        <div v-else class="field">
          <input v-model="form.expression" class="input mono" placeholder="0 3 * * *" :aria-label="$t('Cron expression')" required>
          <small class="muted">{{ $t("minute hour day-of-month month day-of-week, server time — “0 3 * * *” is every day at 03:00, “0 */6 * * *” every 6 hours.") }}</small>
        </div>
        <label v-if="form.job_type === 'duplicate_scan'" class="switch"><input v-model="form.verbose" type="checkbox">{{ $t("Keep a detailed log of each run") }}</label>
        <label class="switch"><input v-model="form.enabled" type="checkbox">{{ $t("Enabled") }}</label>
        <p v-if="formError" class="error-text">{{ formError }}</p>
      </form>
      <template #footer>
        <button class="btn" type="button" @click="dialog = null">{{ $t("Cancel") }}</button>
        <button class="btn btn-primary" type="submit" form="job-form" :disabled="busy">{{ busy ? $t("Saving…") : $t("Save") }}</button>
      </template>
    </ModalDialog>
  </div>
</template>

<style scoped>
.job { gap: 0.625rem; }
.job-title { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.meta { margin: 0; display: flex; flex-wrap: wrap; gap: 0.25rem 1.25rem; font-size: 0.82rem; color: var(--text-2); }
.history { border-top: 1px solid var(--border); padding-top: 0.625rem; display: flex; flex-direction: column; gap: 0.5rem; }
.run { display: flex; flex-direction: column; gap: 0.25rem; padding: 0.5rem 0.625rem; background: var(--panel-2); border-radius: var(--radius); }
.run-head { display: flex; gap: 0.625rem; align-items: center; flex-wrap: wrap; font-size: 0.82rem; }
.run-sum { margin: 0; font-size: 0.8rem; color: var(--text-2); overflow-wrap: anywhere; }
.run .btn { align-self: flex-start; }
.log { margin: 0; max-height: 18rem; overflow: auto; padding: 0.5rem; background: var(--bg); border-radius: var(--radius-sm); white-space: pre-wrap; }
.small { font-size: 0.8rem; margin: 0; }
.form { display: flex; flex-direction: column; gap: 0.875rem; }
.field { display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.84rem; font-weight: 600; }
.field .input, .field .select, .field small { font-weight: 400; }
.seg { display: flex; gap: 0.25rem; }
.sched { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
.sched .select { width: auto; }
.sched .input[type="time"] { width: 8rem; }
.inline { display: inline-flex; align-items: center; gap: 0.5rem; font-size: 0.875rem; }
.live { display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.84rem; padding: 0.5rem 0.625rem; border-radius: var(--radius);
  background: color-mix(in srgb, var(--accent) 7%, transparent); margin-bottom: 0.5rem; }
.live .bar { height: 0.375rem; border-radius: 999px; background: var(--panel-hover); overflow: hidden; max-width: 30rem; }
.live .bar div { height: 100%; background: var(--accent); transition: width 0.5s ease; }
.live .small { font-size: 0.76rem; align-self: flex-start; }
.live-actions { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
</style>
