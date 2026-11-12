<script setup>
// The library: search, filters, sorting and paging all live in the URL
// (?q=…&tag=…&page=2), so back/forward, refresh and bookmarks keep the view.
// The server does the filtering/paging; live events refresh the page only
// when something on it (or the number of videos) changed.
import { ref, computed, watch, onMounted, onActivated, onDeactivated, onBeforeUnmount } from "vue";
import { useRoute, useRouter } from "vue-router";
import Icon from "../components/Icon.vue";
import VideoCard from "../components/VideoCard.vue";
import VideoRow from "../components/VideoRow.vue";
import VideoTable from "../components/VideoTable.vue";
import FilterPanel, { qualityLabel, ORIENTATIONS } from "../components/FilterPanel.vue";
import PageNav from "../components/PageNav.vue";
import SearchHelp from "../components/SearchHelp.vue";
import { api, qs } from "../lib/api.js";
import { session, savePref, PREF_OPTIONS, isAdmin } from "../stores/session.js";
import { live, onLibraryChange } from "../stores/live.js";
import { openDialog } from "../stores/dialogs.js";
import { toApiParams } from "../lib/libraryQuery.js";
import BulkEditDialog from "../components/dialogs/BulkEditDialog.vue";
import { featureOn } from "../lib/actions.js";
import BulkActionDialog from "../components/dialogs/BulkActionDialog.vue";
import { extensionActions } from "../stores/extensions.js";
import { hasPermission } from "../stores/session.js";

defineOptions({ name: "LibraryView" });

const route = useRoute();
const router = useRouter();

const SORTS = [
  { key: "download_datetime", label: "Date acquired" },
  { key: "upload_date", label: "Uploaded to PMVHaven" },
  { key: "title", label: "Title" },
  { key: "creator", label: "Creator" },
  { key: "ratings", label: "Rating" },
  { key: "views", label: "Views on PMVHaven" },
  { key: "tech_duration", label: "Duration" },
  { key: "tech_filesize", label: "File size" },
  { key: "tech_resolution", label: "Resolution" },
];

const arr = (v) => (v == null ? [] : Array.isArray(v) ? v : [v]);
const num = (v) => (v === undefined || v === null || v === "" || !Number.isFinite(Number(v)) ? null : Number(v));

// Only follow the URL while this page is the active route (it is kept alive
// in the background while a video is open).
const query = ref({ ...route.query });
watch(() => route.fullPath, () => { if (route.name === "library") query.value = { ...route.query }; });

const state = computed(() => {
  const q = query.value;
  return {
    q: q.q || "",
    tags: arr(q.tag), performers: arr(q.performer), creators: arr(q.creator), codecs: arr(q.codec),
    // gone: "1" only videos removed from PMVHaven 💀, "0" only ones still there.
    all: q.match === "all", gone: q.gone === "1" ? true : q.gone === "0" ? false : null,
    dmin: num(q.dmin), dmax: num(q.dmax), rating: num(q.rating),
    // quality: exact quality buckets (any of them); qmin: that quality or better.
    quality: arr(q.quality), qmin: q.qmin || null,
    orient: ["landscape", "portrait", "square"].includes(q.orient) ? q.orient : null,
    // Performer details (2.19.0): cup sizes, hair, eyes, age range, enhanced ("1"/"0").
    pcup: arr(q.pcup), phair: arr(q.phair), peyes: arr(q.peyes), pamin: num(q.pamin), pamax: num(q.pamax),
    penh: q.penh === "1" ? true : q.penh === "0" ? false : null,
    // Without an explicit sort in the URL the user's default (Settings) applies.
    // "created_at" (Date added) was merged into Date acquired in 2.4.0; old links still work.
    sort: (q.sort === "created_at" ? "download_datetime" : q.sort) || session.prefs.defaultSort,
    dir: q.dir === "asc" || q.dir === "desc" ? q.dir : q.sort ? "desc" : session.prefs.defaultDir,
    page: Math.max(1, num(q.page) || 1),
  };
});
const activeFilterCount = computed(() => {
  const s = state.value;
  return s.tags.length + s.performers.length + s.creators.length + s.codecs.length
    + (s.dmin != null) + (s.dmax != null) + (s.rating != null) + (s.gone !== null)
    + s.quality.length + (s.qmin !== null) + (s.orient !== null)
    + s.pcup.length + s.phair.length + s.peyes.length + (s.pamin != null || s.pamax != null) + (s.penh !== null);
});

