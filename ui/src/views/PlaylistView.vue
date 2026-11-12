<script setup>
import { askConfirm } from "../stores/confirm.js";
import CoverDialog from "../components/playlists/CoverDialog.vue";
// One playlist: play it (in order or shuffled, from any video), as a list or
// a grid, each video with the full ⋮ menu; for its owner: rename it, find and
// add videos (same search as the library), reorder (drag or ↑/↓), remove
// videos, delete it. A smart playlist shows its filter instead of a fixed order.
import { ref, computed, watch, onBeforeUnmount } from "vue";
import { useRouter } from "vue-router";
import Icon from "../components/Icon.vue";
import VideoThumb from "../components/VideoThumb.vue";
import ActionMenu from "../components/ActionMenu.vue";
import SearchHelp from "../components/SearchHelp.vue";
import PlaylistEditDialog from "../components/dialogs/PlaylistEditDialog.vue";
import { api, qs } from "../lib/api.js";
import { parseDurationSeconds, formatDuration } from "../lib/format.js";
import { describeQuery, toLibraryQuery } from "../lib/libraryQuery.js";
import { onLibraryChange } from "../stores/live.js";
import { player, playPlaylist } from "../stores/player.js";
import { toast } from "../stores/toast.js";
import { setPageTitle } from "../lib/title.js";

const props = defineProps({ id: String });
const router = useRouter();
const pl = ref(null);
const error = ref("");
const editing = ref(false);
const coverOpen = ref(false); // Cover… dialog (2.18.0)
const dragId = ref(null);
const overId = ref(null);

async function load() {
  try { pl.value = await api(`/api/playlists/${encodeURIComponent(props.id)}`); error.value = ""; }
  catch (err) { error.value = err.status === 404 ? "This playlist doesn't exist (any more), or it's private." : err.message; }
}
watch(() => props.id, () => { pl.value = null; load(); }, { immediate: true });
const stop = onLibraryChange((changed) => {
  if (!changed || changed.includes(`playlist:${props.id}`) || (pl.value && changed.some((id) => pl.value.items.some((i) => i.id === id)))) load();
});
onBeforeUnmount(stop);

watch([pl, error], ([p, e]) => setPageTitle(`/playlist/${props.id}`, e ? "Playlist not found" : p?.name), { immediate: true });
const manual = computed(() => pl.value?.kind === "manual");
const canReorder = computed(() => manual.value && pl.value?.can_edit);
const totalTime = computed(() => {
  const secs = (pl.value?.items || []).reduce((n, i) => n + (parseDurationSeconds(i.tech_duration) || 0), 0);
  return secs ? formatDuration(secs) : "";
});
const playingHere = computed(() => player.queue?.playlistId === props.id ? player.queue.order[player.queue.index] : null);

function play(startId = null, shuffle = false) {
  if (!pl.value?.items.length) return;
  playPlaylist(pl.value, { startId, shuffle });
}

async function saveOrder(ids) {
  const before = pl.value.items;
  const byId = Object.fromEntries(before.map((i) => [i.id, i]));
  pl.value = { ...pl.value, items: ids.map((id) => byId[id]) }; // show it at once
  try { await api(`/api/playlists/${props.id}/order`, { method: "PUT", body: { item_ids: ids } }); }
  catch (err) { pl.value = { ...pl.value, items: before }; toast(err.message || "Could not reorder", { type: "error" }); }
}
function move(index, delta) {
  const ids = pl.value.items.map((i) => i.id);
  const to = index + delta;
  if (to < 0 || to >= ids.length) return;
  [ids[index], ids[to]] = [ids[to], ids[index]];
  saveOrder(ids);
}
function onDrop(targetId) {
  const from = dragId.value;
  dragId.value = null;
  overId.value = null;
  if (!from || from === targetId) return;
  const ids = pl.value.items.map((i) => i.id).filter((i) => i !== from);
  ids.splice(ids.indexOf(targetId), 0, from);
  saveOrder(ids);
}
async function removeItem(item) {
  try {
    await api(`/api/playlists/${props.id}/items/remove`, { method: "POST", body: { item_ids: [item.id] } });
    pl.value = { ...pl.value, items: pl.value.items.filter((i) => i.id !== item.id), item_count: pl.value.item_count - 1 };
  } catch (err) { toast(err.message || "Could not remove it", { type: "error" }); }
}
async function removePlaylist() {
  if (!(await askConfirm(`Delete the playlist “${pl.value.name}”? The videos themselves stay in the library.`, { ok: "Delete", danger: true }))) return;
  try { await api(`/api/playlists/${props.id}`, { method: "DELETE" }); toast("Playlist deleted"); router.push({ name: "playlists" }); }
  catch (err) { toast(err.message || "Could not delete it", { type: "error" }); }
}
function saved(p) { editing.value = false; pl.value = p; }

