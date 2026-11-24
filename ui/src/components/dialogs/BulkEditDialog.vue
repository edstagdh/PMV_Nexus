<script setup>
// Library -> Select -> Edit: add or remove tags, performers and music on
// every selected video at once (POST /api/library/bulk-edit). Saved as hand
// edits, like Edit details, so later syncs don't undo them.
import { t, tn } from "../../lib/i18n.js";
import { ref, computed } from "vue";
import ModalDialog from "../ModalDialog.vue";
import TagInput from "../TagInput.vue";
import { api } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";

const props = defineProps({ ids: { type: Array, required: true } });
const emit = defineEmits(["close", "done"]);
const FIELDS = [
  { key: "performers", label: t("Performers"), chip: "chip-performer" },
  { key: "tags", label: t("Tags"), chip: "chip-tag" },
  { key: "music", label: t("Music"), chip: "chip-music" },
];
const add = ref({ performers: [], tags: [], music: [] });
const remove = ref({ performers: [], tags: [], music: [] });
const busy = ref(false);
const error = ref("");
const any = computed(() => FIELDS.some((f) => add.value[f.key].length || remove.value[f.key].length));

async function save() {
  const changes = {};
  for (const f of FIELDS) {
    if (add.value[f.key].length || remove.value[f.key].length) changes[f.key] = { add: add.value[f.key], remove: remove.value[f.key] };
  }
  busy.value = true;
  error.value = "";
  try {
    const r = await api("/api/library/bulk-edit", { method: "POST", body: { ids: props.ids, changes } });
    toast(tn("Changed {changed} of {n} video", "Changed {changed} of {n} videos", props.ids.length, { changed: r.videos }), { type: "success" });
    emit("done");
    emit("close");
  } catch (err) { error.value = err.message || t("Could not save"); }
  finally { busy.value = false; }
}
</script>

<template>
  <ModalDialog :title="$tn('Edit {n} video', 'Edit {n} videos', ids.length)" @close="emit('close')">
    <p class="muted small intro">{{ $t("What you add is added to each video; what you remove is taken off the ones that have it. Everything else on them stays as it is.") }}</p>
    <div v-for="f in FIELDS" :key="f.key" class="fld">
      <div class="lbl">{{ f.label }}</div>
      <div class="two">
        <div class="side"><label :for="`bulk-add-${f.key}`">{{ $t("Add") }}</label>
          <TagInput v-model="add[f.key]" :input-id="`bulk-add-${f.key}`" :field="f.key" :chip-class="f.chip" :placeholder="$t('Add {field}', { field: f.label.toLowerCase() })" inline /></div>
        <div class="side"><label :for="`bulk-remove-${f.key}`">{{ $t("Remove") }}</label>
          <TagInput v-model="remove[f.key]" :input-id="`bulk-remove-${f.key}`" :field="f.key" :chip-class="f.chip" :placeholder="$t('Remove {field}', { field: f.label.toLowerCase() })" inline /></div>
      </div>
    </div>
    <p v-if="error" class="error-text">{{ error }}</p>
    <template #footer>
      <button class="btn" @click="emit('close')">{{ $t("Cancel") }}</button>
      <button class="btn btn-primary" :disabled="busy || !any" @click="save">{{ busy ? $t("Saving…") : $t("Apply to {n}", { n: ids.length }) }}</button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.intro { margin: 0 0 0.75rem; }
.small { font-size: 0.8rem; }
.fld { margin-bottom: 0.875rem; }
.lbl { font-weight: 650; font-size: 0.86rem; margin-bottom: 0.3rem; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }
.side { display: flex; flex-direction: column; gap: 0.2rem; font-size: 0.75rem; color: var(--text-2); min-width: 0; }
@media (max-width: 40rem) { .two { grid-template-columns: 1fr; } }
</style>
