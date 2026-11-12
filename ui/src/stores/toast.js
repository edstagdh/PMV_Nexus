import { reactive } from "vue";

export const toasts = reactive([]);
let nextId = 1;

/** toast("Saved") or toast("Downloaded “X”", { type: "success", body: "…", link: "/video/1", timeout: 6000 }).
 *  type: info | success | warning | error. A toast with a link opens it when clicked. */
export function toast(message, { type = "info", timeout = 3500, body = null, link = null } = {}) {
  const id = nextId++;
  toasts.push({ id, message, type, body, link });
  if (toasts.length > 5) toasts.splice(0, toasts.length - 5);
  setTimeout(() => dismissToast(id), timeout);
}

export function dismissToast(id) {
  const i = toasts.findIndex((t) => t.id === id);
  if (i >= 0) toasts.splice(i, 1);
}