function setQuery(patch, { keepPage = false, push = false } = {}) {
  const next = { ...query.value, ...patch };
  if (!keepPage) delete next.page;
  for (const [k, v] of Object.entries(next)) {
    if (v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length)) delete next[k];
  }
  router[push ? "push" : "replace"]({ name: "library", query: next });
}
function addFilter(kind, value) {
  const key = { creator: "creator", performer: "performer", tag: "tag" }[kind];
  const current = arr(query.value[key]);
  if (!current.includes(value)) setQuery({ [key]: [...current, value] }, { push: true });
}
function clearFilters() {
  const { q, sort, dir } = query.value;
  router.replace({ name: "library", query: { ...(q ? { q } : {}), ...(sort ? { sort } : {}), ...(dir ? { dir } : {}) } });
}

// ---------- search box (debounced into the URL) ----------
const searchText = ref(state.value.q);
const searchInput = ref(null);
let searchTimer = null;
watch(searchText, (v) => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { if (v !== state.value.q) setQuery({ q: v }); }, 300); });
watch(() => state.value.q, (v) => { if (v !== searchText.value) searchText.value = v; });

// ---------- data ----------
const data = ref(null);
const loading = ref(false);
const error = ref("");
let controller = null;
let doneCountAtFetch = null;
let active = true;
let stale = false;

// The dot next to the count is for loads you caused (filters, sort, page), and
// only when they take a moment -- background refreshes after live changes are
// silent (2.16.1: a job touching a listed video every second made it blink).
let dotTimer = null;
async function fetchPage({ background = false } = {}) {
  controller?.abort();
  const mine = (controller = new AbortController());
  const s = state.value;
  clearTimeout(dotTimer);
  if (!background) dotTimer = setTimeout(() => { if (controller === mine) loading.value = true; }, 250);
  try {
    const res = await api(`/api/library?${qs({
      statuses: "done", page: s.page, page_size: session.prefs.pageSize, ...toApiParams(s),
    })}`, { signal: mine.signal });
    data.value = res;
    error.value = "";
    doneCountAtFetch = live.summary?.done_count ?? null;
    stale = false;
  } catch (err) {
    if (err.name !== "AbortError") error.value = err.message || "Could not load the library";
  } finally {
    if (controller === mine) { clearTimeout(dotTimer); loading.value = false; }
  }
}

// While hidden (a video is open) just remember to reload; onActivated does it.
watch([state, () => session.prefs.pageSize, () => session.prefs.defaultSort, () => session.prefs.defaultDir], () => { if (active) fetchPage(); else stale = true; }, { deep: true });

// Live changes: one quiet refresh at most every few seconds, however often
// the listed videos change (a running job can touch one every second).
const REFRESH_EVERY = 3000;
let refreshTimer = null;
let lastRefresh = 0;
function scheduleRefresh(reason) {
  if (refreshTimer) return; // one is already coming
  const wait = Math.max(400, lastRefresh + REFRESH_EVERY - Date.now());
  refreshTimer = setTimeout(() => {
    refreshTimer = null;
    lastRefresh = Date.now();
    console.debug(`[library] refreshing: ${reason}`);
    fetchPage({ background: true });
  }, wait);
}
const stopLive = onLibraryChange((changed) => {
  if (!active) { stale = true; return; }
  const visible = new Set((data.value?.items || []).map((i) => i.id));
  const countChanged = doneCountAtFetch != null && live.summary && live.summary.done_count !== doneCountAtFetch;
  const hit = changed?.find((id) => visible.has(id));
  if (changed === null) scheduleRefresh("many changes at once");
  else if (countChanged) scheduleRefresh(`videos in the library: ${doneCountAtFetch} → ${live.summary.done_count}`);
  else if (hit) scheduleRefresh(`a listed video changed (${hit})`);
});

const facets = ref(null);
const showFilters = ref(activeFilterCount.value > 0);
let facetsKey = "";
watch(() => live.summary, (s) => {
  if (!s) return;
  // Every live event carries all tags/performers; re-rendering the filter lists
  // only when they changed keeps a big library smooth while jobs run.
  const key = JSON.stringify(s.facets);
  if (key !== facetsKey) { facetsKey = key; facets.value = s.facets; }
}, { immediate: true });

