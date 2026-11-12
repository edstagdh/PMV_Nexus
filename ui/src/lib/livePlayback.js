// Converting while watching (2.13.0): plays a video as the HLS stream the
// server makes on the fly (backend/transcode.py), at most 720p. hls.js does it
// through Media Source Extensions (Chrome, Edge, Firefox, iPhone on iOS 17.1+);
// browsers with built-in HLS (Safari) get the playlist directly. hls.js is
// only downloaded the first time it's needed.
import { api } from "./api.js";

const PREF_KEY = "pmv-quality";
export const wantsLive = () => { try { return localStorage.getItem(PREF_KEY) === "720p"; } catch { return false; } };
export const setWantsLive = (on) => { try { localStorage.setItem(PREF_KEY, on ? "720p" : "original"); } catch { /* private mode */ } };

/** Starts a conversion for `itemId` on `video`, from `at` seconds.
 * Returns { stop(), height }. onFatal(message) is called when it can't go on. */
export async function startLive(video, itemId, at, onFatal) {
  const s = await api(`/api/library/${encodeURIComponent(itemId)}/live`, { method: "POST" });
  let hls = null;
  let stopped = false;
  const end = () => fetch(`/api/live/${s.session}`, { method: "DELETE", credentials: "same-origin", keepalive: true }).catch(() => {});
  const stop = () => {
    if (stopped) return;
    stopped = true;
    try { hls?.destroy(); } catch { /* gone */ }
    end();
  };
  const { default: Hls } = await import("hls.js/light");
  if (Hls.isSupported()) {
    hls = new Hls({
      startPosition: at || 0,
      maxBufferLength: 30,        // the server pauses its FFmpeg ~70 s ahead of this
      maxMaxBufferLength: 60,
      fragLoadPolicy: { default: { maxTimeToFirstByteMs: 60000, maxLoadTimeMs: 120000,
        timeoutRetry: { maxNumRetry: 2, retryDelayMs: 0, maxRetryDelayMs: 0 },
        errorRetry: { maxNumRetry: 3, retryDelayMs: 1000, maxRetryDelayMs: 8000 } } },
    });
    hls.on(Hls.Events.ERROR, (_e, data) => {
      if (!data.fatal || stopped) return;
      if (data.type === Hls.ErrorTypes.MEDIA_ERROR) { hls.recoverMediaError(); return; }
      const status = data.response?.code;
      stop();
      onFatal(status === 404 ? "The conversion ended (the video was paused for a long time)"
        : status === 410 ? "Converting was stopped on Tasks" : "The converted stream stopped");
    });
    hls.loadSource(s.playlist);
    hls.attachMedia(video);
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = s.playlist;
    if (at) video.addEventListener("loadedmetadata", () => { video.currentTime = at; }, { once: true });
    video.load();
  } else {
    stop();
    throw new Error("This browser can't play converted streams");
  }
  window.addEventListener("pagehide", stop, { once: true });
  return { stop, height: s.height };
}
