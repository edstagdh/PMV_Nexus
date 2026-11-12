// The browser tab's title: "<page> - PMV Nexus" ("PMV Nexus"
// alone on the library). Fixed page names come from the route's
// meta.title (router.js); pages named after their content (a video, a
// playlist) report it with setPageTitle(), tied to the path they're on so a
// stale name never sticks after navigating away. App.vue applies it.
import { reactive } from "vue";

export const APP_NAME = "PMV Nexus";
export const pageTitle = reactive({ path: null, text: "" });

/** Called by a page once its name is known (e.g. the video's title). */
export function setPageTitle(path, text) {
  pageTitle.path = path;
  pageTitle.text = text || "";
}

export function formatTitle(name) {
  return name ? `${name} - ${APP_NAME}` : APP_NAME;
}

/** meta.title may be a string or a function of the route (e.g. Help tabs). */
export function routeTitle(route) {
  const t = route.meta?.title;
  return typeof t === "function" ? t(route) : t || "";
}
