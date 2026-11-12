// Notifications (backend/notifications.py): finished / failed downloads,
// re-encodes, repairs, uploads, extensions and jobs, low disk space... New ones
// arrive through the live stream ("notifications" change), pop up as toasts
// (if Settings -> Notifications allows that group) and stay in the
// notification center (components/NotificationBell.vue).
import { reactive } from "vue";
import { api } from "../lib/api.js";
import { toast } from "./toast.js";
import { session } from "./session.js";
import { onLibraryChange } from "./live.js";

export const notes = reactive({ items: [], unread: 0, lastId: 0, loaded: false });

// Category -> the Settings switch that controls its toasts.
const GROUP = { download: "download", reencode: "reencode", repair: "reencode", upload: "upload", extension: "job", job: "job", system: null };
const TYPE = { success: "success", info: "info", warning: "warning", error: "error" };
const MAX_TOASTS_AT_ONCE = 3;

function wantsToast(n) {
  const group = GROUP[n.category];
  if (group === null || group === undefined) return true; // disk space and other system notices always show
  return session.prefs.toasts?.[group] !== false;
}

export async function loadNotifications() {
  try {
    const r = await api("/api/notifications?limit=100");
    notes.items = r.items;
    notes.unread = r.unread;
    notes.lastId = r.last_id;
    notes.loaded = true;
  } catch { /* signed out or offline: try again on the next change */ }
}

async function fetchNew() {
  if (!notes.loaded) return loadNotifications();
  let r;
  try { r = await api(`/api/notifications?after=${notes.lastId}&limit=50`); } catch { return; }
  notes.unread = r.unread;
  if (!r.items.length) return;
  notes.lastId = Math.max(notes.lastId, r.last_id);
  notes.items = [...r.items, ...notes.items].slice(0, 200);
  const fresh = r.items.filter(wantsToast).reverse(); // oldest first
  if (fresh.length > MAX_TOASTS_AT_ONCE) {
    const bad = fresh.filter((n) => n.level === "error").length;
    toast(`${fresh.length} updates${bad ? ` (${bad} failed)` : ""} — see the notifications`, { type: bad ? "error" : "info", timeout: 6000 });
  } else {
    for (const n of fresh) {
      toast(n.title, { type: TYPE[n.level] || "info", body: n.body, link: n.link, timeout: n.level === "error" || n.level === "warning" ? 8000 : 5000 });
    }
  }
}

let stop = null;
export function startNotifications() {
  if (stop) return;
  loadNotifications();
  stop = onLibraryChange((changed) => { if (!changed || changed.includes("notifications")) fetchNew(); });
}
export function stopNotifications() {
  stop?.();
  stop = null;
  Object.assign(notes, { items: [], unread: 0, lastId: 0, loaded: false });
}

export async function markAllRead() {
  const r = await api("/api/notifications/read", { method: "POST", body: {} });
  notes.unread = r.unread;
  notes.items = notes.items.map((n) => ({ ...n, read: true }));
}
export async function clearNotifications() {
  await api("/api/notifications", { method: "DELETE" });
  notes.items = [];
  notes.unread = 0;
}
