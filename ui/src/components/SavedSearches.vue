<script setup>
// Library -> Saved (3.5.0): your saved searches -- the whole view (search, filters, exclude,
// sort) under a name, saved with your account (My Settings' "ui": savedSearches). Pick one to
// open it; "Save this view" adds the current one (the same name replaces it).
import { ref, computed, nextTick, onBeforeUnmount } from "vue";
import { useRoute, useRouter } from "vue-router";
import Icon from "./Icon.vue";
import { session, savePref } from "../stores/session.js";
import { toast } from "../stores/toast.js";

const route = useRoute();
const router = useRouter();
const open = ref(false);
const name = ref("");
const box = ref(null);
const root = ref(null);

const SKIP = new Set(["page"]);  // not part of a search
const current = computed(() => Object.fromEntries(Object.entries(route.query).filter(([k, v]) => !SKIP.has(k) && v !== "" && v != null)));
const same = (a, b) => JSON.stringify(Object.entries(a).sort()) === JSON.stringify(Object.entries(b).sort());
const list = computed(() => session.prefs.savedSearches || []);
const activeName = computed(() => list.value.find((s) => same(s.query, current.value))?.name || "");

function toggle() {
  open.value = !open.value;
  if (open.value) nextTick(() => box.value?.focus());
}
function apply(s) { open.value = false; router.push({ name: "library", query: { ...s.query } }); }
function save() {
  const n = name.value.trim().slice(0, 80);
  if (!n) return;
  if (!Object.keys(current.value).length) { toast("Nothing to save: no search, filters or sort in this view", { type: "error" }); return; }
  const rest = list.value.filter((s) => s.name.toLowerCase() !== n.toLowerCase());
  if (rest.length >= 50) { toast("You can keep 50 saved searches; remove one first", { type: "error" }); return; }
  savePref("savedSearches", [...rest, { name: n, query: { ...current.value } }].sort((a, b) => a.name.localeCompare(b.name)));
  toast(`Saved “${n}”`, { type: "success" });
  name.value = "";
}
function remove(s) { savePref("savedSearches", list.value.filter((x) => x !== s)); }

function outside(e) { if (open.value && root.value && !root.value.contains(e.target)) open.value = false; }
function onKey(e) { if (e.key === "Escape") open.value = false; }
document.addEventListener("pointerdown", outside);
document.addEventListener("keydown", onKey);
onBeforeUnmount(() => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", onKey); });
</script>

<template>
  <div ref="root" class="saved">
    <button class="btn" :class="{ active: open || activeName, 'btn-icon': !activeName }" :aria-expanded="open"
            :aria-label="activeName ? `Saved search: ${activeName}` : 'Saved searches'"
            :title="activeName ? `Saved search: ${activeName}` : 'Saved searches: save this view, or open one you saved'" @click="toggle">
      <Icon name="bookmark" /><span v-if="activeName" class="lbl">{{ activeName }}</span>
    </button>
    <div v-if="open" class="pop card" role="dialog" aria-label="Saved searches">
      <form class="add" @submit.prevent="save">
        <input ref="box" v-model="name" class="input" maxlength="80" placeholder="Name this view…" aria-label="Name for this search">
        <button class="btn btn-sm btn-primary" type="submit" :disabled="!name.trim()">Save</button>
      </form>
      <p class="muted small">Saves the search, filters, exclude and sort.</p>
      <ul v-if="list.length" class="list">
        <li v-for="s in list" :key="s.name" :class="{ on: s.name === activeName }">
          <button class="open" type="button" :title="`Open “${s.name}”`" @click="apply(s)">{{ s.name }}</button>
          <button class="btn btn-ghost btn-sm x" type="button" :aria-label="`Remove “${s.name}”`" title="Remove" @click="remove(s)">✕</button>
        </li>
      </ul>
      <p v-else class="muted small">No saved searches yet.</p>
    </div>
  </div>
</template>

<style scoped>
.saved { position: relative; }
.lbl { max-width: 10rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pop { position: absolute; right: 0; top: calc(100% + 0.35rem); z-index: 45; width: 19rem; padding: 0.75rem;
  display: flex; flex-direction: column; gap: 0.5rem; box-shadow: var(--shadow, 0 8px 24px rgba(0,0,0,.35)); }
.add { display: flex; gap: 0.4rem; }
.add .input { flex: 1; min-width: 0; }
.small { font-size: 0.78rem; margin: 0; }
.list { list-style: none; margin: 0; padding: 0; max-height: 50vh; overflow: auto; display: flex; flex-direction: column; gap: 0.1rem; }
.list li { display: flex; align-items: center; gap: 0.25rem; border-radius: var(--radius); }
.list li.on { background: var(--accent-soft); }
.open { flex: 1; min-width: 0; text-align: left; background: none; border: 0; color: var(--text); font: inherit; padding: 0.4rem 0.5rem;
  cursor: pointer; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; border-radius: var(--radius); }
.open:hover { background: var(--panel-2); }
.x { flex: none; }
@media (max-width: 640px) { .lbl { display: none; } .pop { right: auto; left: 0; width: min(19rem, calc(100vw - 2rem)); } }
</style>
