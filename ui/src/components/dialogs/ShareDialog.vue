<script setup>
import { ref, computed, onMounted } from "vue";
import ModalDialog from "../ModalDialog.vue";
import { api } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";
import { onLibraryChange } from "../../stores/live.js";
import { onBeforeUnmount } from "vue";

const props = defineProps({ item: Object });
const emit = defineEmits(["close"]);
const id = encodeURIComponent(props.item.id);
const config = ref(null);
const picked = ref([]);
const telegramTarget = ref("channel");
const error = ref("");
const busy = ref(false);
const shares = ref(props.item.shares || {});

const DESTS = [
  { key: "telegram", label: "Telegram" },
  { key: "gofile", label: "Gofile" },
  { key: "pixeldrain", label: "Pixeldrain" },
];
const available = (key) => !!config.value?.[key];
const statusText = (s) => ({ queued: "Queued", sharing: `Uploading${s.progress_percent != null ? ` ${Math.round(s.progress_percent)}%` : "…"}`, done: "Shared", failed: `Failed${s.error ? `: ${s.error}` : ""}` }[s.status] || "");
const anyShares = computed(() => Object.values(shares.value).some((s) => s.status !== "idle"));

onMounted(async () => {
  try {
    config.value = await api("/api/share/config");
    if (!config.value.telegram_channel && config.value.telegram_group) telegramTarget.value = "group";
  } catch (err) { error.value = err.message; }
});
// Keep the per-destination status live while the dialog is open.
const stop = onLibraryChange(async (changed) => {
  if (changed && !changed.includes(props.item.id)) return;
  try { shares.value = (await api(`/api/library/${id}/item`)).shares || {}; } catch { /* ignore */ }
});
onBeforeUnmount(stop);

async function submit() {
  if (!picked.value.length) { error.value = "Pick at least one destination"; return; }
  busy.value = true;
  error.value = "";
  try {
    await api(`/api/library/${id}/share`, { method: "POST", body: { destinations: picked.value, telegram_target: telegramTarget.value } });
    toast("Sharing started", { type: "success" });
    picked.value = [];
  } catch (err) {
    error.value = err.message || "Could not start sharing";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <ModalDialog title="Share" @close="emit('close')">
    <div class="dests">
      <label v-for="d in DESTS" :key="d.key" class="dest" :class="{ off: config && !available(d.key) }"
             :title="config && !available(d.key) ? 'Not configured yet (Server settings → Credentials)' : ''">
        <input v-model="picked" type="checkbox" :value="d.key" :disabled="!config || !available(d.key)">
        <span class="dest-name">{{ d.label }}</span>
        <span v-if="shares[d.key] && shares[d.key].status !== 'idle'" class="dest-status" :class="shares[d.key].status">
          {{ statusText(shares[d.key]) }}
          <a v-if="shares[d.key].link" :href="shares[d.key].link" target="_blank" rel="noopener noreferrer">link</a>
        </span>
        <span v-else-if="config && !available(d.key)" class="muted small">not configured</span>
      </label>
      <div v-if="picked.includes('telegram')" class="tg">
        <label class="switch"><input v-model="telegramTarget" type="radio" value="channel" :disabled="!config?.telegram_channel">Channel</label>
        <label class="switch"><input v-model="telegramTarget" type="radio" value="group" :disabled="!config?.telegram_group">Group</label>
      </div>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="anyShares" class="muted small">Uploads keep running if you close this dialog.</p>
    <template #footer>
      <button class="btn" @click="emit('close')">Close</button>
      <button class="btn btn-primary" :disabled="busy || !picked.length" @click="submit">{{ busy ? "Starting…" : "Share" }}</button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.dests { display: flex; flex-direction: column; gap: 0.5rem; }
.dest { display: flex; align-items: center; gap: 0.625rem; padding: 0.625rem 0.75rem; border: 1px solid var(--border); border-radius: var(--radius); cursor: pointer; }
.dest.off { opacity: 0.55; cursor: not-allowed; }
.dest-name { font-weight: 600; }
.dest-status { margin-left: auto; font-size: 0.78rem; }
.dest-status.done { color: var(--success); }
.dest-status.failed { color: var(--danger); }
.tg { display: flex; gap: 1rem; padding-left: 0.75rem; }
.tg .switch input { appearance: auto; width: auto; height: auto; }
.tg .switch input::after { display: none; }
.small { font-size: 0.78rem; margin-left: auto; }
.error { color: var(--danger); font-size: 0.84rem; }
</style>
