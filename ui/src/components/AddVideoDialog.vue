<script setup>
import { ref, computed } from "vue";
import ModalDialog from "./ModalDialog.vue";
import { api } from "../lib/api.js";
import { toast } from "../stores/toast.js";

const emit = defineEmits(["close"]);
const text = ref("");
const busy = ref(false);
const results = ref([]);
const urls = computed(() => text.value.split(/\s+/).map((s) => s.trim()).filter(Boolean));

async function submit() {
  if (!urls.value.length || busy.value) return;
  busy.value = true;
  results.value = [];
  let added = 0;
  for (const url of urls.value) {
    try {
      await api("/api/library", { method: "POST", body: { url } });
      results.value.push({ url, ok: true });
      added++;
    } catch (err) {
      results.value.push({ url, ok: false, message: err.message });
    }
  }
  busy.value = false;
  if (added) toast(`Added ${added} video${added === 1 ? "" : "s"} — see Tasks for progress`, { type: "success" });
  if (added === urls.value.length) emit("close");
}
</script>

<template>
  <ModalDialog title="Add videos" @close="emit('close')">
    <label class="field">
      <span>Video page URLs (one per line)</span>
      <textarea v-model="text" class="input textarea" rows="5" autofocus
                placeholder="https://pmvhaven.com/video/..." @keydown.ctrl.enter="submit" />
    </label>
    <ul v-if="results.length" class="results">
      <li v-for="r in results" :key="r.url" :class="r.ok ? 'ok' : 'bad'">
        {{ r.ok ? "Added" : "Failed" }} — <span class="muted">{{ r.url }}</span><span v-if="r.message">: {{ r.message }}</span>
      </li>
    </ul>
    <template #footer>
      <button class="btn" @click="emit('close')">Cancel</button>
      <button class="btn btn-primary" :disabled="!urls.length || busy" @click="submit">
        {{ busy ? "Adding…" : urls.length > 1 ? `Add ${urls.length} videos` : "Add video" }}
      </button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.textarea { height: auto; padding: 0.625rem 0.75rem; resize: vertical; font-family: var(--mono); font-size: 0.8125rem; }
.results { margin: 0.75rem 0 0; padding: 0; list-style: none; font-size: 0.8125rem; display: flex; flex-direction: column; gap: 0.25rem; overflow-wrap: anywhere; }
.results .ok { color: var(--success); }
.results .bad { color: var(--danger); }
</style>
