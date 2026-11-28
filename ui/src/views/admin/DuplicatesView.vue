<script setup>
// Admin -> Duplicates: groups of videos the duplicate scan matched, shown
// side by side with what tells them apart (quality, size, bitrate, ...), so
// you can keep the best copy, remove one, compare them playing, or mark the
// group as "not duplicates".
import { t, locale } from "../../lib/i18n.js";
import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { useRoute, useRouter } from "vue-router";
import Icon from "../../components/Icon.vue";
import VideoThumb from "../../components/VideoThumb.vue";
import ModalDialog from "../../components/ModalDialog.vue";
import CompareDialog from "../../components/CompareDialog.vue";
import DuplicateHistory from "../../components/admin/DuplicateHistory.vue";
import { api } from "../../lib/api.js";
import { releaseMedia } from "../../lib/media.js";
import { itemUrlTitle, codecLabel, qualityInfo, parseSizeBytes, bitrateKbps, parseDurationSeconds, formatDate, siteLabel } from "../../lib/format.js";
import { hasPermission } from "../../stores/session.js";
import { onLibraryChange } from "../../stores/live.js";
import { toast } from "../../stores/toast.js";

const route = useRoute();
const router = useRouter();
// 3.12.0: Found / Merged / Not duplicates
const VIEWS = [{ key: "found", label: t("Possible duplicates") }, { key: "merged", label: t("Merged") }, { key: "notdup", label: t("Not duplicates") }];
const view = computed(() => (VIEWS.some((v) => v.key === route.query.view) ? route.query.view : "found"));
const openView = (key) => router.replace({ query: { ...route.query, view: key === "found" ? undefined : key } });
const canManage = computed(() => hasPermission("duplicates.manage"));

const minMatch = computed(() => Math.min(100, Math.max(50, Number(route.query.min) || 75)));
const minDraft = ref(null);
function commitMin(e) { minDraft.value = null; router.replace({ query: { ...route.query, min: e.target.value === "75" ? undefined : e.target.value } }); }

const groups = ref(null);
const error = ref("");
const status = ref(null);
const busy = ref(false);
let statusTimer = null;

async function load() {
  try {
    const res = await api(`/api/library/issues?min_percentage=${minMatch.value}`);
    groups.value = res.issues.filter((g) => g.type === "duplicate");
    error.value = "";
  } catch (err) {
    error.value = err.message || t("Could not load duplicates");
  }
}

async function loadStatus() {
  clearTimeout(statusTimer);
  try {
    const wasRunning = status.value?.running;
    status.value = await api("/api/library/issues/scan-status");
    if (status.value.running) statusTimer = setTimeout(loadStatus, 3000);
    else if (wasRunning) { load(); loadPlan(); } // a scan just finished
  } catch { /* best effort */ }
}

const plan = ref(null);
async function loadPlan() { try { plan.value = await api("/api/library/issues/scan-plan"); } catch { /* optional */ } }
const planText = computed(() => {
  const p = plan.value;
  if (!p || status.value?.running) return "";
  if (!p.to_read) return p.full_compare ? t("Next scan: nothing new to read (will re-compare stored fingerprints) — quick.") : t("Next scan: nothing new to read — quick.");
  const why = [p.new && t("{n} new", { n: p.new }), p.changed && t("{n} changed", { n: p.changed }),
    p.settings && t("{n} after a settings change", { n: p.settings }), p.audio_retry && t("{n} audio retries", { n: p.audio_retry })].filter(Boolean).join(", ");
  return t("Next scan reads {n} of {total} videos ({why}); the others reuse stored fingerprints.", { n: p.to_read, total: p.total, why });
});
const lastText = computed(() => {
  const r = status.value?.last_result;
  if (!r || status.value?.running) return "";
  const mins = r.elapsed_seconds >= 90 ? t("{n} min", { n: Math.round(r.elapsed_seconds / 60) }) : t("{n} s", { n: Math.round(r.elapsed_seconds) });
  return t("Last scan read {n} of {total} videos in {time}.", { n: r.read, total: r.of, time: mins });
});

const statusText = computed(() => {
  const s = status.value;
  if (!s) return "";
  if (s.running && s.phase === "hashing") return t("Scanning: reading video {n} of {total}…", { n: s.hashed, total: s.hash_total });
  if (s.running && s.phase === "comparing") return t("Scanning: comparing video {n} of {total}…", { n: s.compared, total: s.compare_total });
  if (s.running) return t("Scan running…");
  const parts = [s.last_run_at ? t("Last scan {time}", { time: new Date(s.last_run_at * 1000).toLocaleString(locale()) }) : t("No scan has run yet")];
  if (s.pending_backfill) parts.push(t("{n} of {total} videos not scanned yet", { n: s.pending_backfill, total: s.total_done_items }));
  if (s.unusable) parts.push(t("{n} couldn't be read", { n: s.unusable }));
  return parts.join(" · ");
});

