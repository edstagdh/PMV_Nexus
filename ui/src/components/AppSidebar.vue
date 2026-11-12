<script setup>
import { PORTAL_TABS } from "../lib/portalTabs.js";
import { computed } from "vue";
import Icon from "./Icon.vue";
import AppLogo from "./AppLogo.vue";
import { session, savePref, hasPermission, isAdmin, logout } from "../stores/session.js";
import { live } from "../stores/live.js";
import { discover } from "../lib/discover.js";
import NotificationBell from "./NotificationBell.vue";

defineProps({ open: Boolean });
const emit = defineEmits(["close", "add"]);

const taskCount = computed(() => {
  const t = live.summary?.tasks;
  return t ? t.active_total + t.queued_total : 0;
});
const collapsed = computed(() => session.prefs.sidebarCollapsed);
const adminFolded = computed(() => session.prefs.adminFolded);
// The "Server" section: Tasks, Management (whose tabs are Users, Duplicates,
// Maintenance, Scheduler, Backups, Stats, Logs and Tools --
// views/admin/AdminPortalView.vue) and the server's Settings
// (views/admin/ServerSettingsView.vue). Personal settings stay at the bottom.
const ADMIN_LINKS = [
  { to: "/tasks", label: "Tasks", icon: "tasks", permission: "tasks.view", count: true },
  { to: "/admin/portal", label: "Management", icon: "admin", anyOf: PORTAL_TABS.flatMap((t) => (t.admin ? ["*"] : [t.permission].flat())) },
  { to: "/admin/server", label: "Settings", title: "Server settings", icon: "server", admin: true },
];
const adminLinks = computed(() => ADMIN_LINKS.filter((l) => (l.admin ? isAdmin() : l.anyOf ? isAdmin() || l.anyOf.some(hasPermission) : hasPermission(l.permission))));

function nav(fn) { emit("close"); fn?.(); }
</script>

