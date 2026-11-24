<script setup>
// ⋮ / video page -> Re-encode… (2.22.0): one entry instead of one per codec.
import { t } from "../../lib/i18n.js";
import { ref, reactive, computed, onMounted } from "vue";
import ModalDialog from "../ModalDialog.vue";
import ReencodeOptions from "../ReencodeOptions.vue";
import { api } from "../../lib/api.js";
import { loadReencodeDefaults, reencodeBody, reencodeState, CODEC_INFO } from "../../lib/reencodeOptions.js";
import { toast } from "../../stores/toast.js";
import { currentCodec } from "../../lib/actions.js";

const props = defineProps({ item: Object });
const emit = defineEmits(["close"]);
const defaults = ref(null);
const state = reactive({});
const error = ref("");
const busy = ref(false);
// The codec the video is in now (it can't be re-encoded to the same one).
const current = computed(() => currentCodec(props.item));
// Larger than 1080p (3.4.0): its own codec may be picked to only downscale it (same rule as the server).
const big = computed(() => {
  const [w, h] = String(props.item.tech_resolution || "").split(/[x×]/i).map((n) => parseInt(n, 10));
  if (!w || !h) return false;
  return h > w ? h > 1080 : w > 1920 || h > 1080;
});

onMounted(async () => {
  try {
    defaults.value = await loadReencodeDefaults();
    Object.assign(state, reencodeState(defaults.value, current.value));
  } catch (err) { error.value = err.message || t("Couldn't load the re-encode settings"); }
});

async function go() {
  busy.value = true;
  error.value = "";
  try {
    const body = reencodeBody(state, defaults.value);
    if (body.codec === current.value) body.same_codec = true;
    await api(`/api/library/${encodeURIComponent(props.item.id)}/reencode`, { method: "POST", body });
    toast(body.same_codec ? t("Queued: made 1080p, staying {codec}", { codec: CODEC_INFO[body.codec].label })
      : body.options ? t("Queued for re-encode to {codec} (your settings)", { codec: CODEC_INFO[body.codec].label })
        : t("Queued for re-encode to {codec}", { codec: CODEC_INFO[body.codec].label }), { type: "success" });
    emit("close");
  } catch (err) { error.value = err.message || t("That didn't work"); }
  finally { busy.value = false; }
}
</script>

<template>
  <ModalDialog :title="$t('Re-encode')" width="40rem" @close="emit('close')">
    <p class="lead">{{ $t("Re-encodes “{title}”: smaller in a more efficient codec, or AVC for older devices. It's queued and runs on the server; follow it on Tasks.", { title: item.title || item.url }) }}</p>
    <ReencodeOptions v-if="defaults && state.codec" :defaults="defaults" :state="state" :exclude="current" :big="big" />
    <p v-else-if="!error" class="muted">{{ $t("Loading…") }}</p>
    <p v-if="error" class="error-text">{{ error }}</p>
    <template #footer>
      <button class="btn" @click="emit('close')">{{ $t("Cancel") }}</button>
      <button class="btn btn-primary" :disabled="busy || !defaults || (state.codec === current && !(big && state.downscale))" @click="go">{{ busy ? $t("Queuing…") : $t("Re-encode") }}</button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.lead { margin: 0 0 1rem; font-size: 0.875rem; color: var(--text-2); }
</style>
