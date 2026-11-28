// Tabs of Management (views/admin/AdminPortalView.vue): key, label and
// who may open each (admin, or a permission / list of permissions -- any one
// is enough; backend/permissions.py). Shared with the router (tab titles, old addresses).
import { t } from "./i18n.js";

// Labels are getters: the list is made before a language loads, so they translate when read (3.7.0).
export const PORTAL_TABS = [
  { key: "users", get label() { return t("Users"); }, admin: true },
  { key: "duplicates", get label() { return t("Duplicates"); }, permission: "duplicates.manage" },
  { key: "names", get label() { return t("Names"); }, permission: "names.manage" },
  { key: "songs", get label() { return t("Songs"); }, permission: "songs.manage" },
  { key: "faces", get label() { return t("Faces"); }, permission: "performers.manage" },
  { key: "maintenance", get label() { return t("Maintenance"); }, permission: ["settings.manage", "maintenance.run", "performers.manage", "videos.maintain"] },
  { key: "scheduler", get label() { return t("Scheduler"); }, permission: "settings.manage" },
  { key: "backups", get label() { return t("Backups"); }, permission: "settings.manage" },
  { key: "stats", get label() { return t("Stats"); }, permission: "stats.view" },
  { key: "logs", get label() { return t("Logs"); }, permission: "logs.view" },
  { key: "tools", get label() { return t("Tools"); }, permission: "settings.manage" },
];
