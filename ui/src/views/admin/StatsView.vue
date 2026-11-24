<script setup>
// Management -> Stats (2.11.0):
//  - Live: CPU, memory, network, disks, what's being worked on -- every 5 s
//    while the page is visible (GET /api/stats/machine).
//  - Over a range (7 days ... all): library growth, activity (downloads,
//    uploads, re-encodes, repairs and failures), watching (time,
//    plays, top videos / performers / tags, per user) and free disk space
//    (GET /api/stats/history). Growth and downloads come from the videos
//    themselves (whole history); the other activity counts and watching are
//    recorded from 2.11.0 on.
//  - The library now: codecs, resolutions, top tags / performers... (GET /api/stats/live).
import { t, formatNumber, tn, locale } from "../../lib/i18n.js";
import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import Icon from "../../components/Icon.vue";
import MiniChart from "../../components/MiniChart.vue";
import { api } from "../../lib/api.js";
import { formatBytes, formatUptime } from "../../lib/format.js";
import { toast } from "../../stores/toast.js";

const RANGES = [["7d", t("7 days")], ["30d", t("30 days")], ["90d", t("90 days")], ["1y", t("Year")], ["all", t("All")]];
const RANGE_KEY = "pmv-stats-range";
const range = ref((() => { try { return localStorage.getItem(RANGE_KEY) || "30d"; } catch { return "30d"; } })());
const hist = ref(null);
const lib = ref(null);
const live = ref(null);
const error = ref("");

// Colours: one per kind of activity, the same in every chart.
const C = { downloads: "#3b82f6", uploads: "#14b8a6", reencodes: "#a855f7", repairs: "#64748b",
  failures: "#ef4444", watch: "#ec4899", plays: "#8b5cf6", videos: "#3b82f6", size: "#14b8a6", free: "#22c55e" };

async function loadHistory() {
  try { hist.value = await api(`/api/stats/history?range=${range.value}`); error.value = ""; }
  catch (err) { error.value = err.message || t("Could not load the stats"); }
}
async function loadLibrary() { try { lib.value = (await api("/api/stats/live")).library; } catch { /* shown as missing */ } }
async function loadLive() {
  if (document.hidden) return;
  try { live.value = await api("/api/stats/machine"); } catch { /* next tick */ }
}
watch(range, (r) => { try { localStorage.setItem(RANGE_KEY, r); } catch { /* private mode */ } loadHistory(); });
let timer = null;
onMounted(() => { loadHistory(); loadLibrary(); loadLive(); timer = setInterval(loadLive, 5000); });
onBeforeUnmount(() => clearInterval(timer));

async function snapshot() {
  try { await api("/api/stats/snapshot", { method: "POST" }); toast(t("Snapshot saved"), { type: "success" }); loadHistory(); }
  catch (err) { toast(err.message || t("Could not save a snapshot"), { type: "error" }); }
}

