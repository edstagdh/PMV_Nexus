<script setup>
// Upload page, after the video is in the library: choose its thumbnail --
// one of several frames spread over the video, any frame you pick with the
// slider, or your own image. Frames come from the server (GET .../frame), so
// this works for codecs the browser can't decode too.
import { ref, computed, onBeforeUnmount } from "vue";
import Icon from "../Icon.vue";
import { api } from "../../lib/api.js";
import { thumbUrl } from "../../lib/format.js";
import { toast } from "../../stores/toast.js";

const props = defineProps({ item: Object, duration: Number });
const emit = defineEmits(["update", "next"]);
const id = computed(() => encodeURIComponent(props.item.id));
const frameUrl = (t, w = 480) => `/api/library/${id.value}/frame?t=${t.toFixed(2)}&w=${w}`;

const COUNT = 8;
const candidates = computed(() => {
  const d = props.duration || 0;
  if (!d) return [];
  return Array.from({ length: COUNT }, (_, i) => +((d * (0.06 + (0.88 * i) / (COUNT - 1)))).toFixed(2));
});
const busy = ref(false);
const chosen = ref(null); // seconds of the frame last applied
const scrub = ref(0);
const scrubShown = ref(0);
let timer = null;
function onScrub(v) {
  scrub.value = Number(v);
  clearTimeout(timer);
  timer = setTimeout(() => { scrubShown.value = scrub.value; }, 200); // don't ask for a frame on every pixel
}
onBeforeUnmount(() => clearTimeout(timer));
const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}.${Math.floor((s % 1) * 10)}`;

async function useFrame(t) {
  busy.value = true;
  try {
    const it = await api(`/api/library/${id.value}/thumbnail`, { method: "POST", body: { source: "frame", seconds: t } });
    chosen.value = t;
    emit("update", it);
    toast("Thumbnail set", { type: "success" });
  } catch (err) { toast(err.message || "Could not set the thumbnail", { type: "error" }); }
  finally { busy.value = false; }
}
async function useImage(file) {
  if (!file) return;
  if (!/^image\/(jpeg|png|webp)$/.test(file.type)) { toast("Pick a JPEG, PNG or WebP image", { type: "error" }); return; }
  busy.value = true;
  try {
    const r = await fetch(`/api/library/${id.value}/thumbnail/image`, {
      method: "PUT", body: file, headers: { "Content-Type": file.type }, credentials: "same-origin",
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `Upload failed (${r.status})`);
    chosen.value = "image";
    emit("update", data);
    toast("Thumbnail set", { type: "success" });
  } catch (err) { toast(err.message || "Could not use that image", { type: "error" }); }
  finally { busy.value = false; }
}
</script>

<template>
  <section class="card panel">
    <div class="head">
      <h2>Thumbnail</h2>
      <span class="muted small">Step 1 of 2</span>
    </div>
    <div class="current">
      <figure>
        <figcaption class="muted small">Current</figcaption>
        <img v-if="thumbUrl(item)" :src="thumbUrl(item)" alt="Current thumbnail">
        <div v-else class="empty-thumb muted small">Being made…</div>
      </figure>
      <div class="current-text">
        <p class="help-text">Click a frame to use it, pick any moment with the slider, or upload your own picture.</p>
        <label class="btn btn-sm" :class="{ disabled: busy }">
          <Icon name="image" :size="15" />Upload an image…
          <input type="file" accept="image/jpeg,image/png,image/webp" class="sr-only" :disabled="busy" @change="useImage($event.target.files[0]); $event.target.value = ''">
        </label>
      </div>
    </div>

    <h3>Frames from the video</h3>
    <div class="tiles">
      <button v-for="t in candidates" :key="t" class="tile" :class="{ on: chosen === t }" :disabled="busy" :title="`Use the frame at ${fmt(t)}`" @click="useFrame(t)">
        <img :src="frameUrl(t, 360)" loading="lazy" :alt="`Frame at ${fmt(t)}`"><span>{{ fmt(t) }}</span>
      </button>
    </div>

    <h3>Any frame</h3>
    <div class="scrub">
      <img :src="frameUrl(scrubShown, 640)" class="scrub-img" alt="The frame at the slider's position">
      <div class="scrub-row">
        <input type="range" min="0" :max="Math.max(0, (duration || 0) - 0.1)" step="0.1" :value="scrub" aria-label="Position in the video"
               @input="onScrub($event.target.value)">
        <span class="pos">{{ fmt(scrub) }}</span>
        <button class="btn btn-sm btn-primary" :disabled="busy" @click="useFrame(scrub)">Use this frame</button>
      </div>
    </div>

    <div class="form-actions">
      <button class="btn btn-primary" @click="emit('next')">Next: preview clip<Icon name="chevron" :size="14" class="next-icon" /></button>
    </div>
  </section>
</template>

<style scoped>
.head { display: flex; align-items: baseline; justify-content: space-between; }
h2 { font-size: 1.05rem; margin: 0; }
h3 { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); margin: 0.5rem 0 0; }
.small { font-size: 0.8rem; }
.current { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; }
.current figure { margin: 0; display: flex; flex-direction: column; gap: 0.25rem; }
.current img, .empty-thumb { width: 16rem; max-width: 100%; aspect-ratio: 16 / 9; object-fit: contain; background: #000; border-radius: var(--radius); }
.empty-thumb { display: grid; place-items: center; }
.current-text { flex: 1; min-width: 14rem; display: flex; flex-direction: column; align-items: flex-start; gap: 0.5rem; }
.current-text p { margin: 0; }
.btn.disabled { opacity: 0.5; pointer-events: none; }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr)); gap: 0.5rem; }
.tile { position: relative; padding: 0; border: 2px solid transparent; border-radius: var(--radius); background: #000; cursor: pointer; overflow: hidden; }
.tile:hover { border-color: var(--border-strong); }
.tile.on { border-color: var(--accent); }
.tile img { display: block; width: 100%; aspect-ratio: 16 / 9; object-fit: contain; }
.tile span { position: absolute; right: 0.25rem; bottom: 0.25rem; padding: 0 0.3rem; border-radius: 0.25rem; background: rgb(0 0 0 / 0.7); color: #fff; font-size: 0.7rem; }
.scrub { display: flex; flex-direction: column; gap: 0.5rem; }
.scrub-img { width: 100%; max-width: 32rem; aspect-ratio: 16 / 9; object-fit: contain; background: #000; border-radius: var(--radius); }
.scrub-row { display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap; }
.scrub-row input { flex: 1; min-width: 12rem; }
.pos { font-variant-numeric: tabular-nums; font-size: 0.84rem; min-width: 4rem; }
.next-icon { transform: rotate(-90deg); }
</style>
