// 3.12.0: who has the CPU / the disk and who waits for it (backend/resources.py, in
// GET /api/library/activity -> resources). Used by the Tasks page and its job list.
import { t } from "./i18n.js";

export function needsText(needs) {
  const has = new Set(needs || []);
  if (has.has("cpu") && has.has("disk")) return t("the CPU and the disk");
  return has.has("cpu") ? t("the CPU") : t("the disk");
}

/** {waiting: true, text, detail} when this job (activity id) or video (item id) waits for its turn, else null. */
export function waitingFor(res, { activity, item }) {
  const w = (res?.waiting || []).find((x) => (activity && x.activity === activity) || (item && x.item === item));
  if (!w) return null;
  const blockers = (res.holding || []).filter((h) => h.needs.some((n) => w.needs.includes(n)));
  const live = res.live && w.needs.includes("cpu");
  const who = [...blockers.map((h) => h.label), ...(live ? [t("someone is watching a video that's being converted")] : [])];
  return { waiting: true, text: t("Waiting for {what}", { what: needsText(w.needs) }),
           detail: who.length ? t("In use by: {who}", { who: who.join(" · ") }) : "" };
}
