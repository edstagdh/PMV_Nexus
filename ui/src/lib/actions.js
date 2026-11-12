// Everything you can do with a library item, in one place, used by the
// kebab menu, the list-view buttons and the video page. Mirrors the classic
// interface's rules (who can do what, and when).
import { askConfirm } from "../stores/confirm.js";
import { api } from "./api.js";
import { toast } from "../stores/toast.js";
import { hasPermission } from "../stores/session.js";
import { openDialog } from "../stores/dialogs.js";
import { live } from "../stores/live.js";
import { siteLabel } from "./format.js";
import { extensionActions } from "../stores/extensions.js";

/** "hevc" / "av1" / null: the codec the video is in now. */
export function currentCodec(item) {
  const c = item?.tech_codec || "";
  return /hevc|h\.?265/i.test(c) ? "hevc" : /av1/i.test(c) ? "av1" : null;
}

/** Whole features switched off in Server settings are hidden everywhere (2.22.0). */
export const featureOn = (name) => live.summary?.features?.[name] !== false;

async function call(path, ok, body) {
  try {
    await api(path, { method: "POST", body });
    toast(ok, { type: "success" });
  } catch (err) {
    toast(err.message || "That didn't work", { type: "error" });
  }
}

/** [{ key, label, icon, danger?, href?, run? }] for `item`; `context` "page" leaves out "Open". */
export function itemActions(item, { context = "library" } = {}) {
  const can = (p) => hasPermission(p);
  const id = encodeURIComponent(item.id);
  const out = [];
  if (item.status === "done") {
    if (can("videos.edit")) {
      out.push({ key: "rename", label: "Rename", icon: "edit", run: () => openDialog("rename", item) });
      out.push({ key: "details", label: "Edit details", icon: "tag", run: () => openDialog("details", item) });
      out.push({ key: "thumbnail", label: "Change thumbnail", icon: "image", run: () => openDialog("thumbnail", item) });
    }
    if (can("videos.maintain")) {
      if (item.repair_status === "queued" || item.repair_status === "running") {
        out.push({ key: "cancel-repair", label: "Cancel repair", icon: "x", danger: true, run: async () => {
          if ((await askConfirm(`Cancel the repair/update in progress for "${item.title || item.url}"?`, { ok: "Yes, cancel", danger: true }))) call(`/api/library/${id}/repair/cancel`, "Repair cancelled");
        } });
      } else {
        out.push({ key: "repair", label: item.repair_status === "failed" ? "Retry repair/update" : "Repair/update", icon: "wrench",
          run: () => call(`/api/library/${id}/repair`, "Repair/update queued") });
      }
      if (item.reencode_status === "queued" || item.reencode_status === "encoding") {
        out.push({ key: "cancel-reencode", label: "Cancel re-encode", icon: "x", danger: true, run: () => call(`/api/library/${id}/reencode/cancel`, "Re-encode cancelled") });
      } else if (featureOn("reencode")) {
        // One entry; the dialog picks the codec and this re-encode's settings (2.22.0) --
        // or, with "Ask each time" off in Server settings, straight away with the server's (2.22.1).
        const def = live.summary?.reencode_codec === "av1" ? "av1" : "hevc";
        const direct = live.summary?.reencode_ask === false && currentCodec(item) !== def;
        out.push(direct
          ? { key: "reencode", label: "Re-encode", icon: "cpu",
              run: () => call(`/api/library/${id}/reencode`, `Queued for re-encode to ${def === "av1" ? "AV1" : "HEVC"}`, { codec: def }) }
          : { key: "reencode", label: "Re-encode…", icon: "cpu", run: () => openDialog("reencode", item) });
      }
    }
    if (can("songs.manage")) {
      out.push({ key: "songs", label: "Find songs", icon: "music", run: () => openDialog("songs", item) });
    }
    if (hasPermission("library.view")) {
      out.push({ key: "playlist", label: "Add to playlist…", icon: "playlist", run: () => openDialog("playlist", item) });
    }
  }
  // Extensions' actions (2.30.0): only those offered for one video, and only for downloaded ones unless they say otherwise.
  for (const a of extensionActions("video")) {
    if (item.status === "done" || !a.needs_done_video) {
      out.push({ key: `ext:${a.id}`, label: a.label, icon: a.icon || "plus", run: () => openDialog("extension", item, { action: a }) });
    }
  }
  if (item.status === "failed" && can("videos.add")) {
    out.push({ key: "retry", label: "Retry download", icon: "repeat", run: () => call(`/api/library/${id}/retry`, "Retrying") });
  }
  if (item.site !== "local") out.push({ key: "open", label: `Open on ${siteLabel(item)}`, icon: "external", href: item.url });
  if (can("videos.delete")) out.push({ key: "remove", label: context === "playlist" ? "Remove from the library" : "Remove", icon: "trash", danger: true, divider: true, run: () => openDialog("remove", item, { context }) });
  return out;
}
