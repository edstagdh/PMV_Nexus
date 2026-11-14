<script setup>
// The row of frames under the player (3.0.1, backend/video_frames.py): small
// pictures from across the video with their time -- clicking one jumps there.
// The site's own timeline frames when the video has them, else made from the
// file. A video without them yet gets them now (the server queues it first);
// this asks again every few seconds until they're there.
import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import Icon from "./Icon.vue";
import { api } from "../lib/api.js";
import { formatDuration } from "../lib/format.js";
import { player, playerVideo, seekPlayer } from "../stores/player.js";
import { toast } from "../stores/toast.js";

const props = defineProps({
  itemId: { type: String, required: true },
  canRedo: Boolean,
});

const data = ref(null);    // { pending, frames: [{seconds, url}], width, height, source }
const now = ref(-1);       // the player's time while it shows this video
let retry = null;
let tries = 0;
let ticker = null;

async function load() {
  clearTimeout(retry);
  const id = props.itemId;
  try {
    const r = await api(`/api/library/${encodeURIComponent(id)}/frames`);
    if (id !== props.itemId) return;
    data.value = r;
    if (r.pending && tries++ < 40) retry = setTimeout(load, tries < 6 ? 2500 : 6000);
  } catch {
    if (id === props.itemId) data.value = null;
  }
}

watch(() => props.itemId, () => { data.value = null; tries = 0; now.value = -1; load(); });
onMounted(() => {
  load();
  ticker = setInterval(() => {
    const v = playerVideo();
    now.value = v && player.item?.id === props.itemId ? v.currentTime || 0 : -1;
  }, 500);
});
onBeforeUnmount(() => { clearTimeout(retry); clearInterval(ticker); });

const frames = computed(() => data.value?.frames || []);
const portrait = computed(() => (data.value?.height || 0) > (data.value?.width || 0));
// The frame the player is in now (the last one at or before its time).
const current = computed(() => {
  if (now.value <= 0 || !frames.value.length) return -1;
  let at = -1;
  frames.value.forEach((f, i) => { if (f.seconds <= now.value + 0.25) at = i; });
  return at;
});

async function redo() {
  try {
    await api(`/api/library/${encodeURIComponent(props.itemId)}/frames/redo`, { method: "POST" });
    data.value = { pending: true, frames: [] };
    tries = 0;
    retry = setTimeout(load, 2500);
  } catch (err) { toast(err.message || "Couldn't do it", { type: "error" }); }
}
</script>

<template>
  <section v-if="frames.length || data?.pending" class="frames" :class="{ portrait }" aria-label="Video frames">
    <div class="frames-row" :style="{ '--n': frames.length || 8 }">
      <template v-if="frames.length">
        <button v-for="(f, i) in frames" :key="f.url" type="button" class="frame" :class="{ on: i === current }"
                :aria-label="`Jump to ${formatDuration(f.seconds)}`" :title="`Jump to ${formatDuration(f.seconds)}`"
                @click="seekPlayer(f.seconds)">
          <span class="pic"><img :src="f.url" alt="" loading="lazy" decoding="async"></span>
          <span class="time">{{ formatDuration(f.seconds) }}</span>
        </button>
      </template>
      <template v-else>
        <div v-for="n in 8" :key="n" class="frame placeholder" aria-hidden="true"><span class="pic skeleton" /><span class="time">&nbsp;</span></div>
      </template>
    </div>
    <button v-if="canRedo && frames.length" class="btn btn-ghost btn-sm redo" type="button"
            :title="data?.source === 'site' ? 'From PMVHaven\'s video timeline — get them again' : 'Made from the video file — make them again'"
            @click="redo"><Icon name="image" :size="14" />{{ data?.source === "site" ? "From the site" : "From the file" }} · redo</button>
  </section>
</template>

<style scoped>
.frames { margin-top: 0.75rem; display: flex; flex-direction: column; gap: 0.25rem; }
.frames-row { display: grid; grid-template-columns: repeat(var(--n), minmax(0, 1fr)); gap: 0.5rem; }
.frame { display: flex; flex-direction: column; gap: 0.3rem; padding: 0; border: 0; background: none; color: inherit; cursor: pointer; min-width: 0; font: inherit; }
.pic { display: block; aspect-ratio: 16 / 9; border-radius: var(--radius); overflow: hidden; background: var(--panel-2); outline: 2px solid transparent; outline-offset: 1px;
  transition: outline-color 0.15s, transform 0.15s; }
.portrait .pic { aspect-ratio: 9 / 16; }
.pic img { width: 100%; height: 100%; object-fit: cover; display: block; transition: filter 0.15s; }
.time { font-size: 0.78rem; font-variant-numeric: tabular-nums; text-align: center; color: var(--text-dim); }
.frame:hover .pic img { filter: brightness(1.12); }
.frame:hover .pic { transform: translateY(-1px); }
.frame:focus-visible { outline: none; }
.frame:focus-visible .pic { outline-color: var(--accent); }
.frame.on .pic { outline-color: var(--accent); }
.frame.on .time { color: var(--accent); font-weight: 600; }
.placeholder { cursor: default; }
.redo { align-self: flex-end; font-size: 0.75rem; opacity: 0.7; }
.redo:hover { opacity: 1; }
/* Phones / narrow windows: one scrollable row of fixed-size frames. */
@media (max-width: 640px) {
  .frames-row { grid-template-columns: none; grid-auto-flow: column; grid-auto-columns: 6rem; overflow-x: auto;
    scroll-snap-type: x proximity; padding-bottom: 0.25rem; }
  .portrait .frames-row { grid-auto-columns: 4.25rem; }
  .frame { scroll-snap-align: start; }
}
</style>
