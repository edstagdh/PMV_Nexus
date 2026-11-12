<script setup>
// Plays two or more videos side by side, with one set of controls that
// plays, pauses and seeks all of them together.
import { ref, watch, onBeforeUnmount } from "vue";
import ModalDialog from "./ModalDialog.vue";
import { itemUrlTitle } from "../lib/format.js";

const props = defineProps({ items: Array });
const emit = defineEmits(["close"]);
const players = ref([]);
const position = ref(0);
const duration = ref(0);
const playing = ref(false);
const audioFrom = ref(0);
// One volume for the video you're listening to; starts at 5% and is remembered.
const VOLUME_KEY = "pmv-compare-volume";
function savedVolume() {
  try { const v = Number(localStorage.getItem(VOLUME_KEY)); return Number.isFinite(v) && v >= 0 && v <= 1 && localStorage.getItem(VOLUME_KEY) !== null ? v : 0.05; }
  catch { return 0.05; }
}
const volume = ref(savedVolume());
function applyVolume() { all().forEach((v) => { v.volume = volume.value; }); }
watch(volume, (v) => { applyVolume(); try { localStorage.setItem(VOLUME_KEY, String(v)); } catch { /* not critical */ } });

const all = () => players.value.filter(Boolean);
function onMeta(e) { e.target.volume = volume.value; duration.value = Math.max(...all().map((v) => v.duration || 0)); }
function onTime(i, e) { if (i === 0) position.value = e.target.currentTime; }
function toggle() {
  playing.value = !playing.value;
  all().forEach((v) => (playing.value ? v.play().catch(() => {}) : v.pause()));
}
function seek(e) { const t = Number(e.target.value); all().forEach((v) => { v.currentTime = Math.min(t, v.duration || t); }); }
function jump(delta) { all().forEach((v) => { v.currentTime = Math.max(0, v.currentTime + delta); }); }
function resync() { const t = all()[0]?.currentTime || 0; all().forEach((v) => { v.currentTime = t; }); }
const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

onBeforeUnmount(() => all().forEach((v) => { v.pause(); v.removeAttribute("src"); v.load(); }));
</script>

<template>
  <ModalDialog title="Compare side by side" width="96rem" @close="emit('close')">
    <div class="grid" :style="{ gridTemplateColumns: `repeat(${Math.min(items.length, 3)}, minmax(0, 1fr))` }">
      <figure v-for="(it, i) in items" :key="it.id">
        <video :ref="(el) => (players[i] = el)" :src="`/api/library/${encodeURIComponent(it.id)}/stream`" :data-item-id="it.id"
               preload="metadata" playsinline :muted="audioFrom !== i" @loadedmetadata="onMeta" @timeupdate="onTime(i, $event)"
               @ended="playing = false" />
        <figcaption>
          <span class="num">#{{ i + 1 }}</span> {{ itemUrlTitle(it) }}
          <span class="muted">· {{ it.tech_resolution }} · {{ it.tech_filesize }}</span>
          <button class="btn btn-ghost btn-sm" :class="{ active: audioFrom === i }" title="Hear this one" @click="audioFrom = i">🔊</button>
        </figcaption>
      </figure>
    </div>
    <div class="controls">
      <button class="btn btn-primary btn-sm" @click="toggle">{{ playing ? "Pause all" : "Play all" }}</button>
      <button class="btn btn-sm" @click="jump(-5)">−5s</button>
      <input type="range" min="0" :max="duration || 0" step="0.1" :value="position" aria-label="Position" @input="seek">
      <button class="btn btn-sm" @click="jump(5)">+5s</button>
      <span class="pos">{{ fmt(position) }} / {{ fmt(duration) }}</span>
      <button class="btn btn-sm" title="Move all to the first video's position" @click="resync">Re-sync</button>
      <label class="vol" :title="`Volume of #${audioFrom + 1}`">
        <span aria-hidden="true">{{ volume === 0 ? "🔇" : "🔊" }}</span>
        <input v-model.number="volume" type="range" min="0" max="1" step="0.01" aria-label="Volume">
        <output>{{ Math.round(volume * 100) }}%</output>
      </label>
    </div>
  </ModalDialog>
</template>

<style scoped>
.grid { display: grid; gap: 0.75rem; }
figure { margin: 0; display: flex; flex-direction: column; gap: 0.375rem; min-width: 0; }
video { width: 100%; aspect-ratio: 16 / 9; background: #000; border-radius: var(--radius); object-fit: contain; }
figcaption { font-size: 0.8rem; display: flex; align-items: center; gap: 0.375rem; min-width: 0; }
.num { font-weight: 800; color: var(--accent); }
.controls { display: flex; align-items: center; gap: 0.5rem; margin-top: 0.75rem; }
.controls input { flex: 1; accent-color: var(--accent); }
.pos { font-family: var(--mono); font-size: 0.8rem; }
.vol { display: inline-flex; align-items: center; gap: 0.375rem; font-size: 0.8rem; }
.controls .vol input { flex: none; width: 7rem; }
.vol output { min-width: 2.5rem; font-variant-numeric: tabular-nums; }
@media (max-width: 56rem) { .grid { grid-template-columns: 1fr !important; } }
</style>
