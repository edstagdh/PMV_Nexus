<script setup>
// Management -> Faces (3.12.0, backend/library_mixins/faces.py): performers recognised in videos by
// their face, completely offline. Waiting matches to approve or mark "Not them", the approved and
// rejected ones (each can be undone), Scan, and the settings.
import { t, tn } from "../../lib/i18n.js";
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { RouterLink } from "vue-router";
import Icon from "../../components/Icon.vue";
import { api, qs } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";
import { askConfirm } from "../../stores/confirm.js";
import { hasPermission } from "../../stores/session.js";
import { onLibraryChange } from "../../stores/live.js";
import { formatDateTime } from "../../lib/format.js";

const PAGE = 60;
const data = ref(null);
const cfg = ref(null);
const list = ref(null);
const status = ref("suggested");
const shown = ref(PAGE);
const error = ref("");
const busy = ref("");
const above = ref(80);
const canSettings = hasPermission("settings.manage");
const TABS = computed(() => [
  { key: "suggested", label: t("Waiting ({n})", { n: data.value?.matches?.suggested ?? "…" }) },
  { key: "approved", label: t("Approved ({n})", { n: data.value?.matches?.approved ?? "…" }) },
  { key: "rejected", label: t("Not them ({n})", { n: data.value?.matches?.rejected ?? "…" }) },
]);

async function load() {
  try {
    data.value = await api("/api/faces");
    if (!cfg.value) cfg.value = { ...data.value.settings };
    list.value = await api(`/api/faces/matches?${qs({ status: status.value, limit: shown.value })}`);
    error.value = "";
  } catch (err) { error.value = err.message || t("Could not load the faces"); }
}
let timer = null;
let stop = null;
let poll = null;
onMounted(() => {
  load();
  stop = onLibraryChange((c) => { if (!c || c.includes("faces")) { clearTimeout(timer); timer = setTimeout(load, 400); } });
  poll = setInterval(() => { if (data.value?.running) load(); }, 3000);
});
onBeforeUnmount(() => { stop?.(); clearTimeout(timer); clearInterval(poll); });

function pick(key) { status.value = key; shown.value = PAGE; list.value = null; load(); }
function more() { shown.value += PAGE; load(); }

async function scan(again = false) {
  if (again && !(await askConfirm(t("Read the pictures of every video again? It takes a while; your decisions stay as they are."), { ok: t("Read again") }))) return;
  busy.value = "scan";
  try {
    const r = await api("/api/faces/scan", { method: "POST", body: { again } });
    toast(r.started ? t("Finding faces — follow it on Tasks") : t("Already finding faces: it runs once more after"), { type: "success" });
    load();
  } catch (err) { toast(err.message || t("Could not start"), { type: "error" }); }
  finally { busy.value = ""; }
}
async function stopScan() {
  await api("/api/faces/stop", { method: "POST" }).catch(() => null);
  load();
}
async function decide(m, action) {
  busy.value = `${m.video.id}/${m.performer_id}`;
  try {
    await api(`/api/faces/matches/${encodeURIComponent(m.video.id)}/${encodeURIComponent(m.performer_id)}`, { method: "POST", body: { action } });
    list.value.rows = list.value.rows.filter((r) => r !== m);
    load();
  } catch (err) { toast(err.message || t("Could not save"), { type: "error" }); }
  finally { busy.value = ""; }
}
async function approveAbove() {
  const n = (list.value?.rows || []).filter((r) => r.score >= above.value).length;
  if (!(await askConfirm(t("Approve every waiting match with a score of {score} or more?", { score: above.value }) + (n ? " " + t("({n} on this page)", { n }) : ""), { ok: t("Approve") }))) return;
  busy.value = "above";
  try {
    const r = await api("/api/faces/approve-above", { method: "POST", body: { score: above.value } });
    toast(tn("{n} match approved", "{n} matches approved", r.approved), { type: "success" });
    load();
  } catch (err) { toast(err.message || t("Could not save"), { type: "error" }); }
  finally { busy.value = ""; }
}
async function saveSettings() {
  busy.value = "settings";
  try {
    await api("/api/settings/faces", { method: "POST", body: cfg.value });
    toast(t("Saved"), { type: "success" });
    load();
  } catch (err) { toast(err.message || t("Could not save"), { type: "error" }); }
  finally { busy.value = ""; }
}
const mb = (n) => `${((n || 0) / 1024 / 1024).toFixed(1)} MB`;
</script>