function onKey(e) {
  if (e.key === "/" && !/input|textarea|select/i.test(document.activeElement?.tagName || "")) {
    e.preventDefault();
    searchInput.value?.focus();
  }
}

onMounted(async () => {
  fetchPage();
  if (!facets.value) {
    try { facets.value = (await api("/api/library/summary")).facets; } catch { /* filters fill in when live data arrives */ }
  }
});
onActivated(() => {
  active = true;
  document.addEventListener("keydown", onKey);
  if (stale) fetchPage({ background: true });
});
onDeactivated(() => { active = false; document.removeEventListener("keydown", onKey); });
onBeforeUnmount(() => { stopLive(); controller?.abort(); clearTimeout(refreshTimer); clearTimeout(dotTimer); clearTimeout(searchTimer); document.removeEventListener("keydown", onKey); });

const chips = computed(() => {
  const s = state.value;
  const out = [];
  s.performers.forEach((v) => out.push({ label: v, key: "performer", value: v }));
  s.tags.forEach((v) => out.push({ label: `#${v}`, key: "tag", value: v }));
  s.creators.forEach((v) => out.push({ label: `by ${v}`, key: "creator", value: v }));
  s.codecs.forEach((v) => out.push({ label: v, key: "codec", value: v }));
  if (s.dmin != null || s.dmax != null) out.push({ label: `${s.dmin ?? 0}–${s.dmax ?? "∞"} min`, key: "duration" });
  if (s.rating != null) out.push({ label: `★ ${s.rating}%+`, key: "rating" });
  s.quality.forEach((v) => out.push({ label: qualityLabel(v), key: "quality", value: v }));
  if (s.qmin) out.push({ label: `${qualityLabel(s.qmin)} or better`, key: "qmin" });
  if (s.orient) out.push({ label: ORIENTATIONS.find((o) => o.key === s.orient)?.label || s.orient, key: "orient" });
  if (s.gone !== null) out.push({ label: s.gone ? "💀 gone from PMVHaven" : "Still on PMVHaven", key: "gone" });
  s.pcup.forEach((v) => out.push({ label: `Cup ${v}`, key: "pcup", value: v }));
  if (s.pamin != null || s.pamax != null) out.push({ label: `Age ${s.pamin ?? "…"}–${s.pamax ?? "…"}`, key: "page" });
  s.phair.forEach((v) => out.push({ label: `${v} hair`, key: "phair", value: v }));
  s.peyes.forEach((v) => out.push({ label: `${v} eyes`, key: "peyes", value: v }));
  if (s.penh !== null) out.push({ label: s.penh ? "Enhanced" : "Natural", key: "penh" });
  return out;
});
function removeChip(c) {
  if (c.key === "duration") setQuery({ dmin: "", dmax: "" });
  else if (c.key === "page") setQuery({ pamin: "", pamax: "" });
  else if (c.value !== undefined) setQuery({ [c.key]: arr(query.value[c.key]).filter((v) => v !== c.value) });
  else setQuery({ [c.key]: "" });
}

const total = computed(() => data.value?.total ?? 0);

// ---- Select (bulk edit): click videos to pick them (kept across pages) ----
const canBulk = computed(() => hasPermission("library.view"));  // pick videos -> playlists (everyone), edit (videos.edit)
const canBulkEdit = computed(() => hasPermission("videos.edit"));
const selectMode = ref(false);
const picked = ref(new Set());
const bulkOpen = ref(false);
const bulkAction = ref(null); // "songs" | "repair" | "reencode" (2.18.0)
// Layouts (2.23.0): admins pick grid / list / table; everyone else always gets the grid.
const view = computed(() => (isAdmin() ? session.prefs.view : "grid"));
function togglePick(id) {
  const s = new Set(picked.value);
  s.has(id) ? s.delete(id) : s.add(id);
  picked.value = s;
}
// In select mode a click picks the video instead of opening it.
function onPickClick(e) {
  if (!selectMode.value) return;
  const el = e.target.closest("[data-id]");
  if (!el) return;
  e.preventDefault();
  e.stopPropagation();
  togglePick(el.dataset.id);
}
function pickPage() { picked.value = new Set([...picked.value, ...(data.value?.items || []).map((i) => i.id)]); }
function endSelect() { selectMode.value = false; picked.value = new Set(); }
</script>

