<script setup>
// A performer's photos, one at a time, cross-fading to the next every few
// seconds (paused while the pointer is over it or the tab is hidden). Shows
// each photo whole (portrait ThePornDB photos aren't cropped). With more than
// one photo (2.17.2): arrows on the sides, drag / swipe sideways, the
// keyboard's ←/→ when focused, and the dots jump to a photo. Moving by hand
// restarts the timer, so the photo you picked stays a while.
import { ref, watch, onMounted, onBeforeUnmount } from "vue";
import Icon from "./Icon.vue";
import PerformerPlaceholder from "./PerformerPlaceholder.vue";

const props = defineProps({ photos: { type: Array, default: () => [] }, alt: String, interval: { type: Number, default: 5000 },
  gender: { type: String, default: "female" } }); // placeholder without photos (2.17.0)
const index = ref(0);
const loaded = ref(new Set([0]));
const box = ref(null);
let timer = null;
let paused = false;

function show(n) {
  if (!props.photos.length) return;
  index.value = (n + props.photos.length) % props.photos.length;
  // Preload the neighbours, so moving either way is instant.
  loaded.value = new Set([...loaded.value, index.value, (index.value + 1) % props.photos.length,
    (index.value - 1 + props.photos.length) % props.photos.length]);
}
function start() {
  stop();
  if (props.photos.length > 1) timer = setInterval(() => { if (!paused && !document.hidden && !drag.value) show(index.value + 1); }, props.interval);
}
function stop() { clearInterval(timer); timer = null; }
function go(step) { show(index.value + step); start(); }
watch(() => props.photos, () => { index.value = 0; loaded.value = new Set([0, 1]); start(); });
onMounted(() => { loaded.value = new Set([0, 1]); start(); });
onBeforeUnmount(stop);

// ---- drag / swipe: the photo follows the pointer, far enough (or a quick flick) moves on
const drag = ref(null); // { id, x, y, t, dx, sideways }
const offset = ref(0);
function down(e) {
  if (props.photos.length < 2 || e.button > 0 || e.target.closest("button")) return;
  drag.value = { id: e.pointerId, x: e.clientX, y: e.clientY, t: Date.now(), dx: 0, sideways: null };
}
function move(e) {
  const d = drag.value;
  if (!d || e.pointerId !== d.id) return;
  const dx = e.clientX - d.x;
  const dy = e.clientY - d.y;
  if (d.sideways === null && Math.abs(dx) + Math.abs(dy) > 6) {
    d.sideways = Math.abs(dx) > Math.abs(dy); // mostly up/down: let the page scroll instead
    if (d.sideways) box.value?.setPointerCapture?.(e.pointerId);
    else { drag.value = null; return; }
  }
  if (d.sideways) { d.dx = dx; offset.value = dx; }
}
function up(e) {
  const d = drag.value;
  if (!d || e.pointerId !== d.id) return;
  drag.value = null;
  const width = box.value?.clientWidth || 200;
  const fast = Math.abs(d.dx) > 25 && Date.now() - d.t < 250;
  if (d.sideways && (Math.abs(d.dx) > width * 0.2 || fast)) go(d.dx < 0 ? 1 : -1);
  offset.value = 0;
}
function key(e) {
  if (e.key === "ArrowRight") { e.preventDefault(); go(1); }
  else if (e.key === "ArrowLeft") { e.preventDefault(); go(-1); }
}
</script>

<template>
  <div ref="box" class="slides" :class="{ many: photos.length > 1, dragging: !!drag?.sideways }"
       :tabindex="photos.length > 1 ? 0 : undefined" :aria-label="photos.length > 1 ? `${alt || 'Photos'}: photo ${index + 1} of ${photos.length}` : undefined"
       @mouseenter="paused = true" @mouseleave="paused = false" @keydown="key"
       @pointerdown="down" @pointermove="move" @pointerup="up" @pointercancel="up">
    <template v-if="photos.length">
      <img v-for="(src, n) in photos" :key="src" :src="loaded.has(n) ? src : undefined" :alt="n === index ? alt : ''"
           :class="{ on: n === index }" :aria-hidden="n !== index" decoding="async" draggable="false"
           :style="n === index && offset ? { transform: `translateX(${offset}px)`, opacity: 1 - Math.min(0.6, Math.abs(offset) / 400) } : null">
      <template v-if="photos.length > 1">
        <button type="button" class="arrow prev" aria-label="Previous photo" @click="go(-1)"><Icon name="left" :size="22" /></button>
        <button type="button" class="arrow next" aria-label="Next photo" @click="go(1)"><Icon name="right" :size="22" /></button>
        <span class="count" aria-hidden="true">{{ index + 1 }} / {{ photos.length }}</span>
        <div class="dots" role="tablist" aria-label="Photos">
          <button v-for="(src, n) in photos" :key="src" class="dot" :class="{ on: n === index }" role="tab" :aria-selected="n === index"
                  :aria-label="`Photo ${n + 1}`" @click="show(n); start()" />
        </div>
      </template>
    </template>
    <PerformerPlaceholder v-else :gender="gender" />
  </div>
</template>

<style scoped>
/* Portrait frame (ThePornDB photos are mostly 2:3); a photo of another shape
   is shown whole on the dark background instead of being cropped. */
.slides { position: relative; width: 14rem; aspect-ratio: 2 / 3; border-radius: var(--radius-lg); overflow: hidden; background: #0d0d10; flex-shrink: 0; }
.slides.many { touch-action: pan-y; cursor: grab; user-select: none; }
.slides.dragging { cursor: grabbing; }
.slides:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.slides img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; opacity: 0; transition: opacity 1.2s ease, transform 0.25s ease; -webkit-user-drag: none; }
.slides img.on { opacity: 1; }
.slides.dragging img { transition: opacity 1.2s ease; } /* follow the pointer without lag */
.arrow { position: absolute; top: 50%; transform: translateY(-50%); width: 2.25rem; height: 2.75rem; display: grid; place-items: center;
  border: 0; border-radius: var(--radius); background: rgb(0 0 0 / 0.45); color: #fff; cursor: pointer; opacity: 0; transition: opacity 0.15s, background 0.15s; }
.arrow.prev { left: 0.375rem; }
.arrow.next { right: 0.375rem; }
.arrow:hover { background: rgb(0 0 0 / 0.7); }
.slides:hover .arrow, .slides:focus-within .arrow { opacity: 1; }
.count { position: absolute; top: 0.4rem; right: 0.45rem; font-size: 0.7rem; font-weight: 650; color: #fff; background: rgb(0 0 0 / 0.45);
  border-radius: 999px; padding: 0.05rem 0.45rem; opacity: 0; transition: opacity 0.15s; pointer-events: none; }
.slides:hover .count, .slides:focus-within .count { opacity: 1; }
/* Touch screens have no hover: keep the arrows and the count visible. */
@media (hover: none) { .arrow, .count { opacity: 1; } }
.dots { position: absolute; left: 0; right: 0; bottom: 0.5rem; display: flex; justify-content: center; gap: 0.375rem; flex-wrap: wrap; padding: 0 2.75rem; }
.dot { width: 0.5rem; height: 0.5rem; border-radius: 50%; border: 0; padding: 0; background: rgb(255 255 255 / 0.45); cursor: pointer; }
.dot.on { background: #fff; }
@media (prefers-reduced-motion: reduce) { .slides img { transition: none; } }
@media (max-width: 40rem) { .slides { width: 12rem; } }
</style>
