<script setup>
import { t, tn, N_ } from "../lib/i18n.js";
import { ref, computed } from "vue";
import ModalDialog from "./ModalDialog.vue";
import { api } from "../lib/api.js";
import { toast } from "../stores/toast.js";

const emit = defineEmits(["close"]);
const text = ref("");
const busy = ref(false);
const results = ref([]);
const urls = computed(() => text.value.split(/\s+/).map((s) => s.trim()).filter(Boolean));

async function submit() {
  if (!urls.value.length || busy.value) return;
  busy.value = true;
  results.value = [];
  let added = 0;
  for (const url of urls.value) {
    try {
      const r = await api("/api/library", { method: "POST", body: { url } });
      // 3.11.0: the server says what it did -- a link already in the library used to look "Added"
      const said = OUTCOME[r.outcome];
      const ok = !said || said.ok;
      results.value.push({ url, ok, message: said ? t(said.text, { title: r.title || "" }) : "" });
      if (ok) added++;
    } catch (err) {
      results.value.push({ url, ok: false, message: err.message });
    }
  }
  busy.value = false;
  if (added) toast(tn("Added {n} video — see Tasks for progress", "Added {n} videos — see Tasks for progress", added), { type: "success" });
  if (added === urls.value.length && results.value.every((r) => !r.message)) emit("close");
}

// what POST /api/library did, other than adding it
const OUTCOME = {
  redownload: { ok: true, text: N_("in the library, but its file was missing — downloading it again") },
  again: { ok: true, text: N_("it had failed — trying again") },
  busy: { ok: false, text: N_("on its way already — see Tasks") },
  exists: { ok: false, text: N_("already in the library (“{title}”), and its file is there") },
  merged: { ok: false, text: N_("merged into another video as its duplicate — not downloaded") },
  missing_removed: { ok: false, text: N_("in the library, its file is missing, and the site doesn't have it any more — look for it in Maintenance → Check video files") },
  missing_local: { ok: false, text: N_("uploaded from a computer — its file is missing") },
};
</script>

<template>
  <ModalDialog :title="$t('Add videos')" @close="emit('close')">
    <label class="field">
      <span>{{ $t("Video page URLs (one per line)") }}</span>
      <textarea v-model="text" class="input textarea" rows="5" autofocus
                :placeholder="$t('https://pmvhaven.com/video/...')" @keydown.ctrl.enter="submit" />
    </label>
    <ul v-if="results.length" class="results">
      <li v-for="r in results" :key="r.url" :class="r.ok ? 'ok' : 'bad'">
        {{ r.ok ? $t("Added") : $t("Not added") }} — <span class="muted">{{ r.url }}</span><span v-if="r.message">: {{ r.message }}</span>
      </li>
    </ul>
    <template #footer>
      <button class="btn" @click="emit('close')">{{ $t("Cancel") }}</button>
      <button class="btn btn-primary" :disabled="!urls.length || busy" @click="submit">
        {{ busy ? $t("Adding…") : urls.length > 1 ? $t("Add {n} videos", { n: urls.length }) : $t("Add video") }}
      </button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.textarea { height: auto; padding: 0.625rem 0.75rem; resize: vertical; font-family: var(--mono); font-size: 0.8125rem; }
.results { margin: 0.75rem 0 0; padding: 0; list-style: none; font-size: 0.8125rem; display: flex; flex-direction: column; gap: 0.25rem; overflow-wrap: anywhere; }
.results .ok { color: var(--success); }
.results .bad { color: var(--danger); }
</style>