<template>
  <div class="library">
    <!-- Toolbar and Filters stay at the top while the page scrolls; an open
         Filters panel sits over the videos until it's closed. -->
    <div class="topbar">
    <div class="toolbar">
      <div class="search">
        <Icon name="search" class="search-icon" />
        <input ref="searchInput" v-model="searchText" class="input search-input" type="search"
               placeholder="Search… (try performer:, tag:, creator:, title:)" aria-label="Search the library">
        <span class="search-end"><SearchHelp @pick="(q) => { searchText = q; searchInput?.focus(); }" /><kbd class="kbd">/</kbd></span>
      </div>
      <button class="btn" :class="{ active: showFilters }" @click="showFilters = !showFilters">
        <Icon name="filter" />Filters<span v-if="activeFilterCount" class="count-pill">{{ activeFilterCount }}</span>
      </button>
      <div class="sort">
        <select class="select" :value="state.sort" aria-label="Sort by" @change="setQuery({ sort: $event.target.value })">
          <option v-for="s in SORTS" :key="s.key" :value="s.key">{{ s.label }}</option>
        </select>
        <button class="btn btn-icon" :title="state.dir === 'asc' ? 'Ascending' : 'Descending'"
                @click="setQuery({ dir: state.dir === 'asc' ? 'desc' : 'asc' })">
          <Icon :name="state.dir === 'asc' ? 'sortAsc' : 'sortDesc'" />
        </button>
      </div>
      <button class="btn" title="Add every video in this view to a playlist, or save it as a smart playlist" :disabled="!total"
              @click="openDialog('playlist', null, { query: toApiParams(state), count: total })"><Icon name="playlist" />Playlist</button>
      <button v-if="canBulk" class="btn" :class="{ active: selectMode }" :title="canBulkEdit ? 'Pick videos to add them to playlists or edit their tags, performers or music together' : 'Pick videos to add them to playlists'"
              @click="selectMode ? endSelect() : (selectMode = true)"><Icon name="check" />{{ selectMode ? "Done" : "Select" }}</button>
      <div v-if="isAdmin()" class="views" role="group" aria-label="Layout">
        <button v-for="v in PREF_OPTIONS.VIEWS" :key="v" class="btn btn-icon" :class="{ active: session.prefs.view === v }"
                :title="`${v[0].toUpperCase()}${v.slice(1)} view`" @click="savePref('view', v)"><Icon :name="v" /></button>
      </div>
    </div>

    <FilterPanel v-if="showFilters" :state="state" :facets="facets" :gone-count="live.summary?.gone_count"
                 :active-count="activeFilterCount" class="filters-pop" @set="setQuery($event)" @clear="clearFilters" />
    </div>

    <div class="summary-row">
      <span class="count">
        <strong>{{ total.toLocaleString() }}</strong> {{ total === 1 ? "video" : "videos" }}
        <span v-if="loading" class="dot-loading" aria-label="Loading" />
      </span>
      <span v-for="c in chips" :key="c.key + (c.value ?? '')" class="chip chip-accent">
        {{ c.label }}<button class="chip-x" :aria-label="`Remove ${c.label}`" @click="removeChip(c)">×</button>
      </span>
      <button v-if="chips.length" class="btn btn-ghost btn-sm" @click="clearFilters">Clear all</button>
    </div>

    <div v-if="error" class="empty"><div class="empty-icon">⚠️</div>{{ error }}<button class="btn" @click="fetchPage">Try again</button></div>

    <div v-else-if="!data" class="grid">
      <div v-for="n in 12" :key="n" class="skel"><div class="skeleton skel-thumb" /><div class="skeleton skel-line" /><div class="skeleton skel-line short" /></div>
    </div>

    <div v-else-if="!data.items.length" class="empty">
      <div class="empty-icon">{{ chips.length || state.q ? "🔍" : "🎞️" }}</div>
      <template v-if="chips.length || state.q">
        <h3>No videos match</h3>
        <button class="btn" @click="clearFilters(); searchText = ''">Clear search and filters</button>
      </template>
      <template v-else><h3>Your library is empty</h3><span>Use <strong>Add videos</strong> in the sidebar to get started.</span></template>
    </div>

    <template v-else>
      <div :class="{ picking: selectMode }" @click.capture="onPickClick">
      <div v-if="view === 'grid'" class="grid">
        <div v-for="item in data.items" :key="item.id" class="pick-wrap" :class="{ picked: picked.has(item.id) }" :data-id="item.id">
          <VideoCard :item="item" @filter="addFilter" />
          <span v-if="selectMode" class="pick-box" aria-hidden="true"><Icon v-if="picked.has(item.id)" name="check" :size="16" /></span>
        </div>
      </div>
      <div v-else-if="view === 'list'" class="rows">
        <div v-for="item in data.items" :key="item.id" class="pick-wrap" :class="{ picked: picked.has(item.id) }" :data-id="item.id">
          <VideoRow :item="item" @filter="addFilter" />
          <span v-if="selectMode" class="pick-box" aria-hidden="true"><Icon v-if="picked.has(item.id)" name="check" :size="16" /></span>
        </div>
      </div>
      <VideoTable v-else :items="data.items" :sort="state.sort" :dir="state.dir" :picked="selectMode ? picked : null"
                  @sort="(k) => setQuery({ sort: k, dir: state.sort === k && state.dir === 'desc' ? 'asc' : 'desc' })"
                  @filter="addFilter" />
      </div>

      <div v-if="selectMode" class="pick-bar card" role="toolbar" aria-label="Selected videos">
        <strong>{{ picked.size }} selected</strong>
        <button class="btn btn-sm" @click="pickPage">Select this page</button>
        <button class="btn btn-ghost btn-sm" :disabled="!picked.size" @click="picked = new Set()">Clear</button>
        <span style="flex: 1" />
        <button class="btn btn-sm" :disabled="!picked.size" @click="openDialog('playlist', null, { ids: [...picked] })"><Icon name="playlist" :size="15" />Add to playlists…</button>
        <button v-if="hasPermission('songs.manage')" class="btn btn-sm" :disabled="!picked.size" @click="bulkAction = 'songs'"><Icon name="music" :size="15" />Find songs</button>
        <template v-if="hasPermission('videos.maintain')">
          <button class="btn btn-sm" :disabled="!picked.size" @click="bulkAction = 'repair'"><Icon name="wrench" :size="15" />Repair / update</button>
          <button v-if="featureOn('reencode')" class="btn btn-sm" :disabled="!picked.size" @click="bulkAction = 'reencode'"><Icon name="cpu" :size="15" />Re-encode…</button>
        </template>
        <button v-for="a in extensionActions('bulk')" :key="a.id" class="btn btn-sm" :disabled="!picked.size"
                @click="openDialog('extension', { ids: [...picked] }, { action: a })"><Icon :name="a.icon || 'plus'" :size="15" />{{ a.label }}</button>
        <button v-if="canBulkEdit" class="btn btn-primary btn-sm" :disabled="!picked.size" @click="bulkOpen = true"><Icon name="edit" :size="15" />Edit tags, performers, music…</button>
        <button class="btn btn-ghost btn-sm" @click="endSelect">Done</button>
      </div>
      <BulkEditDialog v-if="bulkOpen" :ids="[...picked]" @close="bulkOpen = false" @done="fetchPage" />
      <BulkActionDialog v-if="bulkAction" :ids="[...picked]" :action="bulkAction" @close="bulkAction = null" @done="fetchPage({ background: true })" />

      <footer class="foot">
        <PageNav :page="data.page" :total-pages="data.total_pages" @go="(p) => setQuery({ page: p }, { keepPage: true, push: true })" />
        <label class="page-size muted">
          Per page
          <select class="select" :value="session.prefs.pageSize" @change="savePref('pageSize', Number($event.target.value)); setQuery({})">
            <option v-for="n in PREF_OPTIONS.PAGE_SIZES" :key="n" :value="n">{{ n }}</option>
          </select>
        </label>
      </footer>
    </template>
  </div>
