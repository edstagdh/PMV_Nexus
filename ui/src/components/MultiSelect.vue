<script setup>
// Searchable multi-select (tags, performers, creators): type to filter the
// options, click to toggle. Selected values show as removable chips.
import { ref, computed, onMounted, onBeforeUnmount } from "vue";

const props = defineProps({
  options: { type: Array, default: () => [] },
  modelValue: { type: Array, default: () => [] },
  placeholder: { type: String, default: "Search…" },
  // For a <label for="…"> title. Don't wrap this component in a <label>: a click on
  // the label would go to the first chip's × and remove it (2.17.3).
  inputId: { type: String, default: undefined },
});
const emit = defineEmits(["update:modelValue"]);
const query = ref("");
const open = ref(false);
const root = ref(null);
const MAX_SHOWN = 80;

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  const list = q ? props.options.filter((o) => o.toLowerCase().includes(q)) : props.options;
  return list.slice(0, MAX_SHOWN);
});
const selected = computed(() => new Set(props.modelValue));

function toggle(option) {
  const next = selected.value.has(option)
    ? props.modelValue.filter((v) => v !== option)
    : [...props.modelValue, option];
  emit("update:modelValue", next);
  query.value = "";
}
function onKey(e) {
  if (e.key === "Enter" && filtered.value.length) { e.preventDefault(); toggle(filtered.value[0]); }
  if (e.key === "Escape" || e.key === "Tab") open.value = false;
  if (e.key === "Backspace" && !query.value && props.modelValue.length) emit("update:modelValue", props.modelValue.slice(0, -1));
}
const outside = (e) => { if (root.value && !root.value.contains(e.target)) open.value = false; };
const onScroll = () => { open.value = false; };
onMounted(() => { document.addEventListener("mousedown", outside); window.addEventListener("scroll", onScroll, { passive: true }); });
onBeforeUnmount(() => { document.removeEventListener("mousedown", outside); window.removeEventListener("scroll", onScroll); });
</script>

<template>
  <div ref="root" class="ms" :class="{ open }">
    <div class="ms-box" @click="open = true; $refs.input.focus()">
      <span v-for="v in modelValue" :key="v" class="chip chip-accent">
        {{ v }}<button type="button" class="chip-x" :aria-label="`Remove ${v}`" @click.stop="toggle(v)">×</button>
      </span>
      <input :id="inputId" ref="input" v-model="query" class="ms-input" :placeholder="modelValue.length ? '' : placeholder"
             @focus="open = true" @keydown="onKey">
    </div>
    <div v-if="open" class="ms-menu card scroll-thin">
      <button v-for="o in filtered" :key="o" type="button" class="ms-option" :class="{ on: selected.has(o) }" @click="toggle(o)">
        <span class="ms-check">{{ selected.has(o) ? "✓" : "" }}</span>{{ o }}
      </button>
      <div v-if="!filtered.length" class="ms-empty muted">No matches</div>
      <div v-else-if="filtered.length === MAX_SHOWN" class="ms-empty muted">Type to narrow down…</div>
    </div>
  </div>
</template>

<style scoped>
.ms { position: relative; }
.ms-box { display: flex; flex-wrap: wrap; gap: 0.25rem; align-items: center; min-height: 2.25rem; padding: 0.25rem 0.375rem; border: 1px solid var(--border); border-radius: var(--radius); background: var(--bg-elev); cursor: text; }
.ms.open .ms-box { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.ms-input { flex: 1; min-width: 5rem; height: 1.625rem; border: 0; outline: none; background: transparent; color: var(--text); font: 0.875rem var(--font); }
.ms-menu { position: absolute; z-index: 20; top: calc(100% + 0.25rem); left: 0; right: 0; max-height: 16.25rem; overflow-y: auto; padding: 0.25rem; box-shadow: var(--shadow); }
.ms-option { display: flex; align-items: center; gap: 0.375rem; width: 100%; padding: 0.375rem 0.5rem; border: 0; border-radius: var(--radius-sm); background: none; color: var(--text-2); font: 0.8125rem var(--font); text-align: left; cursor: pointer; }
.ms-option:hover { background: var(--panel-2); color: var(--text); }
.ms-option.on { color: var(--accent); }
.ms-check { width: 0.875rem; font-weight: 700; }
.ms-empty { padding: 0.5rem; font-size: 0.7812rem; }
</style>
