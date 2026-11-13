import { createRouter, createWebHistory } from "vue-router";
import LibraryView from "./views/LibraryView.vue";
import VideoView from "./views/VideoView.vue";
import NotFoundView from "./views/NotFoundView.vue";
import SettingsView from "./views/SettingsView.vue";
import TasksView from "./views/TasksView.vue";
import HelpView from "./views/HelpView.vue";
import PlaylistsView from "./views/PlaylistsView.vue";
import PlaylistView from "./views/PlaylistView.vue";
import UploadView from "./views/UploadView.vue";
import PerformerView from "./views/PerformerView.vue";
import PerformersView from "./views/PerformersView.vue";
import NowPlayingView from "./views/NowPlayingView.vue";
import BlankView from "./views/BlankView.vue";
import ServerSettingsView from "./views/admin/ServerSettingsView.vue";
import AdminPortalView from "./views/admin/AdminPortalView.vue";
import { PORTAL_TABS } from "./lib/portalTabs.js";
import { rememberLibrary } from "./lib/nav.js";

// Where the library was scrolled to when a video was opened, per library URL,
// so coming back (by Back or by the browser) lands on the same card.
const libraryScroll = new Map();

export const router = createRouter({
  history: createWebHistory("/"),
  // meta.admin / meta.permission: who may open the page (App.vue shows
  // "not allowed" otherwise; the server checks every request again).
  routes: [
    { path: "/", name: "library", component: LibraryView },
    { path: "/video/:id", name: "video", component: VideoView, props: true, meta: { title: "Video" } },
    { path: "/settings", name: "settings", component: SettingsView, meta: { title: "My settings" } },
    { path: "/playlists", name: "playlists", component: PlaylistsView, meta: { title: "Playlists" } },
    { path: "/playlist/:id", name: "playlist", component: PlaylistView, props: true, meta: { title: "Playlist" } },
    { path: "/performers", name: "performers", component: PerformersView, meta: { title: "Performers" } },
    { path: "/performer/:name", name: "performer", component: PerformerView, props: true, meta: { title: (to) => to.params.name } },
    { path: "/upload", name: "upload", component: UploadView, meta: { title: "Upload", permission: "videos.upload" } },
    // The panic keys' page (2.24.0): an ordinary-looking article (2.24.4), at an ordinary-looking address.
    { path: "/read/:slug?", name: "blank", component: BlankView, meta: { bare: true } },
    { path: "/playing", name: "playing", component: NowPlayingView, meta: { title: "Currently Playing", permission: "live.view" } },
    { path: "/tasks", name: "tasks", component: TasksView, meta: { title: "Tasks", permission: "tasks.view" } },
    { path: "/help/:tab(guide|changelog|terms|about)?", name: "help", component: HelpView,
      meta: { title: (to) => ({ changelog: "Changelog", about: "About" }[to.params.tab] || "Help") } },
    { path: "/admin/server", name: "server", component: ServerSettingsView, meta: { title: "Server settings", admin: true } },
    // Users, Duplicates, Maintenance, Scheduler, Backups, Stats, Logs, Tools: tabs of one page (2.7.0).
    { path: "/admin/portal/:tab?", name: "portal", component: AdminPortalView,
      meta: { title: (to) => PORTAL_TABS.find((t) => t.key === to.params.tab)?.label || "Management" } },
    { path: "/admin/:tab(users|duplicates|maintenance|scheduler|backups|stats|logs|tools)",
      redirect: (to) => ({ name: "portal", params: { tab: to.params.tab }, query: to.query }) },
    // Addresses of the old (classic) interface, so bookmarks keep working.
    { path: "/admin", redirect: "/admin/server" },
    { path: "/admin/dashboard", redirect: "/admin/server" },
    { path: "/admin/:tab(download|reencode|credentials)", redirect: (to) => ({ path: "/admin/server", query: { tab: to.params.tab } }) },
    { path: "/admin/tasks", redirect: "/tasks" },
    { path: "/admin/issues", redirect: "/admin/portal/duplicates" },
    { path: "/admin/library", redirect: "/admin/portal/maintenance" },
    { path: "/users", redirect: "/admin/portal/users" },
    // Signed in already (e.g. Back to the sign-in page): the library, not "Page not found".
    { path: "/login", redirect: "/" },
    { path: "/:pathMatch(.*)*", name: "not-found", component: NotFoundView, meta: { title: "Page not found" } },
  ],
  scrollBehavior(to, from, saved) {
    // Filter/search edits replace the query on the same page: keep position.
    if (!saved && to.name === "library" && from.name === "library" && to.query.page === from.query.page) return false;
    let top = saved?.top ?? 0;
    if (!saved && to.name === "library" && from.name && from.name !== "library") top = libraryScroll.get(to.fullPath) ?? 0;
    if (!top) return { top: 0 };
    // Wait until the new page is in the DOM, or the old (shorter) page would clamp the position.
    return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve({ top }))));
  },
});

router.beforeEach((to, from) => {
  if (from.name === "library" && to.name !== "library") libraryScroll.set(from.fullPath, window.scrollY);
});
router.afterEach((to) => { if (to.name === "library") rememberLibrary(to); });
