<script setup>
// Performers (2.17.0): everyone in the library's videos. Performers matched to
// a ThePornDB profile always come first, then the others (grey placeholder
// unless someone added a photo of them). Search, sort and a filter; the list
// grows as you scroll ("Show more").
import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { RouterLink } from "vue-router";
import Icon from "../components/Icon.vue";
import PerformerPlaceholder from "../components/PerformerPlaceholder.vue";
import MultiSelect from "../components/MultiSelect.vue";
import { api } from "../lib/api.js";
import { onLibraryChange } from "../stores/live.js";

// Sort by a field, either way (2.31.0); each field starts in its natural direction.
const SORTS = [
  { key: "videos", label: "Videos", dir: "desc", asc: "Fewest videos first", desc: "Most videos first" },
  { key: "name", label: "Name", dir: "asc", asc: "A to Z", desc: "Z to A" },
];
const SHOWS = [
  { key: "all", label: "Everyone" },
  { key: "linked", label: "On ThePornDB" },
  { key: "unlinked", label: "Not on ThePornDB" },
];
const PREF = "pmv-performers-view";
let saved = {};
try { saved = JSON.parse(localStorage.getItem(PREF) || "{}"); } catch { /* private mode */ }

const all = ref(null);
const error = ref("");
const q = ref("");
const sortOf = (key) => SORTS.find((s) => s.key === key);
// "fewest" was its own choice before 2.31.0: it's Videos, ascending.
const sort = ref(saved.sort === "fewest" ? "videos" : sortOf(saved.sort) ? saved.sort : "videos");
const dir = ref(saved.sort === "fewest" ? "asc" : saved.dir === "asc" || saved.dir === "desc" ? saved.dir : sortOf(sort.value).dir);
const show = ref(SHOWS.some((s) => s.key === saved.show) ? saved.show : "all");
const shown = ref(120);
function setSort(key) { sort.value = key; dir.value = sortOf(key).dir; }
watch([sort, dir, show], () => {
  shown.value = 120;
  try { localStorage.setItem(PREF, JSON.stringify({ sort: sort.value, dir: dir.value, show: show.value })); } catch { /* private mode */ }
});
watch(q, () => { shown.value = 120; });

async function load() {
  try { all.value = (await api("/api/performers/browse")).performers; error.value = ""; }
  catch (err) { error.value = err.message || "Could not load the performers"; }
}
let timer = null;
let stop = null;
onMounted(() => {
  load();
  // A performer linked, a photo added, videos added or removed: reload quietly.
  stop = onLibraryChange((c) => {
    if (!c || c.some((id) => id === "performers" || String(id).startsWith("performer:"))) { clearTimeout(timer); timer = setTimeout(load, 1500); }
  });
});
onBeforeUnmount(() => { stop?.(); clearTimeout(timer); });

const collator = new Intl.Collator(undefined, { sensitivity: "base", numeric: true });

// ---- detail filters (2.18.0): ThePornDB's facts; a performer without the detail is left out
// while that filter is set. Kept for this browser tab (sessionStorage).
const FKEY = "pmv-performers-filters";
const EMPTY = { gender: [], nationality: [], ethnicity: [], hair: [], eyes: [], cup: [],
  ageMin: null, ageMax: null, heightMin: null, heightMax: null, weightMin: null, weightMax: null,
  active: "any", enhanced: "any", tattoos: "any", piercings: "any" };
let savedF = {};
try { savedF = JSON.parse(sessionStorage.getItem(FKEY) || "{}"); } catch { /* private mode */ }
const f = ref({ ...EMPTY, ...savedF });
const showFilters = ref(false);
watch(f, (v) => { shown.value = 120; try { sessionStorage.setItem(FKEY, JSON.stringify(v)); } catch { /* private mode */ } }, { deep: true });
const CHOICES = [
  { key: "gender", label: "Gender", field: "gender_label" },
  { key: "nationality", label: "Nationality", field: "nationality" },
  { key: "ethnicity", label: "Ethnicity", field: "ethnicity" },
  { key: "hair", label: "Hair", field: "hair" },
  { key: "eyes", label: "Eyes", field: "eyes" },
  { key: "cup", label: "Cup size", field: "cup" },
];
const RANGES = [
  { key: "age", label: "Age", field: "age", unit: "years" },
  { key: "height", label: "Height", field: "height", unit: "cm" },
  { key: "weight", label: "Weight", field: "weight", unit: "kg" },
];
const YES_NO = [
  { key: "active", label: "Still active", field: "active", yes: "Active", no: "Retired" },
  { key: "enhanced", label: "Enhanced breasts", field: "enhanced", yes: "Yes", no: "No" },
  { key: "tattoos", label: "Tattoos", field: "tattoos", yes: "Yes", no: "No" },
  { key: "piercings", label: "Piercings", field: "piercings", yes: "Yes", no: "No" },
];
const options = computed(() => {
  const out = {};
  for (const c of CHOICES) {
    out[c.key] = [...new Set((all.value || []).map((p) => p[c.field]).filter(Boolean))].sort(collator.compare);
  }
  return out;
});
const known = (field) => (all.value || []).some((p) => p[field] !== null && p[field] !== undefined);
const activeFilters = computed(() => {
  const v = f.value;
  return CHOICES.filter((c) => v[c.key].length).length
    + RANGES.filter((r) => v[`${r.key}Min`] != null || v[`${r.key}Max`] != null).length
    + YES_NO.filter((y) => v[y.key] !== "any").length;
});
function clearFilters() { f.value = { ...EMPTY }; }
function passes(p) {
  const v = f.value;
  for (const c of CHOICES) if (v[c.key].length && !v[c.key].includes(p[c.field])) return false;
  for (const r of RANGES) {
    const lo = v[`${r.key}Min`], hi = v[`${r.key}Max`];
    if (lo == null && hi == null) continue;
    const n = p[r.field];
    if (n == null || (lo != null && n < lo) || (hi != null && n > hi)) return false;
  }
  for (const y of YES_NO) if (v[y.key] !== "any" && p[y.field] !== (v[y.key] === "yes")) return false;
  return true;
}
const num = (e) => (e.target.value === "" ? null : Number(e.target.value));

