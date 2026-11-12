<script setup>
// Admin -> Logs: every action a user took and everything the app's
// background jobs logged, newest first, 200 at a time. Each entry is one
// JSON record (click a row to see all of it); filters live in the URL and
// "Export" downloads the matching entries as JSON Lines.
import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { useRoute, useRouter } from "vue-router";
import Icon from "../../components/Icon.vue";
import { api, qs } from "../../lib/api.js";

const route = useRoute();
const router = useRouter();
const ACTIONS = "!system";
const LEVELS = [
  { key: "DEBUG", label: "Everything (debug)" }, { key: "INFO", label: "Info and up" },
  { key: "WARNING", label: "Warnings and errors" }, { key: "ERROR", label: "Errors only" },
];
const IP_MODES = { hashed: "hashed", masked: "masked", full: "full addresses", off: "not recorded" };

const filters = computed(() => ({
  q: route.query.q || "", min_level: route.query.level || "INFO",
  // Default: user actions only, like most audit views; "all" shows system entries too.
  category: route.query.cat === "all" ? "" : route.query.cat || ACTIONS,
  event: route.query.event || "", user: route.query.user || "", ip: route.query.ip || "",
}));
function setFilter(patch) {
  const next = { ...route.query, ...patch };
  if ("cat" in patch) next.event = "";
  for (const k of Object.keys(next)) if (!next[k]) delete next[k];
  router.replace({ query: next });
}

const items = ref([]);
const total = ref(null);
const hasMore = ref(false);
const loading = ref(false);
const error = ref("");
const facets = ref({ category: {}, event: {}, user: {} });
const recording = ref(true);
const ipMode = ref("");
const open = ref(new Set());
const live = ref(true);
let timer = null;
let generation = 0;

function params(extra = {}) {
  const f = filters.value;
  return qs({ ...f, limit: 200, ...extra });
}
async function loadFacets() {
  try {
    const r = await api(`/api/admin/logs/facets?${qs({ min_level: filters.value.min_level })}`);
    facets.value = r;
    ipMode.value = r.ip_mode;
    recording.value = r.recording !== false;
  } catch { /* menus stay as they were */ }
}
async function load() {
  const gen = ++generation;
  loading.value = true;
  try {
    const res = await api(`/api/admin/logs?${params()}`);
    if (gen !== generation) return;
    items.value = res.items;
    total.value = res.total;
    hasMore.value = res.has_more;
    error.value = "";
  } catch (err) {
    if (gen === generation) error.value = err.message || "Could not load the logs";
  } finally {
    if (gen === generation) loading.value = false;
  }
}
async function loadOlder() {
  const last = items.value[items.value.length - 1];
  if (!last) return;
  loading.value = true;
  try {
    const res = await api(`/api/admin/logs?${params({ before_id: last.id })}`);
    items.value = [...items.value, ...res.items];
    hasMore.value = res.has_more;
  } finally {
    loading.value = false;
  }
}
async function poll() {
  if (!live.value || document.hidden || loading.value) return;
  const gen = generation;
  try {
    const res = await api(`/api/admin/logs?${params({ after_id: items.value[0]?.id ?? 0 })}`);
    if (gen !== generation || !res.items.length) return;
    items.value = [...res.items, ...items.value].slice(0, 2000);
    if (total.value !== null) total.value += res.items.length;
  } catch { /* next tick */ }
}

const searchText = ref(filters.value.q);
let searchTimer = null;
watch(searchText, (v) => { clearTimeout(searchTimer); searchTimer = setTimeout(() => setFilter({ q: v }), 300); });
watch(filters, () => { open.value = new Set(); load(); }, { deep: true });
watch(() => filters.value.min_level, loadFacets);

onMounted(() => { load(); loadFacets(); timer = setInterval(poll, 3000); });
onBeforeUnmount(() => { clearInterval(timer); clearTimeout(searchTimer); });

