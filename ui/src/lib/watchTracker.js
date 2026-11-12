// Watched time for the Stats page (POST /api/library/{id}/watch): counts only
// time actually played (not seeking, not paused), says "a play" once 10 s of
// a video have been watched, and reports every ~30 s and when playback stops
// (pause, end, another video, leaving the page).
const PLAY_AFTER = 10;   // seconds watched before it counts as a play
const FLUSH_EVERY = 30;  // seconds watched between reports

export function trackWatching(video, getItemId) {
  let itemId = null, last = null, pending = 0, watched = 0, started = false;

  function send(keepalive = false) {
    const secs = Math.round(pending * 10) / 10;
    const startNow = !started && watched >= PLAY_AFTER;
    if (!itemId || (!secs && !startNow)) return;
    pending = 0;
    if (startNow) started = true;
    fetch(`/api/library/${encodeURIComponent(itemId)}/watch`, {
      method: "POST", keepalive, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({ seconds: secs, started: startNow }),
    }).catch(() => { /* offline: this bit isn't counted */ });
  }
  function reset() {
    send(true);
    itemId = getItemId() || null; last = null; pending = 0; watched = 0; started = false;
  }
  const onTime = () => {
    if (getItemId() !== itemId) reset();
    const t = video.currentTime;
    if (!video.paused && !video.seeking && last !== null) {
      const d = t - last;
      if (d > 0 && d < 2.5) { pending += d; watched += d; }  // media time watched
    }
    last = t;
    if (pending >= FLUSH_EVERY || (!started && watched >= PLAY_AFTER)) send();
  };
  const onSeek = () => { last = video.currentTime; };
  const onStop = () => { send(); last = null; };
  const onHide = () => { if (document.visibilityState === "hidden") send(true); };
  video.addEventListener("timeupdate", onTime);
  video.addEventListener("seeked", onSeek);
  video.addEventListener("pause", onStop);
  video.addEventListener("ended", onStop);
  document.addEventListener("visibilitychange", onHide);
  window.addEventListener("pagehide", onHide);
  itemId = getItemId() || null;
  return () => {
    send(true);
    video.removeEventListener("timeupdate", onTime);
    video.removeEventListener("seeked", onSeek);
    video.removeEventListener("pause", onStop);
    video.removeEventListener("ended", onStop);
    document.removeEventListener("visibilitychange", onHide);
    window.removeEventListener("pagehide", onHide);
  };
}
