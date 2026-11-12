<script setup>
// Admin -> Duplicates: groups of videos the duplicate scan matched, shown
// side by side with what tells them apart (quality, size, bitrate, ...), so
// you can keep the best copy, remove one, compare them playing, or mark the
// group as "not duplicates".
import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { useRoute, useRouter } from "vue-router";
import Icon from "../../components/Icon.vue";
import VideoThumb from "../../components/VideoThumb.vue";
import ModalDialog from "../../components/ModalDialog.vue";
import CompareDialog from "../../components/CompareDialog.vue";
import { api } from "../../lib/api.js";
import { releaseMedia } from "../../lib/media.js";
import { itemUrlTitle, codecLabel, qualityInfo, parseSizeBytes, bitrateKbps, parseDurationSeconds, formatDate, siteLabel } from "../../lib/format.js";
import { hasPermission } from "../../stores/session.js";
import { onLibraryChange } from "../../stores/live.js";
import { toast } from "../../stores/toast.js";

const route = useRoute();
const router = useRouter();
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
    error.value = err.message || "Could not load duplicates";
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
  if (!p.to_read) return `Next scan: nothing new to read${p.full_compare ? " (will re-compare stored fingerprints)" : ""} — quick.`;
  const why = [p.new && `${p.new} new`, p.changed && `${p.changed} changed`, p.settings && `${p.settings} after a settings change`,
    p.audio_retry && `${p.audio_retry} audio retries`].filter(Boolean).join(", ");
  return `Next scan reads ${p.to_read} of ${p.total} videos (${why}); the others reuse stored fingerprints.`;
});
const lastText = computed(() => {
  const r = status.value?.last_result;
  if (!r || status.value?.running) return "";
  const mins = r.elapsed_seconds >= 90 ? `${Math.round(r.elapsed_seconds / 60)} min` : `${Math.round(r.elapsed_seconds)} s`;
  return `Last scan read ${r.read} of ${r.of} videos in ${mins}.`;
});

const statusText = computed(() => {
  const s = status.value;
  if (!s) return "";
  if (s.running && s.phase === "hashing") return `Scanning: reading video ${s.hashed} of ${s.hash_total}…`;
  if (s.running && s.phase === "comparing") return `Scanning: comparing video ${s.compared} of ${s.compare_total}…`;
  if (s.running) return "Scan running…";
  const parts = [s.last_run_at ? `Last scan ${new Date(s.last_run_at * 1000).toLocaleString()}` : "No scan has run yet"];
  if (s.pending_backfill) parts.push(`${s.pending_backfill} of ${s.total_done_items} videos not scanned yet`);
  if (s.unusable) parts.push(`${s.unusable} couldn't be read`);
  return parts.join(" · ");
});