async function scanNow() {
  try {
    await api("/api/library/issues/scan", { method: "POST" });
    toast(t("Duplicate scan started"));
    setTimeout(loadStatus, 500);
  } catch (err) { toast(err.message || t("Could not start the scan"), { type: "error" }); }
}
// "Auto: after downloads, re-encodes" -- set in Server Settings -> Duplicate scan.
const autoText = computed(() => {
  const trig = status.value?.auto_triggers;
  if (!trig) return "";
  const on = [trig.download && t("downloads"), trig.reencode && t("re-encodes"), trig.repair && t("repairs")].filter(Boolean);
  return on.length ? t("Auto: after {events}", { events: on.join(", ") }) : t("Auto: off");
});

// ---- per-group facts and "best" markers ----
function facts(it) {
  const q = qualityInfo(it.tech_resolution);
  return {
    height: q ? Number(String(it.tech_resolution).split("x")[1]) || 0 : 0,
    quality: q ? `${q.label} · ${it.tech_resolution}` : it.tech_resolution || "—",
    codec: codecLabel(it.tech_codec) || "—",
    size: it.tech_filesize || "—",
    bytes: parseSizeBytes(it.tech_filesize) || 0,
    duration: it.tech_duration || "—",
    seconds: parseDurationSeconds(it.tech_duration) || 0,
    bitrate: bitrateKbps(it.tech_filesize, it.tech_duration),
  };
}
function decorate(group) {
  const rows = group.items.map((it, i) => ({ it, n: i + 1, f: facts(it) }));
  // A copy is only marked "best"/"smallest" when it clearly stands out; for
  // identical copies there's nothing to pick between, so no marks.
  const sameBits = (a, b) => !a || !b || Math.abs(a - b) / Math.max(a, b) < 0.03;
  const byQuality = [...rows].sort((a, b) => (b.f.height - a.f.height) || ((b.f.bitrate || 0) - (a.f.bitrate || 0)));
  const [top, second] = byQuality;
  const bestStandsOut = second && (top.f.height > second.f.height || !sameBits(top.f.bitrate, second.f.bitrate));
  const bySize = [...rows].filter((r) => r.f.bytes).sort((a, b) => a.f.bytes - b.f.bytes);
  const smallStandsOut = bySize.length > 1 && bySize[1].f.bytes - bySize[0].f.bytes > bySize[1].f.bytes * 0.01;
  const longest = Math.max(...rows.map((r) => r.f.seconds));
  for (const r of rows) {
    r.tags = [];
    if (bestStandsOut && r === top) r.tags.push(t("Best quality"));
    if (smallStandsOut && r === bySize[0]) r.tags.push(t("Smallest file"));
    if (longest - r.f.seconds > 3) r.tags.push(t("Shorter"));
  }
  const identical = rows.length > 1 && rows.every((r) => r.f.quality === rows[0].f.quality && r.f.size === rows[0].f.size
    && r.f.codec === rows[0].f.codec && r.f.duration === rows[0].f.duration);
  const num = Object.fromEntries(rows.map((r) => [r.it.id, r.n]));
  const pairs = [...group.pairs].sort((a, b) => b.match_percentage - a.match_percentage)
    .map((p) => ({ ...p, a: num[p.item_id_a], b: num[p.item_id_b] }));
  return { ...group, rows, pairs, identical, top: pairs[0]?.match_percentage };
}
const shown = computed(() => (groups.value || []).map(decorate));

// ---- actions ----
const confirmKeep = ref(null); // { group, keep }
const confirmRemove = ref(null); // item
const comparing = ref(null);