const sum = (a) => (a || []).reduce((x, y) => x + y, 0);
const h = computed(() => hist.value);
const failures = computed(() => {
  const a = h.value?.activity || {};
  return h.value ? h.value.labels.map((_, i) => ["download_failures", "upload_failures", "reencode_failures", "repair_failures"]
    .reduce((s, k) => s + (a[k]?.[i] || 0), 0)) : [];
});
const tiles = computed(() => {
  if (!h.value) return [];
  const g = h.value.growth, a = h.value.activity;
  const last = g.videos.length - 1;
  return [
    { k: t("Videos"), v: formatNumber(g.videos[last] || 0), s: t("+{n} in this range", { n: formatNumber(sum(g.added)) }) },
    { k: t("Library size"), v: formatBytes(g.bytes[last] || 0), s: `+${formatBytes(sum(g.added_bytes))}` },
    { k: t("Downloads"), v: formatNumber(sum(a.downloads)), s: t("{uploads} uploads · {reencodes} re-encodes", { uploads: formatNumber(sum(a.uploads)), reencodes: formatNumber(sum(a.reencodes)) }) },
    { k: t("Failures"), v: formatNumber(sum(failures.value)), s: t("downloads, uploads, re-encodes, repairs"), bad: sum(failures.value) > 0 },
    { k: t("Watched"), v: hours(sum(h.value.watch.seconds)), s: tn("{n} play", "{n} plays", sum(h.value.watch.plays)) },
  ];
});
function hours(s) {
  if (s < 60) return t("{n} s", { n: Math.round(s) });
  if (s < 3600) return t("{n} min", { n: Math.round(s / 60) });
  return t("{n} h", { n: formatNumber(Number((s / 3600).toFixed(s < 36000 ? 1 : 0))) });
}
// Dates on the charts in the reader's language (3.7.0: the browser's month names).
function dateText(y, m, d, opts) {
  try { return new Date(y, m - 1, d || 1, 12).toLocaleDateString(locale(), opts); } catch { return `${y}-${m}-${d || 1}`; }
}
function xFmt(label, long) {
  if (!label) return "";
  const [y, m, d] = label.split("-").map(Number);
  if (h.value?.unit === "month") return dateText(y, m, 1, long || m === 1 ? { month: "short", year: "numeric" } : { month: "short" });
  const day = dateText(y, m, d, long ? { day: "numeric", month: "short", year: "numeric" } : { day: "numeric", month: "short" });
  return long && h.value?.unit === "week" ? t("Week of {date}", { date: day }) : day;
}
const growthSeries = computed(() => (h.value ? [{ name: t("Videos"), values: h.value.growth.videos, color: C.videos }] : []));
const sizeSeries = computed(() => (h.value ? [{ name: t("Library size"), values: h.value.growth.bytes, color: C.size }] : []));
const activitySeries = computed(() => {
  if (!h.value) return [];
  const a = h.value.activity;
  return [
    { name: t("Downloads"), values: a.downloads, color: C.downloads }, { name: t("Uploads"), values: a.uploads, color: C.uploads },
    { name: t("Re-encodes"), values: a.reencodes, color: C.reencodes },
    { name: t("Repairs"), values: a.repairs, color: C.repairs }, { name: t("Failures"), values: failures.value, color: C.failures },
  ].filter((s, i) => s.values.some((v) => v) || i === 0);  // downloads always
});
const watchSeries = computed(() => (h.value ? [{ name: t("Watched"), values: h.value.watch.seconds, color: C.watch }] : []));
const diskSeries = computed(() => (h.value?.disk?.length ? [{ name: t("Free space"), values: h.value.disk.map((d) => d.free_bytes), color: C.free }] : []));
const diskLabels = computed(() => (h.value?.disk || []).map((d) => new Date(d.t * 1000).toISOString().slice(0, 10)));
const fmtCount = (v) => formatNumber(Math.round(v));
const fmtBytesShort = (v) => formatBytes(v);
const UNITS = { day: t("per day"), week: t("per week"), month: t("per month") };