</template>

<style scoped>
.library { padding: 1rem 1.5rem 2rem; display: flex; flex-direction: column; gap: 0.875rem; }
.topbar { position: sticky; top: 0; z-index: 10; margin: -0.75rem 0 -0.25rem; padding-bottom: 0.25rem; background: var(--bg);
  display: flex; flex-direction: column; gap: 0.25rem; }
.toolbar { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; padding: 0.75rem 0 0.5rem; }
/* Long filter lists scroll inside the panel, so it never covers the whole screen. */
.filters-pop { max-height: min(32rem, 60vh); overflow-y: auto; overscroll-behavior: contain; box-shadow: var(--shadow); margin-bottom: 0.5rem; }
.search { position: relative; z-index: 12; flex: 1 1 20rem; min-width: 0; } /* its tips pop-up opens over the Filters panel */
.search-icon { position: absolute; left: 0.6875rem; top: 50%; transform: translateY(-50%); color: var(--text-dim); pointer-events: none; }
.search-input { width: 100%; padding-left: 2.25rem; padding-right: 3.75rem; height: 2.5rem; }
.search-end { position: absolute; right: 0.375rem; top: 50%; transform: translateY(-50%); display: inline-flex; align-items: center; gap: 0.25rem; }
.kbd { font: 0.6875rem var(--mono); color: var(--text-dim); border: 1px solid var(--border-strong); border-radius: 0.25rem; padding: 0 0.3125rem; }
.toolbar > .btn, .sort .btn, .sort .select, .views .btn { height: 2.5rem; }
.toolbar > .btn { gap: 0.5rem; }
.sort { display: flex; gap: 0.25rem; }
.views { display: flex; gap: 2px; padding: 2px; background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); }
.views .btn { height: 2.125rem; width: 2.125rem; border: 0; background: none; }
.views .btn.active { background: var(--accent-soft); color: var(--accent); }
.summary-row { display: flex; flex-wrap: wrap; gap: 0.375rem; align-items: center; min-height: 1.75rem; }
.count { margin-right: 0.375rem; color: var(--text-2); display: inline-flex; align-items: center; gap: 0.5rem; }
.dot-loading { width: 0.5rem; height: 0.5rem; border-radius: 50%; background: var(--accent); animation: pulse 0.8s infinite alternate; }
@keyframes pulse { to { opacity: 0.25; } }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(15.625rem, 1fr)); gap: 1.375rem 1rem; }
.rows { display: flex; flex-direction: column; gap: 0.625rem; }
.skel { display: flex; flex-direction: column; gap: 0.5rem; }
.skel-thumb { aspect-ratio: 16 / 9; border-radius: var(--radius); }
.skel-line { height: 0.75rem; border-radius: 0.375rem; }
.skel-line.short { width: 60%; }
.foot { display: flex; align-items: center; justify-content: center; gap: 1rem; flex-wrap: wrap; padding-top: 0.5rem; }
.page-size { display: inline-flex; align-items: center; gap: 0.5rem; font-size: 0.8125rem; }
.page-size .select { height: 2rem; }
@media (max-width: 56.25rem) {
  .library { padding: 0.75rem 1rem 1.5rem; }
  .topbar { top: 3.25rem; }
  .search-input { padding-right: 2.25rem; }
  .grid { grid-template-columns: repeat(auto-fill, minmax(min(100%, 13.75rem), 1fr)); gap: 1.125rem 0.75rem; }
  .kbd { display: none; }
}
@media (max-width: 32.5rem) {
  .sort { flex: 1; } .sort .select { flex: 1; min-width: 0; }
}
.pick-wrap { position: relative; min-width: 0; }
.picking .pick-wrap, .picking :deep(tr[data-id]) { cursor: pointer; }
.picking .pick-wrap :deep(*) { pointer-events: none; }
.pick-box { position: absolute; top: 0.5rem; left: 0.5rem; width: 1.6rem; height: 1.6rem; border-radius: 0.4rem; z-index: 3;
  border: 2px solid #fff; background: rgb(0 0 0 / 0.45); color: #fff; display: grid; place-items: center; box-shadow: 0 1px 4px rgb(0 0 0 / 0.5); }
.pick-wrap.picked .pick-box { background: var(--accent); border-color: var(--accent); }
.picking .pick-wrap.picked { outline: 3px solid var(--accent); outline-offset: 2px; border-radius: var(--radius-lg); }
.pick-bar { position: sticky; bottom: 0.75rem; z-index: 20; display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;
  padding: 0.625rem 0.875rem; margin-top: 1rem; box-shadow: var(--shadow); }
</style>
