<script setup>
// Pick a folder on the server's disk (GET /api/settings/browse-dirs).
// An empty path lists the drives (Windows) / the filesystem root.
import { ref, onMounted } from "vue";
import ModalDialog from "../ModalDialog.vue";
import { api, qs } from "../../lib/api.js";

const props = defineProps({ start: String, title: { type: String, default: "Choose a folder" } });
const emit = defineEmits(["close", "pick"]);
const current = ref("");
const parent = ref(null);
const entries = ref([]);
const jump = ref("");
const error = ref("");
const newName = ref("");
const creating = ref(false);

async function open(path) {
  error.value = "";
  try {
    const d = await api(`/api/settings/browse-dirs?${qs({ path })}`);
    current.value = d.current;
    parent.value = d.parent;
    entries.value = d.entries;
    jump.value = d.current;
  } catch (err) {
    error.value = err.message || "Could not open that folder";
  }
}
onMounted(() => open(props.start || ""));

const nameOf = (p) => p.split(/[\\/]/).filter(Boolean).pop() || p;

async function mkdir() {
  if (!newName.value.trim() || !current.value) return;
  try {
    await api("/api/settings/browse-dirs/mkdir", { method: "POST", body: { parent_path: current.value, name: newName.value.trim() } });
    newName.value = "";
    creating.value = false;
    open(current.value);
  } catch (err) {
    error.value = err.message || "Could not create that folder";
  }
}
</script>

<template>
  <ModalDialog :title="title" width="40rem" @close="emit('close')">
    <form class="jump" @submit.prevent="open(jump)">
      <input v-model="jump" class="input mono" placeholder="Type a path and press Enter" aria-label="Path">
      <button class="btn btn-sm" type="submit">Go</button>
    </form>
    <div class="where">
      <button class="btn btn-ghost btn-sm" :disabled="!current" title="Up one level" @click="open(parent || '')">↑ Up</button>
      <button class="btn btn-ghost btn-sm" title="Drives / root" @click="open('')">Drives</button>
      <span class="mono path">{{ current || "Choose a drive" }}</span>
    </div>
    <p v-if="error" class="error-text">{{ error }}</p>
    <ul class="list scroll-thin">
      <li v-for="e in entries" :key="e"><button type="button" @click="open(e)">📁 {{ nameOf(e) }}</button></li>
      <li v-if="!entries.length" class="none">No subfolders here.</li>
    </ul>
    <form v-if="creating" class="jump" @submit.prevent="mkdir">
      <input v-model="newName" class="input" placeholder="New folder name" aria-label="New folder name" autofocus>
      <button class="btn btn-sm" type="submit">Create</button>
      <button class="btn btn-ghost btn-sm" type="button" @click="creating = false">Cancel</button>
    </form>
    <template #footer>
      <button v-if="current && !creating" class="btn btn-ghost" @click="creating = true">+ New folder</button>
      <span style="flex: 1" />
      <button class="btn" @click="emit('close')">Cancel</button>
      <button class="btn btn-primary" :disabled="!current" @click="emit('pick', current)">Use this folder</button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.jump { display: flex; gap: 0.5rem; margin-bottom: 0.625rem; }
.jump .input { flex: 1; min-width: 0; }
.where { display: flex; align-items: center; gap: 0.375rem; margin-bottom: 0.5rem; }
.path { color: var(--text-2); }
.list { list-style: none; margin: 0; padding: 0.25rem; max-height: 50vh; overflow-y: auto; border: 1px solid var(--border); border-radius: var(--radius); }
.list button { width: 100%; text-align: left; border: 0; background: none; color: var(--text); padding: 0.45rem 0.6rem; border-radius: var(--radius-sm); cursor: pointer; font: inherit; font-size: 0.875rem; }
.list button:hover { background: var(--panel-2); }
.none { padding: 0.75rem; color: var(--text-dim); font-size: 0.84rem; }
</style>
