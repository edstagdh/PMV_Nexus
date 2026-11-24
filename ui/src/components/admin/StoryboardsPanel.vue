<script setup>
// Management -> Maintenance -> Seekbar previews (2.21.0): how many videos have them,
// the space they use, and "Make missing ones" (one job on Tasks).
import { t, tn } from "../../lib/i18n.js";
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
    toast(r.queued ? tn("Making seekbar previews for {n} video — follow it on Tasks", "Making seekbar previews for {n} videos — follow it on Tasks", r.queued) : t("Every video has them already"),
      { type: r.queued ? "success" : "info" });
    load();
  } catch (err) { toast(err.message || t("Couldn't start it"), { type: "error" }); }
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
        <h2>{{ $t("Seekbar previews") }}</h2>
        <p class="muted small">{{ $t("The small pictures above the player's seek bar when you point at it (or drag it on a phone): a frame every 2 seconds (less often in videos over ~13 minutes, at most 400 per video), kept in the cache folder. New videos get them by themselves; a video without them gets them the first time it's played. This makes them for the whole library at once, one video after the other at low priority — it can be stopped on Tasks.") }}</p>
        <p v-if="info" class="small">
          <Tx msg="{made} of {videos} videos have them · {size}" :params="{ videos: info.videos, size: formatBytes(info.size) }"><template #made><strong>{{ info.made }}</strong></template></Tx>
          <span v-if="info.running" class="pill pill-accent">{{ $t("making them now · {n} waiting", { n: info.queued }) }}</span>
        </p>
      </div>
      <button class="btn btn-sm" :disabled="busy || !info || !info.missing || info.running" @click="makeMissing">
        <Icon name="image" />{{ info?.missing ? $t("Make the {n} missing", { n: info.missing }) : $t("None missing") }}</button>
    </div>
  </section>
</template>
