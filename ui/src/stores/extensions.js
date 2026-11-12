// Extensions (2.30.0; backend/extensions.py): the actions and panels the
// installed add-ons offer this person. With no extensions everything here is
// empty and nothing about them shows.
import { reactive } from "vue";
import { api } from "../lib/api.js";

export const ext = reactive({ loaded: false, actions: [], panels: [], extensions: [] });

let pending = null;
export function loadExtensions(force = false) {
  if (pending && !force) return pending;
  pending = api("/api/extensions")
    .then((r) => Object.assign(ext, { loaded: true, actions: r.actions || [], panels: r.panels || [], extensions: r.extensions || [] }))
    .catch(() => Object.assign(ext, { loaded: true }));
  return pending;
}

/** Actions for a place: "video" (one video's menu) or "bulk" (the picked-videos bar). */
export const extensionActions = (where) => ext.actions.filter((a) => a.where.includes(where));
