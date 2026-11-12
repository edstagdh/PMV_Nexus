<script setup>
// Upload page: the hover preview is a few short clips from the video joined
// together. Suggested start times come from the server; each has a slider
// (kept inside the video and clear of its neighbours), ▶ plays that clip, and
// "Play all" plays them in a row -- at low volume, with its own slider.
// "Make the preview" builds the clip from these times.
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import Icon from "../Icon.vue";
import { api } from "../../lib/api.js";
import { previewUrl } from "../../lib/format.js";
import { toast } from "../../stores/toast.js";

const props = defineProps({ item: Object, plan: Object });
const emit = defineEmits(["update", "back", "finish"]);
const id = computed(() => encodeURIComponent(props.item.id));
const clip = computed(() => props.plan?.clip_seconds || 2);
const duration = computed(() => props.plan?.duration || 0);
const times = ref([...(props.plan?.timestamps || [])]);
const video = ref(null);
const volume = ref(0.05);
const playing = ref(-1); // index of the clip playing, -1 = none
const playAll = ref(false);
const busy = ref(false);
const made = ref(false);
let stopAt = null;

const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}.${Math.floor((s % 1) * 10)}`;
// Each clip stays within the video and doesn't overlap the ones next to it.
const lo = (i) => (i === 0 ? 0 : times.value[i - 1] + clip.value);
const hi = (i) => (i === times.value.length - 1 ? Math.max(0, duration.value - clip.value) : times.value[i + 1] - clip.value);
function setTime(i, v) {
  const t = Math.min(Math.max(Number(v), lo(i)), Math.max(lo(i), hi(i)));
  times.value[i] = Math.round(t * 10) / 10;
  if (playing.value === i) playClip(i);
}
function addClip() {
  // Into the biggest gap.
  const edges = [0, ...times.value.flatMap((t) => [t, t + clip.value]), duration.value];
  let best = null;
  for (let k = 0; k < edges.length; k += 2) {
    const gap = edges[k + 1] - edges[k];
    if (gap >= clip.value * 3 && (!best || gap > best.gap)) best = { gap, at: edges[k] + (gap - clip.value) / 2 };
  }
  if (!best) { toast("No room for another clip", { type: "error" }); return; }
  times.value = [...times.value, Math.round(best.at * 10) / 10].sort((a, b) => a - b);
}
function removeClip(i) { if (times.value.length > 1) times.value = times.value.filter((_, j) => j !== i); }

function onTime() {
  const v = video.value;
  if (!v || playing.value < 0 || stopAt === null || v.currentTime < stopAt) return;
  if (playAll.value && playing.value < times.value.length - 1) playClip(playing.value + 1, true);
  else stop();
}
function playClip(i, all = false) {
  const v = video.value;
  if (!v) return;
  playAll.value = all;
  playing.value = i;
  stopAt = times.value[i] + clip.value;
  v.volume = volume.value;
  v.currentTime = times.value[i];
  v.play().catch(() => {});
}
function stop() { video.value?.pause(); playing.value = -1; playAll.value = false; stopAt = null; }
function setVolume(v) { volume.value = Number(v); if (video.value) { video.value.volume = volume.value; video.value.muted = false; } }

async function make() {
  stop();
  busy.value = true;
  try {
    const it = await api(`/api/library/${id.value}/preview`, { method: "POST", body: { timestamps: times.value, clip_seconds: clip.value } });
    made.value = true;
    emit("update", it);
    toast("Preview made", { type: "success" });
  } catch (err) { toast(err.message || "Could not make the preview", { type: "error" }); }
  finally { busy.value = false; }
}
onMounted(() => { if (video.value) video.value.volume = volume.value; });
onBeforeUnmount(() => { const v = video.value; if (v) { v.pause(); v.removeAttribute("src"); v.load(); } });
</script>

<template>
  <section class="card panel">
    <div class="head">
      <h2>Preview clip</h2>
      <span class="muted small">Step 2 of 2</span>
    </div>
    <p class="help-text">The preview that plays when you hover a video is made of {{ times.length }} clips of {{ clip }} seconds.
      Move them to the best moments, play them to check, then make the preview.</p>
    <p v-if="!duration" class="error-text">The video's length couldn't be read, so the clips can't be placed.</p>
    <template v-else>
      <div class="stage">
        <video ref="video" :src="`/api/library/${id}/stream`" :data-item-id="item.id" preload="metadata" playsinline @timeupdate="onTime" />
        <div class="vol" title="Volume while checking the clips">
          <Icon name="volume" :size="16" />
          <input type="range" min="0" max="1" step="0.01" :value="volume" aria-label="Volume" @input="setVolume($event.target.value)">
          <output>{{ Math.round(volume * 100) }}%</output>
        </div>
      </div>

      <div class="timeline" aria-hidden="true">
        <span v-for="(t, i) in times" :key="i" class="mark" :class="{ on: playing === i }"
              :style="{ left: `${(t / duration) * 100}%`, width: `${Math.max(0.6, (clip / duration) * 100)}%` }" />
      </div>

      <ol class="clips">
        <li v-for="(t, i) in times" :key="i" :class="{ on: playing === i }">
          <span class="n">{{ i + 1 }}</span>
          <button class="btn btn-icon btn-sm" :title="playing === i ? 'Stop' : `Play clip ${i + 1}`" @click="playing === i ? stop() : playClip(i)">
            <Icon :name="playing === i ? 'pause' : 'play'" :size="15" />
          </button>
          <input type="range" min="0" :max="Math.max(0, duration - clip)" step="0.1" :value="t" :aria-label="`Start of clip ${i + 1}`"
                 @input="setTime(i, $event.target.value)">
          <span class="pos">{{ fmt(t) }}</span>
          <button class="btn btn-ghost btn-icon btn-sm" title="Remove this clip" :disabled="times.length <= 1" @click="removeClip(i)"><Icon name="x" :size="14" /></button>
        </li>
      </ol>
      <div class="row-actions">
        <button class="btn btn-sm" @click="playAll && playing >= 0 ? stop() : playClip(0, true)"><Icon :name="playAll && playing >= 0 ? 'pause' : 'play'" :size="14" />{{ playAll && playing >= 0 ? "Stop" : "Play all clips" }}</button>
        <button class="btn btn-sm btn-ghost" :disabled="times.length >= 10" @click="addClip"><Icon name="plus" :size="14" />Add a clip</button>
      </div>

      <div v-if="made && previewUrl(item)" class="result">
        <span class="muted small">The new preview:</span>
        <video :src="previewUrl(item)" autoplay loop muted playsinline class="result-video" />
      </div>
    </template>

    <div class="form-actions">
      <button class="btn" @click="emit('back')">Back</button>
      <button class="btn" :class="made ? '' : 'btn-primary'" :disabled="busy || !duration" @click="make">{{ busy ? "Making…" : made ? "Make it again" : "Make the preview" }}</button>
      <button class="btn" :class="made ? 'btn-primary' : 'btn-ghost'" :disabled="busy" @click="emit('finish')">{{ made ? "Done" : "Keep the automatic preview" }}</button>
    </div>
  </section>
</template>

<style scoped>
.head { display: flex; align-items: baseline; justify-content: space-between; }
h2 { font-size: 1.05rem; margin: 0; }
.small { font-size: 0.8rem; }
.stage { position: relative; max-width: 40rem; }
.stage video { display: block; width: 100%; aspect-ratio: 16 / 9; background: #000; border-radius: var(--radius); }
.vol { position: absolute; right: 0.5rem; bottom: 0.5rem; display: flex; align-items: center; gap: 0.375rem; padding: 0.25rem 0.5rem;
  border-radius: 999px; background: rgb(0 0 0 / 0.6); color: #fff; font-size: 0.75rem; }
.vol input { width: 6rem; }
.vol output { min-width: 2.25rem; text-align: right; font-variant-numeric: tabular-nums; }
.timeline { position: relative; height: 0.5rem; border-radius: 1rem; background: var(--panel-2); max-width: 40rem; }
.mark { position: absolute; top: 0; bottom: 0; border-radius: 1rem; background: var(--text-dim); }
.mark.on { background: var(--accent); }
.clips { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.25rem; max-width: 40rem; }
.clips li { display: flex; align-items: center; gap: 0.5rem; padding: 0.125rem 0.25rem; border-radius: var(--radius); }
.clips li.on { background: var(--accent-soft); }
.clips input { flex: 1; min-width: 6rem; }
.n { width: 1.25rem; text-align: center; color: var(--text-dim); font-size: 0.78rem; }
.pos { min-width: 3.75rem; font-variant-numeric: tabular-nums; font-size: 0.84rem; }
.row-actions { display: flex; gap: 0.5rem; flex-wrap: wrap; }
.result { display: flex; flex-direction: column; gap: 0.25rem; }
.result-video { width: 20rem; max-width: 100%; aspect-ratio: 16 / 9; background: #000; border-radius: var(--radius); }
</style>