// ---- list or grid (remembered per browser) ----
const VIEW_KEY = "pmv-playlist-view";
const view = ref((() => { try { return localStorage.getItem(VIEW_KEY) === "grid" ? "grid" : "list"; } catch { return "list"; } })());
function setView(v) { view.value = v; try { localStorage.setItem(VIEW_KEY, v); } catch { /* ignore */ } }

const menuExtra = (it) => [
  { key: "play-here", label: "Play from here", icon: "play", run: () => play(it.id) },
  ...(pl.value?.can_edit && manual.value
    ? [{ key: "pl-remove", label: "Remove from this playlist", icon: "x", danger: true, run: () => removeItem(it) }]
    : []),
];

// ---- finding videos to add (owner, manual playlists) ----
const adding = ref(false);
const findText = ref("");
const found = ref(null);
const findBusy = ref("");
const findBox = ref(null);
let findTimer = null;
let findSeq = 0;
const inPlaylist = computed(() => new Set((pl.value?.items || []).map((i) => i.id)));
async function runFind() {
  const mine = ++findSeq;
  try {
    const r = await api(`/api/library?${qs({ search: findText.value.trim(), statuses: "done", page_size: 30, sort_key: "download_datetime", sort_dir: "desc" })}`);
    if (mine === findSeq) found.value = r;
  } catch (err) { if (mine === findSeq) found.value = { items: [], total: 0, error: err.message }; }
}
watch(findText, () => { clearTimeout(findTimer); findTimer = setTimeout(runFind, 250); });
async function toggleAdding() {
  adding.value = !adding.value;
  if (adding.value) { if (!found.value) runFind(); setTimeout(() => findBox.value?.focus(), 0); }
}
async function addOne(it) {
  findBusy.value = it.id;
  try {
    await api(`/api/playlists/${props.id}/items`, { method: "POST", body: { item_ids: [it.id] } });
    pl.value = { ...pl.value, items: [...pl.value.items, it], item_count: pl.value.item_count + 1 };
  } catch (err) { toast(err.message || "Could not add it", { type: "error" }); }
  finally { findBusy.value = ""; }
}
onBeforeUnmount(() => clearTimeout(findTimer));
</script>

