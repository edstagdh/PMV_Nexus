<script setup>
// Management -> Maintenance -> Shared scenes (3.9.0): videos that use the same footage, found by
// comparing the frames of their seekbar previews (backend/scene_match.py). Runs by itself after
// new seekbar previews; "Find now" reads the new ones and compares every pair (one job on Tasks).
import { t } from "../../lib/i18n.js";
import { ref, onMounted, onBeforeUnmount } from "vue";
import Icon from "../Icon.vue";
import { api } from "../../lib/api.js";
import { formatBytes, formatDateTime } from "../../lib/format.js";
import { toast } from "../../stores/toast.js";

const info = ref(null);
const busy = ref(false);
async function load() { try { info.value = await api("/api/maintenance/shared-scenes"); } catch { /* shown as "…" */ } }
async function run() {
  busy.value = true;
  try {
    const r = await api("/api/maintenance/shared-scenes", { method: "POST" });
    toast(r.started ? t("Finding shared scenes — follow it on Tasks") : t("Already running: it goes once more when done"), { type: "success" });
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
        <h2>{{ $t("Shared scenes") }}</h2>
        <p class="muted small">{{ $t("Videos that use the same footage, found by comparing the frames of their seekbar previews (black, white and nearly flat frames, the first and last seconds, and frames found in many videos — intros, logos — are left out). Shown on the video page, and they rank higher among related videos. It runs by itself after new seekbar previews are made; videos without seekbar previews aren't compared.") }}</p>
        <p v-if="info" class="small">
          {{ $t("{hashed} of {videos} videos read · {pairs} pairs share scenes · {size}", { hashed: info.hashed, videos: info.videos, pairs: info.pairs, size: formatBytes(info.size) }) }}
          <span v-if="info.with_previews < info.videos" class="muted"> · {{ $t("{n} without seekbar previews", { n: info.videos - info.with_previews }) }}</span>
          <span v-if="info.running" class="pill pill-accent">{{ $t("running now") }}</span>
        </p>
        <p v-if="info?.last" class="muted small">{{ $t("Last compared {time} ({s} s)", { time: formatDateTime(info.last.at), s: info.last.seconds }) }}</p>
      </div>
      <button class="btn btn-sm" :disabled="busy || !info || info.running" @click="run"><Icon name="search" />{{ $t("Find now") }}</button>
    </div>
  </section>
</template>
