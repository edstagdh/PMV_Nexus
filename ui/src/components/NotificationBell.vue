<script setup>
// The bell (sidebar and mobile bar) and the notification center it opens:
// problems (failed downloads / jobs, low disk space...) or everything,
// newest first. The badge counts unread problems.
import { askConfirm } from "../stores/confirm.js";
import { ref, computed, onBeforeUnmount } from "vue";
import { useRouter } from "vue-router";
import Icon from "./Icon.vue";
import { notes, markAllRead, clearNotifications } from "../stores/notifications.js";
import { toast } from "../stores/toast.js";
import { formatDateTime } from "../lib/format.js";

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
function go(n) {
  if (!n.link) return;
  open.value = false;
  router.push(n.link);
}
async function readAll() {
  try { await markAllRead(); } catch (err) { toast(err.message || "That didn't work", { type: "error" }); }
}
async function clearAll() {
  if (!(await askConfirm("Delete all notifications?", { ok: "Delete", danger: true }))) return;
  try { await clearNotifications(); } catch (err) { toast(err.message || "That didn't work", { type: "error" }); }
}
function onKey(e) { if (e.key === "Escape") open.value = false; }
window.addEventListener("keydown", onKey);
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <button class="btn btn-ghost btn-icon bell" :class="{ 'btn-sm': compact }" :title="notes.unread ? `${notes.unread} unread problem(s)` : 'Notifications'"
          :aria-label="notes.unread ? `Notifications, ${notes.unread} unread problems` : 'Notifications'" :aria-expanded="open" @click="toggle">
    <Icon name="bell" />
    <span v-if="notes.unread" class="badge">{{ notes.unread > 99 ? "99+" : notes.unread }}</span>
  </button>
  <Teleport to="body">
    <div v-if="open" class="nc-overlay" @click="open = false" />
    <Transition name="nc">
      <aside v-if="open" class="nc card" role="dialog" aria-label="Notifications">
        <header class="nc-head">
          <h2>Notifications</h2>
          <button class="btn btn-ghost btn-icon btn-sm" aria-label="Close" @click="open = false"><Icon name="x" /></button>
        </header>
        <div class="nc-bar">
          <div class="seg" role="tablist">
            <button role="tab" :aria-selected="show === 'problems'" :class="{ on: show === 'problems' }" @click="show = 'problems'">Problems<span v-if="notes.unread" class="n">{{ notes.unread }}</span></button>
            <button role="tab" :aria-selected="show === 'all'" :class="{ on: show === 'all' }" @click="show = 'all'">All</button>
          </div>
          <span style="flex: 1" />
          <button class="btn btn-ghost btn-sm" :disabled="!notes.unread" @click="readAll">Mark read</button>
          <button class="btn btn-ghost btn-sm" :disabled="!notes.items.length" @click="clearAll">Clear</button>
        </div>
        <ul v-if="items.length" class="nc-list scroll-thin">
          <li v-for="n in items" :key="n.id" :class="[n.level, { unread: !n.read && (n.level === 'error' || n.level === 'warning'), link: n.link }]" @click="go(n)">
            <span class="ic"><Icon :name="ICON[n.level] || 'bell'" :size="16" /></span>
            <div class="txt">
              <div class="t">{{ n.title }}</div>
              <div v-if="n.body" class="b">{{ n.body }}</div>
              <div class="when">{{ formatDateTime(n.created_at) }}</div>
            </div>
          </li>
        </ul>
        <p v-else class="muted nc-empty">{{ show === "problems" ? "No problems — nothing failed." : "Nothing yet." }}</p>
        <p class="muted nc-foot">Pop-ups for these can be turned off per kind in My Settings → Notifications.</p>
      </aside>
    </Transition>
  </Teleport>
</template>

<style scoped>
.bell { position: relative; }
.badge { position: absolute; top: -0.15rem; right: -0.15rem; min-width: 1.1rem; height: 1.1rem; padding: 0 0.25rem; border-radius: 999px;
  background: var(--danger); color: #fff; font-size: 0.62rem; font-weight: 800; display: grid; place-items: center; line-height: 1; }
.nc-overlay { position: fixed; inset: 0; z-index: 150; background: rgb(0 0 0 / 0.25); }
.nc { position: fixed; top: 0.75rem; right: 0.75rem; bottom: 0.75rem; width: min(26rem, calc(100vw - 1.5rem)); z-index: 151;
  display: flex; flex-direction: column; padding: 0; overflow: hidden; box-shadow: var(--shadow); }
.nc-head { display: flex; align-items: center; justify-content: space-between; padding: 0.75rem 1rem 0.25rem; }
.nc-head h2 { font-size: 1.05rem; margin: 0; }
.nc-bar { display: flex; align-items: center; gap: 0.25rem; padding: 0.25rem 1rem 0.625rem; border-bottom: 1px solid var(--border); }
.seg { display: inline-flex; border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
.seg button { border: 0; background: none; color: var(--text-2); font: inherit; font-size: 0.8rem; padding: 0.3rem 0.7rem; cursor: pointer; display: inline-flex; gap: 0.35rem; align-items: center; }
.seg button.on { background: var(--panel-hover); color: var(--text); font-weight: 650; }
.seg .n { background: var(--danger); color: #fff; border-radius: 999px; font-size: 0.65rem; padding: 0 0.35rem; font-weight: 800; }
.nc-list { list-style: none; margin: 0; padding: 0; overflow-y: auto; flex: 1; }
.nc-list li { display: grid; grid-template-columns: 1.75rem minmax(0, 1fr); gap: 0.5rem; padding: 0.625rem 1rem; border-bottom: 1px solid var(--border); }
.nc-list li.link { cursor: pointer; }
.nc-list li.link:hover { background: var(--panel-hover); }
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
.nc-enter-from, .nc-leave-to { opacity: 0; transform: translateX(1rem); }
.nc-enter-active, .nc-leave-active { transition: all 0.18s var(--ease); }
</style>