async function scanNow() {
  try {
    await api("/api/library/issues/scan", { method: "POST" });
    toast("Duplicate scan started");
    setTimeout(loadStatus, 500);
  } catch (err) { toast(err.message || "Could not start the scan", { type: "error" }); }
}
// "Auto: after downloads, re-encodes" -- set in Server settings -> Duplicate scan.
const autoText = computed(() => {
  const t = status.value?.auto_triggers;
  if (!t) return "";
  const on = [t.download && "downloads", t.reencode && "re-encodes", t.repair && "repairs"].filter(Boolean);
  return on.length ? `Auto: after ${on.join(", ")}` : "Auto: off";
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
    if (bestStandsOut && r === top) r.tags.push("Best quality");
    if (smallStandsOut && r === bySize[0]) r.tags.push("Smallest file");
    if (longest - r.f.seconds > 3) r.tags.push("Shorter");
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
      toast(`Kept 1, removed ${r.merged_count} — but ${r.undeleted_files.length} file(s) were in use and stayed on disk. `
        + "Maintenance → Untracked files can delete them later.", { type: "error", timeout: 12000 });
    } else {
      toast(`Kept 1, removed ${r.merged_count}`, { type: "success" });
    }
    confirmKeep.value = null;
    load();
  } catch (err) { toast(err.message || "Could not merge", { type: "error" }); }
  finally { busy.value = false; }
}
async function doRemove() {
  const it = confirmRemove.value;
  busy.value = true;
  releaseMedia(it.id);
  try {
    await api(`/api/library/${encodeURIComponent(it.id)}?delete_files=true`, { method: "DELETE" });
    toast("Removed", { type: "success" });
    confirmRemove.value = null;
    load();
  } catch (err) { toast(err.message || "Could not remove", { type: "error" }); }
  finally { busy.value = false; }
}
async function ignore(group) {
  try {
    await api("/api/library/issues/ignore", { method: "POST", body: { issue_type: "duplicate", item_ids: group.items.map((i) => i.id) } });
    toast("Marked as not duplicates");
    load();
  } catch (err) { toast(err.message || "Could not save", { type: "error" }); }
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
        <h1>Duplicates</h1>
        <p class="muted small">Videos the duplicate scan found to be the same clip. Pick the copy to keep, compare them playing, or mark a group as not duplicates.</p>
      </div>
    </header>

    <section class="card bar">
      <div class="scan">
        <div class="status-lines">
          <span class="status" :class="{ running: status?.running }">{{ statusText }}</span>
          <span v-if="lastText || planText" class="muted small">{{ lastText }} {{ planText }}</span>
        </div>
        <div class="bar-actions">
          <RouterLink v-if="canManage && autoText" class="btn btn-ghost btn-sm" :to="{ path: '/admin/server', query: { tab: 'duplicates' } }"
                      title="When scans start on their own (Server settings → Duplicate scan; the daily scan is on the Scheduler page)">{{ autoText }}</RouterLink>
          <button v-if="canManage" class="btn btn-sm" :disabled="status?.running" @click="scanNow"><Icon name="search" />Scan now</button>
        </div>
      </div>
      <label class="threshold">
        <span>Minimum match</span>
        <input type="range" min="50" max="100" step="1" :value="minDraft ?? minMatch" @input="minDraft = Number($event.target.value)" @change="commitMin">
        <output>{{ minDraft ?? minMatch }}%</output>
      </label>
    </section>

    <div v-if="error" class="empty"><div class="empty-icon">⚠️</div>{{ error }}</div>
    <div v-else-if="!groups" class="skeleton group-skel" />
    <div v-else-if="!shown.length" class="empty"><div class="empty-icon">✅</div>No duplicates at {{ minMatch }}% or higher.</div>

    <template v-else>
      <p class="muted small count">{{ shown.length }} {{ shown.length === 1 ? "group" : "groups" }}</p>
      <section v-for="g in shown" :key="g.items.map((i) => i.id).join()" class="card group">
        <header class="group-head">
          <strong>{{ g.items.length }} videos</strong>
          <span class="match">{{ Math.round(g.top) }}% match</span>
          <span class="pairs muted small">
            <span v-for="p in g.pairs" :key="p.item_id_a + p.item_id_b" class="pair" :title="p.audio_score != null ? `Audio similarity ${Math.round(p.audio_score * 100)}%` : ''">
              #{{ p.a }}↔#{{ p.b }} {{ Math.round(p.match_percentage) }}%
            </span>
          </span>
          <span v-if="g.identical" class="same-specs" title="Same quality, codec, size and duration">Identical files</span>
          <span class="spacer" />
          <button class="btn btn-sm btn-compare" @click="comparing = g.items"><Icon name="play" />Compare</button>
          <button v-if="canManage" class="btn btn-sm btn-notdup" title="These are different videos: stop showing this group" @click="ignore(g)">Not duplicates</button>
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
              <dt>Quality</dt><dd>{{ r.f.quality }}</dd>
              <dt>Codec</dt><dd>{{ r.f.codec }}</dd>
              <dt>Size</dt><dd>{{ r.f.size }}</dd>
              <dt>Bitrate</dt><dd>{{ r.f.bitrate ? `${r.f.bitrate.toLocaleString()} kbit/s` : "—" }}</dd>
              <dt>Duration</dt><dd>{{ r.f.duration }}</dd>
              <dt>Rating</dt><dd>{{ r.it.ratings || "—" }}</dd>
              <dt>Uploaded</dt><dd>{{ r.it.upload_date ? formatDate(r.it.upload_date) : "—" }}</dd>
              <dt>Downloaded</dt><dd>{{ r.it.download_datetime ? formatDate(r.it.download_datetime) : "—" }}</dd>
              <dt>On the site</dt><dd>{{ r.it.site_status === "removed" ? "💀 Gone" : r.it.site_status === "available" ? "Yes" : "—" }}</dd>
            </dl>
            <div class="col-actions">
              <button v-if="canManage" class="btn btn-primary btn-sm" @click="confirmKeep = { group: g, keep: r.it }">Keep this one</button>
              <a v-if="r.it.url && r.it.site !== 'local'" class="btn btn-sm" :href="r.it.url" target="_blank" rel="noopener noreferrer" title="Open this video's page on the site">
                <Icon name="external" />View on {{ siteLabel(r.it) }}
              </a>
              <button v-if="canManage" class="btn btn-ghost btn-sm btn-danger" @click="confirmRemove = r.it">Remove</button>
            </div>
          </article>
        </div>
      </section>
    </template>

    <ModalDialog v-if="confirmKeep" title="Keep this copy?" @close="confirmKeep = null">
      <p>Keep <strong>{{ itemUrlTitle(confirmKeep.keep) }}</strong> and remove the other {{ confirmKeep.group.items.length - 1 }}:</p>
      <ul class="rm-list">
        <li v-for="o in confirmKeep.group.items.filter((i) => i.id !== confirmKeep.keep.id)" :key="o.id">
          {{ itemUrlTitle(o) }} <span class="muted">· {{ o.tech_resolution }} · {{ o.tech_filesize }}</span>
        </li>
      </ul>
      <p class="muted small">Their files are deleted from disk. Each stays linked to the video you keep, so it won't be downloaded
        again and the userscript reports it as a duplicate — until you remove the kept video, which removes these links too.</p>
      <template #footer>
        <button class="btn" @click="confirmKeep = null">Cancel</button>
        <button class="btn btn-primary" :disabled="busy" @click="doKeep">{{ busy ? "Working…" : "Keep and remove the others" }}</button>
      </template>
    </ModalDialog>

    <ModalDialog v-if="confirmRemove" title="Remove this video?" @close="confirmRemove = null">
      <p>Remove <strong>{{ itemUrlTitle(confirmRemove) }}</strong> from the library and delete its files?</p>
      <template #footer>
        <button class="btn" @click="confirmRemove = null">Cancel</button>
        <button class="btn btn-danger" :disabled="busy" @click="doRemove">{{ busy ? "Removing…" : "Remove" }}</button>
      </template>
    </ModalDialog>

    <CompareDialog v-if="comparing" :items="comparing" @close="comparing = null" />
  </div>
</template>

<style scoped>
.dups { padding: 1rem 1.5rem 2rem; display: flex; flex-direction: column; gap: 0.875rem; }
.head h1 { font-size: 1.4rem; }
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
.bar-actions { display: flex; gap: 0.75rem; align-items: center; margin-left: auto; }
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
.num { font-weight: 800; color: var(--accent); margin-right: 0.25rem; }
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
.rm-list { margin: 0.5rem 0; padding-left: 1.25rem; font-size: 0.875rem; }
@media (max-width: 56rem) {
  .dups { padding: 0.75rem 1rem 1.5rem; }
  .cols { grid-template-columns: 1fr !important; }
}
</style>
