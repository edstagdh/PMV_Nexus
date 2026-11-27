<script setup>
// Shows stores/flyIn.js: the picture starts where the wall left it, waits (up to 4 s) for the video
// page's player box (.player-slot), slides and grows onto it, then fades out. Click hides it.
import { ref, watch, onBeforeUnmount } from "vue";
import { flyIn, endFlyIn } from "../stores/flyIn.js";

const box = ref(null);   // {top, left, width, height} now shown
const moving = ref(false);
const fading = ref(false);
let timers = [];
let frame = 0;
const clear = () => { timers.forEach(clearTimeout); timers = []; cancelAnimationFrame(frame); };

function target() {
  const el = document.querySelector(".player-slot");
  const r = el?.getBoundingClientRect();
  // only once the player is in it (an empty slot is a placeholder box of another height)
  if (!r || r.width < 80 || !el.firstElementChild) return null;
  // the player may not have its height yet: a 16:9 box of its width until it has
  const height = r.height >= 80 ? r.height : r.width * 9 / 16;
  return { top: r.top, left: r.left, width: r.width, height };
}

watch(() => flyIn.id, () => {
  clear();
  if (!flyIn.src || !flyIn.rect) return;
  moving.value = false;
  fading.value = false;
  box.value = { ...flyIn.rect };
  const started = performance.now();
  const look = () => {
    const to = target();
    if (to) {
      moving.value = true;
      box.value = to;
      timers.push(setTimeout(() => { fading.value = true; }, flyIn.ms));
      timers.push(setTimeout(() => { box.value = null; endFlyIn(); }, flyIn.ms + 400));
      follow();
    } else if (performance.now() - started < 4000) {
      frame = requestAnimationFrame(look);
    } else {
      fading.value = true;
      timers.push(setTimeout(() => { box.value = null; endFlyIn(); }, 400));
    }
  };
  // a moment first, so the starting place is drawn before it moves (else there's nothing to slide from)
  timers.push(setTimeout(() => { frame = requestAnimationFrame(look); }, 60));
});
function follow() {   // the player can still change size (its video's shape arrives): keep aiming at it
  const to = target();
  if (to && box.value && (Math.abs(to.top - box.value.top) > 3 || Math.abs(to.height - box.value.height) > 3
      || Math.abs(to.left - box.value.left) > 3 || Math.abs(to.width - box.value.width) > 3)) box.value = to;
  if (box.value && !fading.value) frame = requestAnimationFrame(follow);
}
function hide() { clear(); box.value = null; endFlyIn(); }
onBeforeUnmount(clear);
</script>

<template>
  <img v-if="box && flyIn.src" :src="flyIn.src" alt="" class="fly-in" :class="{ fading }"
       :style="{ top: box.top + 'px', left: box.left + 'px', width: box.width + 'px', height: box.height + 'px',
                 transitionDuration: moving ? `${flyIn.ms}ms, ${flyIn.ms}ms, ${flyIn.ms}ms, ${flyIn.ms}ms, 400ms` : '0ms' }"
       @click="hide">
</template>

<style scoped>
.fly-in { position: fixed; z-index: 180; object-fit: cover; border-radius: var(--radius-lg); background: #000;
  box-shadow: 0 1rem 3rem rgba(0, 0, 0, 0.5); transition-property: top, left, width, height, opacity;
  transition-timing-function: cubic-bezier(0.2, 0.8, 0.2, 1); }
.fly-in.fading { opacity: 0; }
@media (prefers-reduced-motion: reduce) { .fly-in { display: none; } }
</style>
