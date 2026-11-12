<script setup>
// "Add to playlist". Two uses:
//  - one video (props.item): a box like the tags/performers inputs -- the
//    playlists it's in are chips (× takes it out), typing finds your other
//    playlists and picking one adds it -- or make a new playlist with it;
//  - a whole library view (options.query = GET /api/library parameters,
//    options.count = how many videos): add all of them (every page) to a
//    playlist, or keep the view as a SMART playlist that updates itself;
//  - videos picked in the library (options.ids, Library -> Select): add
//    them to one or more playlists, or a new one.
import { ref, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import ModalDialog from "../ModalDialog.vue";
import { api } from "../../lib/api.js";
import { describeQuery } from "../../lib/libraryQuery.js";
import { toast } from "../../stores/toast.js";
import { session } from "../../stores/session.js";

const props = defineProps({ item: Object, options: Object });
const emit = defineEmits(["close"]);
const router = useRouter();

const ids = computed(() => props.options?.ids || null);
const query = computed(() => (ids.value ? null : props.options?.query || null));
const count = computed(() => (ids.value ? ids.value.length : props.options?.count ?? 0));
const addedTo = ref(new Set());
const playlists = ref(null);
const containing = ref(new Set());
const busy = ref("");
const error = ref("");
const fresh = ref({ name: "", visibility: "private" });
const smart = ref({ name: "", visibility: "private", replace: "" });

const manual = computed(() => (playlists.value || []).filter((p) => p.mine && p.kind === "manual"));
const mySmart = computed(() => (playlists.value || []).filter((p) => p.mine && p.kind === "smart"));

onMounted(async () => {
  try {
    const [list, has] = await Promise.all([
      api("/api/playlists"),
      props.item ? api(`/api/playlists/for-item/${encodeURIComponent(props.item.id)}`) : Promise.resolve({ playlist_ids: [] }),
    ]);
    playlists.value = list.playlists;
    containing.value = new Set(has.playlist_ids);
  } catch (err) {
    error.value = err.message || "Could not load your playlists";
  }
});

async function toggle(p, on) {
  busy.value = p.id;
  try {
    if (on) await api(`/api/playlists/${p.id}/items`, { method: "POST", body: { item_ids: [props.item.id] } });
    else await api(`/api/playlists/${p.id}/items/remove`, { method: "POST", body: { item_ids: [props.item.id] } });
    const s = new Set(containing.value);
    on ? s.add(p.id) : s.delete(p.id);
    containing.value = s;
    toast(on ? `Added to ${p.name}` : `Removed from ${p.name}`);
  } catch (err) {
    toast(err.message || "That didn't work", { type: "error" });
  } finally {
    busy.value = "";
  }
}

// ---- the playlist box (one video) ----
const find = ref("");
const listOpen = ref(false);
const active = ref(-1);
const findBox = ref(null);
const inList = computed(() => manual.value.filter((p) => containing.value.has(p.id)));
const offered = computed(() => {
  const n = find.value.trim().toLowerCase();
  return manual.value.filter((p) => !containing.value.has(p.id) && (!n || p.name.toLowerCase().includes(n)));
});
async function pickPlaylist(p) {
  await toggle(p, true);
  find.value = "";
  active.value = -1;
  if (!session.prefs.suggestKeepOpen) listOpen.value = false;
  findBox.value?.focus();
}
function onFindKey(e) {
  const n = offered.value.length;
  if (e.key === "ArrowDown" && n) { e.preventDefault(); listOpen.value = true; active.value = (active.value + 1) % n; }
  else if (e.key === "ArrowUp" && n) { e.preventDefault(); active.value = active.value <= 0 ? n - 1 : active.value - 1; }
  else if (e.key === "Enter") {
    e.preventDefault();
    const hit = offered.value[active.value >= 0 ? active.value : 0];
    if (hit && (active.value >= 0 || find.value.trim())) pickPlaylist(hit);
  } else if (e.key === "Escape" && listOpen.value) { e.preventDefault(); e.stopPropagation(); listOpen.value = false; }
  else if (e.key === "Backspace" && !find.value && inList.value.length) toggle(inList.value[inList.value.length - 1], false);
}
function onFindBlur() { setTimeout(() => { listOpen.value = false; }, 150); }

async function addView(p) {
  busy.value = p.id;
  try {
    const r = await api(`/api/playlists/${p.id}/items`, { method: "POST", body: ids.value ? { item_ids: ids.value } : { query: query.value } });
    toast(`Added ${r.added} to ${p.name}${r.skipped ? ` (${r.skipped} already in it)` : ""}`, { type: "success" });
    if (ids.value) { addedTo.value = new Set([...addedTo.value, p.id]); busy.value = ""; return; }  // picked videos: more playlists can follow
    emit("close");
  } catch (err) {
    toast(err.message || "That didn't work", { type: "error" });
    busy.value = "";
  }
}

async function createManual() {
  if (!fresh.value.name.trim()) return;
  busy.value = "new";
  try {
    const p = await api("/api/playlists", {
      method: "POST",
      body: { name: fresh.value.name, visibility: fresh.value.visibility, kind: "manual", item_ids: props.item ? [props.item.id] : ids.value || [] },
    });
    if (query.value) await api(`/api/playlists/${p.id}/items`, { method: "POST", body: { query: query.value } });
    toast(`Created ${p.name}`, { type: "success" });
    emit("close");
  } catch (err) {
    toast(err.message || "Could not create the playlist", { type: "error" });
    busy.value = "";
  }
}

async function saveSmart() {
  busy.value = "smart";
  try {
    let p;
    if (smart.value.replace) {
      p = await api(`/api/playlists/${smart.value.replace}`, { method: "PATCH", body: { smart_query: query.value } });
      toast(`Updated the filter of ${p.name}`, { type: "success" });
    } else {
      if (!smart.value.name.trim()) { busy.value = ""; return; }
      p = await api("/api/playlists", {
        method: "POST", body: { name: smart.value.name, visibility: smart.value.visibility, kind: "smart", smart_query: query.value },
      });
      toast(`Created smart playlist ${p.name}`, { type: "success" });
    }
    emit("close");
    router.push({ name: "playlist", params: { id: p.id } });
  } catch (err) {
    toast(err.message || "Could not save the smart playlist", { type: "error" });
    busy.value = "";
  }
}
</script>

<template>
  <ModalDialog :title="item ? 'Add to playlist' : ids ? `Add ${count} ${count === 1 ? 'video' : 'videos'} to playlists` : `Playlist from this view (${count.toLocaleString()} videos)`" width="34rem" @close="emit('close')">
    <p v-if="error" class="error-text">{{ error }}</p>
    <div v-else-if="!playlists" class="skeleton" style="height: 6rem; border-radius: var(--radius)" />
    <template v-else>
      <p v-if="item" class="lead">“{{ item.title || item.url }}”</p>
      <p v-else-if="ids" class="lead muted">The videos you picked. Add them to as many playlists as you like.</p>
      <p v-else class="lead muted">Filter: {{ describeQuery(query) }}</p>

      <template v-if="item">
        <h4>In your playlists</h4>
        <div v-if="manual.length" class="combo">
          <div class="tag-input input" @click="findBox?.focus()">
            <span v-for="p in inList" :key="p.id" class="chip">
              {{ p.name }}<button type="button" class="chip-x" :aria-label="`Remove from ${p.name}`" :disabled="busy === p.id"
                                  @click.stop="toggle(p, false)">×</button>
            </span>
            <input ref="findBox" v-model="find" role="combobox" autocomplete="off" :aria-expanded="listOpen && offered.length > 0"
                   aria-label="Find a playlist" :placeholder="inList.length ? '' : 'Find one of your playlists…'"
                   @keydown="onFindKey" @focus="listOpen = true" @input="listOpen = true; active = find.trim() ? 0 : -1" @blur="onFindBlur">
          </div>
          <ul v-if="listOpen && offered.length" class="suggestions scroll-thin" role="listbox">
            <li v-for="(p, i) in offered" :key="p.id" role="option" :aria-selected="i === active" :class="{ active: i === active }"
                @mousedown.prevent="pickPlaylist(p)" @mouseenter="active = i">
              <span>{{ p.name }}</span><span class="count">{{ p.item_count }} · {{ p.visibility }}</span>
            </li>
          </ul>
          <p v-else-if="listOpen && find.trim()" class="muted small none">No other playlist matches — create it below.</p>
        </div>
        <p v-else class="muted small">You have no playlists yet.</p>
        <h4>New playlist with this video</h4>
      </template>
      <h4 v-else>Add all of them to</h4>
      <ul v-if="!item && manual.length" class="list scroll-thin">
        <li v-for="p in manual" :key="p.id">
          <div class="add-row">
            <span class="pname">{{ p.name }}</span><span class="muted small">{{ p.item_count }} · {{ p.visibility }}</span>
            <button class="btn btn-sm" :disabled="!!busy || addedTo.has(p.id)" @click="addView(p)">{{ busy === p.id ? "Adding…" : addedTo.has(p.id) ? "Added ✓" : "Add" }}</button>
          </div>
        </li>
      </ul>
      <p v-else-if="!item" class="muted small">You have no playlists yet.</p>

      <form class="new" @submit.prevent="createManual">
        <input v-model="fresh.name" class="input" maxlength="120" :placeholder="item ? 'Playlist name' : 'Or a new playlist with all of them'" aria-label="New playlist name">
        <select v-model="fresh.visibility" class="select" aria-label="Who can see it">
          <option value="private">Private</option>
          <option value="public">Public</option>
        </select>
        <button class="btn btn-primary" :disabled="!fresh.name.trim() || !!busy">{{ busy === "new" ? "Creating…" : "Create" }}</button>
      </form>

      <section v-if="query" class="smart">
        <h4>Smart playlist</h4>
        <p class="muted small">Keeps this filter instead of a fixed list: new videos that match show up in it by themselves.</p>
        <form class="new" @submit.prevent="saveSmart">
          <select v-if="mySmart.length" v-model="smart.replace" class="select" aria-label="New or existing smart playlist">
            <option value="">New smart playlist</option>
            <option v-for="p in mySmart" :key="p.id" :value="p.id">Replace the filter of “{{ p.name }}”</option>
          </select>
          <template v-if="!smart.replace">
            <input v-model="smart.name" class="input" maxlength="120" placeholder="Smart playlist name" aria-label="Smart playlist name">
            <select v-model="smart.visibility" class="select" aria-label="Who can see it">
              <option value="private">Private</option>
              <option value="public">Public</option>
            </select>
          </template>
          <button class="btn" :disabled="(!smart.replace && !smart.name.trim()) || !!busy">{{ busy === "smart" ? "Saving…" : "Save" }}</button>
        </form>
      </section>
      <p class="muted small hint">Public playlists can be seen and played by every user of this app; only you can change them.</p>
    </template>
    <template #footer><button class="btn" @click="emit('close')">Done</button></template>
  </ModalDialog>
</template>

<style scoped>
.lead { margin: 0 0 0.75rem; font-weight: 600; overflow-wrap: anywhere; }
.lead.muted { font-weight: 400; font-size: 0.84rem; }
h4 { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); margin: 0.5rem 0 0.375rem; }
.list { list-style: none; margin: 0; padding: 0; max-height: 14rem; overflow-y: auto; display: flex; flex-direction: column; gap: 0.25rem; }
.pname { font-weight: 600; color: var(--text); }
.small { font-size: 0.76rem; }
.add-row { display: flex; align-items: center; gap: 0.5rem; padding: 0.2rem 0; }
.add-row .pname { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.new { display: flex; gap: 0.5rem; margin-top: 0.75rem; flex-wrap: wrap; align-items: stretch; }
/* Box, menu and button the same height. */
.new .input, .new .select, .new .btn { height: 2.5rem; }
.new .input { flex: 1 1 12rem; min-width: 0; }
.new .select { width: auto; }
.combo { position: relative; }
.tag-input { display: flex; flex-wrap: wrap; gap: 0.25rem; align-items: center; min-height: 2.5rem; height: auto; padding: 0.3rem 0.4rem; cursor: text; }
.tag-input input { flex: 1; min-width: 8rem; border: 0; outline: 0; background: transparent; color: var(--text); font: inherit; padding: 0.2rem; }
.suggestions { margin: 0.25rem 0 0; padding: 0.25rem; list-style: none; max-height: 13rem; overflow-y: auto;
  background: var(--panel); border: 1px solid var(--border-strong); border-radius: var(--radius); }
.suggestions li { display: flex; justify-content: space-between; gap: 1rem; padding: 0.4rem 0.6rem; border-radius: var(--radius-sm); cursor: pointer; font-size: 0.875rem; }
.suggestions li.active { background: var(--accent-soft); }
.count { color: var(--text-dim); font-size: 0.75rem; white-space: nowrap; }
.none { margin: 0.375rem 0 0; }
.smart { margin-top: 1rem; padding-top: 0.75rem; border-top: 1px solid var(--border); }
.smart p { margin: 0; }
.hint { margin: 0.875rem 0 0; }
</style>
