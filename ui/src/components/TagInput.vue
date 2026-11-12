<script setup>
// Editable list of values shown as chips, with suggestions from what already
// exists in the library (GET /api/library/suggest): typing "skyla" offers
// "Skylar Vox" and "Skylar Snow". Enter or comma adds the highlighted
// suggestion (or the typed text) and closes the list (keepOpen: it stays open
// for the next pick); ↑/↓ move (and reopen it), Esc closes; × or Backspace on
// an empty box removes. showAll lists every existing value (scrolling) instead
// of the best dozen; inline puts the list under the box in the page flow
// instead of floating over what's below.
import { ref, computed, watch, onBeforeUnmount } from "vue";
import { api, qs } from "../lib/api.js";

const props = defineProps({
  modelValue: Array, field: String, placeholder: String, chipClass: String,
  keepOpen: Boolean, showAll: Boolean, inline: Boolean,
  inputId: String, // for a <label for="…">; never wrap it in a <label> (a click would remove the first chip)
});
const emit = defineEmits(["update:modelValue"]);
const text = ref("");
const box = ref(null);
const results = ref([]);
const open = ref(false);
const active = ref(-1);
const listId = `sugg-${Math.random().toString(36).slice(2)}`;
let timer = null;
let seq = 0;

const shown = computed(() => {
  const have = new Set(props.modelValue.map((v) => v.toLowerCase()));
  const left = results.value.filter((r) => !have.has(r.value.toLowerCase()));
  return props.showAll ? left : left.slice(0, 12);
});

async function fetchSuggestions() {
  if (!props.field) return;
  const mine = ++seq;
  try {
    const r = await api(`/api/library/suggest?${qs({ field: props.field, q: text.value, limit: props.showAll ? 1000 : 30 })}`);
    if (mine !== seq) return;
    results.value = r.items;
    active.value = shown.value.length && text.value.trim() ? 0 : -1;
  } catch { results.value = []; }
}
// After a value is added the box is emptied: that must not reopen the list
// (it closes on Enter / pick and reopens as soon as you type again).
let quiet = false;
watch(text, () => {
  if (quiet) { quiet = false; return; }
  clearTimeout(timer); timer = setTimeout(fetchSuggestions, 120); open.value = true;
});
onBeforeUnmount(() => clearTimeout(timer));

function add(raw) {
  const values = String(raw).split(",").map((v) => v.trim()).filter(Boolean);
  const next = [...props.modelValue];
  for (const v of values) if (!next.some((x) => x.toLowerCase() === v.toLowerCase())) next.push(v);
  emit("update:modelValue", next);
  if (text.value) quiet = true;
  text.value = "";
  active.value = -1;
  if (props.keepOpen) { open.value = true; fetchSuggestions(); } // the full list again, minus what was just added
  else open.value = false;
}
function pick(r) { add(r.value); box.value?.focus(); }
function remove(i) { emit("update:modelValue", props.modelValue.filter((_, j) => j !== i)); }

function onKey(e) {
  if (e.key === "ArrowDown" && shown.value.length) {
    e.preventDefault(); open.value = true; active.value = (active.value + 1) % shown.value.length;
  } else if (e.key === "ArrowUp" && shown.value.length) {
    e.preventDefault(); active.value = active.value <= 0 ? shown.value.length - 1 : active.value - 1;
  } else if (e.key === "Enter" || e.key === ",") {
    const hit = open.value && active.value >= 0 ? shown.value[active.value] : null;
    if (hit || text.value.trim()) { e.preventDefault(); hit ? add(hit.value) : add(text.value); }
  } else if (e.key === "Escape" && open.value) {
    e.preventDefault(); e.stopPropagation(); open.value = false;
  } else if (e.key === "Backspace" && !text.value && props.modelValue.length) {
    remove(props.modelValue.length - 1);
  }
}
function onFocus() { open.value = true; fetchSuggestions(); }
function onBlur() {
  // Let a click on a suggestion land before closing.
  setTimeout(() => { open.value = false; if (text.value.trim()) add(text.value); }, 150);
}
</script>

<template>
  <div class="tag-input-wrap">
    <div class="tag-input input" @click="box?.focus()">
      <span v-for="(v, i) in modelValue" :key="v" class="chip" :class="chipClass">
        {{ v }}<button type="button" class="chip-x" :aria-label="`Remove ${v}`" @click.stop="remove(i)">×</button>
      </span>
      <input :id="inputId" ref="box" v-model="text" role="combobox" autocomplete="off" :aria-expanded="open && shown.length > 0"
             :aria-controls="listId" :aria-activedescendant="active >= 0 ? `${listId}-${active}` : undefined"
             :placeholder="modelValue.length ? '' : placeholder" @keydown="onKey" @focus="onFocus" @blur="onBlur">
    </div>
    <ul v-if="open && shown.length" :id="listId" class="suggestions" :class="{ inline }" role="listbox">
      <li v-for="(r, i) in shown" :id="`${listId}-${i}`" :key="r.value" role="option" :aria-selected="i === active"
          :class="{ active: i === active }" @mousedown.prevent="pick(r)" @mouseenter="active = i">
        <span>{{ r.value }}</span><span v-if="r.count" class="count">{{ r.count }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.tag-input-wrap { position: relative; }
.tag-input { display: flex; flex-wrap: wrap; gap: 0.25rem; align-items: center; min-height: 2.5rem; height: auto; padding: 0.3rem 0.4rem; cursor: text; }
.tag-input input { flex: 1; min-width: 8rem; border: 0; outline: 0; background: transparent; color: var(--text); font: inherit; padding: 0.2rem; }
.suggestions { position: absolute; z-index: 30; left: 0; right: 0; top: calc(100% + 0.25rem); margin: 0; padding: 0.25rem; list-style: none; max-height: 16rem; overflow-y: auto;
  background: var(--panel); border: 1px solid var(--border-strong); border-radius: var(--radius); box-shadow: var(--shadow-lg, 0 10px 30px rgba(0,0,0,.4)); }
.suggestions.inline { position: static; margin-top: 0.25rem; box-shadow: none; max-height: 14rem; }
.suggestions li { display: flex; justify-content: space-between; gap: 1rem; padding: 0.4rem 0.6rem; border-radius: var(--radius-sm); cursor: pointer; font-size: 0.875rem; }
.suggestions li.active { background: var(--accent-soft); color: var(--text); }
.count { color: var(--text-dim); font-size: 0.75rem; font-variant-numeric: tabular-nums; }
</style>
