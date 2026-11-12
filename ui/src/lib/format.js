export function parseDurationSeconds(text) {
  if (!text) return null;
  const parts = String(text).trim().split(":").map(Number);
  if (parts.some((n) => Number.isNaN(n))) return null;
  return parts.reduce((acc, n) => acc * 60 + n, 0);
}

export function formatDuration(seconds) {
  if (seconds == null) return "";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

export function ratingPercent(text) {
  const n = parseFloat(String(text || "").replace("%", ""));
  return Number.isFinite(n) ? n : null;
}

export function ratingTier(text) {
  const n = ratingPercent(text);
  if (n == null) return null;
  if (n >= 90) return "excellent";
  if (n >= 75) return "good";
  if (n >= 50) return "medium";
  return "low";
}

export function isHevc(codec) {
  return /hevc|h\.?265/i.test(codec || "");
}

/** "1920x1080" -> "1080p" */
export function shortResolution(res) {
  const nums = String(res || "").match(/\d+/g);
  if (!nums) return res || "";
  return `${nums.length >= 2 ? nums[1] : nums[0]}p`;
}

export function compactNumber(n) {
  if (n == null) return "";
  return Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function formatDate(iso) {
  if (!iso) return "";
  // A bare "YYYY-MM-DD" is a calendar date: read it as local, not UTC midnight
  // (which shows the previous day west of UTC).
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  const d = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString();
}

export function thumbUrl(item) {
  if (!item?.thumbnail_path) return null;
  return `/api/library/${encodeURIComponent(item.id)}/thumbnail?v=${encodeURIComponent(item.thumbnail_updated_at || "")}`;
}

export function previewUrl(item) {
  // ?v= changes when the clip is replaced (repair), so the new one shows without a page reload.
  return item?.preview_path ? `/api/library/${encodeURIComponent(item.id)}/preview?v=${encodeURIComponent(item.preview_updated_at || "")}` : null;
}

export function itemUrlTitle(item) {
  return item?.title || item?.url || "Untitled";
}

// Same labels as the classic interface.
const CODEC_LABELS = [
  { re: /av0?1/i, label: "AV1" },
  { re: /hevc|h\.?265|x265/i, label: "HEVC" },
  { re: /avc|h\.?264|x264/i, label: "AVC" },
];
export function codecLabel(codec) {
  if (!codec) return "";
  const hit = CODEC_LABELS.find(({ re }) => re.test(codec));
  return hit ? hit.label : codec;
}

// "1920x1080" -> { label: "FHD", orientation: "landscape" }. Bucketed by the
// shorter side, so 1080x1920 portrait is FHD as well.
const QUALITY_BUCKETS = [[3240, "8K"], [1800, "UHD"], [1260, "QHD"], [900, "FHD"], [600, "HD"], [420, "SD"], [0, "LD"]];
export function qualityInfo(resolution) {
  const m = String(resolution || "").match(/(\d+)\s*[x×]\s*(\d+)/i);
  if (!m) return resolution ? { label: resolution, orientation: null } : null;
  const w = Number(m[1]), h = Number(m[2]);
  const label = QUALITY_BUCKETS.find(([min]) => Math.min(w, h) >= min)[1];
  return { label, orientation: w > h ? "landscape" : w < h ? "portrait" : "square" };
}
export const ORIENTATION_GLYPH = { landscape: "🖥️", portrait: "📱", square: "⬛" };

/** Short status for background work on an item (re-encode, repair), or null. */
export function workStatus(item) {
  if (item.reencode_status === "encoding") return { label: `Re-encoding ${Math.round(item.reencode_progress_percent || 0)}%`, kind: "busy" };
  if (item.reencode_status === "queued") return { label: "Re-encode queued", kind: "queued" };
  if (item.repair_status === "running") return { label: item.repair_progress_label || "Repairing…", kind: "busy" };
  if (item.repair_status === "queued") return { label: "Repair queued", kind: "queued" };
  if (item.reencode_status === "failed") return { label: "Re-encode failed", kind: "failed", detail: item.reencode_error };
  if (item.repair_status === "failed") return { label: "Repair failed", kind: "failed", detail: item.repair_error };
  return null;
}

/** "247 MB" / "1.2 GB" -> bytes (null if unreadable). */
export function parseSizeBytes(text) {
  const m = String(text || "").trim().match(/^([\d.]+)\s*(B|KB|MB|GB|TB)$/i);
  if (!m) return null;
  const pow = { B: 0, KB: 1, MB: 2, GB: 3, TB: 4 }[m[2].toUpperCase()];
  return Number(m[1]) * 1024 ** pow;
}

/** Average bitrate in kbit/s from a file-size string and a duration string. */
export function bitrateKbps(sizeText, durationText) {
  const bytes = parseSizeBytes(sizeText);
  const secs = parseDurationSeconds(durationText);
  return bytes && secs ? Math.round((bytes * 8) / secs / 1000) : null;
}

const SITE_LABELS = { "pmvhaven.com": "PMVHaven", "porntubeai.com": "PornTubeAI", "hmvhaven.com": "HMVHaven" };
/** "PMVHaven" / "PornTubeAI" / "HMVHaven" for a video, from its page URL. */
export function siteLabel(item) {
  try {
    const host = new URL(item?.url || "").hostname.replace(/^www\./, "");
    return SITE_LABELS[host] || host || "the site";
  } catch { return "the site"; }
}

/** 1536 -> "1.5 KB". */
export function formatBytes(bytes) {
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}

/** Unix seconds or ISO text -> short local date and time ("—" if missing). */
export function formatDateTime(value) {
  if (value === null || value === undefined || value === "") return "—";
  const d = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

/** Seconds -> "3d 4h" / "5h 12m" / "8m". */
export function formatUptime(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}
