<script setup>
// The bell (sidebar and mobile bar) and the notification center it opens:
// problems (failed downloads / jobs, low disk space...) or everything,
// newest first. The badge counts unread problems.
import { t } from "../lib/i18n.js";
import { askConfirm, confirmState } from "../stores/confirm.js";
import { ref, computed, onBeforeUnmount } from "vue";
import { useRouter } from "vue-router";
import Icon from "./Icon.vue";
import { notes, markAllRead, clearNotifications, deleteNotification } from "../stores/notifications.js";
import { toast } from "../stores/toast.js";
import { formatDateTime } from "../lib/format.js";
import { hasPermission } from "../stores/session.js";
import { copyText } from "../lib/clipboard.js";

defineProps({ compact: Boolean });
const router = useRouter();
const open = ref(false);
const show = ref("problems");
const ICON = { success: "check", info: "bell", warning: "disk", error: "x" };
const items = computed(() => (show.value === "problems" ? notes.items.filter((n) => n.level === "error" || n.level === "warning") : notes.items));

function toggle() {
  open.value = !open.value;
  if (open.value) show.value = notes.unread || !notes.items.length ? "problems" : "all";
}
// Clicking a notification opens its menu (3.6.0): where it points, Tasks for work that ran there,
// the log from around that moment, and Copy.
const openId = ref(null);
const PLACES = [[/^\/video\//, t("Open the video")], [/^\/tasks/, t("Open Tasks")], [/songs/, t("Open Songs")],
  [/duplicates/, t("Open Duplicates")], [/backups/, t("Open Backups")], [/logs/, t("Open Logs")]];
const WORK = new Set(["download", "reencode", "repair", "upload", "job", "duplicate"]);
function linksOf(n) {
  const out = [];
  if (n.link) out.push({ label: (PLACES.find(([re]) => re.test(n.link)) || [null, t("Open")])[1], to: n.link, icon: "external" });
  if (WORK.has(n.category) && !String(n.link || "").startsWith("/tasks")) out.push({ label: t("Open Tasks"), to: "/tasks", icon: "list" });
  if (hasPermission("logs.view") && n.created_at) {
    const at = Number(n.created_at);
    out.push({ label: t("See the log from then"), icon: "logs", to: { name: "portal", params: { tab: "logs" }, query: {
      cat: "all", ...(n.level === "error" || n.level === "warning" ? { level: "WARNING" } : {}),
      from: Math.floor(at - 120), to: Math.ceil(at + 5) } } });
  }
  return out;
}
function toggleMenu(n) { openId.value = openId.value === n.id ? null : n.id; }
function go(to) {
  open.value = false;
  openId.value = null;
  router.push(to);
}
async function copyNote(n) {
  const ok = await copyText([n.title, n.body, formatDateTime(n.created_at)].filter(Boolean).join("\n"));
  toast(ok ? t("Copied") : t("The browser blocked copying"), { type: ok ? "success" : "error" });
}
async function dismiss(n) {   // 3.11.3: one at a time
  if (openId.value === n.id) openId.value = null;
  try { await deleteNotification(n.id); } catch (err) { toast(err.message || t("That didn't work"), { type: "error" }); }
}
async function readAll() {
  try { await markAllRead(); } catch (err) { toast(err.message || t("That didn't work"), { type: "error" }); }
}
async function clearAll() {
  if (!(await askConfirm(t("Delete all notifications?"), { ok: t("Delete"), danger: true }))) return;
  try { await clearNotifications(); } catch (err) { toast(err.message || t("That didn't work"), { type: "error" }); }
}
// Esc answers an open confirmation ("Delete all notifications?") first, not this panel (3.2.1).
function onKey(e) { if (e.key === "Escape" && !confirmState.open) open.value = false; }
window.addEventListener("keydown", onKey);
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <button class="btn btn-ghost btn-icon bell" :class="{ 'btn-sm': compact }" :title="notes.unread ? $t('{n} unread problem(s)', { n: notes.unread }) : $t('Notifications')"
          :aria-label="notes.unread ? $t('Notifications, {n} unread problems', { n: notes.unread }) : $t('Notifications')" :aria-expanded="open" @click="toggle">
    <Icon name="bell" />
    <span v-if="notes.unread" class="badge">{{ notes.unread > 99 ? "99+" : notes.unread }}</span>
  </button>
  <Teleport to="body">
    <div v-if="open" class="nc-overlay" @click="open = false" />
    <Transition name="nc">
      <aside v-if="open" class="nc card" role="dialog" :aria-label="$t('Notifications')">
        <header class="nc-head">
          <h2>{{ $t("Notifications") }}</h2>
          <button class="btn btn-ghost btn-icon btn-sm" :aria-label="$t('Close')" @click="open = false"><Icon name="x" /></button>
        </header>
        <div class="nc-bar">
          <div class="seg" role="tablist">
            <button role="tab" :aria-selected="show === 'problems'" :class="{ on: show === 'problems' }" @click="show = 'problems'">{{ $t("Problems") }}<span v-if="notes.unread" class="n">{{ notes.unread }}</span></button>
            <button role="tab" :aria-selected="show === 'all'" :class="{ on: show === 'all' }" @click="show = 'all'">{{ $t("All") }}</button>
          </div>
          <span style="flex: 1" />
          <button class="btn btn-ghost btn-sm" :disabled="!notes.unread" @click="readAll">{{ $t("Mark read") }}</button>
          <button class="btn btn-ghost btn-sm" :disabled="!notes.items.length" @click="clearAll">{{ $t("Clear") }}</button>
        </div>
        <ul v-if="items.length" class="nc-list scroll-thin">
          <li v-for="n in items" :key="n.id" :class="[n.level, { unread: !n.read && (n.level === 'error' || n.level === 'warning'), open: openId === n.id }]">
            <button class="row" type="button" :aria-expanded="openId === n.id" @click="toggleMenu(n)">
              <span class="ic"><Icon :name="ICON[n.level] || 'bell'" :size="16" /></span>
              <span class="txt">
                <span class="t">{{ n.title }}</span>
                <span v-if="n.body" class="b">{{ n.body }}</span>
                <span class="when">{{ formatDateTime(n.created_at) }}</span>
              </span>
            </button>
            <button class="dismiss btn btn-ghost btn-icon btn-sm" type="button" :title="$t('Delete this notification')"
                    :aria-label="$t('Delete this notification')" @click="dismiss(n)"><Icon name="x" :size="14" /></button>
            <div v-if="openId === n.id" class="nmenu" role="menu">
              <button v-for="l in linksOf(n)" :key="l.label" class="btn btn-sm" role="menuitem" type="button" @click="go(l.to)">{{ l.label }}</button>
              <button class="btn btn-ghost btn-sm" role="menuitem" type="button" @click="copyNote(n)">{{ $t("Copy") }}</button>
            </div>
          </li>
        </ul>
        <p v-else class="muted nc-empty">{{ show === "problems" ? $t("No problems — nothing failed.") : $t("Nothing yet.") }}</p>
        <p class="muted nc-foot">{{ $t("Pop-ups for these can be turned off per kind in My Settings → Notifications.") }}</p>
      </aside>
    </Transition>
  </Teleport>
</template>

<style scoped>
.bell { position: relative; }
.badge { position: absolute; top: -0.15rem; inset-inline-end: -0.15rem; min-width: 1.1rem; height: 1.1rem; padding: 0 0.25rem; border-radius: 999px;
  background: var(--danger); color: #fff; font-size: 0.62rem; font-weight: 800; display: grid; place-items: center; line-height: 1; }
.nc-overlay { position: fixed; inset: 0; z-index: 150; background: rgb(0 0 0 / 0.25); }
.nc { position: fixed; top: 0.75rem; inset-inline-end: 0.75rem; bottom: 0.75rem; width: min(26rem, calc(100vw - 1.5rem)); z-index: 151;
  display: flex; flex-direction: column; padding: 0; overflow: hidden; box-shadow: var(--shadow); }
.nc-head { display: flex; align-items: center; justify-content: space-between; padding: 0.75rem 1rem 0.25rem; }
.nc-head h2 { font-size: 1.05rem; margin: 0; }
.nc-bar { display: flex; align-items: center; gap: 0.25rem; padding: 0.25rem 1rem 0.625rem; border-bottom: 1px solid var(--border); }
.seg { display: inline-flex; border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
.seg button { border: 0; background: none; color: var(--text-2); font: inherit; font-size: 0.8rem; padding: 0.3rem 0.7rem; cursor: pointer; display: inline-flex; gap: 0.35rem; align-items: center; }
.seg button.on { background: var(--panel-hover); color: var(--text); font-weight: 650; }
.seg .n { background: var(--danger); color: #fff; border-radius: 999px; font-size: 0.65rem; padding: 0 0.35rem; font-weight: 800; }
.nc-list { list-style: none; margin: 0; padding: 0; overflow-y: auto; flex: 1; }
.nc-list li { border-bottom: 1px solid var(--border); }
.row { display: grid; grid-template-columns: 1.75rem minmax(0, 1fr); gap: 0.5rem; width: 100%; padding: 0.625rem 1rem; border: 0;
  background: none; color: inherit; font: inherit; text-align: start; cursor: pointer; }
.row:hover, li.open .row { background: var(--panel-hover); }
.nc-list li { position: relative; }
.nc-list .row { padding-inline-end: 2.5rem; }
.dismiss { position: absolute; top: 0.4rem; inset-inline-end: 0.5rem; opacity: 0; }
.nc-list li:hover .dismiss, .dismiss:focus-visible { opacity: 1; }
@media (hover: none) { .dismiss { opacity: 0.7; } }
.row:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.txt { display: flex; flex-direction: column; min-width: 0; }
.nmenu { display: flex; flex-wrap: wrap; gap: 0.35rem; padding-block: 0 0.65rem; padding-inline: 3.25rem 1rem; background: var(--panel-hover); }
.nc-list li.unread { background: color-mix(in srgb, var(--danger) 7%, transparent); }
.ic { width: 1.75rem; height: 1.75rem; border-radius: 50%; display: grid; place-items: center; background: var(--panel-hover); color: var(--text-2); }
.success .ic { color: var(--success); }
.error .ic { color: var(--danger); }
.warning .ic { color: var(--warning, #f59e0b); }
.t { font-size: 0.84rem; font-weight: 600; overflow-wrap: anywhere; }
.b { font-size: 0.78rem; color: var(--text-2); overflow-wrap: anywhere; margin-top: 0.1rem; }
.when { font-size: 0.7rem; color: var(--text-dim); margin-top: 0.15rem; }
.nc-empty { padding: 2rem 1rem; text-align: center; flex: 1; }
.nc-foot { font-size: 0.72rem; padding: 0.5rem 1rem; margin: 0; border-top: 1px solid var(--border); }
.nc-enter-from, .nc-leave-to { opacity: 0; transform: translateX(calc(1rem * var(--dir-x))); }
.nc-enter-active, .nc-leave-active { transition: all 0.18s var(--ease); }
</style>
