// Continue where you left off (2.24.0): saves my place in the playing video
// (PUT /api/library/{id}/position) every 5 s while it plays (3.0.1; was 10), when it's paused,
// after a seek, when it ends, when another video loads and when the tab closes.
import { setWatch, FINISHED } from "../stores/watchProgress.js";

const EVERY = 5000;

export function trackPosition(video, getItemId, getDuration) {
  let last = { id: null, pos: 0, dur: 0 };   // what's playing, as of the last timeupdate
  let sent = { id: null, pos: -1 };
  let lastSentAt = 0;
  let seekTimer = null;

  const duration = () => getDuration() || (Number.isFinite(video.duration) ? video.duration : 0);
  function send(id, pos, dur, keepalive = false) {
    if (!id || !dur || (sent.id === id && Math.abs(sent.pos - pos) < 1)) return;
    sent = { id, pos };
    lastSentAt = Date.now();
    const shown = pos >= dur * FINISHED ? dur : pos;
    setWatch(id, { pos: shown, dur });
    fetch(`/api/library/${encodeURIComponent(id)}/position`, {
      method: "PUT", keepalive, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({ position: Math.round(pos * 10) / 10, duration: Math.round(dur * 10) / 10 }),
    }).catch(() => { /* offline: the next save catches up */ });
  }
  const now = () => send(last.id, last.pos, last.dur);

  function onTime() {
    const id = getItemId();
    if (!id || video.seeking) return;
    last = { id, pos: video.currentTime, dur: duration() };
    if (last.pos >= 3 && !video.paused && Date.now() - lastSentAt >= EVERY) now();
  }
  const onPause = () => { if (!video.ended && last.pos >= 3) now(); };
  const onSeeked = () => { clearTimeout(seekTimer); seekTimer = setTimeout(() => { onTime(); if (last.pos >= 3) now(); }, 1500); };
  const onEnded = () => { const id = getItemId(); const dur = duration(); if (id && dur) send(id, dur, dur); };
  const onEmptied = () => { if (last.id && last.pos >= 3) now(); last = { id: null, pos: 0, dur: 0 }; }; // another video is loading
  const onLeave = () => { if (last.id && last.pos >= 3) send(last.id, last.pos, last.dur, true); };

  video.addEventListener("timeupdate", onTime);
  video.addEventListener("pause", onPause);
  video.addEventListener("seeked", onSeeked);
  video.addEventListener("ended", onEnded);
  video.addEventListener("emptied", onEmptied);
  window.addEventListener("pagehide", onLeave);
  return () => {
    onLeave();
    clearTimeout(seekTimer);
    video.removeEventListener("timeupdate", onTime);
    video.removeEventListener("pause", onPause);
    video.removeEventListener("seeked", onSeeked);
    video.removeEventListener("ended", onEnded);
    video.removeEventListener("emptied", onEmptied);
    window.removeEventListener("pagehide", onLeave);
  };
}
