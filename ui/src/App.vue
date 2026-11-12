<script setup>
import { ref, computed, watch, watchEffect } from "vue";
import { pageTitle, formatTitle, routeTitle } from "./lib/title.js";
import { useRoute, useRouter } from "vue-router";
import AppSidebar from "./components/AppSidebar.vue";
import AddVideoDialog from "./components/AddVideoDialog.vue";
import ToastHost from "./components/ToastHost.vue";
import DialogHost from "./components/DialogHost.vue";
import ConfirmHost from "./components/ConfirmHost.vue";
import BlankView from "./views/BlankView.vue";
import { installPanic } from "./lib/panic.js";
import { pickDecoy } from "./lib/decoyPages.js";
import { closeDialog } from "./stores/dialogs.js";
import { settleConfirm } from "./stores/confirm.js";
import PlayerHost from "./components/PlayerHost.vue";
import { setNavigator, closePlayer, restorePlayer } from "./stores/player.js";
import Icon from "./components/Icon.vue";
import AuthView from "./views/AuthView.vue";
import { session, loadSession, hasAny, isAdmin, refreshPermissions } from "./stores/session.js";
import { live, connectLive, disconnectLive, onLibraryChange } from "./stores/live.js";
import { setUnauthorizedHandler } from "./lib/api.js";
import { discover } from "./lib/discover.js";
import NotificationBell from "./components/NotificationBell.vue";
import { startNotifications, stopNotifications } from "./stores/notifications.js";
import { loadExtensions } from "./stores/extensions.js";

const navOpen = ref(false);
const addOpen = ref(false);
const updateDismissed = ref(false);
const reload = () => window.location.reload();
const route = useRoute();
const router = useRouter();
setNavigator(router.push);

// Panic keys (2.24.0, My settings -> Video page): stop everything, show an empty page.
installPanic(() => (session.user ? (session.prefs.panicKeys || []).map((k) => k.code) : []), () => {
  document.querySelectorAll("video, audio").forEach((m) => { try { m.pause(); } catch { /* gone */ } });
  if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
  closePlayer();
  closeDialog();
  settleConfirm(false);
  navOpen.value = false;
  addOpen.value = false;
  router.replace({ name: "blank", params: { slug: pickDecoy().slug } });
});
watch(() => session.user, (user) => { if (user) restorePlayer(window.location.pathname); else closePlayer(); });
const allowed = computed(() => {
  const m = route.meta || {};
  return !(m.admin && !isAdmin()) && !(m.permission && !hasAny(m.permission));
});
// Roles / groups / someone's access changed (Management -> Users): pick up my
// own permissions right away, so menus and pages match what I may do.
onLibraryChange((changed) => { if (changed?.includes("permissions")) refreshPermissions(); });
// Browser tab title: the page's own name (video / playlist title) when it has
// reported one for this address, else the route's name; just the app name on the library.
watchEffect(() => {
  if (route.meta?.bare) return; // the panic page sets its own (an article's) title
  let name;
  if (!session.ready) name = "";
  else if (!session.user) name = session.setupRequired ? "Set up" : "Sign in";
  else if (!allowed.value) name = "Not allowed";
  else name = (pageTitle.path === route.path && pageTitle.text) || routeTitle(route);
  document.title = formatTitle(name);
});

setUnauthorizedHandler(() => { session.user = null; });
loadSession();
watch(() => session.user, (user) => {
  if (user) { connectLive(); startNotifications(); loadExtensions(true); } else { disconnectLive(); stopNotifications(); }
});

// Lost the server (it's down, restarting, or the network dropped): a banner
// after a few seconds, so a short blip doesn't flash one.
const offline = ref(false);
let offlineTimer = null;
watch(() => live.connected, (ok) => {
  clearTimeout(offlineTimer);
  if (ok) offline.value = false;
  else if (session.user) offlineTimer = setTimeout(() => { offline.value = !live.connected; }, 6000);
});
const disk = computed(() => live.summary?.disk);
</script>