<template>
  <div class="page">
    <p v-if="error" class="empty"><span class="empty-icon">🎶</span>{{ error }}<RouterLink to="/playlists" class="btn">All playlists</RouterLink></p>
    <div v-else-if="!pl" class="skeleton" style="height: 20rem; border-radius: var(--radius-lg)" />
    <template v-else>
      <header class="head">
        <RouterLink to="/playlists" class="btn btn-ghost btn-sm back"><Icon name="back" />Playlists</RouterLink>
        <div class="title-row">
          <h1>{{ pl.name }}</h1>
          <span v-if="pl.kind === 'smart'" class="pill pill-accent">Smart</span>
          <span class="pill" :class="{ 'pill-ok': pl.visibility === 'public' }">{{ pl.visibility === "public" ? "Public" : "Private" }}</span>
        </div>
        <p class="meta muted">
          {{ pl.item_count }} {{ pl.item_count === 1 ? "video" : "videos" }}<template v-if="totalTime"> · {{ totalTime }}</template>
          <template v-if="!pl.mine"> · by {{ pl.owner }}</template>
        </p>
        <p v-if="pl.description" class="desc">{{ pl.description }}</p>
        <p v-if="pl.kind === 'smart'" class="smart muted">
          Filled automatically by this filter: <strong>{{ describeQuery(pl.smart_query) }}</strong>.
          <RouterLink :to="{ name: 'library', query: toLibraryQuery(pl.smart_query) }">Open it in the library</RouterLink>
          <template v-if="pl.can_edit"> — to change it, adjust the library view and use <em>Playlist → Smart playlist → Replace the filter</em>.</template>
        </p>
        <div class="page-actions">
          <button class="btn btn-primary" :disabled="!pl.items.length" @click="play()"><Icon name="play" />Play all</button>
          <button class="btn" :disabled="!pl.items.length" @click="play(null, true)"><Icon name="shuffle" />Shuffle</button>
          <button v-if="pl.can_edit && manual" class="btn" :class="{ active: adding }" @click="toggleAdding"><Icon name="plus" />Add videos</button>
          <template v-if="pl.can_edit">
            <button class="btn btn-ghost" @click="editing = true"><Icon name="edit" />Edit</button>
            <button class="btn btn-ghost" @click="coverOpen = true"><Icon name="image" />Cover…</button>
            <button v-if="!pl.system" class="btn btn-ghost btn-danger" @click="removePlaylist"><Icon name="trash" />Delete</button>
          </template>
          <div class="views" role="group" aria-label="Layout">
            <button v-for="v in ['list', 'grid']" :key="v" class="btn btn-icon" :class="{ active: view === v }"
                    :title="v === 'grid' ? 'Grid view' : 'List view'" @click="setView(v)"><Icon :name="v" /></button>
          </div>
        </div>
      </header>

      <section v-if="adding" class="card finder">
        <div class="find-head">
          <div class="search">
            <Icon name="search" class="search-icon" />
            <input ref="findBox" v-model="findText" class="input" type="search" placeholder="Search the library… (try performer:, tag:, creator:, title:)"
                   aria-label="Search the library for videos to add">
            <span class="search-end"><SearchHelp @pick="(q) => { findText = q; findBox?.focus(); }" /></span>
          </div>
          <button class="btn btn-ghost btn-icon" title="Close" @click="adding = false"><Icon name="x" /></button>
        </div>
        <p v-if="found?.error" class="error-text">{{ found.error }}</p>
        <div v-else-if="!found" class="skeleton" style="height: 6rem; border-radius: var(--radius)" />
        <p v-else-if="!found.items.length" class="muted small">No videos match.</p>
        <template v-else>
          <ul class="found scroll-thin">
            <li v-for="it in found.items" :key="it.id">
              <span class="fthumb"><VideoThumb :item="it" compact /></span>
              <span class="text"><span class="t">{{ it.title || it.url }}</span>
                <span class="muted small">{{ it.creator }}<template v-if="it.tech_duration"> · {{ it.tech_duration }}</template></span></span>
              <span v-if="inPlaylist.has(it.id)" class="pill pill-ok">Added</span>
              <button v-else class="btn btn-sm" :disabled="findBusy === it.id" @click="addOne(it)"><Icon name="plus" :size="14" />Add</button>
            </li>
          </ul>
          <p v-if="found.total > found.items.length" class="muted small">Showing the newest {{ found.items.length }} of {{ found.total.toLocaleString() }} — search to narrow it down.</p>
        </template>
      </section>

      <div v-if="!pl.items.length" class="card empty">
        <div class="empty-icon">🎞️</div>
        <p v-if="pl.kind === 'smart'">No videos match this filter right now.</p>
        <p v-else>Empty. Add videos from their ⋮ menu (<strong>Add to playlist…</strong>) or with the <strong>Playlist</strong> button in the library.</p>
      </div>
      <ol v-else-if="view === 'list'" class="items card">
        <li v-for="(it, n) in pl.items" :key="it.id" class="row"
            :class="{ dragging: dragId === it.id, over: overId === it.id && dragId !== it.id, current: playingHere === it.id }"
            :draggable="canReorder" @dragstart="dragId = it.id" @dragend="dragId = null; overId = null"
            @dragover.prevent="canReorder && (overId = it.id)" @drop.prevent="canReorder && onDrop(it.id)">
          <span v-if="canReorder" class="handle" title="Drag to reorder"><Icon name="drag" :size="16" /></span>
          <span class="num muted">{{ playingHere === it.id ? "▶" : n + 1 }}</span>
          <button class="main" :title="`Play from here: ${it.title}`" @click="play(it.id)">
            <span class="thumb"><VideoThumb :item="it" compact /></span>
            <span class="text">
              <span class="t">{{ it.title || it.url }}</span>
              <span class="muted small">{{ it.creator }}<template v-if="it.tech_duration"> · {{ it.tech_duration }}</template></span>
            </span>
          </button>
          <span v-if="pl.can_edit && manual" class="tools">
            <button class="btn btn-ghost btn-icon btn-sm" title="Move up" :disabled="n === 0" @click="move(n, -1)">↑</button>
            <button class="btn btn-ghost btn-icon btn-sm" title="Move down" :disabled="n === pl.items.length - 1" @click="move(n, 1)">↓</button>
          </span>
          <RouterLink :to="{ name: 'video', params: { id: it.id } }" class="btn btn-ghost btn-icon btn-sm open-page" title="Open the video page without playing the playlist"><Icon name="external" :size="15" /></RouterLink>
          <ActionMenu :item="it" context="playlist" :extra="menuExtra(it)" />
        </li>
      </ol>
      <ol v-else class="tiles">
        <li v-for="(it, n) in pl.items" :key="it.id" class="tile"
            :class="{ dragging: dragId === it.id, over: overId === it.id && dragId !== it.id, current: playingHere === it.id }"
            :draggable="canReorder" @dragstart="dragId = it.id" @dragend="dragId = null; overId = null"
            @dragover.prevent="canReorder && (overId = it.id)" @drop.prevent="canReorder && onDrop(it.id)">
          <button class="tile-thumb" :title="`Play from here: ${it.title}`" @click="play(it.id)">
            <VideoThumb :item="it" :playing="playingHere === it.id" />
            <span class="tile-num">{{ playingHere === it.id ? "▶" : n + 1 }}</span>
          </button>
          <div class="tile-head">
            <RouterLink :to="{ name: 'video', params: { id: it.id } }" class="t" title="Open the video page without playing the playlist">{{ it.title || it.url }}</RouterLink>
            <ActionMenu :item="it" context="playlist" :extra="menuExtra(it)" />
          </div>
          <span class="muted small">{{ it.creator }}</span>
        </li>
      </ol>
    </template>
    <PlaylistEditDialog v-if="editing" :playlist="pl" @close="editing = false" @saved="saved" />
    <CoverDialog v-if="coverOpen && pl" :playlist="pl" @close="coverOpen = false" @changed="load" />
  </div>
