<script setup>
// The video player: Video.js v10 with the app's skin (lib/pmvSkin.js) --
// frosted controls, touch gestures (tap shows the controls, double-tap at
// the sides seeks, double-tap in the middle goes fullscreen), hotkeys, a
// settings menu, plus our Loop, A-B repeat and Download buttons, a Next button
// for playlists and a big centre play/pause button on touch screens. In
// fullscreen it shows the title at the top and, on touch screens, swiping up /
// down on the left / right side sets brightness / volume (My settings).
// There is one instance, owned by PlayerHost (see stores/player.js); it moves
// between the video page and the floating mini-player without reloading.
// The source is set on the <video> element directly -- or, with the 720p
// button (remembered in this browser) or when the browser can't play the
// file, the server converts it while watching (lib/livePlayback.js).
import { trackWatching } from "../lib/watchTracker.js";
import { reportPlaying } from "../lib/nowPlaying.js";
import { trackPosition } from "../lib/watchPosition.js";
import { startLive, wantsLive, setWantsLive } from "../lib/livePlayback.js";
import { toast } from "../stores/toast.js";
import { session } from "../stores/session.js";
import { ref, onMounted, onBeforeUnmount, watch } from "vue";
import "../lib/pmvSkin.js";

const props = defineProps({
  src: String, filename: String, itemId: String,
  autoplay: Boolean,     // start as soon as the source is ready (playlists)
  queueActive: Boolean,  // a playlist is playing: per-video loop is off so the next one can start
  resumeAt: { type: Number, default: null }, // continue from here (seconds), paused -- after a page reload
  globalKeys: Boolean,   // hotkeys anywhere on the page (video page) vs only when the player is focused
  compact: Boolean,      // the small floating player: fewer buttons
  title: String,         // shown at the top in fullscreen
  shape: String,         // "1920x1080" (from the library): turns the phone before the video has loaded
  hasNext: Boolean,      // a playlist has a next video (the Next button)
  duration: Number,      // seconds, from the library (saving my place works while converting too)
});
let stopWatch = null;
let stopReport = null;
let stopPosition = null;
const emit = defineEmits(["ended", "playing", "autoplayed", "ready", "resumed", "timeupdate", "next"]);
const videoEl = ref(null);
const skinEl = ref(null);
const aspect = ref("16 / 9");
const VOLUME_KEY = "pmv-volume";
const DEFAULT_VOLUME = 0.1;
const LOOP_KEY = "pmv-loop";
let loopBtn = null;
let downloadSrc = "";      // ⚙ -> Download (2.22.0)
// "720p" (converted while watching) in the ⚙ menu. Looked up each time: the menu is copied afresh
// when the player moves (pmvSkin refreshPopups), so a kept reference goes stale.
const qualityItem = () => skinEl.value?.part?.(".pmv-quality");
let nextBtn = null;
let live = null;          // the running conversion ({ stop, height }) or null
let liveToken = 0;        // ignores a conversion that finished starting after the source changed
let fellBack = false;     // already switched to converting because the original didn't play
// A-B repeat (2.18.0, shown when turned on in My settings): first click marks A,
// second marks B (then A..B plays over and over), third click clears it.
let abBtn = null;
let abA = null;
let abB = null;

const loopWanted = () => localStorage.getItem(LOOP_KEY) !== "off";

function renderLoop() {
  const v = videoEl.value;
  if (!loopBtn || !v) return;
  loopBtn.setAttribute("aria-pressed", v.loop ? "true" : "false");
  loopBtn.disabled = props.queueActive;
  const label = props.queueActive ? "Loop is off while a playlist plays (use its Repeat)" : v.loop ? "Loop: on" : "Loop: off";
  loopBtn.title = label;
  loopBtn.setAttribute("aria-label", label);
}
// While a playlist plays, Loop is off (the playlist has its own Repeat).
function applyLoop() {
  if (!videoEl.value) return;
  videoEl.value.loop = props.queueActive ? false : loopWanted();
  renderLoop();
}

