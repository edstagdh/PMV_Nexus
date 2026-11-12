// Turns the saved metadata JSON (raw PMVHaven API shape, or the older
// curated shape) into what the video page shows. Same rules as the classic UI.
const FLAG_LABELS = [
  ["hasExplicitContent", "Explicit content"], ["hasExtremeContent", "Extreme content"],
  ["hasGayContent", "Gay content"], ["hasTransContent", "Trans content"],
  ["hasVoiceOver", "Voice over"], ["seizureWarning", "Seizure warning"],
];

const nameOf = (x) => (typeof x === "string" ? x : (x && (x.name || x.title || x.label)) || "");

export function normalizeMetadata(meta) {
  if (!meta) return null;
  const raw = typeof meta.uploader !== "undefined" || typeof meta._id !== "undefined";
  if (!raw) {
    return {
      title: meta.title || "", creator: meta.creator?.name || "", uploadDate: meta.upload_date || null,
      duration: meta.duration || null, views: meta.views ?? null, rating: meta.rating_percent || null,
      likes: null, dislikes: null, favorites: null, description: meta.description || "",
      tags: (meta.tags || []).map(nameOf).filter(Boolean), performers: (meta.models || []).map(nameOf).filter(Boolean),
      music: (meta.music || []).map(nameOf).filter(Boolean),
      flags: (meta.content_flags || []).map(nameOf).filter(Boolean), technical: meta.video_details || {},
    };
  }
  return {
    title: meta.title || "",
    creator: meta.uploader || meta.uploaderUsername || "",
    uploadDate: meta.uploadDate ? String(meta.uploadDate).split("T")[0] : null,
    duration: meta.duration || null,
    views: meta.views ?? null,
    rating: typeof meta.bayesianRating === "number" ? `${Math.round(meta.bayesianRating)}%` : null,
    likes: meta.likes ?? null, dislikes: meta.dislikes ?? null, favorites: meta.favorites ?? null,
    description: meta.description || "",
    tags: meta.tags || [],
    performers: (meta.starsTags || []).map(nameOf).filter(Boolean),
    music: (meta.music || []).map((m) => (m && (m.artist || m.song) ? [m.song, m.artist].filter(Boolean).join(" — ") : nameOf(m))).filter(Boolean),
    flags: FLAG_LABELS.filter(([k]) => meta[k]).map(([, label]) => label),
    technical: meta.video_details || {},
  };
}
