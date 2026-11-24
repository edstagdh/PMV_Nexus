import { t, N_ } from "./i18n.js";
// Library view state (the URL: ?q=…&tag=…&dmin=…) <-> GET /api/library
// parameters. Used by the library page, and by playlists: "add everything in
// this view" sends the parameters, a smart playlist stores them, and "open
// in library" turns them back into a library URL.

/** Library state (see LibraryView) -> API filter/sort parameters (no paging). */
export function toApiParams(s) {
  const out = {
    search: s.q, tags: s.tags, performers: s.performers, creators: s.creators, codecs: s.codecs,
    match_all: s.all || undefined, gone: s.gone ?? undefined,
    min_duration: s.dsmin ?? undefined, max_duration: s.dsmax ?? undefined,
    exclude: s.x?.trim() || undefined, no_music: s.nomusic || undefined, no_performers: s.noperf || undefined,  // 3.5.0
    min_rating: s.rating ?? undefined, qualities: s.quality, min_quality: s.qmin ?? undefined, orientation: s.orient ?? undefined,
    // performer details (2.19.0): one of the video's performers has them all
    p_cups: s.pcup, p_hair: s.phair, p_eyes: s.peyes, p_age_min: s.pamin ?? undefined, p_age_max: s.pamax ?? undefined,
    p_enhanced: s.penh ?? undefined,
    sort_key: s.sort, sort_dir: s.sort === "random" ? undefined : s.dir,
    sort_seed: s.sort === "random" ? s.seed : undefined,  // Random (3.4.1): the same seed, the same order
  };
  for (const [k, v] of Object.entries(out)) {
    if (v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length)) delete out[k];
  }
  return out;
}

/** API parameters (e.g. a smart playlist's filter) -> library URL query. */
export function toLibraryQuery(p = {}) {
  const q = {};
  if (p.search) q.q = p.search;
  if (p.tags?.length) q.tag = p.tags;
  if (p.performers?.length) q.performer = p.performers;
  if (p.creators?.length) q.creator = p.creators;
  if (p.codecs?.length) q.codec = p.codecs;
  if (p.match_all) q.match = "all";
  if (p.gone === true) q.gone = "1";
  if (p.gone === false) q.gone = "0";
  if (p.min_duration != null) q.dsmin = String(Math.round(p.min_duration));
  if (p.max_duration != null) q.dsmax = String(Math.round(p.max_duration));
  if (p.exclude) q.x = p.exclude;
  if (p.no_music) q.nomusic = "1";
  if (p.no_performers) q.noperf = "1";
  if (p.min_rating != null) q.rating = String(p.min_rating);
  if (p.qualities?.length) q.quality = p.qualities;
  if (p.min_quality) q.qmin = p.min_quality;
  if (p.orientation) q.orient = p.orientation;
  if (p.p_cups?.length) q.pcup = p.p_cups;
  if (p.p_hair?.length) q.phair = p.p_hair;
  if (p.p_eyes?.length) q.peyes = p.p_eyes;
  if (p.p_age_min != null) q.pamin = String(p.p_age_min);
  if (p.p_age_max != null) q.pamax = String(p.p_age_max);
  if (p.p_enhanced != null) q.penh = p.p_enhanced ? "1" : "0";
  if (p.sort_key) q.sort = p.sort_key;
  if (p.sort_dir) q.dir = p.sort_dir;
  if (p.sort_seed != null) q.seed = String(p.sort_seed);
  return q;
}

// Words for describeQuery(), marked N_ and translated when used (made before a language loads; 3.7.0).
const SORT_LABELS = {
  download_datetime: N_("date acquired"), upload_date: N_("upload date"), created_at: N_("date acquired"), title: N_("title"),
  creator: N_("creator"), ratings: N_("rating"), views: N_("views"), tech_duration: N_("duration"), tech_filesize: N_("file size"),
  tech_resolution: N_("resolution"), random: N_("random order"), fav_performers: N_("favourite performers"),
};

/** A new order for sort "random" (the URL's ?seed=). */
export const newSeed = () => Math.floor(Math.random() * 2 ** 31);

/** "performer Aurora Vale · tag pmv · 10+ min · sorted by rating" */
export function describeQuery(p = {}) {
  const parts = [];
  if (p.search) parts.push(`“${p.search}”`);
  const lists = { performers: [N_("performer: {values}"), N_("performers: {values}")], tags: [N_("tag: {values}"), N_("tags: {values}")],
    creators: [N_("creator: {values}"), N_("creators: {values}")], codecs: [N_("codec: {values}"), N_("codecs: {values}")],
    qualities: [N_("quality: {values}"), N_("qualities: {values}")] };
  for (const [key, [one, many]] of Object.entries(lists)) {
    if (p[key]?.length) parts.push(t(p[key].length > 1 ? many : one, { values: p[key].join(p.match_all ? " + " : " / ") }));
  }
  if (p.min_quality) parts.push(`${p.min_quality}+`);
  if (p.orientation) parts.push(p.orientation);
  if (p.min_duration != null || p.max_duration != null) {
    const a = p.min_duration != null ? Math.round(p.min_duration / 60) : null;
    const b = p.max_duration != null ? Math.round(p.max_duration / 60) : null;
    parts.push(a != null && b != null ? t("{from}–{to} min", { from: a, to: b }) : a != null ? t("{from}+ min", { from: a }) : t("up to {to} min", { to: b }));
  }
  if (p.exclude) parts.push(t("not {words}", { words: p.exclude }));
  if (p.no_music) parts.push(t("no music"));
  if (p.no_performers) parts.push(t("no performers"));
  if (p.min_rating != null) parts.push(t("rating {percent}%+", { percent: p.min_rating }));
  const who = [];
  if (p.p_cups?.length) who.push(t("cup {cups}", { cups: p.p_cups.join("/") }));
  if (p.p_age_min != null || p.p_age_max != null) who.push(t("age {from}–{to}", { from: p.p_age_min ?? "", to: p.p_age_max ?? "" }));
  if (p.p_hair?.length) who.push(t("{colours} hair", { colours: p.p_hair.join("/") }));
  if (p.p_eyes?.length) who.push(t("{colours} eyes", { colours: p.p_eyes.join("/") }));
  if (p.p_enhanced != null) who.push(p.p_enhanced ? t("enhanced") : t("natural"));
  if (who.length) parts.push(t("a performer with {details}", { details: who.join(", ") }));
  if (p.gone === true) parts.push(t("💀 gone from the site"));
  if (p.gone === false) parts.push(t("still on the site"));
  if (p.sort_key) {
    const by = t(SORT_LABELS[p.sort_key] || p.sort_key);
    parts.push(p.sort_dir === "asc" ? t("sorted by {field} (ascending)", { field: by }) : t("sorted by {field}", { field: by }));
  }
  return parts.join(" · ") || t("every video");
}