async function doKeep() {
  const { group, keep } = confirmKeep.value;
  const others = group.items.filter((i) => i.id !== keep.id).map((i) => i.id);
  busy.value = true;
  others.forEach(releaseMedia);
  try {
    const r = await api("/api/library/issues/merge", { method: "POST", body: { keep_id: keep.id, merge_ids: others } });
    if (r.undeleted_files?.length) {
      toast(t("Kept 1, removed {n} — but {files} file(s) were in use and stayed on disk.", { n: r.merged_count, files: r.undeleted_files.length })
        + " " + t("Maintenance → Untracked files can delete them later."), { type: "error", timeout: 12000 });
    } else {
      toast(t("Kept 1, removed {n}", { n: r.merged_count }), { type: "success" });
    }
    confirmKeep.value = null;
    load();
  } catch (err) { toast(err.message || t("Could not merge"), { type: "error" }); }
  finally { busy.value = false; }
}
async function doRemove() {
  const it = confirmRemove.value;
  busy.value = true;
  releaseMedia(it.id);
  try {
    await api(`/api/library/${encodeURIComponent(it.id)}?delete_files=true`, { method: "DELETE" });
    toast(t("Removed"), { type: "success" });
    confirmRemove.value = null;
    load();
  } catch (err) { toast(err.message || t("Could not remove"), { type: "error" }); }
  finally { busy.value = false; }
}
async function ignore(group) {
  try {
    await api("/api/library/issues/ignore", { method: "POST", body: { issue_type: "duplicate", item_ids: group.items.map((i) => i.id) } });
    toast(t("Marked as not duplicates"));
    load();
  } catch (err) { toast(err.message || t("Could not save"), { type: "error" }); }
}

let reloadTimer = null;
const stopLive = onLibraryChange((changed) => {
  const ids = new Set((groups.value || []).flatMap((g) => g.items.map((i) => i.id)));
  if (!changed || changed.some((id) => ids.has(id))) { clearTimeout(reloadTimer); reloadTimer = setTimeout(load, 600); }
});

onMounted(() => { load(); loadStatus(); loadPlan(); });
onBeforeUnmount(() => { clearTimeout(statusTimer); clearTimeout(reloadTimer); stopLive(); });
watch(minMatch, load);
</script>

