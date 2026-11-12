<script setup>
// Pick the ThePornDB profile for a performer name: the candidates found so
// far, or a search. Used on the profile page and in Management ->
// Maintenance -> Performers (POST /api/performers/link | skip).
import { ref } from "vue";
import Icon from "./Icon.vue";
import { api, qs } from "../lib/api.js";
import { toast } from "../stores/toast.js";

const props = defineProps({ name: String, candidates: { type: Array, default: () => [] } });
const emit = defineEmits(["done"]);
const q = ref(props.name);
const results = ref(null);
const busy = ref("");

async function search() {
  busy.value = "search";
  try { results.value = (await api(`/api/performers/search?${qs({ q: q.value })}`)).results; }
  catch (err) { toast(err.message || "Search failed", { type: "error" }); }
  finally { busy.value = ""; }
}
async function pick(c) {
  busy.value = c.tpdb_id;
  try {
    await api("/api/performers/link", { method: "POST", body: { name: props.name, tpdb_id: c.tpdb_id } });
    toast(`“${props.name}” is ${c.name}${c.disambiguation ? ` (${c.disambiguation})` : ""}`, { type: "success" });
    emit("done");
  } catch (err) { toast(err.message || "Could not link", { type: "error" }); }
  finally { busy.value = ""; }
}
async function skip() {
  busy.value = "skip";
  try { await api("/api/performers/skip", { method: "POST", body: { name: props.name } }); toast(`Left “${props.name}” alone`); emit("done"); }
  catch (err) { toast(err.message || "That didn't work", { type: "error" }); }
  finally { busy.value = ""; }
}
const list = () => results.value || props.candidates;
</script>

<template>
  <div class="picker">
    <ul v-if="list().length" class="cands">
      <li v-for="c in list()" :key="c.tpdb_id">
        <img v-if="c.image" :src="c.image" alt="" loading="lazy" referrerpolicy="no-referrer">
        <span v-else class="noimg"><Icon name="user" :size="20" /></span>
        <span class="info">
          <strong>{{ c.name }}</strong><span v-if="c.disambiguation" class="muted"> ({{ c.disambiguation }})</span>
          <span class="muted small">{{ [c.gender, c.birthday, c.birthplace].filter(Boolean).join(" · ") }}</span>
          <span v-if="c.aliases?.length" class="muted small">Also: {{ c.aliases.slice(0, 6).join(", ") }}</span>
        </span>
        <a :href="c.url" target="_blank" rel="noopener noreferrer" class="btn btn-ghost btn-icon btn-sm" title="Open on ThePornDB"><Icon name="external" :size="14" /></a>
        <button class="btn btn-sm btn-primary" :disabled="!!busy" @click="pick(c)">{{ busy === c.tpdb_id ? "Linking…" : "This is her" }}</button>
      </li>
    </ul>
    <p v-else-if="results" class="muted small">Nothing found on ThePornDB.</p>
    <form class="search" @submit.prevent="search">
      <input v-model="q" class="input" placeholder="Search ThePornDB" aria-label="Search ThePornDB">
      <button class="btn btn-sm" :disabled="!!busy || !q.trim()">{{ busy === "search" ? "Searching…" : "Search" }}</button>
      <button type="button" class="btn btn-ghost btn-sm" :disabled="!!busy" title="No ThePornDB profile for this name" @click="skip">Skip</button>
    </form>
  </div>
</template>

<style scoped>
.picker { display: flex; flex-direction: column; gap: 0.5rem; }
.cands { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.375rem; }
.cands li { display: flex; align-items: center; gap: 0.625rem; padding: 0.375rem; border: 1px solid var(--border); border-radius: var(--radius); }
.cands img, .noimg { width: 3rem; height: 3rem; border-radius: 50%; object-fit: cover; flex-shrink: 0; background: var(--panel-2); display: grid; place-items: center; color: var(--text-dim); }
.info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 0.1rem; font-size: 0.875rem; }
.small { font-size: 0.76rem; }
.search { display: flex; gap: 0.5rem; flex-wrap: wrap; }
.search .input { flex: 1 1 12rem; height: 2.25rem; }
</style>
