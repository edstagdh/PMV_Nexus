<script setup>
// Currently Playing (2.20.0, permission live.view): the videos someone is playing
// right now, so people see what others enjoy. Only the videos -- never who
// plays them, where they are in them or since when. Updates by itself.
import { ref, onMounted, onBeforeUnmount } from "vue";
import VideoCard from "../components/VideoCard.vue";
import { api } from "../lib/api.js";
import { onLibraryChange } from "../stores/live.js";

const items = ref(null);
const error = ref("");
let busy = false;
let again = false;

async function load() {
  if (busy) { again = true; return; }
  busy = true;
  try { items.value = (await api("/api/now-playing")).items; error.value = ""; }
  catch (err) { error.value = err.message || "Could not load what's playing"; }
  finally {
    busy = false;
    if (again) { again = false; load(); }
  }
}
onMounted(load);
const stopLive = onLibraryChange((changed) => { if (!changed || changed.includes("playing")) load(); });
const poll = setInterval(load, 30000); // in case a live update was missed
onBeforeUnmount(() => { stopLive(); clearInterval(poll); });
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>Currently Playing</h1>
        <p>Videos being played right now — see what others are into. It updates by itself; it never shows who is
          watching, where they are in the video or since when.</p>
      </div>
    </header>

    <p v-if="error" class="error-text">{{ error }}</p>
    <div v-else-if="!items" class="grid"><div v-for="n in 3" :key="n" class="skeleton" style="aspect-ratio: 16 / 12; border-radius: var(--radius-lg)" /></div>
    <template v-else-if="items.length">
      <p class="count muted">{{ items.length }} {{ items.length === 1 ? "video" : "videos" }} playing</p>
      <TransitionGroup name="np" tag="div" class="grid">
        <div v-for="v in items" :key="v.id" class="np-item"><VideoCard :item="v" /></div>
      </TransitionGroup>
    </template>
    <div v-else class="card empty"><div class="empty-icon">📺</div><p>Nothing is playing right now. Videos show up here as soon as someone plays one.</p></div>
  </div>
</template>

<style scoped>
.count { margin: 0 0 0.75rem; font-size: 0.875rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(15.625rem, 1fr)); gap: 1.375rem 1rem; }
@media (max-width: 40rem) { .grid { grid-template-columns: repeat(auto-fill, minmax(min(100%, 13.75rem), 1fr)); } }
.np-enter-active, .np-leave-active { transition: opacity 0.3s, transform 0.3s; }
.np-enter-from, .np-leave-to { opacity: 0; transform: scale(0.97); }
.np-move { transition: transform 0.3s; }
</style>
