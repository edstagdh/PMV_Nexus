<script setup>
// Video page -> "Faces found" (3.12.0; 3.13.0: only the waiting ones -- an approved match adds the
// performer to the video's performers -- closed / open / hidden by My Settings, in pages, with
// where in the video and bigger pictures). For people who manage performers.
import { t, tn } from "../../lib/i18n.js";
import { ref, computed, watch, onBeforeUnmount } from "vue";
import { RouterLink } from "vue-router";
import Icon from "../Icon.vue";
import { api } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";
import { session, hasPermission } from "../../stores/session.js";
import { onLibraryChange } from "../../stores/live.js";
import { seekPlayer } from "../../stores/player.js";
import { formatDuration } from "../../lib/format.js";

const props = defineProps({ id: String });
const emit = defineEmits(["added"]);
const data = ref({ suggested: [], added: [] });
const busy = ref("");
const page = ref(0);
const canManage = hasPermission("performers.manage");
const mode = computed(() => session.prefs.videoFaces || "collapsed");
const size = computed(() => session.prefs.videoFacesPageSize || 6);
const pages = computed(() => Math.max(1, Math.ceil(data.value.suggested.length / size.value)));
const shown = computed(() => data.value.suggested.slice(page.value * size.value, (page.value + 1) * size.value));
const px = computed(() => Math.min(120, session.prefs.facesPictureSize || 160));   // the video page's column is narrow
const SOURCE = { thumbnail: t("thumbnail"), frame: t("frame under the player"), storyboard: t("seekbar picture") };

async function load() {
  const id = props.id;
  const d = await api(`/api/library/${encodeURIComponent(id)}/faces`).catch(() => null);
  if (id !== props.id) return;
  data.value = d || { suggested: [], added: [] };
  if (page.value >= pages.value) page.value = 0;
  emit("added", Object.fromEntries((data.value.added || []).map((a) => [a.name.toLowerCase(), a.score])));
}
watch(() => props.id, () => { page.value = 0; load(); }, { immediate: true });
const stop = onLibraryChange((c) => { if (!c || c.includes("faces") || c.includes(props.id)) load(); });
onBeforeUnmount(stop);

async function decide(m, action) {
  busy.value = m.performer_id;
  try {
    const r = await api(`/api/faces/matches/${encodeURIComponent(m.video.id)}/${encodeURIComponent(m.performer_id)}`, { method: "POST", body: { action } });
    if (action === "approve" && r.videos_changed) toast(t("{name} added to the video's performers", { name: m.performer.name }), { type: "success" });
    await load();
  } catch (err) { toast(err.message || t("Could not save"), { type: "error" }); }
  finally { busy.value = ""; }
}
const pic = (url) => (url ? `${url}&size=${Math.round(px.value * (window.devicePixelRatio || 1))}` : null);
</script>

<template>
  <details v-if="canManage && mode !== 'hidden' && data.suggested.length" class="card faces" :open="mode === 'open'" :style="{ '--face': `${px}px` }">
    <summary><h3>{{ $t("Faces found") }} <span class="muted small">({{ data.suggested.length }})</span></h3></summary>
    <p class="muted small">{{ $t("Performers whose face may be in this video, waiting for you. Approve adds them to the video's performers.") }}
      <RouterLink :to="{ name: 'portal', params: { tab: 'faces' } }">{{ $t("All on the Faces page") }}</RouterLink></p>
    <div v-for="m in shown" :key="m.performer_id" class="sugg">
      <img v-if="m.face" :src="pic(m.face)" alt="" class="face" loading="lazy">
      <img v-else-if="m.crop" :src="m.crop" alt="" class="face" loading="lazy">
      <img v-if="m.performer.photo" :src="m.performer.photo" alt="" class="face photo" loading="lazy">
      <span class="who">
        <RouterLink :to="{ name: 'performer', params: { name: m.performer.name } }">{{ m.performer.name }}</RouterLink>
        <span class="muted small">{{ $t("score {score}", { score: Math.round(m.score) }) }} · {{ tn("{n} face", "{n} faces", m.support) }}</span>
        <span v-if="m.source" class="muted small">
          <button v-if="m.at != null" class="link" :title="$t('Jump there')" @click="seekPlayer(m.at)">{{ $t("at {time}", { time: formatDuration(m.at) }) }}</button>
          <template v-if="m.at != null"> · </template>{{ SOURCE[m.source] || m.source }}</span>
        <span class="btns">
          <button class="btn btn-sm" :disabled="busy === m.performer_id" @click="decide(m, 'approve')"><Icon name="check" :size="14" />{{ $t("Approve") }}</button>
          <button class="btn btn-sm btn-ghost" :disabled="busy === m.performer_id" @click="decide(m, 'reject')"><Icon name="x" :size="14" />{{ $t("Not them") }}</button>
        </span>
      </span>
    </div>
    <nav v-if="pages > 1" class="pager">
      <button class="btn btn-sm btn-ghost" :disabled="page === 0" @click="page -= 1">‹</button>
      <span class="small">{{ $t("Page {n} of {total}", { n: page + 1, total: pages }) }}</span>
      <button class="btn btn-sm btn-ghost" :disabled="page >= pages - 1" @click="page += 1">›</button>
    </nav>
  </details>
</template>

<style scoped>
.faces { padding: 0.9rem 1rem; margin-top: 1rem; display: flex; flex-direction: column; gap: 0.5rem; }
.faces summary { cursor: pointer; }
.faces h3 { margin: 0; font-size: 1rem; display: inline; }
.faces p { margin: 0.25rem 0 0.5rem; }
.small { font-size: 0.8rem; }
.sugg { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; padding: 0.5rem 0; border-top: 1px solid var(--border); }
.face { width: var(--face); height: var(--face); max-width: 28vw; max-height: 28vw; border-radius: 0.5rem; object-fit: cover; background: var(--panel-2); }
.face.photo { object-position: 50% 20%; }
.who { display: flex; flex-direction: column; gap: 0.1rem; min-width: 9rem; flex: 1; }
.btns { display: flex; gap: 0.35rem; flex-wrap: wrap; margin-top: 0.3rem; }
.link { background: none; border: 0; padding: 0; color: var(--accent); cursor: pointer; font: inherit; }
.pager { display: flex; gap: 0.4rem; align-items: center; }
</style>