<template>
  <div class="page faces">
    <p class="muted small intro">{{ $t("Performers recognised in videos by their face: the faces in each video's seekbar previews, thumbnail and frames are compared with the photos of the performers in your library. Everything happens on this computer; nothing is sent anywhere. A match only shows on the video's and the performer's page once approved — the video's own details are never changed.") }}</p>
    <p v-if="error" class="error-text">{{ error }}</p>

    <section v-if="data && !data.models.ok" class="card block warn">
      <strong>{{ $t("The face models aren't there") }}</strong>
      <p class="small">{{ $t("Setup.exe brings them. Otherwise run this once in the app's folder (it downloads two files from OpenCV and checks them):") }}</p>
      <code>python -m Tools.get_face_models</code>
      <p class="muted small">{{ [...data.models.missing, ...data.models.bad].join(", ") }} · {{ data.models.folder }}</p>
    </section>

    <section v-if="data" class="card block head">
      <div class="stats">
        <div><strong>{{ data.read_videos }} / {{ data.videos }}</strong><span>{{ $t("videos read") }}</span></div>
        <div><strong>{{ data.read_faces }}</strong><span>{{ $t("faces found") }}</span></div>
        <div><strong>{{ data.read_performers }} / {{ data.performers }}</strong><span>{{ $t("performers with a usable photo") }}</span></div>
        <div><strong>{{ data.matches.suggested }}</strong><span>{{ $t("waiting for you") }}</span></div>
        <div><strong>{{ mb(data.read_size) }}</strong><span>{{ $t("kept (can be made again)") }}</span></div>
      </div>
      <p v-if="!data.enabled" class="small"><Icon name="lock" :size="13" /> {{ canSettings ? $t("Faces is off: switch it on in the settings below.") : $t("Faces is off (Server Settings permission needed to switch it on).") }}</p>
      <div class="acts">
        <button class="btn btn-sm btn-primary" :disabled="busy === 'scan' || !data.enabled || !data.models.ok" @click="scan(false)"><Icon name="search" :size="14" />{{ $t("Find faces now") }}</button>
        <button class="btn btn-sm" :disabled="busy === 'scan' || !data.enabled || !data.models.ok" @click="scan(true)">{{ $t("Read every video again") }}</button>
        <button v-if="data.running" class="btn btn-sm" @click="stopScan"><Icon name="stop" :size="14" />{{ $t("Stop") }}</button>
      </div>
      <p v-if="data.running && data.state" class="muted small">{{ data.state.stage }} · {{ data.state.done }} / {{ data.state.total }}</p>
      <p v-else-if="data.last" class="muted small">{{ $t("Last run {time}: {videos} videos read, {faces} faces, {suggested} new suggestions", { time: formatDateTime(data.last.at), videos: data.last.videos, faces: data.last.faces, suggested: data.last.suggested }) }}</p>
    </section>
    <div v-else class="skeleton" style="height: 6rem; border-radius: var(--radius)" />

    <div class="seg" role="tablist">
      <button v-for="v in TABS" :key="v.key" role="tab" class="btn btn-sm" :class="{ 'btn-primary': status === v.key }" :aria-selected="status === v.key" @click="pick(v.key)">{{ v.label }}</button>
    </div>
    <div v-if="status === 'suggested' && list?.total" class="acts above">
      <label class="small">{{ $t("Approve all with a score of") }} <input v-model.number="above" class="input input-num" type="number" min="20" max="100"> {{ $t("or more") }}</label>
      <button class="btn btn-sm" :disabled="busy === 'above'" @click="approveAbove">{{ $t("Approve") }}</button>
    </div>

    <div v-if="list" class="rows">
      <div v-for="m in list.rows" :key="`${m.video.id}/${m.performer_id}`" class="card row">
        <div class="pics">
          <img v-if="m.crop" :src="m.crop" alt="" class="face" :title="$t('The face in the video')" loading="lazy">
          <span v-else class="face none" :title="$t('The face picture is made again at the next scan')" />
          <img v-if="m.performer.photo" :src="m.performer.photo" alt="" class="face" :title="m.performer.name" loading="lazy">
        </div>
        <div class="txt">
          <RouterLink :to="{ name: 'performer', params: { name: m.performer.name } }" class="who">{{ m.performer.name }}</RouterLink>
          <span class="muted small">{{ $t("in") }} <RouterLink :to="{ name: 'video', params: { id: m.video.id } }">{{ m.video.title }}</RouterLink></span>
          <span class="muted small">{{ $t("score {score}", { score: Math.round(m.score) }) }} · {{ tn("{n} face looks like them", "{n} faces look like them", m.support) }}<template v-if="m.decided_by"> · {{ m.auto ? $t("approved by itself") : $t("by {name}", { name: m.decided_by }) }}</template></span>
        </div>
        <RouterLink :to="{ name: 'video', params: { id: m.video.id } }" class="thumb"><img v-if="m.video.thumb" :src="m.video.thumb" alt="" loading="lazy"></RouterLink>
        <div class="btns">
          <template v-if="m.status === 'suggested'">
            <button class="btn btn-sm btn-primary" :disabled="busy === `${m.video.id}/${m.performer_id}`" @click="decide(m, 'approve')"><Icon name="check" :size="14" />{{ $t("Approve") }}</button>
            <button class="btn btn-sm" :disabled="busy === `${m.video.id}/${m.performer_id}`" @click="decide(m, 'reject')"><Icon name="x" :size="14" />{{ $t("Not them") }}</button>
          </template>
          <button v-else class="btn btn-sm" :disabled="busy === `${m.video.id}/${m.performer_id}`" @click="decide(m, 'undo')">{{ $t("Undo") }}</button>
        </div>
      </div>
      <p v-if="!list.rows.length" class="muted">{{ status === "suggested" ? $t("Nothing waiting.") : $t("None yet.") }}</p>
      <button v-if="list.total > list.rows.length" class="btn btn-sm" @click="more">{{ $t("Show more ({n} left)", { n: list.total - list.rows.length }) }}</button>
    </div>

    <section v-if="canSettings && cfg" class="card block">
      <h2>{{ $t("Settings") }}</h2>
      <label class="switch"><input v-model="cfg.enabled" type="checkbox">{{ $t("Recognise performers by their face") }}</label>
      <div class="grid">
        <label class="switch"><input v-model="cfg.scan_new" type="checkbox">{{ $t("Read new videos by themselves (after their seekbar previews / frames are made)") }}</label>
        <label class="switch"><input v-model="cfg.use_storyboards" type="checkbox">{{ $t("Read the seekbar previews") }}</label>
        <label class="switch"><input v-model="cfg.use_thumbnail" type="checkbox">{{ $t("Read the thumbnail") }}</label>
        <label class="switch"><input v-model="cfg.use_frames" type="checkbox">{{ $t("Read the frames under the player") }}</label>
        <label>{{ $t("Seekbar previews: every") }}<span><input v-model.number="cfg.storyboard_every" class="input input-num" type="number" min="1" max="20"> {{ $t("th picture (1 = all)") }}</span></label>
        <label>{{ $t("Faces smaller than") }}<span><input v-model.number="cfg.min_face_px" class="input input-num" type="number" min="16" max="256"> {{ $t("pixels are left out") }}</span></label>
        <label>{{ $t("Face found when the detector is at least") }}<span><input v-model.number="cfg.min_quality" class="input input-num" type="number" min="0.5" max="0.99" step="0.01"> {{ $t("sure (0.5–0.99)") }}</span></label>
        <label>{{ $t("At most") }}<span><input v-model.number="cfg.max_faces_per_video" class="input input-num" type="number" min="5" max="200"> {{ $t("different faces kept per video") }}</span></label>
        <label>{{ $t("Suggest from a score of") }}<span><input v-model.number="cfg.suggest_score" class="input input-num" type="number" min="20" max="100"> {{ $t("(36: the same person, by OpenCV)") }}</span></label>
        <label>{{ $t("Approve by itself from a score of") }}<span><input v-model.number="cfg.auto_approve_score" class="input input-num" type="number" min="20" max="100"> {{ $t("(100 = never)") }}</span></label>
        <label>{{ $t("At least") }}<span><input v-model.number="cfg.min_support" class="input input-num" type="number" min="1" max="20"> {{ $t("faces in the video that look like them") }}</span></label>
      </div>
      <p class="muted small">{{ $t("The score is how alike two faces are (0–100). Faces from the same photo shoot score in the 60s and up; small, blurred or turned faces score lower, so check those. Changing what's read (the first settings) reads every video again at the next scan; changing the scores only compares again. Not them is never asked again for that video.") }}</p>
      <p class="muted small">{{ $t("Models: {files}, by OpenCV.", { files: (data?.model_files || []).join(", ") }) }}</p>
      <div><button class="btn btn-primary btn-sm" :disabled="busy === 'settings'" @click="saveSettings">{{ $t("Save") }}</button></div>
    </section>
  </div>
