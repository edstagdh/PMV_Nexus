<script setup>
// Edit a video's performers, tags, music and content flags by hand. Edited
// fields are kept when the library is synced, updated or repaired; "Use the
// site's" drops the edit again.
import { ref, onMounted } from "vue";
import { normalizeMetadata } from "../../lib/metadata.js";
import ModalDialog from "../ModalDialog.vue";
import TagInput from "../TagInput.vue";
import { api } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";
import { session } from "../../stores/session.js";

const props = defineProps({ item: Object, options: Object });
// Content flags only exist in the video's metadata file; loaded below.
const siteFlags = ref([]);
const emit = defineEmits(["close"]);

const FIELDS = [
  { key: "performers", label: "Performers", chip: "chip-performer", placeholder: "Add a performer…" },
  { key: "tags", label: "Tags", chip: "chip-tag", placeholder: "Add a tag…" },
  { key: "music", label: "Music", chip: "chip-music", placeholder: "Add music (Song — Artist)…" },
  { key: "flags", label: "Content flags", chip: "", placeholder: "Add a flag…" },
];
const edits = props.item.user_edits || {};
const initial = {
  performers: [...(props.item.performers || [])],
  tags: [...(props.item.tags || [])],
  music: [...(props.item.music || [])],
  flags: [...(edits.flags ?? [])],
};
const values = ref(JSON.parse(JSON.stringify(initial)));
const reset = ref(new Set());
const busy = ref(false);
const error = ref("");

onMounted(async () => {
  try {
    const meta = await api(`/api/library/${encodeURIComponent(props.item.id)}/metadata`);
    siteFlags.value = normalizeMetadata(meta)?.flags || [];
    if (!("flags" in edits)) { initial.flags = [...siteFlags.value]; values.value.flags = [...siteFlags.value]; }
  } catch { /* no metadata file: no site flags */ }
});
const isEdited = (k) => k in edits && !reset.value.has(k);
function useSite(k) {
  const r = new Set(reset.value); r.add(k); reset.value = r;
  values.value[k] = k === "flags" ? [...siteFlags.value] : [...(props.item.site_values?.[k] || [])];
}
function onChange(k, v) {
  values.value[k] = v;
  if (reset.value.has(k)) { const r = new Set(reset.value); r.delete(k); reset.value = r; }
}

async function save() {
  const body = {};
  for (const { key } of FIELDS) {
    if (reset.value.has(key)) body[key] = null;
    else if (JSON.stringify(values.value[key]) !== JSON.stringify(initial[key])) body[key] = values.value[key];
  }
  if (!Object.keys(body).length) { emit("close"); return; }
  busy.value = true;
  error.value = "";
  try {
    await api(`/api/library/${encodeURIComponent(props.item.id)}/details`, { method: "PATCH", body });
    toast("Details saved", { type: "success" });
    emit("close");
  } catch (err) {
    error.value = err.message || "Could not save";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <ModalDialog title="Edit details" width="min(90rem, max(80vw, 44rem))" @close="emit('close')">
    <p class="muted intro">Your changes are kept when the library is synced, updated or repaired.
      Click a box to see every existing value; whether the list stays open after a pick is set in My settings → Video page.</p>
    <div class="fields">
    <div v-for="f in FIELDS" :key="f.key" class="field-block">
      <div class="field-head">
        <span class="field-label">{{ f.label }}</span>
        <span v-if="isEdited(f.key)" class="edited">edited</span>
        <button v-if="isEdited(f.key)" type="button" class="btn btn-ghost btn-sm" title="Drop your edit and use what PMVHaven has"
                @click="useSite(f.key)">Use the site's</button>
        <span v-else-if="reset.has(f.key)" class="muted small">will use the site's on save</span>
      </div>
      <TagInput :model-value="values[f.key]" :field="f.key" :placeholder="f.placeholder" :chip-class="f.chip"
                :keep-open="session.prefs.suggestKeepOpen" show-all inline
                @update:model-value="onChange(f.key, $event)" />
    </div>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <template #footer>
      <button class="btn" @click="emit('close')">Cancel</button>
      <button class="btn btn-primary" :disabled="busy" @click="save">{{ busy ? "Saving…" : "Save" }}</button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.intro { margin: 0 0 0.75rem; font-size: 0.84rem; }
/* The fields side by side (as many columns as fit), each with its list under it. */
.fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr)); gap: 0.875rem 1rem; align-items: start; }
.field-block { display: flex; flex-direction: column; gap: 0.375rem; min-width: 0; }
.field-head { display: flex; align-items: center; gap: 0.5rem; min-height: 1.875rem; }
.field-label { font-weight: 650; font-size: 0.84rem; }
.edited { font-size: 0.7rem; font-weight: 700; color: var(--accent); background: var(--accent-soft); padding: 0.05rem 0.4rem; border-radius: 0.25rem; }
.small { font-size: 0.78rem; }
.error { color: var(--danger); font-size: 0.84rem; }
</style>