<template>
  <div v-if="open" class="nav-overlay" @click="emit('close')" />
  <aside class="sidebar scroll-thin" :class="{ open, collapsed }">
    <div class="brand">
      <AppLogo size="2rem" />
      <div class="brand-text">
        <div class="brand-name">PMV</div>
        <div class="brand-sub">Nexus <span v-if="live.version">v{{ live.version }}</span></div>
      </div>
      <span class="side-bell"><NotificationBell compact /></span>
      <button class="btn btn-ghost btn-icon btn-sm collapse-btn" :title="collapsed ? 'Expand sidebar' : 'Collapse sidebar'"
              @click="savePref('sidebarCollapsed', !collapsed)"><Icon name="sidebar" /></button>
    </div>

    <div v-if="hasPermission('videos.add') || hasPermission('videos.upload')" class="add-group">
      <button v-if="hasPermission('videos.add')" class="btn btn-primary add-btn" title="Add videos" @click="nav(() => emit('add'))">
        <Icon name="plus" /><span class="label">Add videos</span>
      </button>
      <router-link v-if="hasPermission('videos.upload')" to="/upload" class="btn btn-primary add-btn" title="Upload a video from this computer" @click="nav()">
        <Icon name="upload" /><span class="label">Upload</span>
      </router-link>
    </div>

    <nav class="nav">
      <router-link to="/" class="nav-item" exact-active-class="active" title="Library" @click="nav()">
        <Icon name="library" /><span class="label">Library</span>
        <span v-if="live.summary" class="nav-count muted">{{ live.summary.done_count }}</span>
      </router-link>
      <button class="nav-item" title="Discover a random video" @click="nav(discover)">
        <Icon name="dice" /><span class="label">Discover</span>
      </button>
      <router-link to="/playlists" class="nav-item" :class="{ active: $route.name === 'playlist' }" active-class="active" title="Playlists" @click="nav()">
        <Icon name="playlist" /><span class="label">Playlists</span>
      </router-link>
      <router-link to="/performers" class="nav-item" :class="{ active: $route.name === 'performer' }" active-class="active" title="Performers" @click="nav()">
        <Icon name="user" /><span class="label">Performers</span>
      </router-link>
      <router-link v-if="hasPermission('live.view')" to="/playing" class="nav-item" active-class="active" title="Currently Playing" @click="nav()">
        <Icon name="play" /><span class="label">Currently Playing</span>
      </router-link>
      <template v-if="adminLinks.length">
        <button class="nav-section section-toggle" :aria-expanded="!adminFolded" @click="savePref('adminFolded', !adminFolded)">
          <span>Server</span><Icon name="chevron" :size="14" :class="{ folded: adminFolded }" />
        </button>
        <template v-if="!adminFolded || collapsed">
          <router-link v-for="l in adminLinks" :key="l.to" :to="l.to" class="nav-item" active-class="active" :title="l.title || l.label" @click="nav()">
            <Icon :name="l.icon" /><span class="label">{{ l.label }}</span>
            <span v-if="l.count && taskCount" class="count-pill">{{ taskCount }}</span>
          </router-link>
        </template>
        <!-- Folded: Tasks still shows while something is running, so it isn't missed. -->
        <router-link v-else-if="taskCount && hasPermission('tasks.view')" to="/tasks" class="nav-item" active-class="active" title="Tasks" @click="nav()">
          <Icon name="tasks" /><span class="label">Tasks</span><span class="count-pill">{{ taskCount }}</span>
        </router-link>
      </template>
    </nav>

    <div class="sidebar-foot">
      <router-link :to="{ name: 'help', params: { tab: 'guide' } }" class="nav-item" :class="{ active: $route.name === 'help' }" title="Help" @click="nav()">
        <Icon name="help" /><span class="label">Help</span>
      </router-link>
      <router-link to="/settings" class="nav-item" active-class="active" title="My settings" @click="nav()">
        <Icon name="settings" /><span class="label">My settings</span>
      </router-link>
      <button class="nav-item" :title="`Sign out (${session.user?.username})`" @click="logout">
        <Icon name="logout" /><span class="label">Sign out <span class="muted">· {{ session.user?.username }}</span></span>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  position: fixed; inset: 0 auto 0 0; z-index: 40; width: var(--sidebar-w);
  display: flex; flex-direction: column; gap: 0.875rem; padding: 0.875rem 0.75rem;
  background: var(--bg-elev); border-right: 1px solid var(--border); overflow-y: auto;
  transition: width 0.2s var(--ease), transform 0.2s var(--ease);
}
.brand { display: flex; align-items: center; gap: 0.625rem; padding: 2px 0.25rem; }
.brand-text { min-width: 0; flex: 1; }
.brand-name { font-weight: 750; font-size: 0.9375rem; letter-spacing: -0.01em; }
.brand-sub { font-size: 0.6875rem; color: var(--text-dim); }
/* The sidebar scrolls instead of squeezing its parts when the Admin list is open. */
.sidebar > * { flex-shrink: 0; }
.add-group { display: flex; flex-direction: column; gap: 0.375rem; }
.add-btn { width: 100%; height: 2.375rem; text-decoration: none; }
.add-btn:hover { text-decoration: none; }
.nav { display: flex; flex-direction: column; gap: 2px; }
.nav-section { margin: 0.75rem 0.625rem 0.25rem; font-size: 0.6875rem; font-weight: 650; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); }
.nav-item {
  display: flex; align-items: center; gap: 0.75rem; width: 100%; height: 2.375rem; padding: 0 0.625rem;
  border: 0; border-radius: var(--radius); background: none; color: var(--text-2);
  font: 550 0.875rem var(--font); text-align: left; cursor: pointer; text-decoration: none;
}
.nav-item:hover { background: var(--panel-2); color: var(--text); text-decoration: none; }
.nav-item.active { background: var(--accent-soft); color: var(--accent); }
.nav-item .label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nav-count { font-size: 0.75rem; }
.section-toggle { display: flex; align-items: center; justify-content: space-between; border: 0; background: none; cursor: pointer; padding: 0; font-family: var(--font); }
.section-toggle:hover { color: var(--text-2); }
.section-toggle .icon { transition: transform 0.15s var(--ease); }
.section-toggle .icon.folded { transform: rotate(-90deg); }
.sidebar-foot { margin-top: auto; display: flex; flex-direction: column; gap: 2px; border-top: 1px solid var(--border); padding-top: 0.625rem; }

/* collapsed: icons only */
.sidebar.collapsed { width: var(--sidebar-w-collapsed); }
.sidebar.collapsed .brand-text, .sidebar.collapsed .label, .sidebar.collapsed .nav-count,
.sidebar.collapsed .nav-section { display: none; }
.sidebar.collapsed .brand { flex-direction: column; }
.sidebar.collapsed .nav-item { justify-content: center; padding: 0; position: relative; }
.sidebar.collapsed .count-pill { position: absolute; top: 2px; right: 0.25rem; min-width: 1rem; height: 1rem; line-height: 1rem; font-size: 0.625rem; padding: 0 0.25rem; }
.sidebar.collapsed .add-btn { padding: 0; }

.nav-overlay { display: none; }
@media (max-width: 56.25rem) {
  .sidebar, .sidebar.collapsed { width: 16.5rem; transform: translateX(-100%); box-shadow: var(--shadow); }
  .sidebar.open { transform: none; }
  .sidebar.collapsed .brand-text, .sidebar.collapsed .label, .sidebar.collapsed .nav-count, .sidebar.collapsed .nav-section { display: revert; }
  .sidebar.collapsed .brand { flex-direction: row; }
  .sidebar.collapsed .nav-item { justify-content: flex-start; padding: 0 0.625rem; }
  .collapse-btn { display: none; }
  .side-bell { display: none; }  /* the mobile bar has the bell */
  .nav-overlay { display: block; position: fixed; inset: 0; z-index: 39; background: var(--overlay); }
}
</style>
