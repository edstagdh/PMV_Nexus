import { releasePlayerItem } from "../stores/player.js";

// Stops every <video> playing a given item (hover previews, the player) and
// drops its connection. Needed before removing or renaming a video: on
// Windows an open stream keeps the file locked.
export function releaseMedia(itemId) {
  releasePlayerItem(itemId);
  document.querySelectorAll(`video[data-item-id="${CSS.escape(itemId)}"]`).forEach((v) => {
    try { v.pause(); v.removeAttribute("src"); v.load(); } catch { /* element already gone */ }
  });
}
