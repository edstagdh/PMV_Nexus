// Search this frame (3.2.0): the picture the player shows now, searched on a
// reverse image search site in a new tab. The app runs on your own computer, so a
// site can't fetch the picture by its address: the browser uploads it to the
// site directly (like the site's own "upload an image" page), through the app's
// /frame-search page (backend/routers/system.py). Only for Yandex the app's server
// passes it on (see YANDEX).
//
// Sites: My Settings -> Video page -> Search this frame -- each with a name, the
// address its upload form posts to and the name of the form's file field.
// Google Lens is the built-in default.

export const GOOGLE_LENS = { name: "Google Lens", url: "https://lens.google.com/v3/upload", field: "encoded_image" };
// Yandex (3.2.1): refuses an upload straight from the browser, so the app's server uploads
// the frame and the tab opens the results it answers with (mode "yandex").
export const YANDEX = { name: "Yandex", url: "https://yandex.com/images/search", field: "upfile", mode: "yandex" };
export const PRESETS = [GOOGLE_LENS, YANDEX];
export const DEFAULT_ENGINES = [GOOGLE_LENS];
const MAX_SIDE = 1920; // pixels: big enough for any search, small enough to upload quickly

/** Engines from the saved setting: valid https entries only; none saved -> Google Lens. */
export function cleanEngines(list) {
  if (!Array.isArray(list)) return DEFAULT_ENGINES.map((e) => ({ ...e }));
  return list
    .filter((e) => e && typeof e.name === "string" && typeof e.url === "string" && typeof e.field === "string")
    .map((e) => {
      const url = e.url.trim();
      // Any Yandex entry works the Yandex way (one added by hand, and the earlier variants too).
      if (e.mode === "yandex" || /^https:\/\/(www\.)?yandex\.[a-z.]+\//i.test(url)) return { ...YANDEX };
      return { name: e.name.trim().slice(0, 40), url, field: e.field.trim().slice(0, 60) };
    })
    .filter((e) => e.name && /^https:\/\/[^\s]+$/i.test(e.url) && /^[\w.[\]-]+$/.test(e.field))
    .filter((e, i, all) => all.findIndex((o) => o.url === e.url && o.mode === e.mode) === i) // one of each
    .slice(0, 10);
}

/** The frame as a JPEG File, or null when the video shows no picture yet. Synchronous on purpose:
 *  the new tab has to open within the click, or the browser blocks it as a pop-up. */
export function grabFrame(video) {
  const w = video?.videoWidth, h = video?.videoHeight;
  if (!w || !h) return null;
  const scale = Math.min(1, MAX_SIDE / Math.max(w, h));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(w * scale);
  canvas.height = Math.round(h * scale);
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
  const b64 = canvas.toDataURL("image/jpeg", 0.92).split(",")[1];
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const t = Math.floor(video.currentTime || 0);
  return new File([bytes], `frame-${Math.floor(t / 60)}m${String(t % 60).padStart(2, "0")}s.jpg`, { type: "image/jpeg" });
}

/** Uploads `file` to `engine` in a new tab. The app's pages may only send forms to the app
 *  itself (its Content-Security-Policy), so the tab opens the app's small /frame-search page,
 *  which may send the picture on to an https site: it says when it's ready, gets the picture
 *  (same origin only) and posts it there. Must be called within the click (pop-up blockers).
 *  Returns false when the browser blocked the new tab. */
export function searchFile(file, engine) {
  const tab = window.open("/frame-search", "_blank");
  if (!tab) return false;
  const onReady = (e) => {
    if (e.origin !== location.origin || e.source !== tab || e.data?.type !== "pmv-frame-search-ready") return;
    window.removeEventListener("message", onReady);
    tab.postMessage({ type: "pmv-frame-search", file, name: engine.name, url: engine.url, field: engine.field, mode: engine.mode || "form" }, location.origin);
  };
  window.addEventListener("message", onReady);
  setTimeout(() => window.removeEventListener("message", onReady), 30000);
  return true;
}
