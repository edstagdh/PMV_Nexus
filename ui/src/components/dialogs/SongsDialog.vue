<script setup>
// Video -> Find songs (2.14.0): listens to samples of the video (Shazam and / or
// Gemini AI, chosen per scan since 2.34.0) and lists what it heard to add to the video's Music.
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import ModalDialog from "../ModalDialog.vue";
import SongSuggestion from "../songs/SongSuggestion.vue";
import SongMethodPicker from "../songs/SongMethodPicker.vue";
import { api } from "../../lib/api.js";
import { onLibraryChange } from "../../stores/live.js";

const props = defineProps({ item: Object });
const emit = defineEmits(["close"]);
const data = ref(null);
const error = ref("");
const starting = ref(false);
const method = ref(null);   // this scan's way of finding songs; starts as the server's setting
const id = encodeURIComponent(props.item.id);
let timer = null;

async function load() {
  try {
    data.value = await api(`/api/library/${id}/songs`);
    if (!method.value) method.value = data.value.gemini || data.value.method === "shazam" ? data.value.method : "shazam";
    error.value = "";
  } catch (err) { error.value = err.message || "Could not load"; }
  clearTimeout(timer);
  if (data.value?.running) timer = setTimeout(load, 1500);
}
async function scan() {
  starting.value = true;
  try { await api(`/api/library/${id}/songs/scan`, { method: "POST", body: { method: method.value } }); await load(); }
  catch (err) { error.value = err.message || "Could not start"; }
  finally { starting.value = false; }
}
const running = computed(() => data.value?.running);
const last = computed(() => data.value?.last_scan);
const when = (t) => new Date(t * 1000).toLocaleString();

let stop = null;
onMounted(() => { load(); stop = onLibraryChange((c) => { if (!c || c.includes("songs")) load(); }); });
onBeforeUnmount(() => { clearTimeout(timer); stop?.(); });
</script>

<template>
  <ModalDialog :title="`Find songs: ${item.title || 'video'}`" width="44rem" @close="emit('close')">
    <p class="muted small lead">Samples of the video's sound are recognised by Shazam and / or Gemini, Google's AI (its finds are
      marked “Gemini (AI)”; it needs its API key in Server settings → Credentials). Only short sound samples are sent. What's found waits here and in Management → Songs.</p>
    <p v-if="error" class="error-text">{{ error }}</p>
    <div v-if="!data" class="skeleton" style="height: 4rem; border-radius: var(--radius)" />
    <template v-else>
      <section class="block">
        <h4>In its Music now</h4>
        <p v-if="!data.music.length" class="muted small">Nothing yet.</p>
        <ul v-else class="chips"><li v-for="m in data.music" :key="m" class="chip chip-music">{{ m }}</li></ul>
      </section>

      <section class="block">
        <div v-if="running" class="progress">
          <strong>{{ running.state === "waiting" ? "Waiting for another scan to finish…" : "Listening…" }}</strong>
          <span v-if="running.total" class="muted small">sample {{ running.done }} of {{ running.total }} · {{ running.found }} song(s) so far</span>
          <div class="bar" :class="{ indeterminate: !running.total }"><div :style="{ width: `${running.total ? (running.done / running.total) * 100 : 30}%` }" /></div>
        </div>
        <div v-else class="scan-row">
          <span v-if="last" class="muted small" :class="{ 'error-text': last.status === 'failed' }">
            Last listened {{ when(last.scanned_at) }}: {{ last.detail }}</span>
          <span v-else class="muted small">Not listened to yet.</span>
          <button class="btn btn-primary btn-sm" :disabled="starting || !data.scannable" @click="scan">{{ last ? "Listen again" : "Find songs" }}</button>
        </div>
        <SongMethodPicker v-if="!running && method" v-model="method" :gemini="data.gemini" label="Listen with" class="method" />
      </section>

      <section v-if="data.suggestions.length" class="block">
        <h4>Heard in this video</h4>
        <SongSuggestion v-for="s in data.suggestions" :key="s.id" :s="s" @done="load" />
      </section>
      <p v-else-if="last && !running" class="muted small">No new songs to suggest.</p>
    </template>
    <template #footer><button class="btn" @click="emit('close')">Close</button></template>
  </ModalDialog>
</template>

<style scoped>
.lead { margin: 0 0 0.75rem; }
.method { margin-top: 0.75rem; }
.small { font-size: 0.8rem; }
.block { display: flex; flex-direction: column; gap: 0.4rem; margin-bottom: 0.9rem; }
.block h4 { margin: 0; font-size: 0.9rem; }
.chips { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 0.35rem; }
.scan-row { display: flex; justify-content: space-between; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
.progress { display: flex; flex-direction: column; gap: 0.35rem; }
.bar { height: 6px; border-radius: 999px; background: var(--panel-hover); overflow: hidden; }
.bar > div { height: 100%; background: var(--accent); transition: width 0.4s; }
.bar.indeterminate > div { animation: slide 1.2s infinite ease-in-out; }
@keyframes slide { from { transform: translateX(-100%); } to { transform: translateX(330%); } }
</style>