const list = computed(() => {
  const s = q.value.trim().toLowerCase();
  const rows = (all.value || []).filter((p) => (show.value === "all" || (show.value === "linked") === p.linked)
    && (!s || p.name.toLowerCase().includes(s) || p.link.toLowerCase().includes(s)) && passes(p));
  const sign = dir.value === "asc" ? 1 : -1;
  const by = {
    videos: (a, b) => sign * (a.videos - b.videos) || collator.compare(a.name, b.name),
    name: (a, b) => sign * collator.compare(a.name, b.name),
  }[sort.value];
  // ThePornDB matches first, always.
  return rows.sort((a, b) => (b.linked - a.linked) || by(a, b));
});
const counts = computed(() => ({
  linked: (all.value || []).filter((p) => p.linked).length,
  unlinked: (all.value || []).filter((p) => !p.linked).length,
}));
// Where the "not on ThePornDB" part starts, for a divider line.
const firstUnlinked = computed(() => (show.value === "all" ? list.value.findIndex((p) => !p.linked) : -1));
</script>

<template>
  <div class="page performers">
    <div class="toolbar">
      <div class="search-wrap">
        <Icon name="search" :size="16" />
        <input v-model="q" class="input" type="search" placeholder="Find a performer" aria-label="Find a performer">
      </div>
      <div class="seg" role="group" aria-label="Show">
        <button v-for="s in SHOWS" :key="s.key" class="btn btn-sm" :class="{ 'btn-primary': show === s.key }" @click="show = s.key">
          {{ s.label }}<span v-if="all && s.key !== 'all'" class="n">{{ counts[s.key] }}</span>
        </button>
      </div>
      <div class="sortbox">
        <select :value="sort" class="input sort" aria-label="Sort by" @change="setSort($event.target.value)">
          <option v-for="s in SORTS" :key="s.key" :value="s.key">{{ s.label }}</option>
        </select>
        <button class="btn btn-icon" :title="sortOf(sort)[dir]" :aria-label="`Order: ${sortOf(sort)[dir]}`"
                @click="dir = dir === 'asc' ? 'desc' : 'asc'">
          <Icon :name="dir === 'asc' ? 'sortAsc' : 'sortDesc'" />
        </button>
      </div>
      <button class="btn" :class="{ active: showFilters }" :aria-expanded="showFilters" @click="showFilters = !showFilters">
        <Icon name="filter" :size="16" />Filters<span v-if="activeFilters" class="badge-n">{{ activeFilters }}</span>
      </button>
    </div>
    <section v-if="showFilters && all" class="card pfilters">
      <div v-for="c in CHOICES.filter((c) => options[c.key].length)" :key="c.key" class="field">
        <label class="field-title" :for="`pf-${c.key}`">{{ c.label }}</label>
        <MultiSelect v-model="f[c.key]" :input-id="`pf-${c.key}`" :options="options[c.key]" :placeholder="`Any ${c.label.toLowerCase()}`" />
      </div>
      <div v-for="r in RANGES.filter((r) => known(r.field))" :key="r.key" class="field">
        <span>{{ r.label }} ({{ r.unit }})</span>
        <div class="range">
          <input class="input" type="number" min="0" placeholder="Min" :value="f[`${r.key}Min`] ?? ''" :aria-label="`${r.label} from`"
                 @change="f[`${r.key}Min`] = num($event)">
          <span class="muted">to</span>
          <input class="input" type="number" min="0" placeholder="Max" :value="f[`${r.key}Max`] ?? ''" :aria-label="`${r.label} to`"
                 @change="f[`${r.key}Max`] = num($event)">
        </div>
      </div>
      <div v-for="y in YES_NO.filter((y) => known(y.field))" :key="y.key" class="field">
        <span>{{ y.label }}</span>
        <div class="seg">
          <button v-for="o in [['any', 'Any'], ['yes', y.yes], ['no', y.no]]" :key="o[0]" type="button" class="btn btn-sm"
                  :class="{ 'btn-primary': f[y.key] === o[0] }" @click="f[y.key] = o[0]">{{ o[1] }}</button>
        </div>
      </div>
      <p v-if="!CHOICES.some((c) => options[c.key].length) && !RANGES.some((r) => known(r.field))" class="muted small">
        No details to filter by yet — they come from ThePornDB profiles.</p>
      <div class="pf-foot">
        <span class="muted small">Details come from ThePornDB; performers without a detail are hidden while it's filtered on.</span>
        <button class="btn btn-sm" :disabled="!activeFilters" @click="clearFilters">Clear filters</button>
      </div>
    </section>
    <p v-if="error" class="error-text">{{ error }}</p>
    <div v-if="!all" class="grid">
      <div v-for="n in 12" :key="n" class="skeleton" style="aspect-ratio: 2 / 3; border-radius: var(--radius)" />
    </div>
    <template v-else>
      <p class="muted small">{{ list.length.toLocaleString() }} {{ list.length === 1 ? "performer" : "performers" }}
        <template v-if="show === 'all'"> · matched to ThePornDB first</template></p>
      <p v-if="!list.length" class="muted">Nobody matches.</p>
      <div class="grid">
        <template v-for="(p, n) in list.slice(0, shown)" :key="p.name + p.linked">
          <div v-if="n === firstUnlinked && n > 0" class="divider"><span>Not on ThePornDB</span></div>
          <RouterLink :to="{ name: 'performer', params: { name: p.link } }" class="card-p" :title="p.name">
            <span class="pic">
              <img v-if="p.photo" :src="p.photo" alt="" loading="lazy" decoding="async" @error="p.photo = null">
              <PerformerPlaceholder v-else :gender="p.gender" />
            </span>
            <span class="name">{{ p.name }}</span>
            <span class="meta muted">{{ p.videos }} {{ p.videos === 1 ? "video" : "videos" }}<template v-if="p.names > 1"> · {{ p.names }} names</template></span>
          </RouterLink>
        </template>
      </div>
      <button v-if="list.length > shown" class="btn btn-sm more" @click="shown += 240">Show more ({{ (list.length - shown).toLocaleString() }} left)</button>
    </template>
  </div>
