<script setup>
// Video page -> "Recognised by face" (3.12.0, backend/library_mixins/faces.py): performers whose face
// was found in this video's pictures. Approved ones for everyone; waiting ones (with Approve /
// Not them) for those who manage performers. The video's own performers are never changed.
import { t } from "../../lib/i18n.js";
import { ref, watch, onBeforeUnmount } from "vue";
import { RouterLink } from "vue-router";
import Icon from "../Icon.vue";
import { api } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";
import { hasPermission } from "../../stores/session.js";
import { onLibraryChange } from "../../stores/live.js";

const props = defineProps({ id: String });
const data = ref({ approved: [], suggested: [] });
const busy = ref("");
const canManage = hasPermission("performers.manage");

async function load() {
  const id = props.id;
  const d = await api(`/api/library/${encodeURIComponent(id)}/faces`).catch(() => null);
  if (id === props.id) data.value = d || { approved: [], suggested: [] };
}
watch(() => props.id, load, { immediate: true });
const stop = onLibraryChange((c) => { if (!c || c.includes("faces") || c.includes(props.id)) load(); });
onBeforeUnmount(stop);

async function decide(m, action) {
  busy.value = m.performer_id;
  try {
    await api(`/api/faces/matches/${encodeURIComponent(m.video.id)}/${encodeURIComponent(m.performer_id)}`, { method: "POST", body: { action } });
    await load();
  } catch (err) { toast(err.message || t("Could not save"), { type: "error" }); }
  finally { busy.value = ""; }
}
</script>

<template>
  <section v-if="data.approved.length || data.suggested.length" class="card faces">
    <h3>{{ $t("Recognised by face") }}</h3>
    <p class="muted small">{{ $t("Performers whose face was found in this video's pictures (not listed in its details).") }}</p>
    <div class="row">
      <RouterLink v-for="m in data.approved" :key="m.performer_id" :to="{ name: 'performer', params: { name: m.performer.name } }"
                  class="chip" :title="$t('Score {score}', { score: Math.round(m.score) })">
        <img v-if="m.performer.photo" :src="m.performer.photo" alt="" loading="lazy">{{ m.performer.name }}
      </RouterLink>
    </div>
    <template v-if="canManage && data.suggested.length">
      <p class="muted small">{{ $t("Waiting for you:") }}</p>
      <div v-for="m in data.suggested" :key="m.performer_id" class="sugg">
        <img v-if="m.crop" :src="m.crop" alt="" class="face" loading="lazy">
        <img v-if="m.performer.photo" :src="m.performer.photo" alt="" class="face" loading="lazy">
        <span class="who"><RouterLink :to="{ name: 'performer', params: { name: m.performer.name } }">{{ m.performer.name }}</RouterLink>
          <span class="muted small">{{ $t("score {score}", { score: Math.round(m.score) }) }}</span></span>
        <button class="btn btn-sm" :disabled="busy === m.performer_id" @click="decide(m, 'approve')"><Icon name="check" :size="14" />{{ $t("Approve") }}</button>
        <button class="btn btn-sm btn-ghost" :disabled="busy === m.performer_id" @click="decide(m, 'reject')"><Icon name="x" :size="14" />{{ $t("Not them") }}</button>
      </div>
    </template>
  </section>
</template>

<style scoped>
.faces { padding: 0.9rem 1rem; margin-top: 1rem; display: flex; flex-direction: column; gap: 0.5rem; }
.faces h3 { margin: 0; font-size: 1rem; }
.faces p { margin: 0; }
.small { font-size: 0.8rem; }
.row { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.chip { display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.2rem 0.6rem 0.2rem 0.2rem; border-radius: 999px;
  background: var(--panel-2); color: var(--text); font-size: 0.85rem; }
.chip img { width: 1.6rem; height: 1.6rem; border-radius: 50%; object-fit: cover; }
.sugg { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.face { width: 2.75rem; height: 2.75rem; border-radius: 0.4rem; object-fit: cover; background: var(--panel-2); }
.who { display: flex; flex-direction: column; min-width: 8rem; flex: 1; }
</style>
