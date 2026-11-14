// The one video player of the app (components/PlayerHost.vue) and its play
// queue. The player is never re-created while a video plays: on a video page
// its element sits in the page's player slot; when you leave the page while
// it plays, the SAME element moves into the floating mini-player, so playback
// continues while you browse. Opening another video replaces it.
//
// Moving a playing <video> to another place in the document doesn't stop it
// as long as it's moved synchronously (remove + insert in one go), which is
// why leavePage() moves it by hand before Vue removes the page.
import { reactive, watch } from "vue";
import { api } from "../lib/api.js";
import { session } from "./session.js";

export const player = reactive({
  item: null,         // the library item loaded in the player
  mode: null,         // "page" (inside a video page) | "mini" (floating) | null (closed)
  playing: false,
  autoplay: false,    // start the next source as soon as it loads (playlist / "Play all")
  queue: null,        // { playlistId, name, items: {id: item}, ids (playlist order), order (play order), index, shuffle, repeat, autoplay }
  queueEnded: false,  // reached the end of the queue (no repeat)
  resume: null,       // { id, time }: where to continue after a page reload (paused)
});

// DOM elements, set by PlayerHost / VideoView (not reactive on purpose).
const els = { stage: null, mini: null, slot: null, video: null, stash: null };
let navigate = () => {};

export function registerPlayerHost({ stage, mini, video, stash }) {
  if (stash !== undefined) els.stash = stash;
  if (stage !== undefined) els.stage = stage;
  if (mini !== undefined) els.mini = mini;
  if (video !== undefined) els.video = video;
}
/** App.vue passes router.push here (the store can't import the router: circular import). */
export function setNavigator(fn) { navigate = fn; }

/** Puts the player element where it belongs right now (synchronously). */
export function placePlayer() {
  if (!player.item || !els.stage?.isConnected) return; // closed: Vue has removed (or is removing) the element
  const target = player.mode === "page" && els.slot ? els.slot : els.mini;
  if (els.stage && target && els.stage.parentNode !== target) { target.appendChild(els.stage); moved(); }
}
// The skin's popups (volume, menus) need relinking after a move (lib/pmvSkin.js).
function moved() { els.stage?.querySelector("pmv-video-skin")?.refreshPopups?.(); }

/** The video page is switching to another video: keep the player out of the
 * way (hidden, still in the document) until the new one is ready. */
export function parkPlayer() {
  els.slot = null;
  if (els.stage && els.mini && els.stage.parentNode !== els.mini) { els.mini.appendChild(els.stage); moved(); }
}

/** A video page with a playable `item` is showing, with `slot` as the player's place. */
export function openOnPage(item, slot) {
  els.slot = slot;
  const q = player.queue;
  if (q) {
    const at = q.order.indexOf(item.id);
    if (at === -1) clearQueue();          // a video outside the playlist ends the playlist
    else { q.index = at; player.queueEnded = false; }
  }
  if (player.item?.id !== item.id) player.item = item;
  else {
    player.item = { ...player.item, ...item }; // same video: keep playing, refresh its details
    if (player.autoplay) { player.autoplay = false; els.video?.play().catch(() => {}); }
  }
  player.mode = "page";
  placePlayer();
}

/** The video page is about to go away: keep playing in the mini-player, or stop. */
export function leavePage() {
  els.slot = null;
  if (!player.item) return;
  // Settings -> Video page -> Mini-player off: leaving the page stops the video.
  const keep = session.prefs.miniPlayer !== false && (player.playing || (player.queue && !player.queueEnded));
  if (keep) {
    player.mode = "mini";
    placePlayer(); // before Vue removes the page, so the video isn't interrupted
  } else {
    closePlayer();
  }
}

export function closePlayer() {
  try { els.video?.pause(); } catch { /* gone */ }
  // Put the element back where Vue created it before Vue removes it.
  if (els.stage && els.stash && els.stage.parentNode !== els.stash) els.stash.appendChild(els.stage);
  player.item = null;
  player.mode = null;
  player.playing = false;
  player.autoplay = false;
  clearQueue();
}

