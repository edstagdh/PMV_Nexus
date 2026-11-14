<script setup>
// Owns the app's single video player (see stores/player.js). The player's
// element ("stage") is created here and then moved by hand into the video
// page's slot or into the floating mini-player's dock -- both are plain
// elements Vue doesn't render children into, so moving it never confuses Vue.
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } from "vue";
import Icon from "./Icon.vue";
import VideoPlayer from "./VideoPlayer.vue";
import {
  player, registerPlayerHost, placePlayer, closePlayer, expandPlayer, onVideoEnded, queueNext, queuePrev,
  savePlayerState, consumeResume,
} from "../stores/player.js";
import { session } from "../stores/session.js";
import { formatDuration } from "../lib/format.js";
import { resumePoint, itemDuration, setWatch } from "../stores/watchProgress.js";

const stage = ref(null);
const dock = ref(null);
const stash = ref(null);
const playerRef = ref(null);

const src = computed(() => (player.item ? `/api/library/${encodeURIComponent(player.item.id)}/stream` : null));
const filename = computed(() => player.item?.video_path?.split(/[\\/]/).pop());
const queue = computed(() => player.queue);
const title = computed(() => player.item?.title || player.item?.url || "");
// Top of the player in fullscreen: "PLAYLIST | TITLE" while a playlist plays (2.19.1).
const fullscreenTitle = computed(() => (queue.value?.name ? `${queue.value.name} | ${title.value}` : title.value));
const hasNext = computed(() => !!queue.value && (queue.value.index < queue.value.order.length - 1 || queue.value.repeat));

onMounted(() => registerPlayerHost({ mini: dock.value, stash: stash.value }));
watch(stage, (el) => { registerPlayerHost({ stage: el }); placePlayer(); }, { flush: "post" });
watch(() => player.mode, () => placePlayer(), { flush: "post" });

// Mini-player switched off in Settings while it's showing: stop it.
watch(() => session.prefs.miniPlayer, (on) => { if (on === false && player.mode === "mini") closePlayer(); });

function onReady(video) { registerPlayerHost({ video }); }
function onPlaying(on) { player.playing = on; savePlayerState(); }
// Where to start: after a page reload, the same moment (player.resume); otherwise where
// I left off last time (2.24.0, My Settings -> Video page), unless it's nearly finished.
const reloadResume = computed(() => (player.resume && player.resume.id === player.item?.id ? player.resume.time : null));
const resumeAt = computed(() => {
  if (reloadResume.value != null) return reloadResume.value;
  return session.prefs.resumePlayback === false ? null : resumePoint(player.item);
});
const duration = computed(() => itemDuration(player.item));

// "Continued at 3:12 · Start over" for a few seconds after continuing from last time.
const continued = ref(null);
let continuedTimer = null;
function onResumed(at) {
  const fromLastTime = reloadResume.value == null;
  consumeResume();
  if (!fromLastTime) return;
  continued.value = { id: player.item?.id, at };
  clearTimeout(continuedTimer);
  continuedTimer = setTimeout(() => { continued.value = null; }, 9000);
}
function startOver() {
  const id = continued.value?.id;
  continued.value = null;
  const v = playerRef.value?.video?.();
  if (v) { v.currentTime = 0; v.play().catch(() => {}); }
  if (id) {
    setWatch(id, null);
    fetch(`/api/library/${encodeURIComponent(id)}/position`, { method: "DELETE", credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" } }).catch(() => {});
  }
}
watch(() => player.item?.id, () => { continued.value = null; });
onBeforeUnmount(() => clearTimeout(continuedTimer));

// ---- dragging the mini-player (by its bar); the spot is remembered per browser ----
const POS_KEY = "pmv-mini-pos";
const box = ref(null);
const pos = reactive(readPos());
let drag = null;
let dragged = false;
function readPos() {
  try { const p = JSON.parse(localStorage.getItem(POS_KEY) || "null"); if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) return p; } catch { /* none */ }
  return { x: null, y: null }; // null = default corner (bottom right)
}
function clamp() {
  if (pos.x === null || !box.value) return;
  const r = box.value.getBoundingClientRect();
  pos.x = Math.min(Math.max(0, pos.x), Math.max(0, window.innerWidth - r.width));
  pos.y = Math.min(Math.max(0, pos.y), Math.max(0, window.innerHeight - r.height));
}
function startDrag(e) {
  if (e.button !== 0 || (e.target.closest("button") && !e.target.closest(".mini-title"))) return;
  const r = box.value.getBoundingClientRect();
  drag = { dx: e.clientX - r.left, dy: e.clientY - r.top, sx: e.clientX, sy: e.clientY };
  dragged = false;
  window.addEventListener("pointermove", onDrag);
  window.addEventListener("pointerup", endDrag, { once: true });
}
function onDrag(e) {
  if (!drag) return;
  if (!dragged && Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy) < 5) return; // a click, not a drag (yet)
  dragged = true;
  pos.x = e.clientX - drag.dx;
  pos.y = e.clientY - drag.dy;
  clamp();
}
function endDrag() {
  window.removeEventListener("pointermove", onDrag);
  drag = null;
  if (dragged) try { localStorage.setItem(POS_KEY, JSON.stringify({ x: pos.x, y: pos.y })); } catch { /* ignore */ }
}
function onTitleClick() {
  if (dragged) { dragged = false; return; } // the press ended a drag
  expandPlayer();
}
function resetPos() { pos.x = null; pos.y = null; try { localStorage.removeItem(POS_KEY); } catch { /* ignore */ } }
const boxStyle = computed(() => (pos.x === null ? {} : { left: `${pos.x}px`, top: `${pos.y}px`, right: "auto", bottom: "auto" }));
watch(() => player.mode, async (m) => { if (m === "mini") { await nextTick(); clamp(); } });
onMounted(() => window.addEventListener("resize", clamp));
onBeforeUnmount(() => { window.removeEventListener("resize", clamp); window.removeEventListener("pointermove", onDrag); });