</template>

<style scoped>
.toolbar { display: flex; gap: 0.625rem; align-items: center; flex-wrap: wrap; }
.search-wrap { position: relative; flex: 1 1 16rem; max-width: 26rem; display: flex; align-items: center; }
.search-wrap svg { position: absolute; left: 0.7rem; color: var(--text-dim); pointer-events: none; }
.search-wrap .input { padding-left: 2.1rem; width: 100%; height: 2.4rem; }
.seg { display: flex; gap: 0.375rem; flex-wrap: wrap; }
.n { margin-left: 0.35rem; font-size: 0.72rem; opacity: 0.75; }
.sortbox { display: flex; gap: 0.25rem; }
.sort { width: auto; height: 2.4rem; }
.sortbox .btn { height: 2.4rem; }
.badge-n { margin-left: 0.35rem; background: var(--accent); color: #fff; border-radius: 999px; font-size: 0.7rem; padding: 0 0.4rem; }
.pfilters { padding: 1rem 1.125rem; display: grid; grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr)); gap: 0.875rem 1.25rem; }
.range { display: flex; align-items: center; gap: 0.4rem; }
.range .input { width: 100%; min-width: 0; }
.pf-foot { grid-column: 1 / -1; display: flex; justify-content: space-between; align-items: center; gap: 0.75rem; flex-wrap: wrap;
  border-top: 1px solid var(--border); padding-top: 0.75rem; }
.small { font-size: 0.8rem; margin: 0; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(9.5rem, 1fr)); gap: 1rem 0.875rem; }
.divider { grid-column: 1 / -1; display: flex; align-items: center; gap: 0.75rem; color: var(--text-dim); font-size: 0.75rem;
  text-transform: uppercase; letter-spacing: 0.06em; margin-top: 0.5rem; }
.divider::before, .divider::after { content: ""; flex: 1; height: 1px; background: var(--border); }
.card-p { display: flex; flex-direction: column; gap: 0.3rem; color: var(--text); text-decoration: none; min-width: 0; }
.pic { display: block; aspect-ratio: 2 / 3; border-radius: var(--radius); overflow: hidden; background: var(--panel-2); transition: transform 0.15s, box-shadow 0.15s; }
.pic img { width: 100%; height: 100%; object-fit: cover; display: block; }
.card-p:hover .pic, .card-p:focus-visible .pic { transform: translateY(-2px); box-shadow: 0 6px 18px rgb(0 0 0 / 0.25); }
.name { font-weight: 650; font-size: 0.9rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta { font-size: 0.76rem; }
.more { align-self: flex-start; margin-top: 0.5rem; }
@media (max-width: 560px) { .grid { grid-template-columns: repeat(auto-fill, minmax(7rem, 1fr)); gap: 0.75rem 0.6rem; } }
</style>
