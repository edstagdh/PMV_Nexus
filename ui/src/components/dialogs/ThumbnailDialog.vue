<script setup>
import { ref, onMounted, onBeforeUnmount } from "vue";
import ModalDialog from "../ModalDialog.vue";
import { api } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";

const props = defineProps({ item: Object });
const emit = defineEmits(["close"]);
const id = encodeURIComponent(props.item.id);
const options = ref(null);
const error = ref("");
const staged = ref(null);
const busy = ref(false);
const stamp = ref(Date.now());

const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const fmtPrecise = (s) => `${fmt(s)}.${String(Math.floor((s % 1) * 10))}`;
// "timeline": the site's scrubber frames; "video": any frame of the downloaded file.
const mode = ref("timeline");
const player = ref(null);
const position = ref(0);
const duration = ref(0);

async function load() {
  try {
    options.value = await api(`/api/library/${id}/thumbnail-options`);
    stamp.value = Date.now();
    if (!options.value.timeline_thumbnails?.length) mode.value = "video";
  } catch (err) { error.value = err.message || "Could not load thumbnail options"; }
}
function step(seconds) {
  const v = player.value;
  if (!v) return;
  v.pause();
  v.currentTime = Math.min(Math.max(0, v.currentTime + seconds), duration.value || v.currentTime + seconds);
}
function seek(e) { if (player.value) { player.value.currentTime = Number(e.target.value); } }
function pickTimeline(t) { staged.value = { kind: "timeline", ...t }; }
function pickFrame() {
  const v = player.value;
  if (!v) return;
  v.pause();
  let preview = null;
  try { // a still of the chosen frame, so you see what will be saved
    const c = document.createElement("canvas");
    c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext("2d").drawImage(v, 0, 0);
    preview = c.toDataURL("image/jpeg", 0.85);
  } catch { /* the server grabs the frame anyway */ }
  staged.value = { kind: "frame", seconds: v.currentTime, preview };
}
function confirmPick() {
  const st = staged.value;
  if (st.kind === "frame") apply({ source: "frame", seconds: st.seconds }, "Thumbnail updated");
  else apply({ source: "timeline", url: st.url, seconds: st.seconds }, "Thumbnail updated");
}
onBeforeUnmount(() => {
  // Let go of the file (Windows keeps it locked while a page has it open).
  const v = player.value;
  if (v) { v.pause(); v.removeAttribute("src"); v.load(); }
});
async function apply(body, message) {
  busy.value = true;
  error.value = "";
  try {
    await api(`/api/library/${id}/thumbnail`, { method: "POST", body });
    toast(message, { type: "success" });
    staged.value = null;
    await load();
  } catch (err) {
    error.value = err.message || "Could not update the thumbnail";
  } finally {
    busy.value = false;
  }
}
onMounted(load);
</script>

