<script setup>
// "⋮" button with the item's actions. The menu is placed on <body> so it is
// never clipped by a scrolling table or a card. `extra` actions (same shape as
// lib/actions.js's) go first, e.g. a playlist's "Remove from playlist".
import { ref, computed, nextTick, onBeforeUnmount } from "vue";
import Icon from "./Icon.vue";
import { itemActions } from "../lib/actions.js";

const props = defineProps({ item: Object, context: { type: String, default: "library" }, extra: { type: Array, default: () => [] } });
const open = ref(false);
const btn = ref(null);
const menu = ref(null);
const pos = ref({ top: 0, left: 0 });
const actions = computed(() => {
  const base = itemActions(props.item, { context: props.context });
  if (!props.extra.length) return base;
  return [...props.extra, ...base.map((a, i) => (i === 0 ? { ...a, divider: true } : a))];
});

async function toggle() {
  if (open.value) return close();
  open.value = true;
  await nextTick();
  const r = btn.value.getBoundingClientRect();
  const m = menu.value.getBoundingClientRect();
  const left = Math.min(window.innerWidth - m.width - 8, Math.max(8, r.right - m.width));
  const below = r.bottom + 4 + m.height < window.innerHeight;
  pos.value = { top: below ? r.bottom + 4 : Math.max(8, r.top - m.height - 4), left };
  document.addEventListener("mousedown", outside);
  window.addEventListener("scroll", close, { passive: true, capture: true });
  document.addEventListener("keydown", onKey);
}
function close() {
  open.value = false;
  document.removeEventListener("mousedown", outside);
  window.removeEventListener("scroll", close, { capture: true });
  document.removeEventListener("keydown", onKey);
}
function outside(e) { if (!menu.value?.contains(e.target) && !btn.value?.contains(e.target)) close(); }
function onKey(e) { if (e.key === "Escape") close(); }
function run(a) { close(); a.run?.(); }
onBeforeUnmount(close);
</script>

<template>
  <button ref="btn" class="btn btn-ghost btn-icon btn-sm kebab" :class="{ active: open }" title="Actions" aria-haspopup="menu"
          :aria-expanded="open" @click.stop.prevent="toggle"><Icon name="more" /></button>
  <Teleport to="body">
    <div v-if="open" ref="menu" class="menu card" role="menu" :style="{ top: `${pos.top}px`, left: `${pos.left}px` }">
      <template v-for="a in actions" :key="a.key">
        <div v-if="a.divider" class="menu-divider" />
        <a v-if="a.href" class="menu-item" role="menuitem" :href="a.href" target="_blank" rel="noopener noreferrer" @click="close">
          <Icon :name="a.icon" :size="16" />{{ a.label }}
        </a>
        <button v-else class="menu-item" :class="{ danger: a.danger }" role="menuitem" @click="run(a)">
          <Icon :name="a.icon" :size="16" />{{ a.label }}
        </button>
      </template>
    </div>
  </Teleport>
</template>

<style scoped>
.kebab { flex-shrink: 0; }
.menu { position: fixed; z-index: 120; min-width: 12.5rem; padding: 0.25rem; box-shadow: var(--shadow); animation: pop 0.12s var(--ease); }
.menu-item { display: flex; align-items: center; gap: 0.625rem; width: 100%; padding: 0.5rem 0.625rem; border: 0; border-radius: var(--radius-sm); background: none; color: var(--text-2); font: 0.84rem var(--font); text-align: left; cursor: pointer; text-decoration: none; }
.menu-item:hover { background: var(--panel-2); color: var(--text); text-decoration: none; }
.menu-item.danger { color: var(--danger); }
.menu-divider { height: 1px; margin: 0.25rem 0; background: var(--border); }
@keyframes pop { from { opacity: 0; transform: translateY(-0.25rem); } }
</style>
