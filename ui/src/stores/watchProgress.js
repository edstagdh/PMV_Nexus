// Continue where you left off (2.24.0): my place in each video. The server sends it
// with each video ("watch": {pos, dur}); what the player saves in this tab is kept
// here too, so thumbnails and the next play are up to date without reloading.
import { reactive } from "vue";
import { parseDurationSeconds } from "../lib/format.js";

export const FINISHED = 0.95;          // at or past this share it counts as watched
const MIN_RESUME = 10;                 // seconds: less than this isn't worth continuing from
const watchOverrides = reactive({});   // id -> { pos, dur } | null (started over)

export function watchOf(item) {
  if (!item) return null;
  return item.id in watchOverrides ? watchOverrides[item.id] : item.watch || null;
}
export function setWatch(id, value) { watchOverrides[id] = value; }

/** Seconds to continue from, or null (not started, nearly finished, or watched). */
export function resumePoint(item) {
  const w = watchOf(item);
  if (!w || !w.dur || w.pos < MIN_RESUME || w.pos >= w.dur * FINISHED || w.dur - w.pos < MIN_RESUME) return null;
  return w.pos;
}

/** 0-1 for the line under a thumbnail, or null. */
export function watchedShare(item) {
  const w = watchOf(item);
  if (!w || !w.dur || w.pos < 3) return null;
  return Math.min(1, w.pos / w.dur);
}

export const itemDuration = (item) => parseDurationSeconds(item?.tech_duration) || 0;