<template>
  <ModalDialog title="Change thumbnail" width="56rem" @close="emit('close')">
    <div class="top">
      <figure>
        <figcaption>Current{{ options?.thumbnail_source === "custom" ? " (custom)" : "" }}</figcaption>
        <img :src="`/api/library/${id}/thumbnail?_=${stamp}`" alt="">
        <button v-if="options?.thumbnail_source === 'custom'" class="btn btn-sm" :disabled="busy"
                @click="apply({ source: 'original' }, 'Reverted to the original thumbnail')">Revert to original</button>
      </figure>
      <figure v-if="staged">
        <figcaption>Selected: {{ staged.kind === "frame" ? `video frame at ${fmtPrecise(staged.seconds)}` : `timeline frame (${fmt(staged.seconds)})` }}</figcaption>
        <img v-if="staged.kind === 'timeline'" :src="staged.url" alt="">
        <img v-else-if="staged.preview" :src="staged.preview" alt="">
        <div v-else class="frame-note">The picture at {{ fmtPrecise(staged.seconds) }} will be saved as the thumbnail.</div>
      </figure>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <nav class="seg" role="tablist">
      <button class="btn btn-sm" :class="{ active: mode === 'timeline' }" @click="mode = 'timeline'">Site timeline frames</button>
      <button class="btn btn-sm" :class="{ active: mode === 'video' }" @click="mode = 'video'">Pick a frame from the video</button>
    </nav>
    <template v-if="mode === 'timeline'">
      <div v-if="!options && !error" class="muted">Loading timeline frames…</div>
      <div v-else-if="options && !options.timeline_thumbnails?.length" class="muted">No timeline frames available for this video — pick a frame from the video instead.</div>
      <div v-else-if="options" class="tiles">
        <button v-for="t in options.timeline_thumbnails" :key="t.url" class="tile" :class="{ on: staged?.url === t.url }" @click="pickTimeline(t)">
          <img :src="t.url" loading="lazy" :alt="`Frame at ${fmt(t.seconds)}`"><span>{{ fmt(t.seconds) }}</span>
        </button>
      </div>
    </template>
    <div v-else class="frame-picker">
      <video ref="player" :src="`/api/library/${id}/stream`" :data-item-id="item.id" preload="metadata" playsinline muted controls
             @loadedmetadata="duration = $event.target.duration" @timeupdate="position = $event.target.currentTime" @seeked="position = $event.target.currentTime" />
      <input type="range" min="0" :max="duration || 0" step="0.04" :value="position" aria-label="Position in the video" @input="seek">
      <div class="frame-controls">
        <button class="btn btn-sm" title="Back 1 second" @click="step(-1)">−1s</button>
        <button class="btn btn-sm" title="Back one frame" @click="step(-1 / 25)">‹ frame</button>
        <span class="pos">{{ fmtPrecise(position) }} / {{ fmt(duration) }}</span>
        <button class="btn btn-sm" title="Forward one frame" @click="step(1 / 25)">frame ›</button>
        <button class="btn btn-sm" title="Forward 1 second" @click="step(1)">+1s</button>
        <button class="btn btn-primary btn-sm" @click="pickFrame">Use this frame</button>
      </div>
    </div>
    <template #footer>
      <button class="btn" @click="emit('close')">Close</button>
      <button class="btn btn-primary" :disabled="!staged || busy"
              @click="confirmPick">
        {{ busy ? "Applying…" : "Set as thumbnail" }}
      </button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.top { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 0.875rem; }
figure { margin: 0; display: flex; flex-direction: column; gap: 0.375rem; width: min(100%, 16rem); }
figcaption { font-size: 0.78rem; color: var(--text-dim); }
figure img { width: 100%; aspect-ratio: 16 / 9; object-fit: contain; border-radius: var(--radius); background: #000; }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(9.5rem, 1fr)); gap: 0.5rem; }
.tile { position: relative; padding: 0; border: 2px solid transparent; border-radius: var(--radius); overflow: hidden; background: #000; cursor: pointer; }
.tile.on { border-color: var(--accent); }
.tile img { display: block; width: 100%; aspect-ratio: 16 / 9; object-fit: contain; }
.tile span { position: absolute; right: 0.25rem; bottom: 0.25rem; background: rgba(0, 0, 0, 0.75); color: #fff; font-size: 0.69rem; padding: 0 0.3rem; border-radius: 0.25rem; }
.error { color: var(--danger); font-size: 0.84rem; }
.seg { display: flex; gap: 0.25rem; margin-bottom: 0.75rem; flex-wrap: wrap; }
.frame-picker { display: flex; flex-direction: column; gap: 0.5rem; }
.frame-picker video { width: 100%; max-height: 50vh; background: #000; border-radius: var(--radius); }
.frame-picker input[type=range] { width: 100%; accent-color: var(--accent); }
.frame-controls { display: flex; align-items: center; gap: 0.375rem; flex-wrap: wrap; }
.pos { font-family: var(--mono); font-size: 0.8rem; min-width: 7.5rem; text-align: center; }
.frame-note { aspect-ratio: 16 / 9; display: grid; place-items: center; text-align: center; padding: 0.75rem; font-size: 0.8rem; color: var(--text-2); background: var(--panel-2); border-radius: var(--radius); }
</style>
