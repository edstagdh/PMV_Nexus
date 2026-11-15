<script setup>
// A page's tab row whose order you choose (3.4.0, lib/tabOrder.js): drag a tab to
// move it, or "Arrange" for a list with up / down buttons (phones, keyboard) and
// "Back to the usual order". `tabs` comes in already ordered (orderTabs).
import { ref, onBeforeUnmount } from "vue";
import Icon from "./Icon.vue";
import { saveTabOrder, resetTabOrder } from "../lib/tabOrder.js";

const props = defineProps({
  tabs: { type: Array, required: true },       // [{key, label}] in your order
  active: { type: String, default: null },
  page: { type: String, required: true },      // "portal" | "server"
  label: { type: String, default: "Tabs" },
});
const emit = defineEmits(["select"]);

const dragKey = ref(null);
const overKey = ref(null);
const arranging = ref(false);
const root = ref(null);

function move(from, to) {
  const keys = props.tabs.map((t) => t.key);
  if (from < 0 || to < 0 || to >= keys.length || from === to) return;
  const [k] = keys.splice(from, 1);
  keys.splice(to, 0, k);
  saveTabOrder(props.page, keys);
}
function drop(key) {
  const keys = props.tabs.map((t) => t.key);
  if (dragKey.value && dragKey.value !== key) move(keys.indexOf(dragKey.value), keys.indexOf(key));
  dragKey.value = overKey.value = null;
}
function outside(e) { if (arranging.value && root.value && !root.value.contains(e.target)) arranging.value = false; }
function onKey(e) { if (e.key === "Escape") arranging.value = false; }
document.addEventListener("pointerdown", outside);
document.addEventListener("keydown", onKey);
onBeforeUnmount(() => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", onKey); });
</script>

<template>
  <div ref="root" class="tabbar">
    <nav class="tabs scroll-thin" role="tablist" :aria-label="label">
      <button v-for="t in tabs" :key="t.key" role="tab" class="tab" draggable="true"
              :class="{ active: active === t.key, dragging: dragKey === t.key, over: overKey === t.key && dragKey !== t.key }"
              :aria-selected="active === t.key" title="Drag to move this tab"
              @click="emit('select', t.key)"
              @dragstart="dragKey = t.key; $event.dataTransfer.effectAllowed = 'move'"
              @dragover.prevent="overKey = t.key" @dragleave="overKey === t.key && (overKey = null)"
              @drop.prevent="drop(t.key)" @dragend="dragKey = overKey = null">{{ t.label }}</button>
    </nav>
    <button v-if="tabs.length > 1" type="button" class="btn btn-sm btn-ghost arrange" :aria-expanded="arranging"
            title="Arrange tabs" aria-label="Arrange tabs" @click="arranging = !arranging"><Icon name="drag" /></button>
    <div v-if="arranging" class="arrange-pop" role="dialog" aria-label="Arrange tabs">
      <p class="hint">Your order (also: drag a tab).</p>
      <ol>
        <li v-for="(t, i) in tabs" :key="t.key">
          <span class="nm">{{ t.label }}</span>
          <button type="button" class="btn btn-sm btn-ghost" :disabled="i === 0" :aria-label="`Move ${t.label} earlier`" @click="move(i, i - 1)">↑</button>
          <button type="button" class="btn btn-sm btn-ghost" :disabled="i === tabs.length - 1" :aria-label="`Move ${t.label} later`" @click="move(i, i + 1)">↓</button>
        </li>
      </ol>
      <button type="button" class="btn btn-sm" @click="resetTabOrder(page); arranging = false">Back to the usual order</button>
    </div>
  </div>
</template>

<style scoped>
.tabbar { position: relative; display: flex; align-items: flex-end; gap: 0.25rem; min-width: 0; }
.tabbar .tabs { flex: 1 1 auto; min-width: 0; flex-wrap: nowrap; }
.tab { white-space: nowrap; }
.tab.dragging { opacity: 0.45; }
.tab.over { box-shadow: inset 2px 0 0 var(--accent); }
.arrange { flex: none; margin-bottom: 0.25rem; }
.arrange-pop { position: absolute; right: 0; top: 100%; z-index: 40; margin-top: 0.25rem; min-width: 15rem; padding: 0.75rem;
  background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow, 0 8px 24px rgba(0,0,0,.35));
  display: flex; flex-direction: column; gap: 0.5rem; }
.arrange-pop ol { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.15rem; max-height: 60vh; overflow: auto; }
.arrange-pop li { display: flex; align-items: center; gap: 0.25rem; }
.arrange-pop .nm { flex: 1; }
.hint { margin: 0; font-size: 0.8rem; color: var(--text-dim); }
</style>