</script>

<template>
  <div ref="stash" class="player-stash">
    <div v-if="player.item" ref="stage" class="player-stage">
      <VideoPlayer ref="playerRef" :src="src" :filename="filename" :item-id="player.item.id"
                   :title="fullscreenTitle" :shape="player.item.tech_resolution" :has-next="hasNext" @next="queueNext()"
                   :autoplay="player.autoplay" :queue-active="!!queue" :resume-at="resumeAt" :duration="duration" :global-keys="player.mode === 'page'" :compact="player.mode === 'mini'"
                   @ready="onReady" @playing="onPlaying" @ended="onVideoEnded" @autoplayed="player.autoplay = false"
                   @resumed="onResumed" @timeupdate="savePlayerState" />
      <div v-if="continued && player.mode === 'page'" class="continued" role="status">
        <span>Continued at {{ formatDuration(continued.at) }}</span>
        <button type="button" @click="startOver">Start over</button>
      </div>
    </div>
  </div>

  <div ref="box" class="mini card" :class="{ open: player.mode === 'mini', moved: pos.x !== null }" :style="boxStyle"
       role="region" aria-label="Mini player">
    <div ref="dock" class="mini-dock" />
    <div v-if="player.mode === 'mini'" class="mini-bar" title="Drag to move" @pointerdown="startDrag" @dblclick.self="resetPos">
      <button class="mini-title" :title="`Open the video page: ${title} (drag to move)`" @click="onTitleClick">
        <span class="t">{{ title }}</span>
        <span v-if="queue" class="q muted">{{ queue.name }} · {{ queue.index + 1 }}/{{ queue.order.length }}</span>
        <span v-else-if="player.item?.creator" class="q muted">{{ player.item.creator }}</span>
      </button>
      <template v-if="queue">
        <button class="btn btn-ghost btn-icon btn-sm" title="Previous" @click="queuePrev"><Icon name="skipPrev" :size="15" /></button>
        <button class="btn btn-ghost btn-icon btn-sm" title="Next" :disabled="queue.index >= queue.order.length - 1 && !queue.repeat" @click="queueNext()"><Icon name="skipNext" :size="15" /></button>
      </template>
      <button class="btn btn-ghost btn-icon btn-sm" title="Open the video page" @click="expandPlayer"><Icon name="expand" :size="15" /></button>
      <button class="btn btn-ghost btn-icon btn-sm" title="Close the player" @click="closePlayer"><Icon name="x" :size="16" /></button>
    </div>
  </div>
</template>

<style scoped>
.player-stage { position: relative; }
.continued { position: absolute; top: 0.6rem; left: 0.6rem; z-index: 5; display: flex; align-items: center; gap: 0.6rem;
  padding: 0.35rem 0.4rem 0.35rem 0.75rem; border-radius: 999px; background: rgb(0 0 0 / 0.7); color: #fff; font-size: 0.8rem;
  backdrop-filter: blur(6px); animation: fade 0.2s ease; }
.continued button { border: 0; border-radius: 999px; padding: 0.25rem 0.65rem; background: var(--accent); color: #fff; font: inherit;
  font-weight: 600; cursor: pointer; }
.player-stash { display: none; }
.mini { display: none; position: fixed; right: 1rem; bottom: 1rem; z-index: 90; width: min(24rem, calc(100vw - 2rem));
  overflow: hidden; box-shadow: var(--shadow); border-color: var(--border-strong); }
.mini.open { display: flex; flex-direction: column; }
.mini-dock :deep(.player) { border-radius: 0; }
/* Smaller controls in the small box; the volume slider opens from the speaker button. */
.mini-dock :deep(.skin) { max-height: 40vh; --media-scale-unit: 0.75rem; --media-border-radius: 0; }
.mini-bar { cursor: grab; touch-action: none; user-select: none; }
.mini-bar:active { cursor: grabbing; }
.mini-bar { display: flex; align-items: center; gap: 0.25rem; padding: 0.375rem 0.375rem 0.375rem 0.75rem; }
.mini-title { flex: 1; min-width: 0; display: flex; flex-direction: column; align-items: flex-start; border: 0; background: none; padding: 0;
  color: var(--text); font: inherit; text-align: left; cursor: pointer; }
.mini-title .t { width: 100%; font-size: 0.84rem; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mini-title .q { width: 100%; font-size: 0.72rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 56.25rem) {
  .mini { right: 0.5rem; bottom: 0.5rem; width: min(20rem, calc(100vw - 1rem)); }
}
/* Phones held upright: about half the screen's width, so it hides less of the page. */
@media (max-width: 40rem) and (orientation: portrait) {
  .mini { width: 46vw; }
  .mini-bar { padding: 0.25rem 0.25rem 0.25rem 0.5rem; gap: 0; }
  .mini-title .q { display: none; }
  .mini-bar .btn-icon { width: 1.75rem; height: 1.75rem; }
}
</style>
