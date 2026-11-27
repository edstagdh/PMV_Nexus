<script setup>
// 3.9.0: MediaInfo's report of the video file, as `mediainfo <file>` prints it (Video page ->
// Technical Details -> MediaInfo). "Every field" asks for the full report.
import { t } from "../../lib/i18n.js";
import { ref, watch } from "vue";
import ModalDialog from "../ModalDialog.vue";
import Icon from "../Icon.vue";
import { api } from "../../lib/api.js";
import { copyText } from "../../lib/clipboard.js";
import { toast } from "../../stores/toast.js";

const props = defineProps({ id: String });
const emit = defineEmits(["close"]);
const full = ref(false);
const text = ref("");
const error = ref("");
const loading = ref(false);
async function load() {
  loading.value = true; error.value = "";
  try { text.value = (await api(`/api/library/${props.id}/mediainfo?full=${full.value ? 1 : 0}`)).text; }
  catch (err) { error.value = err.message || t("That didn't work"); }
  finally { loading.value = false; }
}
watch(full, load, { immediate: true });
async function copy() {
  if (await copyText(text.value)) toast(t("Copied"), { type: "success" });
}
</script>

<template>
  <ModalDialog :title="$t('MediaInfo')" width="56rem" @close="emit('close')">
    <div class="mi-bar">
      <label class="switch"><input v-model="full" type="checkbox">{{ $t("Every field") }}</label>
      <button class="btn btn-sm" :disabled="!text" @click="copy"><Icon name="copy" :size="14" />{{ $t("Copy") }}</button>
    </div>
    <p v-if="error" class="error-text">{{ error }}</p>
    <div v-else-if="loading && !text" class="skeleton" style="height: 20rem" />
    <pre v-else class="mi-text" dir="ltr">{{ text }}</pre>
  </ModalDialog>
</template>

<style scoped>
.mi-bar { display: flex; justify-content: space-between; align-items: center; gap: 1rem; margin-bottom: 0.6rem; }
.mi-text { margin: 0; max-height: 65vh; overflow: auto; font: 0.78rem/1.45 var(--font-mono, ui-monospace, monospace);
  background: var(--panel-2); border-radius: var(--radius); padding: 0.75rem; white-space: pre; tab-size: 4; }
</style>
