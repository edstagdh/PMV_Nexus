<script setup>
import { ref } from "vue";
import ModalDialog from "../ModalDialog.vue";
import { api } from "../../lib/api.js";
import { releaseMedia } from "../../lib/media.js";
import { toast } from "../../stores/toast.js";

const props = defineProps({ item: Object });
const emit = defineEmits(["close"]);
const name = ref(props.item.title || "");
const error = ref("");
const busy = ref(false);

async function submit() {
  error.value = "";
  busy.value = true;
  releaseMedia(props.item.id); // an open stream keeps the file locked on Windows
  try {
    await api(`/api/library/${encodeURIComponent(props.item.id)}/rename`, { method: "POST", body: { new_name: name.value.trim() } });
    toast("Renamed", { type: "success" });
    emit("close");
  } catch (err) {
    error.value = err.message || "Could not rename that file";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <ModalDialog title="Rename video" @close="emit('close')">
    <form id="rename-form" @submit.prevent="submit">
      <label class="field">
        <span>New name (used for the title and the file names)</span>
        <input v-model="name" class="input" required autofocus>
      </label>
      <p v-if="error" class="error">{{ error }}</p>
    </form>
    <template #footer>
      <button class="btn" @click="emit('close')">Cancel</button>
      <button class="btn btn-primary" form="rename-form" :disabled="busy || !name.trim()">{{ busy ? "Renaming…" : "Rename" }}</button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.error { color: var(--danger); margin: 0.625rem 0 0; font-size: 0.84rem; }
</style>
