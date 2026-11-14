// The videos in my Favorites playlist (3.0.1), for the ♥ badge on thumbnails and the
// ♥ button on the video page. Loaded after signing in; updated when they change here.
import { reactive } from "vue";
import { api } from "../lib/api.js";

export const favorites = reactive({ ids: new Set(), loaded: false });

let pending = null;
export function loadFavorites(force = false) {
  if (pending && !force) return pending;
  pending = api("/api/playlists/favorites?ids=true")
    .then((r) => { favorites.ids = new Set(r.item_ids || []); favorites.loaded = true; })
    .catch(() => { favorites.loaded = true; });
  return pending;
}

export const isFavorite = (id) => favorites.ids.has(id);

export function setFavoriteLocal(id, on) {
  const next = new Set(favorites.ids);
  on ? next.add(id) : next.delete(id);
  favorites.ids = next;
}
