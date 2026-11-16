// Performer badges (3.5.0): each known performer's gender (the badge colour: female / male /
// trans; unknown = grey) and your favourite performers (♥, the "Favourite performers" sort).
// From GET /api/performers/badges, loaded once and again when performers change.
import { reactive } from "vue";
import { api } from "../lib/api.js";
import { onLibraryChange } from "./live.js";
import { toast } from "./toast.js";

export const badges = reactive({ genders: {}, favorites: [], loaded: false });
const fold = (name) => String(name || "").split(/\s+/).filter(Boolean).join(" ").toLowerCase();
let favKeys = new Set();
let loading = null;

export function loadBadges(force = false) {
  if (loading || (badges.loaded && !force)) return loading;
  loading = api("/api/performers/badges").then((r) => {
    badges.genders = r.genders || {};
    badges.favorites = r.favorites || [];
    favKeys = new Set(badges.favorites.map(fold));
    badges.loaded = true;
  }).catch(() => {}).finally(() => { loading = null; });
  return loading;
}
onLibraryChange((changed) => { if (badges.loaded && (!changed || changed.includes("performers"))) loadBadges(true); });

/** "female" / "male" / "trans" / "unknown" (badge colour). */
export function genderOf(name) {
  if (!badges.loaded) loadBadges();
  return badges.genders[fold(name)] || "unknown";
}
const LABEL = { female: "Female", male: "Male", trans: "Trans / non-binary", unknown: "Gender not known (not matched to ThePornDB)" };

/** Classes for a performer badge: its gender colour, ♥ when it's a favourite. */
export function perfClass(name) {
  return [`g-${genderOf(name)}`, { fav: isFavorite(name) }];
}
/** Tooltip: "Aurora Vale · Female · ♥ favourite". */
export function perfTitle(name) {
  return [name, LABEL[genderOf(name)], isFavorite(name) ? "♥ favourite" : ""].filter(Boolean).join(" · ");
}

export function isFavorite(name) {
  if (!badges.loaded) loadBadges();
  void badges.favorites.length;  // reactive: re-render when the favourites change
  return badges.loaded && favKeys.has(fold(name));
}

/** `names`: all of her names in the library (so every spelling counts); `label`: the one shown. */
export async function setFavorite(names, on, label = names[0]) {
  try {
    const r = await api("/api/performers/favorites", { method: "POST", body: { names, on } });
    badges.favorites = r.favorites || [];
    favKeys = new Set(badges.favorites.map(fold));
    toast(on ? `♥ ${label} is a favourite` : `${label} is no longer a favourite`, { type: "success" });
  } catch (e) {
    toast(e.message || "Couldn't change the favourite", { type: "error" });
  }
}