</template>

<style scoped>
.head { display: flex; flex-direction: column; gap: 0.5rem; }
.back { align-self: flex-start; }
.title-row { display: flex; align-items: center; gap: 0.625rem; flex-wrap: wrap; }
.title-row h1 { font-size: 1.5rem; overflow-wrap: anywhere; }
.meta, .desc, .smart { margin: 0; font-size: 0.875rem; }
.desc { white-space: pre-line; color: var(--text-2); max-width: 60rem; }
.items { list-style: none; margin: 0; padding: 0.375rem; display: flex; flex-direction: column; }
.row { display: flex; align-items: center; gap: 0.5rem; padding: 0.3rem 0.375rem; border-radius: var(--radius); border-top: 2px solid transparent; }
.row:hover { background: var(--panel-2); }
.row.current { background: var(--accent-soft); }
.row.dragging { opacity: 0.4; }
.row.over { border-top-color: var(--accent); }
.handle { cursor: grab; color: var(--text-dim); display: grid; place-items: center; }
.num { width: 1.75rem; text-align: center; font-size: 0.78rem; flex-shrink: 0; font-variant-numeric: tabular-nums; }
.row.current .num { color: var(--accent); }
.main { flex: 1; min-width: 0; display: flex; align-items: center; gap: 0.75rem; border: 0; background: none; padding: 0; color: var(--text); font: inherit; text-align: left; cursor: pointer; }
.main > .thumb { width: 9rem; flex-shrink: 0; }
.text { min-width: 0; display: flex; flex-direction: column; gap: 0.125rem; }
.t { font-weight: 600; font-size: 0.9rem; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.small { font-size: 0.78rem; }
.tools { display: flex; gap: 0.125rem; }
.page-actions { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; }
.views { display: flex; gap: 2px; padding: 2px; margin-left: auto; background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); }
.views .btn { height: 2.125rem; width: 2.125rem; border: 0; background: none; }
.views .btn.active { background: var(--accent-soft); color: var(--accent); }
.page-actions > .btn.active { border-color: var(--accent); color: var(--accent); }

