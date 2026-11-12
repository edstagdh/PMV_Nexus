<script setup>
// One suggested song (backend/library_mixins/songs.py): its name can be
// corrected before Add puts it in the video's Music; Dismiss hides it for
// good for that video. Used by the Find songs dialog and Management -> Songs.
import { ref, computed } from "vue";
import Icon from "../Icon.vue";
import { api } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";

const props = defineProps({ s: Object });
const emit = defineEmits(["done"]);
const label = ref(props.s.label);
const busy = ref(false);
const clock = (t) => `${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, "0")}`;
const sure = computed(() => props.s.hits >= 2);

async function act(what) {
  busy.value = true;
  try {
    await api(`/api/songs/${props.s.id}/${what}`, { method: "POST", body: what === "add" ? { label: label.value.trim() } : {} });
    if (what === "add") toast(`Added “${label.value.trim()}” to Music`, { type: "success" });
    emit("done", what);
  } catch (err) { toast(err.message || "That didn't work", { type: "error" }); }
  finally { busy.value = false; }
}
defineExpose({ add: () => act("add") });
</script>

<template>
  <div class="sugg" :class="{ sure }">
    <div class="main">
      <input v-model="label" class="input" maxlength="200" aria-label="Song — Artist">
      <span class="meta">
        <span class="pill" :title="sure ? 'Heard in several samples' : 'Heard in one sample only — check it'">
          {{ s.hits }} of {{ s.samples }} {{ s.samples === 1 ? "sample" : "samples" }}</span>
        at {{ (s.times || []).map(clock).join(", ") }} · {{ s.source }}
        <a v-if="s.url" :href="s.url" target="_blank" rel="noopener noreferrer"><Icon name="external" :size="12" />Open</a>
      </span>
    </div>
    <div class="acts">
      <button class="btn btn-sm btn-primary" :disabled="busy || !label.trim()" @click="act('add')"><Icon name="plus" :size="14" />Add</button>
      <button class="btn btn-sm btn-ghost" :disabled="busy" @click="act('dismiss')">Dismiss</button>
    </div>
  </div>
</template>

<style scoped>
.sugg { display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap; padding: 0.5rem 0; border-bottom: 1px solid var(--border); }
.main { flex: 1 1 18rem; display: flex; flex-direction: column; gap: 0.25rem; min-width: 0; }
.main .input { height: 2.1rem; font-weight: 600; }
.meta { font-size: 0.78rem; color: var(--text-2); display: flex; gap: 0.4rem; align-items: center; flex-wrap: wrap; }
.meta a { display: inline-flex; gap: 0.2rem; align-items: center; color: var(--accent); }
.pill { font-weight: 700; border-radius: 999px; padding: 0.05rem 0.45rem; background: var(--panel-hover); }
.sure .pill { background: color-mix(in srgb, var(--success) 22%, transparent); color: var(--success); }
.acts { display: flex; gap: 0.375rem; }
</style>
