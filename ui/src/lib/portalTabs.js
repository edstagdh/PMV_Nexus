// Tabs of Management (views/admin/AdminPortalView.vue): key, label and
// who may open each (admin, or a permission / list of permissions -- any one
// is enough; backend/permissions.py). Shared with the router (tab titles, old addresses).
export const PORTAL_TABS = [
  { key: "users", label: "Users", admin: true },
  { key: "duplicates", label: "Duplicates", permission: "duplicates.manage" },
  { key: "names", label: "Names", permission: "names.manage" },
  { key: "songs", label: "Songs", permission: "songs.manage" },
  { key: "maintenance", label: "Maintenance", permission: ["settings.manage", "maintenance.run", "performers.manage", "videos.maintain"] },
  { key: "scheduler", label: "Scheduler", permission: "settings.manage" },
  { key: "backups", label: "Backups", permission: "settings.manage" },
  { key: "stats", label: "Stats", permission: "stats.view" },
  { key: "logs", label: "Logs", permission: "logs.view" },
  { key: "tools", label: "Tools", permission: "settings.manage" },
];
