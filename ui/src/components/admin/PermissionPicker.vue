<script setup>
// Checkboxes for permissions (backend/permissions.py), grouped by area.
// v-model is a list of permission keys. The old catch-all "library.manage"
// is shown as the permissions it stands for.
import { computed } from "vue";

const props = defineProps({ catalog: { type: Array, required: true }, modelValue: { type: Array, default: () => [] },
  inherited: { type: Array, default: () => [] } });  // already given by role/groups: shown ticked and greyed
const emit = defineEmits(["update:modelValue"]);
const LIBRARY_MANAGE = ["videos.add", "videos.upload", "videos.edit", "videos.delete", "videos.maintain",
  "names.manage", "performers.manage", "duplicates.manage", "maintenance.run", "tasks.view", "tasks.manage"];
const expand = (list) => {
  const s = new Set(list || []);
  if (s.has("library.manage")) { s.delete("library.manage"); LIBRARY_MANAGE.forEach((p) => s.add(p)); }
  return s;
};
const value = computed(() => expand(props.modelValue));
const inh = computed(() => expand(props.inherited));
const areas = computed(() => {
  const out = [];
  for (const p of props.catalog) {
    let a = out.find((x) => x.name === p.area);
    if (!a) out.push((a = { name: p.area, items: [] }));
    a.items.push(p);
  }
  return out;
});
function toggle(key, on) {
  const s = new Set(value.value);
  on ? s.add(key) : s.delete(key);
  if (key === "tasks.manage" && on) s.add("tasks.view");
  emit("update:modelValue", [...s].sort());
}
</script>

<template>
  <div class="pp">
    <fieldset v-for="a in areas" :key="a.name">
      <legend>{{ a.name }}</legend>
      <label v-for="p in a.items" :key="p.key" class="perm" :class="{ inherited: inh.has(p.key) && !value.has(p.key) }"
             :title="inh.has(p.key) && !value.has(p.key) ? 'Already given by the role or a group' : p.description">
        <input type="checkbox" :checked="value.has(p.key) || inh.has(p.key)" :disabled="inh.has(p.key) && !value.has(p.key)"
               @change="toggle(p.key, $event.target.checked)">
        <span><strong>{{ p.label }}</strong><small>{{ p.description }}</small></span>
      </label>
    </fieldset>
  </div>
</template>

<style scoped>
.pp { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 16rem), 1fr)); gap: 0.75rem 1.25rem; }
fieldset { border: 0; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.3rem; }
legend { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); margin-bottom: 0.25rem; }
.perm { display: flex; gap: 0.5rem; align-items: flex-start; font-size: 0.82rem; cursor: pointer; }
.perm input { margin-top: 0.2rem; }
.perm span { display: flex; flex-direction: column; }
.perm small { color: var(--text-2); font-size: 0.72rem; }
.perm.inherited { opacity: 0.6; cursor: default; }
</style>
