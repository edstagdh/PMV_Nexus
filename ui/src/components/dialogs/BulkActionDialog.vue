<script setup>
// Library -> Select -> Find songs / Repair / Re-encode (2.18.0): confirm,
// pick the options (codec), and see what was queued and what was
// skipped (backend/routers/bulk.py).
import { ref, reactive, computed, onMounted } from "vue";
import ModalDialog from "../ModalDialog.vue";
import ReencodeOptions from "../ReencodeOptions.vue";
import { loadReencodeDefaults, reencodeBody, reencodeState } from "../../lib/reencodeOptions.js";
import { api } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";

const props = defineProps({ ids: Array, action: String });
const emit = defineEmits(["close", "done"]);
const n = props.ids.length;
const videos = `${n} ${n === 1 ? "video" : "videos"}`;
const TEXT = {
  songs: { title: "Find songs", go: "Find songs",
    lead: `Listen to the ${videos} for songs. They're done one after the other, as one task on Tasks; what's found waits in Management → Songs.` },
  repair: { title: "Repair / update", go: "Repair / update",
    lead: `Check the ${videos} and fix what's missing or broken (file, thumbnail, preview, details from the site). They're queued like a single repair.` },
  reencode: { title: "Re-encode", go: "Re-encode",
    lead: `Queue the ${videos} for re-encoding. Videos already in that codec, or already queued, are skipped.` },
}[props.action];

// Re-encode: the server's settings, changeable for these re-encodes (2.22.0).
const reDefaults = ref(null);
const reState = reactive({});
const busy = ref(false);
const error = ref("");
const result = ref(null);

onMounted(async () => {
  if (props.action === "reencode") {
    try { reDefaults.value = await loadReencodeDefaults(); Object.assign(reState, reencodeState(reDefaults.value)); }
    catch (err) { error.value = err.message; }
  }
});
const ready = computed(() => props.action !== "reencode" || !!reDefaults.value);

async function go() {
  busy.value = true;
  error.value = "";
  try {
    const body = { ids: props.ids };
    if (props.action === "reencode") Object.assign(body, reencodeBody(reState, reDefaults.value));
    result.value = await api(`/api/bulk/${props.action}`, { method: "POST", body });
    const r = result.value;
    toast(`${TEXT.title}: ${r.queued} queued${r.skipped_count ? `, ${r.skipped_count} skipped` : ""}`, { type: r.queued ? "success" : "info" });
    emit("done");
    if (!r.skipped_count) emit("close");
  } catch (err) { error.value = err.message || "That didn't work"; }
  finally { busy.value = false; }
}
</script>

<template>
  <ModalDialog :title="`${TEXT.title} — ${videos}`" :width="action === 'reencode' ? '40rem' : undefined" @close="emit('close')">
    <template v-if="!result">
      <p class="lead">{{ TEXT.lead }}</p>
      <ReencodeOptions v-if="action === 'reencode' && reDefaults && reState.codec && reDefaults.ask" :defaults="reDefaults" :state="reState" />
      <p v-else-if="action === 'reencode' && reDefaults" class="lead">With the server's settings:
        <strong>{{ reDefaults.codec === "av1" ? `AV1, CRF ${reDefaults.av1.crf}, preset ${reDefaults.av1.preset}` : `HEVC, CRF ${reDefaults.hevc.crf}, ${reDefaults.hevc.preset}` }}</strong>
        (Server settings → Re-encode).</p>
    </template>
    <template v-else>
      <p class="lead"><strong>{{ result.queued }}</strong> queued<template v-if="result.skipped_count">, <strong>{{ result.skipped_count }}</strong> skipped:</template></p>
      <ul v-if="result.skipped.length" class="skipped">
        <li v-for="s in result.skipped" :key="s.id"><span class="t">{{ s.title }}</span><span class="muted small">{{ s.reason }}</span></li>
      </ul>
    </template>
    <p v-if="error" class="error-text">{{ error }}</p>
    <template #footer>
      <button class="btn" @click="emit('close')">{{ result ? "Close" : "Cancel" }}</button>
      <button v-if="!result" class="btn btn-primary" :disabled="busy || !ready" @click="go">{{ busy ? "Queuing…" : TEXT.go }}</button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.lead { margin: 0 0 0.75rem; font-size: 0.875rem; color: var(--text-2); }
.small { font-size: 0.75rem; margin-left: 0.35rem; }
.skipped { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.35rem; max-height: 16rem; overflow-y: auto; }
.skipped li { display: flex; flex-direction: column; border-bottom: 1px solid var(--border); padding-bottom: 0.3rem; }
.t { font-weight: 600; font-size: 0.85rem; }
</style>
