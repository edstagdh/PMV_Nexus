<script setup>
// Management -> Maintenance -> Seekbar previews (2.21.0): how many videos have them,
// the space they use, and "Make missing ones" (one job on Tasks).
import { ref, onMounted, onBeforeUnmount } from "vue";
import Icon from "../Icon.vue";
import { api } from "../../lib/api.js";
import { formatBytes } from "../../lib/format.js";
import { toast } from "../../stores/toast.js";

const info = ref(null);
const busy = ref(false);
async function load() { try { info.value = await api("/api/maintenance/storyboards"); } catch { /* shown as "…" */ } }
async function makeMissing() {
  busy.value = true;
  try {
    const r = await api("/api/maintenance/storyboards", { method: "POST" });
    toast(r.queued ? `Making seekbar previews for ${r.queued} ${r.queued === 1 ? "video" : "videos"} — follow it on Tasks` : "Every video has them already",
      { type: r.queued ? "success" : "info" });
    load();
  } catch (err) { toast(err.message || "Couldn't start it", { type: "error" }); }
  finally { busy.value = false; }
}
onMounted(load);
const timer = setInterval(() => { if (info.value?.running) load(); }, 5000);
onBeforeUnmount(() => clearInterval(timer));
</script>

<template>
  <section class="card block">
    <div class="block-head">
      <div>
        <h2>Seekbar previews</h2>
        <p class="muted small">The small pictures above the player's seek bar when you point at it (or drag it on a phone): a frame every
          2 seconds (less often in videos over ~13 minutes, at most 400 per video), kept in the cache folder. New videos get them by
          themselves; a video without them gets them the first time it's played. This makes them for the whole library at once, one video
          after the other at low priority — it can be stopped on Tasks.</p>
        <p v-if="info" class="small">
          <strong>{{ info.made }}</strong> of {{ info.videos }} videos have them · {{ formatBytes(info.size) }}
          <span v-if="info.running" class="pill pill-accent">making them now · {{ info.queued }} waiting</span>
        </p>
      </div>
      <button class="btn btn-sm" :disabled="busy || !info || !info.missing || info.running" @click="makeMissing">
        <Icon name="image" />{{ info?.missing ? `Make the ${info.missing} missing` : "None missing" }}</button>
    </div>
  </section>
</template>
