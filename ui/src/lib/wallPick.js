// The Wall's "Random" button (3.9.1 "Pick for me"; renamed 3.9.4): one of a performer's videos, by the way chosen in My Settings
// -> The Wall. Ranked ways take the best one not picked for this performer lately (this browser
// tab), so pressing again gives the next one; ties are shuffled. Videos come from
// GET /api/performers/wall (rating, views, likes, added, plays, started per video).
import { N_ } from "./i18n.js";
import { isFavorite } from "../stores/favorites.js";

export const PICK_MODES = {
  random: N_("Any of them, at random"),
  unwatched: N_("One I haven't watched"),
  favorites: N_("One of my Favorites"),
  most_watched: N_("The one I watch most"),
  highest_rated: N_("Highest rated"),
  most_viewed: N_("Most viewed on the site"),
  most_liked: N_("Most liked on the site"),
  newest: N_("Newest in the library"),
};
const RANK = {
  most_watched: (v) => v.plays || 0,
  highest_rated: (v) => (v.rating == null ? -1 : v.rating),
  most_viewed: (v) => v.views ?? -1,
  most_liked: (v) => v.likes ?? -1,
  newest: (v) => v.added || 0,
};
const KEY = "pmv-wall-picked:";

function recent(pid) {
  try { return JSON.parse(sessionStorage.getItem(KEY + pid) || "[]"); } catch { return []; }
}
function remember(pid, id, max) {
  const list = [id, ...recent(pid).filter((x) => x !== id)].slice(0, Math.max(1, max));
  try { sessionStorage.setItem(KEY + pid, JSON.stringify(list)); } catch { /* not kept */ }
}
function shuffled(list) {
  const a = [...list];
  for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; }
  return a;
}

/** {video, fallback}: fallback = nothing matched the way chosen, so it's a random one. */
export function pickVideo(videos, mode, pid) {
  if (!videos.length) return { video: null, fallback: false };
  let pool = videos;
  let fallback = false;
  if (mode === "unwatched" || mode === "favorites") {
    pool = videos.filter((v) => (mode === "unwatched" ? !v.plays && !v.started : isFavorite(v.id)));
    if (!pool.length) { pool = videos; fallback = true; }
  }
  const seen = new Set(recent(pid));
  const rank = RANK[mode];
  let order = shuffled(pool);
  if (rank) order = order.sort((a, b) => rank(b) - rank(a));   // stable: ties stay shuffled
  const video = order.find((v) => !seen.has(v.id)) || order[0];
  // random: just not the same one twice in a row; ranked: walk down the list, then start over
  remember(pid, video.id, rank ? pool.length - 1 : 1);
  return { video, fallback };
}