/** The player's <video> element (or null), e.g. for the frames under it (3.2.0). */
export function playerVideo() { return els.video; }

/** Jumps the player to `seconds` and plays from there (the frames under the player, 3.2.0). */
export function seekPlayer(seconds) {
  const v = els.video;
  if (!v || !Number.isFinite(seconds)) return;
  v.currentTime = Math.max(0, seconds);
  if (v.paused) v.play().catch(() => {});
}

export function expandPlayer() {
  if (player.item) navigate({ name: "video", params: { id: player.item.id } });
}

// ------------------------------------------------------------------ queue
function shuffled(ids) {
  const a = [...ids];
  for (let i = a.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/** Plays `playlist` (from GET /api/playlists/{id}) starting at `startId` (default: first, or random with shuffle). */
export function playPlaylist(playlist, { startId = null, shuffle = false } = {}) {
  const items = playlist.items || [];
  if (!items.length) return;
  const ids = items.map((i) => i.id);
  let order = ids;
  if (shuffle) {
    order = shuffled(ids);
    if (startId) order = [startId, ...order.filter((i) => i !== startId)];
  }
  const first = startId || order[0];
  player.queue = {
    playlistId: playlist.id, name: playlist.name, items: Object.fromEntries(items.map((i) => [i.id, i])),
    ids, order, index: order.indexOf(first), shuffle, repeat: player.queue?.repeat ?? false, autoplay: player.queue?.autoplay ?? true,
  };
  player.queueEnded = false;
  player.autoplay = true;
  navigate({ name: "video", params: { id: first } }); // "Play" always opens the video page
}

export function clearQueue() {
  player.queue = null;
  player.queueEnded = false;
}

function playQueueIndex(index) {
  const q = player.queue;
  if (!q) return;
  q.index = index;
  player.queueEnded = false;
  player.autoplay = true;
  const id = q.order[index];
  if (player.mode === "mini") player.item = q.items[id];   // keep browsing; just swap the video
  else if (playerIsFullscreen()) {
    // Opening the next page would move the player out of fullscreen: swap the
    // video in place and catch the page up when fullscreen ends.
    player.item = q.items[id];
    pageBehind = true;
  } else navigate({ name: "video", params: { id } });         // on a page (or nothing open): open its page
}

// ---- playlists in fullscreen ----
let pageBehind = false; // the video page shows an earlier video than the (fullscreen) player
function playerIsFullscreen() {
  const fs = document.fullscreenElement || document.webkitFullscreenElement;
  return !!(fs && els.stage && (els.stage.contains(fs) || fs.contains(els.stage)));
}
function onFullscreenChange() {
  if (playerIsFullscreen() || !pageBehind) return;
  pageBehind = false;
  if (player.item && player.mode === "page") navigate({ name: "video", params: { id: player.item.id } });
}
if (typeof document !== "undefined") {
  document.addEventListener("fullscreenchange", onFullscreenChange);
  document.addEventListener("webkitfullscreenchange", onFullscreenChange);
}

export function queueNext({ fromEnd = false } = {}) {
  const q = player.queue;
  if (!q) return false;
  if (q.index < q.order.length - 1) { playQueueIndex(q.index + 1); return true; }
  if (q.repeat) {
    if (q.shuffle) q.order = shuffled(q.ids);
    playQueueIndex(0);
    return true;
  }
  if (fromEnd) player.queueEnded = true;
  return false;
}

export function queuePrev() {
  const q = player.queue;
  if (!q) return;
  if (els.video && els.video.currentTime > 5) { els.video.currentTime = 0; return; } // like most players
  if (q.index > 0) playQueueIndex(q.index - 1);
  else if (q.repeat) playQueueIndex(q.order.length - 1);
}

export function playQueueItem(id) {
  const q = player.queue;
  const at = q ? q.order.indexOf(id) : -1;
  if (at !== -1) playQueueIndex(at);
}

export function toggleShuffle() {
  const q = player.queue;
  if (!q) return;
  const current = q.order[q.index];
  q.shuffle = !q.shuffle;
  // Shuffle the rest; the current video stays where it is (first).
  q.order = q.shuffle ? [current, ...shuffled(q.ids.filter((i) => i !== current))] : [...q.ids];
  q.index = q.order.indexOf(current);
}
export function toggleRepeat() { if (player.queue) player.queue.repeat = !player.queue.repeat; }
export function toggleQueueAutoplay() { if (player.queue) player.queue.autoplay = !player.queue.autoplay; }

/** Called by the player when a video finishes. */
export function onVideoEnded() {
  player.playing = false;
  const q = player.queue;
  if (q && q.autoplay) queueNext({ fromEnd: true });
  else if (q && q.index === q.order.length - 1 && !q.repeat) player.queueEnded = true;
}

/** A video is being removed/renamed: stop it if it's the one playing. */
export function releasePlayerItem(itemId) {
  if (player.item?.id === itemId) closePlayer();
}

// ------------------------------------------------- surviving a page reload
// What's playing (video, position, mini or page, playlist and its order) is
// kept in sessionStorage, so reloading the tab brings the mini-player back --
// paused, at the same moment. sessionStorage is per tab, so other tabs don't
// inherit it.
const STATE_KEY = "pmv-player";

export function savePlayerState() {
  try {
    if (!player.item || !player.mode) { sessionStorage.removeItem(STATE_KEY); return; }
    const q = player.queue;
    sessionStorage.setItem(STATE_KEY, JSON.stringify({
      id: player.item.id,
      time: els.video?.currentTime || player.resume?.time || 0,
      mode: player.mode,
      playing: player.playing,
      queue: q ? { playlistId: q.playlistId, name: q.name, ids: q.ids, order: q.order, index: q.index,
        shuffle: q.shuffle, repeat: q.repeat, autoplay: q.autoplay } : null,
    }));
  } catch { /* storage unavailable: nothing to restore later */ }
}
watch(() => [player.item?.id, player.mode, player.playing, player.queue?.index, player.queue?.shuffle, player.queue?.repeat, player.queue?.autoplay],
  savePlayerState);
window.addEventListener("pagehide", savePlayerState);

export function consumeResume() { player.resume = null; }

/** After sign-in: bring back what was playing before the reload (paused). */
export async function restorePlayer(currentPath) {
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem(STATE_KEY) || "null"); } catch { /* none */ }
  if (!saved?.id || player.item) return;
  const onItsPage = currentPath === `/video/${saved.id}`;
  // A paused video on its own page that the tab then left (reload elsewhere,
  // typed address) just stops -- same as leaving the page normally.
  if (saved.mode === "page" && !saved.playing && !saved.queue && !onItsPage) return;
  if (session.prefs.miniPlayer === false && !onItsPage) return; // no mini-player to bring it back in
  // Set at once: if this tab reloaded on that video's page, the page opens it
  // (maybe before the requests below finish) and the player seeks there.
  player.resume = { id: saved.id, time: saved.time || 0 };
  let item;
  try { item = await api(`/api/library/${encodeURIComponent(saved.id)}/item`); } catch { player.resume = null; return; }
  if (item.status !== "done") { player.resume = null; return; }
  if (saved.queue?.playlistId) {
    try {
      const pl = await api(`/api/playlists/${encodeURIComponent(saved.queue.playlistId)}`);
      const items = Object.fromEntries(pl.items.map((i) => [i.id, i]));
      const keep = (ids) => (ids || []).filter((id) => items[id]);
      const order = keep(saved.queue.order);
      if (order.includes(item.id) && (!player.item || player.item.id === item.id)) {
        player.queue = { ...saved.queue, name: pl.name, items, ids: keep(saved.queue.ids), order, index: order.indexOf(item.id) };
      }
    } catch { /* playlist gone or private now: play the video alone */ }
  }
  if (onItsPage || player.item) return; // the page took (or another video took) the player
  player.item = item;
  player.mode = "mini";
}