const categoryOptions = computed(() => Object.entries(facets.value.category || {}));
const eventOptions = computed(() => {
  const cat = filters.value.category;
  return Object.entries(facets.value.event || {}).filter(([name]) => {
    if (!cat) return true;
    if (cat.startsWith("!")) return !cat.slice(1).split(",").some((c) => name.startsWith(`${c}.`));
    return name.startsWith(`${cat}.`);
  });
});
const userOptions = computed(() => Object.entries(facets.value.user || {}));
const exportHref = computed(() => `/api/admin/logs/export?${qs(filters.value)}`);

function toggle(id) {
  const s = new Set(open.value);
  s.has(id) ? s.delete(id) : s.add(id);
  open.value = s;
}
function onRowKey(e, id) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(id); } }
const fmtWhen = (ts) => new Date(ts * 1000).toLocaleString([], { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
const levelClass = (l) => ({ ERROR: "err", CRITICAL: "err", WARNING: "warn", DEBUG: "dbg", SUCCESS: "ok" }[l] || "info");
const summary = computed(() => {
  if (total.value === null) return loading.value ? "Loading…" : "";
  const n = total.value.toLocaleString();
  return `${n} ${total.value === 1 ? "entry matches" : "entries match"}`
    + (total.value > items.value.length ? ` — showing the newest ${items.value.length.toLocaleString()}` : "");
});
</script>

<template>
  <div class="logs">
    <header class="head">
      <div>
        <h1>Logs</h1>
        <p class="muted small">Every action and everything the app's background jobs record, newest first. Click a row for the full entry.
          <span v-if="ipMode">Addresses: {{ IP_MODES[ipMode] || ipMode }}.</span>
          {{ " " }}<RouterLink :to="{ name: 'server', query: { tab: 'logging' } }">Logging settings</RouterLink></p>
        <p v-if="!recording" class="error-text small">New entries aren't being recorded: logging (or this log) is switched off in Server settings → Logging.</p>
      </div>
      <div class="head-actions">
        <label class="switch" title="Add new entries as they happen"><input v-model="live" type="checkbox">Live</label>
        <button class="btn btn-sm" title="Refresh" @click="load(); loadFacets()"><Icon name="repeat" />Refresh</button>
        <a class="btn btn-sm" :href="exportHref" title="Download the matching entries as JSON Lines (one JSON object per line)"><Icon name="download" />Export</a>
      </div>
    </header>

    <div class="filters">
      <label>Category
        <select class="select" :value="route.query.cat || ACTIONS" @change="setFilter({ cat: $event.target.value === ACTIONS ? '' : $event.target.value })">
          <option :value="ACTIONS">User actions (no system entries)</option>
          <option value="all">Everything</option>
          <option v-for="[c, n] in categoryOptions" :key="c" :value="c">{{ c }} ({{ n.toLocaleString() }})</option>
        </select>
      </label>
      <label>Event
        <select class="select" :value="filters.event" @change="setFilter({ event: $event.target.value })">
          <option value="">All</option>
          <option v-for="[e, n] in eventOptions" :key="e" :value="e">{{ e }} ({{ n.toLocaleString() }})</option>
        </select>
      </label>
      <label>User
        <select class="select" :value="filters.user" @change="setFilter({ user: $event.target.value })">
          <option value="">Anyone</option>
          <option v-for="[u, n] in userOptions" :key="u" :value="u">{{ u }} ({{ n.toLocaleString() }})</option>
        </select>
      </label>
      <label>Level
        <select class="select" :value="filters.min_level" @change="setFilter({ level: $event.target.value === 'INFO' ? '' : $event.target.value })">
          <option v-for="l in LEVELS" :key="l.key" :value="l.key">{{ l.label }}</option>
        </select>
      </label>
      <label class="grow">Search
        <input v-model="searchText" class="input" type="search" placeholder="Any text in the entry" autocomplete="off">
      </label>
    </div>
    <div v-if="filters.ip" class="ip-filter">
      Address <code>{{ filters.ip }}</code> <button class="btn btn-ghost btn-sm" @click="setFilter({ ip: '' })">✕ clear</button>
    </div>

    <p class="summary" aria-live="polite">{{ summary }}</p>
    <div v-if="error" class="empty"><div class="empty-icon">⚠️</div>{{ error }}</div>
    <div v-else class="card table-wrap">
      <table class="log-table">
        <thead><tr><th>Time</th><th>Level</th><th>Event</th><th>User</th><th>Address</th><th>What happened</th></tr></thead>
        <tbody>
          <tr v-if="!items.length && !loading"><td colspan="6" class="none">No entries match these filters.</td></tr>
          <template v-for="r in items" :key="r.id">
            <tr class="row" :class="[levelClass(r.level), { open: open.has(r.id) }]" tabindex="0" :aria-expanded="open.has(r.id)"
                @click="toggle(r.id)" @keydown="onRowKey($event, r.id)">
              <td class="time">{{ fmtWhen(r.ts) }}</td>
              <td class="level">{{ r.level }}</td>
              <td class="mono">{{ r.event }}</td>
              <td>{{ r.user || "—" }}</td>
              <td class="mono">
                <button v-if="r.ip" class="linkish" title="Show only this address" @click.stop="setFilter({ ip: r.ip })">{{ r.ip }}</button>
              </td>
              <td class="msg">{{ r.message }}<span v-if="r.detail" class="trace" title="Has error details"> ⋯</span></td>
            </tr>
            <tr v-if="open.has(r.id)" class="details"><td colspan="6"><pre>{{ JSON.stringify(r, null, 2) }}</pre></td></tr>
          </template>
        </tbody>
      </table>
    </div>
    <div class="foot">
      <button v-if="hasMore" class="btn btn-sm" :disabled="loading" @click="loadOlder">Show older entries</button>
    </div>
  </div>
</template>

<style scoped>
.logs { padding: 1rem 1.5rem 2rem; display: flex; flex-direction: column; gap: 0.75rem; }
.head { display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.head h1 { font-size: 1.4rem; }
.head p { margin: 0.25rem 0 0; }
.head-actions { display: flex; gap: 0.5rem; align-items: center; }
.small { font-size: 0.8rem; }
.filters { display: flex; flex-wrap: wrap; gap: 0.625rem; align-items: flex-end; }
.filters label { display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.75rem; font-weight: 600; color: var(--text-2); }
.filters .select { min-width: 10rem; max-width: 18rem; }
.filters .grow { flex: 1 1 14rem; }
.ip-filter { font-size: 0.84rem; display: flex; align-items: center; gap: 0.5rem; }
.summary { margin: 0; font-size: 0.8rem; color: var(--text-2); }
.table-wrap { overflow-x: auto; padding: 0; }
.log-table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
.log-table th { text-align: left; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); padding: 0.625rem 0.75rem; border-bottom: 1px solid var(--border); position: sticky; top: 0; background: var(--panel); }
.log-table td { padding: 0.45rem 0.75rem; border-bottom: 1px solid var(--border); vertical-align: baseline; }
.row { cursor: pointer; }
.row:hover, .row.open { background: var(--panel-2); }
.row:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.time { white-space: nowrap; color: var(--text-dim); font-variant-numeric: tabular-nums; }
.level { font: 700 0.7rem var(--mono); }
.mono { font-family: var(--mono); font-size: 0.75rem; white-space: nowrap; }
.msg { overflow-wrap: anywhere; min-width: 18rem; }
.trace { color: var(--danger); font-weight: 700; }
.info .level { color: var(--text-2); }
.ok .level { color: var(--success); }
.dbg td { color: var(--text-dim); }
.warn .level { color: var(--warning); }
.err { background: var(--danger-soft); }
.err .level { color: var(--danger); }
.linkish { border: 0; background: none; padding: 0; color: inherit; font: inherit; cursor: pointer; text-decoration: underline dotted; }
.details td { background: var(--panel-2); }
pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: 0.75rem/1.45 var(--mono); max-height: 30rem; overflow: auto; }
.none { text-align: center; color: var(--text-dim); padding: 1.5rem; }
.foot { display: flex; justify-content: center; }
@media (max-width: 56rem) { .logs { padding: 0.75rem 1rem 1.5rem; } }
</style>