</template>

<style scoped>
.intro { margin: 0; max-width: 52rem; }
.small { font-size: 0.8rem; }
.faces { display: flex; flex-direction: column; gap: 0.75rem; }
.block { padding: 1rem 1.125rem; display: flex; flex-direction: column; gap: 0.5rem; }
.block h2 { font-size: 1.05rem; margin: 0; }
.block p { margin: 0; }
.warn { border-left: 3px solid var(--warning, #c90); }
.warn code { font-size: 0.85rem; }
.stats { display: flex; gap: 1.75rem; flex-wrap: wrap; }
.stats div { display: flex; flex-direction: column; }
.stats strong { font-size: 1.35rem; }
.stats span { font-size: 0.78rem; color: var(--text-2); }
.seg { display: flex; gap: 0.35rem; flex-wrap: wrap; }
.acts { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
.above label { display: flex; align-items: center; gap: 0.4rem; }
.rows { display: flex; flex-direction: column; gap: 0.5rem; }
.row { display: flex; align-items: center; gap: 0.75rem; padding: 0.6rem 0.75rem; flex-wrap: wrap; }
.pics { display: flex; gap: 0.35rem; }
.face { width: 3.5rem; height: 3.5rem; border-radius: 0.4rem; object-fit: cover; background: var(--panel-2); display: block; }
.txt { flex: 1; min-width: 12rem; display: flex; flex-direction: column; gap: 0.1rem; }
.who { font-weight: 700; color: var(--text); }
.thumb img { width: 6.5rem; aspect-ratio: 16 / 9; object-fit: cover; border-radius: var(--radius); display: block; background: var(--panel-hover); }
.btns { display: flex; gap: 0.4rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(17rem, 1fr)); gap: 0.6rem 1.25rem; }
.grid label { display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.84rem; font-weight: 600; }
.grid label span { display: flex; align-items: center; gap: 0.4rem; font-weight: 400; color: var(--text-2); }
.switch { display: flex; flex-direction: row !important; align-items: center; gap: 0.4rem; font-size: 0.84rem; font-weight: 600; }
</style>
