// Currently Playing (2.20.0): tells the server which video this tab is playing
// (POST /api/now-playing) -- when it starts, every 20 s while it plays, and
// when it stops. Only "this tab plays video X": no position, no times. The
// server forgets a tab that goes quiet (backend/now_playing.py).
const EVERY = 20000;

function tabId() {
  try {
    let id = sessionStorage.getItem("pmv-tab");
    if (!id) {
      id = (crypto.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`).replace(/[^A-Za-z0-9_-]/g, "");
      sessionStorage.setItem("pmv-tab", id);
    }
    return id;
  } catch { return `t${Math.random().toString(36).slice(2, 14)}`; }
}

export function reportPlaying(video, getItemId) {
  const tab = tabId();
  let current = null;
  let timer = null;
  const send = (itemId, keepalive = false) => fetch("/api/now-playing", {
    method: "POST", keepalive, credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
    body: JSON.stringify({ tab, item_id: itemId }),
  }).catch(() => { /* offline: the server drops this tab after a while */ });

  const start = () => {
    const id = getItemId();
    if (!id) return;
    if (id !== current) { current = id; send(id); }
    clearInterval(timer);
    timer = setInterval(() => { if (current && !video.paused) send(current); }, EVERY);
  };
  const stop = (keepalive = false) => {
    clearInterval(timer);
    timer = null;
    if (current) { current = null; send(null, keepalive === true); }
  };
  const onLeave = () => stop(true);
  video.addEventListener("playing", start);
  video.addEventListener("pause", stop);
  video.addEventListener("ended", stop);
  video.addEventListener("emptied", stop); // another video is loading
  window.addEventListener("pagehide", onLeave);
  return () => {
    stop(true);
    video.removeEventListener("playing", start);
    video.removeEventListener("pause", stop);
    video.removeEventListener("ended", stop);
    video.removeEventListener("emptied", stop);
    window.removeEventListener("pagehide", onLeave);
  };
}