<template>
  <div class="dups">
    <header class="head">
      <div>
        <h1>{{ $t("Duplicates") }}</h1>
        <p class="muted small">{{ $t("Videos the duplicate scan found to be the same clip. Pick the copy to keep, compare them playing, or mark a group as not duplicates.") }}</p>
      </div>
    </header>

    <!-- 3.12.0: what was merged, and what was marked "not duplicates" -->
    <div class="seg views" role="tablist">
      <button v-for="v in VIEWS" :key="v.key" role="tab" class="btn btn-sm" :class="{ 'btn-primary': view === v.key }" :aria-selected="view === v.key" @click="openView(v.key)">{{ v.label }}</button>
    </div>
    <DuplicateHistory v-if="view !== 'found'" :view="view" />

    <template v-if="view === 'found'">
    <section class="card bar">
      <div class="scan">
        <div class="status-lines">
          <span class="status" :class="{ running: status?.running }">{{ statusText }}</span>
          <span v-if="lastText || planText" class="muted small">{{ lastText }} {{ planText }}</span>
        </div>
        <div class="bar-actions">
          <RouterLink v-if="canManage && autoText" class="btn btn-ghost btn-sm" :to="{ path: '/admin/server', query: { tab: 'duplicates' } }"
                      :title="$t('When scans start on their own (Server Settings → Duplicate scan; the daily scan is on the Scheduler page)')">{{ autoText }}</RouterLink>
          <button v-if="canManage" class="btn btn-sm" :disabled="status?.running" @click="scanNow"><Icon name="search" />{{ $t("Scan now") }}</button>
        </div>
      </div>
      <label class="threshold">
        <span>{{ $t("Minimum match") }}</span>
        <input type="range" min="50" max="100" step="1" :value="minDraft ?? minMatch" @input="minDraft = Number($event.target.value)" @change="commitMin">
        <output>{{ minDraft ?? minMatch }}%</output>
      </label>
    </section>

    <div v-if="error" class="empty"><div class="empty-icon">⚠️</div>{{ error }}</div>
    <div v-else-if="!groups" class="skeleton group-skel" />
    <div v-else-if="!shown.length" class="empty"><div class="empty-icon">✅</div>{{ $t("No duplicates at {percent}% or higher.", { percent: minMatch }) }}</div>

    <template v-else>
      <p class="muted small count">{{ $tn("{n} group", "{n} groups", shown.length) }}</p>
      <section v-for="g in shown" :key="g.items.map((i) => i.id).join()" class="card group">
        <header class="group-head">
          <strong>{{ $tn("{n} video", "{n} videos", g.items.length) }}</strong>
          <span class="match">{{ $t("{percent}% match", { percent: Math.round(g.top) }) }}</span>
          <span class="pairs muted small">
            <span v-for="p in g.pairs" :key="p.item_id_a + p.item_id_b" class="pair" :title="p.audio_score != null ? $t('Audio similarity {n}%', { n: Math.round(p.audio_score * 100) }) : ''">
              #{{ p.a }}↔#{{ p.b }} {{ Math.round(p.match_percentage) }}%
            </span>
          </span>
          <span v-if="g.identical" class="same-specs" :title="$t('Same quality, codec, size and duration')">{{ $t("Identical files") }}</span>
          <span class="spacer" />
          <button class="btn btn-sm btn-compare" @click="comparing = g.items"><Icon name="play" />{{ $t("Compare") }}</button>
          <button v-if="canManage" class="btn btn-sm btn-notdup" :title="$t('These are different videos: stop showing this group')" @click="ignore(g)">{{ $t("Not duplicates") }}</button>
        </header>
        <div class="cols" :style="{ gridTemplateColumns: `repeat(${Math.min(g.rows.length, 4)}, minmax(0, 28rem))` }">
          <article v-for="r in g.rows" :key="r.it.id" class="col">
            <div class="col-top">
              <span class="num">#{{ r.n }}</span>
              <span v-for="t in r.tags" :key="t" class="tag" :class="{ good: t === 'Best quality', warn: t === 'Shorter' }">{{ t }}</span>
            </div>
            <router-link :to="{ name: 'video', params: { id: r.it.id } }" class="thumb-link"><VideoThumb :item="r.it" /></router-link>
            <router-link :to="{ name: 'video', params: { id: r.it.id } }" class="title">{{ itemUrlTitle(r.it) }}</router-link>
            <div class="creator text-creator">{{ r.it.creator }}</div>
            <dl class="facts">
              <dt>{{ $t("Quality") }}</dt><dd>{{ r.f.quality }}</dd>
              <dt>{{ $t("Codec") }}</dt><dd>{{ r.f.codec }}</dd>
              <dt>{{ $t("Size") }}</dt><dd>{{ r.f.size }}</dd>
              <dt>{{ $t("Bitrate") }}</dt><dd>{{ r.f.bitrate ? $t("{bitrate} kbit/s", { bitrate: r.f.bitrate.toLocaleString(locale()) }) : "—" }}</dd>
              <dt>{{ $t("Duration") }}</dt><dd>{{ r.f.duration }}</dd>
              <dt>{{ $t("Rating") }}</dt><dd>{{ r.it.ratings || "—" }}</dd>
              <dt>{{ $t("Uploaded") }}</dt><dd>{{ r.it.upload_date ? formatDate(r.it.upload_date) : "—" }}</dd>
              <dt>{{ $t("Downloaded") }}</dt><dd>{{ r.it.download_datetime ? formatDate(r.it.download_datetime) : "—" }}</dd>
              <dt>{{ $t("On the site") }}</dt><dd>{{ r.it.site_status === "removed" ? $t("💀 Gone") : r.it.site_status === "available" ? $t("Yes") : "—" }}</dd>
            </dl>
            <div class="col-actions">
              <button v-if="canManage" class="btn btn-primary btn-sm" @click="confirmKeep = { group: g, keep: r.it }">{{ $t("Keep this one") }}</button>
              <a v-if="r.it.url && r.it.site !== 'local'" class="btn btn-sm" :href="r.it.url" target="_blank" rel="noopener noreferrer" :title="$t('Open this video\'s page on the site')">
                <Icon name="external" />{{ $t("View on {site}", { site: siteLabel(r.it) }) }}
              </a>
              <button v-if="canManage" class="btn btn-ghost btn-sm btn-danger" @click="confirmRemove = r.it">{{ $t("Remove") }}</button>
            </div>
          </article>
        </div>
      </section>
    </template>

    <ModalDialog v-if="confirmKeep" :title="$t('Keep this copy?')" @close="confirmKeep = null">
      <Tx tag="p" msg="Keep {video} and remove the other {n}:" :params="{ n: confirmKeep.group.items.length - 1 }"><template #video><strong>{{ itemUrlTitle(confirmKeep.keep) }}</strong></template></Tx>
      <ul class="rm-list">
        <li v-for="o in confirmKeep.group.items.filter((i) => i.id !== confirmKeep.keep.id)" :key="o.id">
          {{ itemUrlTitle(o) }} <span class="muted">· {{ o.tech_resolution }} · {{ o.tech_filesize }}</span>
        </li>
      </ul>
      <p class="muted small">{{ $t("Their files are deleted from disk. Each stays linked to the video you keep, so it won't be downloaded again and the userscript reports it as a duplicate — until you remove the kept video, which removes these links too.") }}</p>
      <template #footer>
        <button class="btn" @click="confirmKeep = null">{{ $t("Cancel") }}</button>
        <button class="btn btn-primary" :disabled="busy" @click="doKeep">{{ busy ? $t("Working…") : $t("Keep and remove the others") }}</button>
      </template>
    </ModalDialog>

    </template>

    <ModalDialog v-if="confirmRemove" :title="$t('Remove this video?')" @close="confirmRemove = null">
      <p><Tx msg="Remove {b1} from the library and delete its files?"><template #b1><strong>{{ itemUrlTitle(confirmRemove) }}</strong></template></Tx></p>
      <template #footer>
        <button class="btn" @click="confirmRemove = null">{{ $t("Cancel") }}</button>
        <button class="btn btn-danger" :disabled="busy" @click="doRemove">{{ busy ? $t("Removing…") : $t("Remove") }}</button>
      </template>
    </ModalDialog>

    <CompareDialog v-if="comparing" :items="comparing" @close="comparing = null" />
  </div>