// Live
const m = computed(() => live.value?.machine || {});
const sys = computed(() => live.value?.system || {});
// One line per drive: the download and cache folders share one when they're on the same drive.
const disks = computed(() => {
  const out = sys.value.output_dir_disk, cache = sys.value.cache_dir_disk;
  const list = out && cache && out.device === cache.device ? [[t("Download & cache folders (same drive)"), out]]
    : [[t("Download folder"), out], [t("Cache folder"), cache]];
  return list.filter(([, d]) => d).map(([label, d]) => ({ label, d, pct: d.total_bytes ? Math.round((d.used_bytes / d.total_bytes) * 100) : 0,
    freePct: d.free_percent ?? (d.total_bytes ? Math.round((d.free_bytes / d.total_bytes) * 1000) / 10 : null) }));
});
const app = computed(() => m.value.app || null);
const pct = (v) => (v == null ? "—" : `${v < 10 ? v.toFixed(1) : Math.round(v)}%`);
const workers = computed(() => Object.entries(sys.value.workers || {}).filter(([, n]) => n));
function rate(b) {
  if (b == null) return "—";
  if (b < 1024) return `${Math.round(b)} B/s`;
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(b < 10240 ? 1 : 0)} KB/s`;
  return `${(b / 1024 ** 2).toFixed(1)} MB/s`;
}

// Library composition
const LISTS = [[t("Codecs"), "top_codecs"], [t("Resolutions"), "top_resolutions"], [t("Top tags"), "top_tags"], [t("Top performers"), "top_performers"]];
const pairs = (v) => (Array.isArray(v) ? v : Object.entries(v || {}));
const maxOf = (rows) => Math.max(1, ...rows.map(([, n]) => Number(n) || 0));
const saved = (b) => (b > 0 ? t("{size} saved", { size: formatBytes(b) }) : b < 0 ? t("{size} larger", { size: formatBytes(-b) }) : t("no size change"));
</script>

<template>
  <div class="page stats">
    <div class="top">
      <div class="seg" role="tablist" :aria-label="$t('Range')">
        <button v-for="[k, l] in RANGES" :key="k" role="tab" :aria-selected="range === k" class="btn btn-sm" :class="{ 'btn-primary': range === k }" @click="range = k">{{ l }}</button>
      </div>
      <span class="muted small">{{ $t("Live figures update every 5 s.") }}</span>
      <button class="btn btn-ghost btn-sm" :title="$t('Save today\'s totals and disk space now (also done daily by the Scheduler)')" @click="snapshot">{{ $t("Snapshot now") }}</button>
    </div>
    <p v-if="error" class="error-text">{{ error }}</p>

    <!-- Live -->
    <section class="card panel">
      <h2><span class="dot" aria-hidden="true" />{{ $t("Right now") }}</h2>
      <div class="live-grid">
        <!-- Machine total (grey) with this app's part of it (colour) inside the same bar. -->
        <div class="gauge"><span class="k">CPU</span>
          <span class="v">{{ pct(app?.cpu_percent) }} <small class="of">{{ $t("this app") }}</small></span>
          <div class="meter two"><span class="all" :style="{ width: `${m.cpu_percent || 0}%` }" /><span class="mine" :style="{ width: `${app?.cpu_percent || 0}%` }" /></div>
          <span class="s">{{ $t("Whole machine {percent}", { percent: pct(m.cpu_percent) }) }}<template v-if="app?.helpers?.count"> · FFmpeg ×{{ app.helpers.count }} {{ pct(app.helpers.cpu_percent) }}</template></span></div>
        <div class="gauge"><span class="k">{{ $t("Memory") }}</span>
          <span class="v">{{ app ? formatBytes(app.memory_bytes) : "—" }} <small class="of">{{ $t("this app") }}</small></span>
          <div class="meter two"><span class="all" :style="{ width: `${m.memory?.percent || 0}%` }" /><span class="mine" :style="{ width: `${app?.memory_percent || 0}%` }" /></div>
          <span class="s">{{ app ? $t("{percent} of the memory", { percent: pct(app.memory_percent) }) + " · " : "" }}{{ $t("whole machine {usage}", { usage: m.memory ? $t("{percent}% ({used} of {total})", { percent: Math.round(m.memory.percent), used: formatBytes(m.memory.used), total: formatBytes(m.memory.total) }) : "—" }) }}</span></div>
        <div class="gauge"><span class="k">{{ $t("Network") }}</span><span class="v small-v">↓ {{ rate(m.net?.rx_per_s) }}</span><span class="s">↑ {{ rate(m.net?.tx_per_s) }} · {{ $t("whole machine") }}</span></div>
        <div class="gauge"><span class="k">{{ $t("App") }}</span><span class="v small-v">{{ $t("running {time}", { time: formatUptime(sys.uptime_seconds) }) }}</span>
          <span class="s">{{ $t("database {size}", { size: formatBytes(sys.database_size_bytes) }) }}<template v-if="app?.self"> · {{ $t("app itself {cpu} CPU, {memory}", { cpu: pct(app.self.cpu_percent), memory: formatBytes(app.self.memory_bytes) }) }}</template></span></div>
      </div>
      <div v-for="x in disks" :key="x.label" class="disk">
        <div class="disk-head"><span>{{ x.label }}</span><span class="muted"><strong :class="{ low: x.freePct != null && x.freePct < 15 }">{{ $t("{percent}% free", { percent: x.freePct ?? "—" }) }}</strong>
          · {{ $t("{done} of {total}", { done: formatBytes(x.d.free_bytes), total: formatBytes(x.d.total_bytes) }) }}</span></div>
        <div class="meter"><span :class="{ full: x.pct > 90 }" :style="{ width: `${x.pct}%` }" /></div>
      </div>
      <div class="workers">
        <span v-if="!workers.length" class="muted small">{{ $t("Nothing is being worked on.") }}</span>
        <span v-for="[k, n] in workers" :key="k" class="pill pill-accent">{{ k.replace(/_/g, " ") }}: {{ n === true ? $t("yes") : n }}</span>
        <span v-for="d in m.downloading || []" :key="d.title" class="pill">⬇ {{ d.title }} {{ d.percent != null ? `${Math.round(d.percent)}%` : "" }} {{ d.speed }}</span>
      </div>
    </section>

    <!-- Range -->
    <div v-if="!h" class="skeleton" style="height: 14rem; border-radius: var(--radius-lg)" />
    <template v-else>
      <div class="tiles">
        <div v-for="t in tiles" :key="t.k" class="card stat"><span class="k">{{ t.k }}</span><span class="v" :class="{ bad: t.bad }">{{ t.v }}</span><span class="s">{{ t.s }}</span></div>
      </div>
      <div class="charts">
        <section class="card panel"><h2>{{ $t("Library") }}</h2>
          <MiniChart type="line" :labels="h.labels" :series="growthSeries" :format="fmtCount" :x-format="xFmt" />
          <MiniChart type="line" :labels="h.labels" :series="sizeSeries" :format="fmtBytesShort" :x-format="xFmt" :height="140" /></section>
        <section class="card panel"><h2>{{ $t("Activity") }} <span class="muted small">{{ UNITS[h.unit] || h.unit }}</span></h2>
          <MiniChart :labels="h.labels" :series="activitySeries" :format="fmtCount" :x-format="xFmt" :height="220" />
          <p class="muted small note">{{ h.counters_since ? $t("Downloads cover the whole history; the rest are counted from {date} on.", { date: new Date(`${h.counters_since}T12:00`).toLocaleDateString(locale(), { day: "numeric", month: "long", year: "numeric" }) }) : $t("Downloads cover the whole history; the rest are counted from this version on.") }}</p></section>
        <section class="card panel"><h2>{{ $t("Watching") }} <span class="muted small">{{ UNITS[h.unit] || h.unit }}</span></h2>
          <MiniChart :labels="h.labels" :series="watchSeries" :format="(v) => hours(v)" :x-format="xFmt" />
          <p class="muted small note">{{ $t("Time actually played (not paused or skipped); a play counts after 10 seconds.") }}</p></section>
        <section class="card panel"><h2>{{ $t("Free disk space") }}</h2>
          <MiniChart v-if="diskSeries.length" type="line" :labels="diskLabels" :series="diskSeries" :format="fmtBytesShort" :x-format="(l) => l.slice(5)" />
          <p v-else class="muted small">{{ $t("Recorded by the daily snapshot (Scheduler → Daily stats snapshot) — the chart fills in from tomorrow.") }}</p></section>
      </div>

      <section class="card panel">
        <h2>{{ $t("Most watched") }}</h2>
        <div class="lists">
          <div class="list wide">
            <h3>{{ $t("Videos") }}</h3>
            <p v-if="!h.top_watched.length" class="muted small">{{ $t("Nothing watched in this range.") }}</p>
            <ol class="toplist">
              <li v-for="v in h.top_watched" :key="v.id"><div class="row">
                <span class="what"><router-link :to="{ name: 'video', params: { id: v.id } }">{{ v.title }}</router-link>
                  <span class="muted small">{{ v.creator }}</span></span>
                <span class="n">{{ hours(v.seconds) }} · {{ $tn("{n} play", "{n} plays", v.plays) }}</span></div></li>
            </ol>
          </div>
          <div v-for="[title, key] in [[$t('Performers'), 'top_performers'], [$t('Tags'), 'top_tags']]" :key="key" class="list">
            <h3>{{ title }}</h3>
            <p v-if="!h[key].length" class="muted small">—</p>
            <div v-for="r in h[key]" :key="r.name" class="bar-row">
              <span class="name" :title="r.name">{{ r.name }}</span>
              <span class="bar"><span :style="{ width: `${(r.seconds / Math.max(1, h[key][0].seconds)) * 100}%`, background: C.watch }" /></span>
              <span class="n">{{ hours(r.seconds) }}</span>
            </div>
          </div>
          <div class="list">
            <h3>{{ $t("Per person") }}</h3>
            <p v-if="!h.per_user.length" class="muted small">—</p>
            <div v-for="u in h.per_user" :key="u.user" class="bar-row">
              <span class="name">{{ u.user }}</span>
              <span class="bar"><span :style="{ width: `${(u.seconds / Math.max(1, h.per_user[0].seconds)) * 100}%`, background: C.plays }" /></span>
              <span class="n">{{ hours(u.seconds) }}</span>
            </div>
          </div>
        </div>
      </section>
    </template>

    <!-- The library now -->
    <section v-if="lib" class="card panel">
      <h2>{{ $t("The library now") }}</h2>
      <div class="tiles">
        <div class="card stat"><span class="k">{{ $t("Re-encodes done") }}</span><span class="v">{{ (lib.cumulative_reencodes_performed || 0).toLocaleString() }}</span>
          <span class="s">{{ saved(lib.cumulative_reencode_bytes_saved) }}</span></div>
        <div class="card stat"><span class="k">{{ $t("Repairs that fixed something") }}</span><span class="v">{{ (lib.cumulative_repairs_performed || 0).toLocaleString() }}</span>
          <span class="s">{{ $tn("{n} fix", "{n} fixes", lib.cumulative_repair_fixes_applied || 0) }}</span></div>
        <div class="card stat"><span class="k">{{ $t("Possible duplicates") }}</span><span class="v">{{ (lib.duplicate_group_count || 0).toLocaleString() }}</span><span class="s">{{ $t("groups") }}</span></div>
      </div>
      <div class="lists">
        <div v-for="[title, key] in LISTS" :key="key" class="list">
          <h3>{{ title }}</h3>
          <p v-if="!pairs(lib[key]).length" class="muted small">{{ $t("No data") }}</p>
          <div v-for="[name, n] in pairs(lib[key])" :key="name" class="bar-row">
            <span class="name" :title="name">{{ name || $t("Unknown") }}</span>
            <span class="bar"><span :style="{ width: `${(Number(n) / maxOf(pairs(lib[key]))) * 100}%` }" /></span>
            <span class="n">{{ Number(n).toLocaleString() }}</span>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.top { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
.seg { display: flex; gap: 0.25rem; flex-wrap: wrap; }
.small { font-size: 0.78rem; }
.panel { padding: 1rem 1.125rem; display: flex; flex-direction: column; gap: 0.75rem; min-width: 0; }
.panel h2 { font-size: 1rem; display: flex; align-items: center; gap: 0.5rem; margin: 0; }
h3 { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); margin: 0 0 0.375rem; }
.dot { width: 0.55rem; height: 0.55rem; border-radius: 50%; background: var(--success); box-shadow: 0 0 0 3px color-mix(in srgb, var(--success) 25%, transparent); }
.live-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr)); gap: 0.75rem; }
.gauge { display: flex; flex-direction: column; gap: 0.2rem; }
.k { font-size: 0.72rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.04em; }
.v { font-size: 1.45rem; font-weight: 750; font-variant-numeric: tabular-nums; }
.v.small-v { font-size: 1.05rem; }
.v.bad { color: var(--danger); }
.s { font-size: 0.75rem; color: var(--text-2); }
.meter { height: 0.4rem; border-radius: 999px; background: var(--panel-hover); overflow: hidden; }
.meter span { display: block; height: 100%; background: var(--accent); transition: width 0.6s ease; }
.meter span.full { background: var(--danger); }
.disk { display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.82rem; }
.disk-head { display: flex; justify-content: space-between; gap: 1rem; }
.workers { display: flex; flex-wrap: wrap; gap: 0.375rem; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); gap: 0.75rem; }
.stat { padding: 0.75rem 0.875rem; display: flex; flex-direction: column; gap: 0.15rem; }
.charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 26rem), 1fr)); gap: 1rem; }
.note { margin: 0; }
.lists { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 15rem), 1fr)); gap: 1rem 1.5rem; }
.list.wide { grid-column: span 2; }
@media (max-width: 40rem) { .list.wide { grid-column: auto; } }
.toplist { margin: 0; padding-inline-start: 1.25rem; display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.84rem; }
.toplist li a { font-weight: 600; color: var(--text); margin-inline-end: 0.4rem; }
/* A long title wraps in its own column; the time and plays stay on the right, on its first line (3.4.0). */
.toplist .row { display: flex; align-items: baseline; gap: 0.75rem; }
.toplist .what { flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; }
.toplist .n { flex: none; white-space: nowrap; font-variant-numeric: tabular-nums; color: var(--text-2); font-size: 0.78rem; }
.bar-row { display: grid; grid-template-columns: minmax(0, 8rem) 1fr auto; gap: 0.5rem; align-items: center; font-size: 0.8rem; padding: 0.1rem 0; }
.bar-row .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar { height: 0.45rem; background: var(--panel-hover); border-radius: 999px; overflow: hidden; }
.bar span { display: block; height: 100%; background: var(--accent); }
.bar-row .n { font-variant-numeric: tabular-nums; color: var(--text-2); }
.of { font-size: 0.72rem; font-weight: 500; color: var(--text-2); }
.meter.two { position: relative; }
.meter.two span { position: absolute; inset-inline-start: 0; top: 0; bottom: 0; }
.meter.two .all { background: var(--border-strong); }
.meter.two .mine { background: var(--accent); }
.disk-head strong { color: var(--text); }
.disk-head strong.low { color: var(--danger); }
</style>
