// Your own order of a page's tabs (3.4.0): Management and Server Settings. Saved
// with your account (My Settings' "ui" object, stores/session.js: tabOrder.<page>).
import { session, savePref } from "../stores/session.js";

/** `tabs` ([{key, label}]) in the saved order of `page`; tabs not in it (new ones) keep their place at the end. */
export function orderTabs(tabs, page) {
  const saved = session.prefs.tabOrder?.[page] || [];
  const rank = (t) => { const i = saved.indexOf(t.key); return i < 0 ? saved.length + tabs.indexOf(t) : i; };
  return [...tabs].sort((a, b) => rank(a) - rank(b));
}

/** Saves `keys` as the order of `page`, keeping saved tabs you can't see now (e.g. Extensions) where they were. */
export function saveTabOrder(page, keys) {
  const saved = session.prefs.tabOrder?.[page] || [];
  const rest = saved.filter((k) => !keys.includes(k));
  savePref("tabOrder", { ...(session.prefs.tabOrder || {}), [page]: [...keys, ...rest] });
}

export function resetTabOrder(page) {
  const next = { ...(session.prefs.tabOrder || {}) };
  delete next[page];
  savePref("tabOrder", next);
}
