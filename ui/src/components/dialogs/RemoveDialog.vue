<script setup>
import { ref } from "vue";
import { useRouter, useRoute } from "vue-router";
import ModalDialog from "../ModalDialog.vue";
import { api } from "../../lib/api.js";
import { releaseMedia } from "../../lib/media.js";
import { lastLibraryLocation } from "../../lib/nav.js";
import { toast } from "../../stores/toast.js";

const props = defineProps({ item: Object, options: Object });
const emit = defineEmits(["close"]);
const router = useRouter();
const route = useRoute();
const deleteFiles = ref(false);
const busy = ref(false);
const hasFiles = props.item.status === "done";

async function confirmRemove() {
  busy.value = true;
  const id = props.item.id;
  const onItsPage = route.name === "video" && route.params.id === id;
  if (onItsPage) await router.push(lastLibraryLocation()); // unmount the player first
  releaseMedia(id); // an open stream keeps the file locked on Windows
  try {
    const result = await api(`/api/library/${encodeURIComponent(id)}?delete_files=${hasFiles && deleteFiles.value}`, { method: "DELETE" });
    toast(deleteFiles.value && hasFiles ? `Removed and deleted ${result?.deleted_files ?? 0} file(s)` : "Removed from the library", { type: "success" });
    emit("close");
  } catch (err) {
    toast(err.message || "Could not remove it", { type: "error" });
    busy.value = false;
  }
}
</script>

<template>
  <ModalDialog title="Remove video" @close="emit('close')">
    <p class="msg">
      {{ hasFiles ? `Remove "${item.title || item.url}" from the library?` : `Cancel "${item.title || item.url}" and remove it from the queue?` }}
    </p>
    <p v-if="!hasFiles" class="muted note">Its thumbnail, preview clip and details file are deleted too.</p>
    <label v-if="hasFiles" class="switch"><input v-model="deleteFiles" type="checkbox">Also delete the video and its files from disk</label>
    <template #footer>
      <button class="btn" @click="emit('close')">Keep it</button>
      <button class="btn btn-primary danger-btn" :disabled="busy" @click="confirmRemove">{{ deleteFiles ? "Remove and delete files" : "Remove" }}</button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.msg { margin: 0 0 0.875rem; }
.note { margin: -0.5rem 0 0.875rem; font-size: 0.84rem; }
.danger-btn { background: var(--danger); border-color: var(--danger); }
</style>