.finder { padding: 0.875rem; display: flex; flex-direction: column; gap: 0.625rem; }
.find-head { display: flex; gap: 0.5rem; align-items: center; }
.search { position: relative; flex: 1; min-width: 0; }
.search .input { width: 100%; height: 2.5rem; padding-left: 2.25rem; padding-right: 2.5rem; }
.search-icon { position: absolute; left: 0.6875rem; top: 50%; transform: translateY(-50%); color: var(--text-dim); pointer-events: none; }
.search-end { position: absolute; right: 0.375rem; top: 50%; transform: translateY(-50%); display: inline-flex; }
.found { list-style: none; margin: 0; padding: 0; max-height: 22rem; overflow-y: auto; display: flex; flex-direction: column; gap: 0.25rem; }
.found li { display: flex; align-items: center; gap: 0.75rem; padding: 0.25rem; border-radius: var(--radius); }
.found li:hover { background: var(--panel-2); }
.fthumb { width: 7rem; flex-shrink: 0; }
.found .text { flex: 1; }
.finder p { margin: 0; }

.tiles { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(15.625rem, 1fr)); gap: 1.375rem 1rem; }
.tile { display: flex; flex-direction: column; gap: 0.25rem; min-width: 0; border-radius: var(--radius); outline: 2px solid transparent; outline-offset: 3px; }
.tile.current { outline-color: var(--accent); }
.tile.dragging { opacity: 0.4; }
.tile.over { outline-color: var(--accent); outline-style: dashed; }
.tile[draggable="true"] { cursor: grab; }
.tile-thumb { position: relative; border: 0; padding: 0; background: none; cursor: pointer; display: block; }
.tile-num { position: absolute; top: 0.375rem; left: 0.375rem; min-width: 1.5rem; padding: 0.05rem 0.4rem; border-radius: 0.375rem;
  background: rgb(0 0 0 / 0.7); color: #fff; font-size: 0.75rem; font-weight: 700; text-align: center; }
.tile-head { display: flex; align-items: flex-start; gap: 0.25rem; }
.tile-head .t { flex: 1; min-width: 0; color: var(--text); text-decoration: none; }
.tile-head .t:hover { color: var(--accent); }
@media (max-width: 56rem) {
  .main > .thumb { width: 6.5rem; }
  .tools, .open-page { display: none; }
  .tiles { grid-template-columns: repeat(auto-fill, minmax(min(100%, 13.75rem), 1fr)); gap: 1.125rem 0.75rem; }
}
</style>