<template>
  <div v-if="!session.ready" class="boot"><div class="spinner" /></div>
  <AuthView v-else-if="!session.user" />
  <BlankView v-else-if="route.meta?.bare" />
  <div v-else class="shell" :class="{ collapsed: session.prefs.sidebarCollapsed }">
    <AppSidebar :open="navOpen" @close="navOpen = false" @add="addOpen = true" />
    <div class="app-main">
      <header class="mobile-bar">
        <button class="btn btn-ghost btn-icon" aria-label="Menu" @click="navOpen = true"><Icon name="menu" /></button>
        <router-link to="/" class="mobile-brand">PMV Nexus</router-link>
        <button class="btn btn-ghost btn-icon" aria-label="Discover a random video" @click="discover"><Icon name="dice" /></button>
        <NotificationBell />
      </header>
      <div v-if="offline" class="app-banner warn" role="status">
        <Icon name="server" :size="18" /><span><strong>Lost the connection to the server.</strong> Trying again… (Is it running? Is the network up?)</span>
      </div>
      <div v-if="disk?.low" class="app-banner bad" role="alert">
        <Icon name="disk" :size="18" />
        <span><strong>Downloads are stopped: low disk space.</strong> Only {{ disk.free_percent }}% ({{ disk.free_gb }} GB) is free where the app
          saves videos. Free up space to avoid damaged files — downloads start again by themselves above {{ disk.limit + 1 }}%.
          Re-encodes and everything else carry on.</span>
        <router-link v-if="isAdmin()" :to="{ name: 'server', query: { tab: 'download' } }" class="btn btn-sm">Settings</router-link>
      </div>
      <div v-if="!allowed" class="empty" style="min-height: 60vh">
        <div class="empty-icon">🔒</div>
        <h2>This page is for the admin</h2>
        <router-link to="/" class="btn">Back to the library</router-link>
      </div>
      <!-- Server unreachable (2.24.1): every page's content shows the same box; the page stays
           loaded underneath and comes back, refreshed, when the connection does. The video
           page is left as it is: its player can keep playing what it already has. -->
      <div v-else-if="offline && route.name !== 'video'" class="empty offline-box" role="alert">
        <div class="empty-icon">⚠️</div>
        <h2>Can't reach the server</h2>
        <span>Is it running? Is the network up? This page comes back by itself once the connection does.</span>
        <button class="btn" @click="reload">Reload now</button>
      </div>
      <div v-if="allowed" v-show="!(offline && route.name !== 'video')" class="page-host">
        <router-view v-slot="{ Component }">
          <keep-alive include="LibraryView"><component :is="Component" /></keep-alive>
        </router-view>
      </div>
    </div>
    <DialogHost />
    <ConfirmHost />
    <PlayerHost />
    <AddVideoDialog v-if="addOpen" @close="addOpen = false" />
    <div v-if="live.updateAvailable && !updateDismissed" class="update-banner card">
      <span>PMV Nexus was updated to v{{ live.serverVersion }}.</span>
      <button class="btn btn-primary btn-sm" @click="reload">Reload</button>
      <button class="btn btn-ghost btn-sm" @click="updateDismissed = true">Later</button>
    </div>
  </div>
  <ToastHost v-if="!route.meta?.bare" />
</template>

<style>
.offline-box { min-height: 60vh; }
.boot { min-height: 100vh; display: grid; place-items: center; }
.spinner { width: 1.75rem; height: 1.75rem; border-radius: 50%; border: 3px solid var(--border-strong); border-top-color: var(--accent); animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.shell { min-height: 100vh; }
.app-main { margin-left: var(--sidebar-w); min-width: 0; transition: margin-left 0.2s var(--ease); }
.shell.collapsed .app-main { margin-left: var(--sidebar-w-collapsed); }
.mobile-bar { display: none; }
.update-banner { position: fixed; left: 50%; bottom: 1rem; transform: translateX(-50%); z-index: 150; display: flex; align-items: center; gap: 0.625rem; padding: 0.625rem 0.875rem; box-shadow: var(--shadow); border-color: var(--accent); max-width: calc(100vw - 2rem); }
@media (max-width: 56.25rem) {
  .app-main, .shell.collapsed .app-main { margin-left: 0; }
  .mobile-bar { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; position: sticky; top: 0; z-index: 30; height: 3.25rem; padding: 0 0.5rem; background: var(--bg-elev); border-bottom: 1px solid var(--border); }
  .mobile-brand { font-weight: 750; color: var(--text); }
}
.app-banner { display: flex; align-items: center; gap: 0.75rem; margin: 0.75rem clamp(1rem, 2vw, 1.75rem) 0; padding: 0.625rem 0.875rem;
  border-radius: var(--radius); font-size: 0.84rem; border: 1px solid; }
.app-banner > span { flex: 1; min-width: 0; }
.app-banner.bad { border-color: var(--danger); background: color-mix(in srgb, var(--danger) 10%, var(--panel)); }
.app-banner.warn { border-color: var(--warning, #f59e0b); background: color-mix(in srgb, var(--warning, #f59e0b) 10%, var(--panel)); }
.app-banner svg { flex-shrink: 0; }
</style>