const clock = (s) => { const t = Math.floor(s); const h = Math.floor(t / 3600); const m = Math.floor(t / 60) % 60; const ss = String(t % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`; };

function renderAb() {
  if (!abBtn) return;
  abBtn.hidden = !session.prefs.abRepeat;
  abBtn.dataset.state = abB !== null ? "ab" : abA !== null ? "a" : "";
  abBtn.setAttribute("aria-pressed", abB !== null ? "true" : "false");
  abBtn.firstElementChild.textContent = abB !== null ? "A-B" : abA !== null ? "A-" : "A-B";
  const label = abB !== null ? `Repeating ${clock(abA)} – ${clock(abB)} — click to stop`
    : abA !== null ? `A is ${clock(abA)} — click at the end of the part (B)` : "A-B repeat: click at the start of the part (A)";
  abBtn.title = label;
  abBtn.setAttribute("aria-label", label);
}
function clearAb() { abA = null; abB = null; renderAb(); }
function clickAb() {
  const t = videoEl.value?.currentTime || 0;
  if (abB !== null) clearAb();
  else if (abA === null) { abA = t; renderAb(); }
  else if (Math.abs(t - abA) < 0.5) toast("B needs to be at least half a second away from A", { type: "info" });
  else { [abA, abB] = t > abA ? [abA, t] : [t, abA]; renderAb(); }
}
// Back to A when playback reaches B (a seek well past B is left alone).
function checkAb() {
  const v = videoEl.value;
  if (abB === null || !v || v.seeking) return;
  if (v.currentTime >= abB && v.currentTime < abB + 1.5) v.currentTime = abA;
}

function renderQuality() {
  const qualityBtn = qualityItem();
  if (!qualityBtn) return;
  qualityBtn.hidden = !props.itemId;
  qualityBtn.setAttribute("aria-checked", live ? "true" : "false");
  qualityBtn.querySelector(".media-menu-hint-label").textContent = live ? (live.height === 720 ? "On" : `On · ${live.height}p`) : "Off";
  qualityBtn.title = live ? `Playing converted to ${live.height}p — click for the original`
    : "Play converted to 720p (less data, for slow connections; the server converts it while you watch)";
}
function downloadVideo() {
  if (!downloadSrc) return;
  const a = Object.assign(document.createElement("a"), { href: downloadSrc, download: props.filename || "" });
  document.body.appendChild(a);
  a.click();
  a.remove();
}
function renderNext() {
  if (!nextBtn) return;
  nextBtn.hidden = !props.queueActive;
  nextBtn.disabled = !props.hasNext;
  nextBtn.title = props.hasNext ? "Next video" : "This is the last video of the playlist";
}

function stopLive() {
  liveToken++;
  if (live) { live.stop(); live = null; }
  renderQuality();
}

/** Plays the converted stream from `at` seconds. */
async function goLive(at, reason) {
  const v = videoEl.value;
  stopLive();
  const token = liveToken;
  try {
    v.removeAttribute("src");
    const started = await startLive(v, props.itemId, at, (message) => {
      if (token !== liveToken) return;
      live = null;
      renderQuality();
      // A long pause ends the server's conversion: carry on from where it was.
      if (message.includes("paused")) goLive(v.currentTime);
      else { toast(`${message} — playing the original`, { type: "error" }); playOriginal(v.currentTime, !v.paused); }
    });
    if (token !== liveToken) { started.stop(); return; }
    live = started;
    if (reason) toast(reason, { timeout: 6000 });
  } catch (err) {
    if (token !== liveToken) return;
    toast(err.message || "Couldn't convert this video", { type: "error", timeout: 8000 });
    if (!reason) playOriginal(at, false);
  }
  renderQuality();
}

function playOriginal(at, play) {
  const v = videoEl.value;
  stopLive();
  v.src = props.src;
  v.load();
  if (at) v.addEventListener("loadedmetadata", () => { v.currentTime = at; }, { once: true });
  if (play) v.addEventListener("canplay", () => v.play().catch(() => {}), { once: true });
}

function toggleQuality() {
  const v = videoEl.value;
  const at = v.currentTime || 0;
  const playing = !v.paused;
  setWantsLive(!live);
  if (live) playOriginal(at, playing);
  else {
    goLive(at);
    if (playing) v.addEventListener("canplay", () => v.play().catch(() => {}), { once: true });
  }
}

// The browser can't play the file (e.g. HEVC in a browser without it): convert instead.
function onMediaError() {
  const v = videoEl.value;
  if (live || fellBack || !props.itemId || !v.error || ![3, 4].includes(v.error.code) || !v.getAttribute("src")) return;
  fellBack = true;
  goLive(v.currentTime || props.resumeAt || 0, "This browser can't play this video's format — converting it to 720p while you watch");
  if (props.autoplay) v.addEventListener("canplay", () => v.play().catch(() => {}), { once: true });
}

// Seekbar previews (2.21.0): the frames shown above the seek bar come from a
// thumbnails track (WebVTT, backend/storyboard.py). A video without them yet gets
// a 404 here -- and is made next, so they're there the next time it's played.
function setStoryboard(v) {
  v.querySelectorAll("track[data-storyboard]").forEach((t) => t.remove());
  if (!props.itemId) return;
  const track = document.createElement("track");
  Object.assign(track, { kind: "metadata", label: "thumbnails", default: true });
  track.dataset.storyboard = "";
  track.src = `/api/library/${encodeURIComponent(props.itemId)}/storyboard.vtt`;
  v.appendChild(track);
  if (track.track) track.track.mode = "hidden"; // load the cues without showing anything
}

function load(src) {
  const v = videoEl.value;
  if (!v || !src) return;
  // Playlist moving on in fullscreen on a phone: turn the screen for the next video
  // now, from its known size, so it doesn't swing to portrait while it loads (2.19.0).
  lockOrientation(shapeOf(props.shape) || wantedShape);
  emit("playing", false); // a new source starts paused (no "pause" event when the source is swapped)
  fellBack = false;
  clearAb();
  if (props.itemId && wantsLive()) goLive(props.resumeAt || 0);
  else {
    stopLive();
    v.src = src;
    v.load();
  }
  reassertOrientation();
  setStoryboard(v);
  downloadSrc = src;
  const mobile = window.matchMedia("(max-width: 64rem)").matches;
  const saved = parseFloat(localStorage.getItem(VOLUME_KEY));
  v.addEventListener("loadedmetadata", () => {
    // A browser that never changed the volume starts at 10 % (2.24.3; was 2 %). Phones play at
    // full volume and use their own volume buttons.
    v.volume = mobile ? 1 : Number.isFinite(saved) ? saved : DEFAULT_VOLUME;
    if (v.videoWidth && v.videoHeight) aspect.value = `${v.videoWidth} / ${v.videoHeight}`;
    // Sound but no picture: the browser can't decode the video track.
    else if (!live && props.itemId && !fellBack) { fellBack = true; goLive(0, "This browser can't show this video's picture — converting it to 720p while you watch"); }
    matchOrientation();
    reassertOrientation();
  }, { once: true });
  if (props.resumeAt) {
    const at = props.resumeAt;
    const converted = props.itemId && wantsLive(); // the converted stream starts at `at` by itself
    v.addEventListener("loadedmetadata", () => {
      if (!converted) v.currentTime = Math.min(at, Math.max(0, (v.duration || at) - 1));
      emit("resumed", at);
    }, { once: true });
  }
  if (props.autoplay) {
    v.addEventListener("canplay", () => {
      v.play().catch(() => { /* the browser blocked autoplay: the play button still works */ });
      emit("autoplayed");
    }, { once: true });
  }
  applyLoop();
}

// In fullscreen on a phone the screen is turned to suit the video (portrait for a
// vertical video, landscape otherwise) and kept there while a playlist swaps
// videos. The browser's own fullscreen-video rotation lets go of the lock while
// the next video loads (2.19.0 then stayed in portrait), so the wanted shape is
// asked for again whenever the screen turns away from it. Phones only allow
// this in fullscreen; elsewhere the call fails quietly.
function inFullscreen() {
  const fs = document.fullscreenElement || document.webkitFullscreenElement;
  return !!(fs && skinEl.value && (fs === skinEl.value || fs.contains(skinEl.value) || skinEl.value.contains(fs)));
}
const touchScreen = () => window.matchMedia("(pointer: coarse)").matches;
function shapeOf(resolution) {
  const m = /(\d+)\s*[x×]\s*(\d+)/.exec(resolution || "");
  return m ? (Number(m[2]) > Number(m[1]) ? "portrait" : "landscape") : null;
}
let wantedShape = null;   // what the video in fullscreen needs ("portrait" / "landscape")
let relockTimers = [];
function lockOrientation(shape) {
  if (!shape || !inFullscreen() || !touchScreen() || !screen.orientation?.lock) return;
  wantedShape = shape;
  // Locked even when the screen already is that way, so the browser doesn't turn it.
  screen.orientation.lock(shape).catch(() => { /* not supported here */ });
}
function matchOrientation() {
  const v = videoEl.value;
  if (v?.videoWidth) lockOrientation(v.videoHeight > v.videoWidth ? "portrait" : "landscape");
}
// Ask again soon after a new video starts (the browser may let go a moment later).
function reassertOrientation() {
  relockTimers.forEach(clearTimeout);
  relockTimers = [0, 400, 1200].map((ms) => setTimeout(() => { if (wantedShape) lockOrientation(wantedShape); }, ms));
}
function onScreenTurned() {
  if (wantedShape && inFullscreen() && !screen.orientation.type?.startsWith(wantedShape)) lockOrientation(wantedShape);
}

// ---- fullscreen: title at the top, orientation, swipe gestures ----
function onFullscreenChange() {
  const on = inFullscreen();
  const host = skinEl.value?.closest("video-player");
  if (on && props.title) host?.setAttribute("content-title", props.title);
  else host?.removeAttribute("content-title");
  if (on) matchOrientation();
  else { wantedShape = null; relockTimers.forEach(clearTimeout); }
  applyGestures();
}
watch(() => props.title, () => { if (inFullscreen()) onFullscreenChange(); });

// Phones / tablets, My settings -> Video page -> Sides of the video: "swipe" (default)
// = in fullscreen, swipe up / down on the left for brightness, on the right for
// volume, and double-tap there does nothing; "seek" = double-tap skips 10 s.
// Outside fullscreen the sides always double-tap to skip (a swipe there scrolls the page).
const swipeMode = () => session.prefs.touchSides !== "seek" && inFullscreen() && touchScreen();
function applyGestures() {
  const skin = skinEl.value;
  if (!skin) return;
  const on = swipeMode();
  skin.shadowRoot.querySelectorAll('media-gesture[type="doubletap"][action="seekStep"]').forEach((g) => { g.disabled = on; });
  skin.style.touchAction = on ? "none" : "";
}
let brightness = 1;         // kept while the page is open
let swipe = null;
let swipeShown = null;
function showSwipe(label, fraction) {
  const box = skinEl.value?.part(".pmv-swipe");
  if (!box) return;
  box.hidden = false;
  box.querySelector(".pmv-swipe-label").textContent = label;
  box.querySelector("i").style.width = `${Math.round(Math.max(0, Math.min(1, fraction)) * 100)}%`;
  clearTimeout(swipeShown);
  swipeShown = setTimeout(() => { box.hidden = true; }, 900);
}
function onSwipeDown(e) {
  if (e.pointerType !== "touch" || !swipeMode() || e.target.closest?.("media-controls-content")) return;
  const r = skinEl.value.getBoundingClientRect();
  const x = (e.clientX - r.left) / r.width;
  const side = x < 1 / 3 ? "brightness" : x > 2 / 3 ? "volume" : null;
  if (!side) return;
  const v = videoEl.value;
  swipe = { id: e.pointerId, side, y: e.clientY, x: e.clientX, h: r.height, start: side === "volume" ? (v.muted ? 0 : v.volume) : brightness, moving: false };
}
function onSwipeMove(e) {
  if (!swipe || e.pointerId !== swipe.id) return;
  const dy = swipe.y - e.clientY;
  if (!swipe.moving) {
    if (Math.abs(dy) < 12 || Math.abs(dy) < Math.abs(e.clientX - swipe.x)) return;
    swipe.moving = true;
  }
  e.preventDefault();
  const value = swipe.start + dy / (swipe.h * 0.6); // 60% of the height = the whole range
  const v = videoEl.value;
  if (swipe.side === "volume") {
    const vol = Math.max(0, Math.min(1, value));
    v.muted = vol === 0;
    v.volume = vol;
    // iPhones and iPads don't let pages change the volume.
    if (Math.abs(v.volume - vol) > 0.01) showSwipe("Use the volume buttons", v.volume);
    else showSwipe(`Volume ${Math.round(vol * 100)}%`, vol);
  } else {
    brightness = Math.max(0.2, Math.min(1.5, value));
    v.style.filter = brightness === 1 ? "" : `brightness(${brightness})`;
    showSwipe(`Brightness ${Math.round(brightness * 100)}%`, (brightness - 0.2) / 1.3);
  }
}
function onSwipeEnd(e) {
  if (!swipe || e.pointerId !== swipe.id) return;
  const moved = swipe.moving;
  swipe = null;
  if (moved) e.stopPropagation(); // not a tap: leave the controls as they are
}

let lastTick = 0;
const onTime = () => { const now = Date.now(); if (now - lastTick > 2000) { lastTick = now; emit("timeupdate"); } };
const onVolume = () => {
  const v = videoEl.value;
  if (v && !v.muted) try { localStorage.setItem(VOLUME_KEY, String(v.volume)); } catch { /* ignore */ }
};

onMounted(async () => {
  await customElements.whenDefined("pmv-video-skin");
  const skin = skinEl.value;
  if (!skin) return;
  loopBtn = skin.part(".pmv-loop");

  skin.shadowRoot.addEventListener("click", (e) => {
    if (e.target.closest?.(".pmv-quality")) toggleQuality();
    else if (e.target.closest?.(".pmv-download")) downloadVideo();
  }, true);
  renderQuality();
  nextBtn = skin.part(".pmv-next");
  nextBtn?.addEventListener("click", () => { if (props.hasNext) emit("next"); });
  renderNext();
  // Captured before the skin's own tap / double-tap handling inside it.
  skin.addEventListener("pointerdown", onSwipeDown, true);
  skin.addEventListener("pointermove", onSwipeMove, { capture: true, passive: false });
  skin.addEventListener("pointerup", onSwipeEnd, true);
  skin.addEventListener("pointercancel", onSwipeEnd, true);
  document.addEventListener("fullscreenchange", onFullscreenChange);
  document.addEventListener("webkitfullscreenchange", onFullscreenChange);
  abBtn = skin.part(".pmv-ab");
  abBtn?.addEventListener("click", clickAb);
  renderAb();
  loopBtn?.addEventListener("click", () => {
    if (props.queueActive) return;
    const v = videoEl.value;
    v.loop = !v.loop;
    try { localStorage.setItem(LOOP_KEY, v.loop ? "on" : "off"); } catch { /* ignore */ }
    renderLoop();
  });
  skin.setGlobalHotkeys(props.globalKeys);
  const v = videoEl.value;
  v.addEventListener("playing", () => { emit("playing", true); reassertOrientation(); });
  screen.orientation?.addEventListener?.("change", onScreenTurned);
  v.addEventListener("pause", () => emit("playing", false));
  v.addEventListener("ended", () => { if (abB === null) emit("ended"); });
  v.addEventListener("timeupdate", onTime);
  v.addEventListener("timeupdate", checkAb);
  // Reaching B right at the end of the video: start A again instead of ending.
  v.addEventListener("ended", () => { if (abB !== null) { v.currentTime = abA; v.play().catch(() => {}); } });
  v.addEventListener("seeked", () => emit("timeupdate"));
  v.addEventListener("volumechange", onVolume);
  v.addEventListener("error", onMediaError);
  stopWatch = trackWatching(v, () => props.itemId);  // watched time for the Stats page
  stopReport = reportPlaying(v, () => props.itemId); // Currently Playing (just the video, never who)
  stopPosition = trackPosition(v, () => props.itemId, () => props.duration); // my place in it (2.24.0)
  load(props.src);
  emit("ready", v);
});
watch(() => props.src, (src) => load(src));
watch(() => props.queueActive, () => { applyLoop(); renderNext(); });
watch(() => props.hasNext, renderNext);
watch(() => session.prefs.touchSides, applyGestures);
watch(() => session.prefs.abRepeat, (on) => { if (!on) clearAb(); else renderAb(); });
watch(() => props.globalKeys, (on) => skinEl.value?.setGlobalHotkeys?.(on));
defineExpose({ video: () => videoEl.value });
onBeforeUnmount(() => {
  screen.orientation?.removeEventListener?.("change", onScreenTurned);
  relockTimers.forEach(clearTimeout);
  document.removeEventListener("fullscreenchange", onFullscreenChange);
  document.removeEventListener("webkitfullscreenchange", onFullscreenChange);
  clearTimeout(swipeShown);
  stopWatch?.();
  stopReport?.();
  stopPosition?.();
  stopLive();
  try { videoEl.value?.pause(); videoEl.value?.removeAttribute("src"); videoEl.value?.load(); } catch { /* already gone */ }
});
</script>

<template>
  <div class="player" :style="{ '--ar': aspect }">
    <video-player>
      <pmv-video-skin ref="skinEl" class="skin" :compact.attr="compact || null">
        <video ref="videoEl" :data-item-id="itemId" playsinline preload="metadata" />
      </pmv-video-skin>
    </video-player>
  </div>
</template>

<style scoped>
.player { border-radius: var(--radius-lg); overflow: hidden; background: #000; }
/* The box takes the video's own shape, never taller than 78% of the window:
   a portrait video then gets black bars at the sides and the controls always
   stay inside the box. In fullscreen the skin fills the screen itself. */
.skin {
  display: block; width: 100%; aspect-ratio: var(--ar, 16 / 9); max-height: 78vh;
  --media-accent-color: var(--accent); --media-border-radius: var(--radius-lg); --media-border-color: transparent;
}
.skin:not(:defined) { visibility: hidden; }
</style>
