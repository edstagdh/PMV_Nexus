<script setup>
// New playlist / edit name, description and who can see it.
import { t } from "../../lib/i18n.js";
import { ref } from "vue";
import ModalDialog from "../ModalDialog.vue";
import { api } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";

const props = defineProps({ playlist: Object }); // absent = create a new (manual) playlist
const emit = defineEmits(["close", "saved"]);
const form = ref({
  name: props.playlist?.name || "",
  description: props.playlist?.description || "",
  visibility: props.playlist?.visibility || "private",
});
const busy = ref(false);
const error = ref("");

async function save() {
  busy.value = true;
  error.value = "";
  try {
    const body = { name: form.value.name.trim(), description: form.value.description, visibility: form.value.visibility };
    const p = props.playlist
      ? await api(`/api/playlists/${props.playlist.id}`, { method: "PATCH", body })
      : await api("/api/playlists", { method: "POST", body: { ...body, kind: "manual" } });
    toast(props.playlist ? t("Saved") : t("Created {name}", { name: p.name }), { type: "success" });
    emit("saved", p);
  } catch (err) {
    error.value = err.message || t("Could not save");
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <ModalDialog :title="playlist ? $t('Edit playlist') : $t('New playlist')" @close="emit('close')">
    <form id="pl-form" class="form" @submit.prevent="save">
      <label class="field">{{ $t("Name") }}<input v-model="form.name" class="input" maxlength="120" required autofocus :disabled="!!playlist?.system"
        :title="playlist?.system ? $t('Built in: its name stays') : undefined"></label>
      <label class="field">{{ $t("Description") }}<textarea v-model="form.description" class="input area" maxlength="2000" rows="3" :placeholder="$t('Optional')" /></label>
      <p v-if="playlist?.system === 'favorites'" class="muted small">{{ $t("Favorites are always private: only you can see them.") }}</p>
      <div v-else class="field">{{ $t("Who can see it") }}
        <label class="radio"><input v-model="form.visibility" type="radio" value="private"><span><strong>{{ $t("Private") }}</strong> {{ $t("— only you") }}</span></label>
        <label class="radio"><input v-model="form.visibility" type="radio" value="public"><span><strong>{{ $t("Public") }}</strong> {{ $t("— every user of this app can see and play it; only you can change it") }}</span></label>
      </div>
      <p v-if="error" class="error-text">{{ error }}</p>
    </form>
    <template #footer>
      <button class="btn" type="button" @click="emit('close')">{{ $t("Cancel") }}</button>
      <button class="btn btn-primary" type="submit" form="pl-form" :disabled="busy || !form.name.trim()">{{ busy ? $t("Saving…") : playlist ? $t("Save") : $t("Create") }}</button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.form { display: flex; flex-direction: column; gap: 0.875rem; }
.field { display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.84rem; font-weight: 600; }
.field .input { font-weight: 400; }
.area { height: auto; padding: 0.5rem 0.75rem; resize: vertical; font: inherit; }
.radio { display: flex; gap: 0.5rem; align-items: flex-start; font-weight: 400; cursor: pointer; }
.radio input { margin-top: 0.2rem; accent-color: var(--accent); }
</style>