</template>

<style scoped>
.dups { padding: 1rem 1.5rem 2rem; display: flex; flex-direction: column; gap: 0.875rem; }
.head h1 { font-size: 1.4rem; }
.views { display: flex; gap: 0.375rem; flex-wrap: wrap; }
.head p { margin: 0.25rem 0 0; }
.small { font-size: 0.8rem; }
.bar { padding: 0.75rem 1rem; display: flex; flex-wrap: wrap; gap: 0.75rem 2rem; align-items: center; justify-content: space-between; }
.scan { display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; align-items: center; flex: 1 1 30rem; }
.status-lines { display: flex; flex-direction: column; gap: 0.15rem; }
.status { font-size: 0.84rem; color: var(--text-2); }
.btn-compare { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); }
.btn-compare:hover { background: var(--accent); color: #fff; }
.btn-notdup { border-color: color-mix(in srgb, var(--warning) 60%, transparent); color: var(--warning); background: color-mix(in srgb, var(--warning) 12%, transparent); }
.btn-notdup:hover { background: color-mix(in srgb, var(--warning) 22%, transparent); }
.same-specs { font-size: 0.72rem; font-weight: 700; padding: 0.1rem 0.45rem; border-radius: 0.25rem; background: var(--panel-hover); color: var(--text-2); }
.status.running { color: var(--accent); font-weight: 600; }
.bar-actions { display: flex; gap: 0.75rem; align-items: center; margin-inline-start: auto; }
.threshold { display: flex; align-items: center; gap: 0.625rem; font-size: 0.84rem; }
.threshold input { width: 12rem; accent-color: var(--accent); }
.threshold output { min-width: 2.75rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.count { margin: 0; }
.group { padding: 0.875rem 1rem 1rem; display: flex; flex-direction: column; gap: 0.875rem; }
.group-head { display: flex; align-items: center; gap: 0.625rem; flex-wrap: wrap; }
.match { font-weight: 700; color: var(--accent); }
.pairs { display: flex; flex-wrap: wrap; gap: 0.25rem 0.625rem; }
.pair { font-variant-numeric: tabular-nums; }
.spacer { flex: 1; }
.cols { display: grid; gap: 1rem; }
.col { display: flex; flex-direction: column; gap: 0.375rem; min-width: 0; padding: 0.625rem; border: 1px solid var(--border); border-radius: var(--radius); background: var(--panel-2); }
.col-top { display: flex; align-items: center; gap: 0.375rem; flex-wrap: wrap; min-height: 1.5rem; }
.num { font-weight: 800; color: var(--accent); margin-inline-end: 0.25rem; }
.tag { font-size: 0.68rem; font-weight: 700; padding: 0.1rem 0.4rem; border-radius: 0.25rem; background: var(--panel-hover); color: var(--text-2); }
.tag.good { background: var(--success-soft); color: var(--success); }
.tag.warn { background: var(--danger-soft); color: var(--danger); }
.thumb-link { display: block; }
.title { color: var(--text); font-weight: 650; font-size: 0.875rem; line-height: 1.3; overflow-wrap: anywhere; }
.title:hover { color: var(--accent); text-decoration: none; }
.creator { font-size: 0.8rem; color: var(--text-2); }
.facts { display: grid; grid-template-columns: auto 1fr; gap: 0.2rem 0.75rem; margin: 0.25rem 0; font-size: 0.78rem; }
.facts dt { color: var(--text-dim); }
.facts dd { margin: 0; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.col-actions { display: flex; gap: 0.375rem; margin-top: auto; flex-wrap: wrap; }
.group-skel { height: 20rem; border-radius: var(--radius-lg); }
.rm-list { margin: 0.5rem 0; padding-inline-start: 1.25rem; font-size: 0.875rem; }
@media (max-width: 56rem) {
  .dups { padding: 0.75rem 1rem 1.5rem; }
  .cols { grid-template-columns: 1fr !important; }
}
</style>
