<script setup>
// Management -> Faces (3.12.0; 3.13.0: sorting, filters, pages, bigger pictures, where in the video,
// a page at once, Approve adds the performer to the video): performers recognised in videos by
// their face, completely offline (backend/library_mixins/faces.py).
import { t, tn } from "../../lib/i18n.js";
import { ref, computed, watch, onMounted, onBeforeUnmount } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import Icon from "../../components/Icon.vue";
import { api, qs } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";
import { askConfirm } from "../../stores/confirm.js";
import { session, savePref, hasPermission } from "../../stores/session.js";
import { onLibraryChange } from "../../stores/live.js";
import { formatDateTime, formatDuration } from "../../lib/format.js";

const route = useRoute();
const router = useRouter();
const data = ref(null);
const cfg = ref(null);
const list = ref(null);
const status = ref("suggested");
const page = ref(0);
const q = ref("");
const error = ref("");
const busy = ref("");
const above = ref(80);
const below = ref(50);
const bigPicture = ref(null);      // a match whose video picture is shown big
const canSettings = hasPermission("settings.manage");
const performer = computed(() => (typeof route.query.performer === "string" ? route.query.performer : ""));
const prefs = computed(() => session.prefs);
const pageSize = computed(() => prefs.value.facesPageSize || 50);
const px = computed(() => prefs.value.facesPictureSize || 160);
const pages = computed(() => Math.max(1, Math.ceil((list.value?.total || 0) / pageSize.value)));
const SORTS = [["score", t("Score")], ["performer", t("Performer")], ["video", t("Video")], ["found", t("When found / decided")],
  ["faces", t("Faces that look like them")], ["time", t("Time in the video")]];
const TABS = computed(() => [
  { key: "suggested", label: t("Waiting ({n})", { n: data.value?.matches?.suggested ?? "…" }) },
  { key: "approved", label: t("Approved ({n})", { n: data.value?.matches?.approved ?? "…" }) },
  { key: "rejected", label: t("Not them ({n})", { n: data.value?.matches?.rejected ?? "…" }) },
]);
const SOURCE = { thumbnail: t("thumbnail"), frame: t("frame under the player"), storyboard: t("seekbar picture") };

async function loadOverview() {
  data.value = await api("/api/faces");
  if (!cfg.value) cfg.value = { ...data.value.settings };
}
async function loadList() {
  const want = qs({ status: status.value, sort: prefs.value.facesSort, desc: prefs.value.facesDesc, min_score: prefs.value.facesMinScore || 0,
    q: q.value.trim() || undefined, performer: performer.value || undefined, offset: page.value * pageSize.value, limit: pageSize.value });
  const r = await api(`/api/faces/matches?${want}`);
  if (r.rows.length === 0 && page.value > 0 && r.total > 0) { page.value = Math.max(0, Math.ceil(r.total / pageSize.value) - 1); return loadList(); }
  list.value = r;
}
async function load() {
  try { await Promise.all([loadOverview(), loadList()]); error.value = ""; } catch (err) { error.value = err.message || t("Could not load the faces"); }
}
let timer = null;
let stop = null;
let poll = null;
let typing = null;
onMounted(() => {
  load();
  stop = onLibraryChange((c) => { if (!c || c.includes("faces")) { clearTimeout(timer); timer = setTimeout(load, 500); } });
  poll = setInterval(() => { if (data.value?.running) load(); }, 3000);
});
onBeforeUnmount(() => { stop?.(); clearTimeout(timer); clearInterval(poll); clearTimeout(typing); });
watch(() => [prefs.value.facesSort, prefs.value.facesDesc, prefs.value.facesMinScore, prefs.value.facesPageSize, performer.value], () => { page.value = 0; loadList(); });
watch(q, () => { clearTimeout(typing); typing = setTimeout(() => { page.value = 0; loadList(); }, 350); });

function pick(key) { status.value = key; page.value = 0; list.value = null; loadList(); }
function go(n) { page.value = Math.min(Math.max(0, n), pages.value - 1); loadList(); window.scrollTo({ top: 0 }); }
function clearPerformer() { const { performer: _p, ...rest } = route.query; router.replace({ query: rest }); }
const pic = (url, size) => (url ? `${url}&size=${Math.min(1280, Math.round(size * (window.devicePixelRatio || 1)))}` : null);
const videoAt = (m) => ({ name: "video", params: { id: m.video.id }, query: m.at != null ? { t: Math.floor(m.at) } : {} });

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
async function stopScan() { await api("/api/faces/stop", { method: "POST" }).catch(() => null); load(); }
const key = (m) => `${m.video.id}/${m.performer_id}`;
async function decide(rows, action) {
  if (!rows.length) return;
  if (rows.length > 1 && !(await askConfirm(action === "approve"
    ? tn("Approve {n} match? Each performer is added to the video's performers.", "Approve these {n} matches? Each performer is added to the video's performers.", rows.length)
    : action === "reject" ? tn("Mark {n} match as Not them?", "Mark these {n} matches as Not them?", rows.length)
      : tn("Put {n} match back to waiting?", "Put these {n} matches back to waiting?", rows.length), { ok: t("Yes") }))) return;
  busy.value = rows.length > 1 ? "page" : key(rows[0]);
  try {
    const r = rows.length > 1
      ? await api("/api/faces/matches/bulk", { method: "POST", body: { pairs: rows.map((m) => [m.video.id, m.performer_id]), action } })
      : await api(`/api/faces/matches/${encodeURIComponent(rows[0].video.id)}/${encodeURIComponent(rows[0].performer_id)}`, { method: "POST", body: { action } });
    if (action === "approve" && r.videos_changed) toast(tn("Added to {n} video's performers", "Added to {n} videos' performers", r.videos_changed), { type: "success" });
    const done = new Set(rows.map(key));
    list.value = { ...list.value, rows: list.value.rows.filter((m) => !done.has(key(m))), total: list.value.total - rows.length };
    loadOverview();
    if (!list.value.rows.length) loadList();
  } catch (err) { toast(err.message || t("Could not save"), { type: "error" }); }
  finally { busy.value = ""; }
}
async function approveAbove() {
  if (!(await askConfirm(t("Approve every waiting match with a score of {score} or more? Each performer is added to the video's performers.", { score: above.value }), { ok: t("Approve") }))) return;
  busy.value = "above";
  try {
    const r = await api("/api/faces/approve-above", { method: "POST", body: { score: above.value } });
    toast(tn("{n} match approved", "{n} matches approved", r.approved), { type: "success" });
    load();
  } catch (err) { toast(err.message || t("Could not save"), { type: "error" }); }
  finally { busy.value = ""; }
}
async function removeBelow() {
  if (!(await askConfirm(t("Remove every waiting match with a score below {score}, for everyone? Approved and Not them stay. They can come back only if a later scan finds them at or above “Suggest from”.", { score: below.value }), { ok: t("Remove"), danger: true }))) return;
  busy.value = "below";
  try {
    const r = await api("/api/faces/remove-below", { method: "POST", body: { score: below.value } });
    toast(tn("{n} waiting match removed", "{n} waiting matches removed", r.removed), { type: "success" });
    page.value = 0; load();
  } catch (err) { toast(err.message || t("Could not save"), { type: "error" }); }
  finally { busy.value = ""; }
}
async function saveSettings() {
  busy.value = "settings";
  try {
    const r = await api("/api/settings/faces", { method: "POST", body: cfg.value });
    cfg.value = { ...r.faces };
    toast(t("Saved"), { type: "success" });
    load();
  } catch (err) { toast(err.message || t("Could not save"), { type: "error" }); }
  finally { busy.value = ""; }
}
const mb = (n) => `${((n || 0) / 1024 / 1024).toFixed(1)} MB`;
</script>

<template>
  <div class="page faces" :style="{ '--face': `${px}px` }">
    <p class="muted small intro">{{ $t("Performers recognised in videos by their face: the faces in each video's seekbar previews, thumbnail and frames are compared with the photos of the performers in your library. Everything happens on this computer; nothing is sent anywhere. Approving a match adds the performer to the video's performers; Undo or Not them takes them off again.") }}</p>
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

    <div class="toolbar">
      <input v-model="q" class="input search" type="search" :placeholder="$t('Performer or video…')" :aria-label="$t('Find a performer or video')">
      <label class="small">{{ $t("Sort") }}
        <select class="select" :value="prefs.facesSort" :aria-label="$t('Sort')" @change="savePref('facesSort', $event.target.value)">
          <option v-for="[v, l] in SORTS" :key="v" :value="v">{{ l }}</option>
        </select>
      </label>
      <button class="btn btn-sm btn-ghost" :title="prefs.facesDesc ? $t('Highest / newest / Z first') : $t('Lowest / oldest / A first')" @click="savePref('facesDesc', !prefs.facesDesc)">
        <Icon :name="prefs.facesDesc ? 'arrow-down' : 'arrow-up'" :size="14" />{{ prefs.facesDesc ? $t("Descending") : $t("Ascending") }}</button>
      <label class="small">{{ $t("Hide below") }}
        <input class="input input-num" type="number" min="0" max="100" :value="prefs.facesMinScore" :aria-label="$t('Hide matches below this score')"
               @change="savePref('facesMinScore', Math.max(0, Math.min(100, Math.round(Number($event.target.value) || 0))))"></label>
      <label class="small">{{ $t("Size") }}
        <select class="select" :value="prefs.facesPictureSize" :aria-label="$t('Picture size')" @change="savePref('facesPictureSize', Number($event.target.value))">
          <option :value="96">{{ $t("Small") }}</option><option :value="160">{{ $t("Medium") }}</option>
          <option :value="240">{{ $t("Large") }}</option><option :value="320">{{ $t("Very large") }}</option>
        </select></label>
      <span v-if="performer" class="pill">{{ $t("One performer") }} <button class="btn btn-ghost btn-sm" :aria-label="$t('Show all performers')" @click="clearPerformer">✕</button></span>
    </div>

    <div v-if="status === 'suggested' && list?.total" class="acts bulk">
      <button class="btn btn-sm" :disabled="busy === 'page'" @click="decide(list.rows, 'approve')"><Icon name="check" :size="14" />{{ $t("Approve this page") }}</button>
      <button class="btn btn-sm" :disabled="busy === 'page'" @click="decide(list.rows, 'reject')"><Icon name="x" :size="14" />{{ $t("Not them: this page") }}</button>
      <span class="sep" />
      <span class="grp"><label class="small">{{ $t("Approve all from") }} <input v-model.number="above" class="input input-num" type="number" min="20" max="100"></label>
        <button class="btn btn-sm" :disabled="busy === 'above'" @click="approveAbove">{{ $t("Approve") }}</button></span>
      <span class="sep" />
      <span class="grp"><label class="small">{{ $t("Remove all below") }} <input v-model.number="below" class="input input-num" type="number" min="0" max="100"></label>
        <button class="btn btn-sm btn-danger" :disabled="busy === 'below'" @click="removeBelow">{{ $t("Remove") }}</button></span>
    </div>

    <div v-if="list" class="rows">
      <p class="muted small">{{ $tn("{n} match", "{n} matches", list.total) }}<template v-if="prefs.facesMinScore"> · {{ $t("below {score} hidden", { score: prefs.facesMinScore }) }}</template></p>
      <div v-for="m in list.rows" :key="key(m)" class="card row">
        <div class="pics">
          <figure>
            <button v-if="m.face" class="pic-btn" :title="$t('Show the picture it was found in')" @click="bigPicture = m">
              <img :src="pic(m.face, px)" alt="" class="face" loading="lazy"></button>
            <img v-else-if="m.crop" :src="m.crop" alt="" class="face" loading="lazy">
            <span v-else class="face none" :title="$t('The face picture is made again at the next scan')" />
            <figcaption>{{ $t("In the video") }}</figcaption>
          </figure>
          <figure>
            <img v-if="m.performer.photo" :src="m.performer.photo" alt="" class="face photo" loading="lazy">
            <span v-else class="face none" />
            <figcaption>{{ m.performer.name }}</figcaption>
          </figure>
        </div>
        <div class="txt">
          <RouterLink :to="{ name: 'performer', params: { name: m.performer.name } }" class="who">{{ m.performer.name }}</RouterLink>
          <span class="muted small">{{ $t("in") }} <RouterLink :to="videoAt(m)">{{ m.video.title }}</RouterLink></span>
          <span class="small"><strong class="score">{{ Math.round(m.score) }}</strong> · {{ tn("{n} face looks like them", "{n} faces look like them", m.support) }}</span>
          <span v-if="m.source" class="muted small">
            <template v-if="m.at != null"><RouterLink :to="videoAt(m)">{{ $t("at {time}", { time: formatDuration(m.at) }) }}</RouterLink> · </template>{{ SOURCE[m.source] || m.source }}</span>
          <span v-if="m.decided_by" class="muted small">{{ m.auto ? $t("approved by itself") : $t("by {name}", { name: m.decided_by }) }}<template v-if="m.decided_at"> · {{ formatDateTime(m.decided_at) }}</template>
            <template v-if="m.added_to_video"> · {{ $t("added to the video") }}</template></span>
          <div class="btns">
            <template v-if="m.status === 'suggested'">
              <button class="btn btn-sm btn-primary" :disabled="busy === key(m)" @click="decide([m], 'approve')"><Icon name="check" :size="14" />{{ $t("Approve") }}</button>
              <button class="btn btn-sm" :disabled="busy === key(m)" @click="decide([m], 'reject')"><Icon name="x" :size="14" />{{ $t("Not them") }}</button>
            </template>
            <button v-else class="btn btn-sm" :disabled="busy === key(m)" @click="decide([m], 'undo')">{{ $t("Undo") }}</button>
          </div>
        </div>
        <RouterLink :to="videoAt(m)" class="thumb"><img v-if="m.video.thumb" :src="m.video.thumb" alt="" loading="lazy"></RouterLink>
      </div>
      <p v-if="!list.rows.length" class="muted">{{ status === "suggested" ? $t("Nothing waiting.") : $t("None yet.") }}</p>
      <nav v-if="pages > 1" class="pager" :aria-label="$t('Pages')">
        <button class="btn btn-sm" :disabled="page === 0" @click="go(0)">«</button>
        <button class="btn btn-sm" :disabled="page === 0" @click="go(page - 1)">‹ {{ $t("Previous") }}</button>
        <span class="small">{{ $t("Page {n} of {total}", { n: page + 1, total: pages }) }}</span>
        <button class="btn btn-sm" :disabled="page >= pages - 1" @click="go(page + 1)">{{ $t("Next") }} ›</button>
        <button class="btn btn-sm" :disabled="page >= pages - 1" @click="go(pages - 1)">»</button>
      </nav>
    </div>

    <div v-if="bigPicture" class="big-overlay" role="dialog" :aria-label="$t('The picture it was found in')" @click.self="bigPicture = null" @keydown.esc="bigPicture = null">
      <div class="card big">
        <img :src="pic(bigPicture.frame, 960)" alt="">
        <p class="small">{{ bigPicture.performer.name }} · {{ Math.round(bigPicture.score) }} ·
          <RouterLink :to="videoAt(bigPicture)">{{ bigPicture.video.title }}<template v-if="bigPicture.at != null"> — {{ $t("at {time}", { time: formatDuration(bigPicture.at) }) }}</template></RouterLink></p>
        <button class="btn btn-sm" autofocus @click="bigPicture = null">{{ $t("Close") }}</button>
      </div>
    </div>

    <section v-if="canSettings && cfg" class="card block">
      <h2>{{ $t("Settings") }}</h2>
      <label class="switch"><input v-model="cfg.enabled" type="checkbox">{{ $t("Recognise performers by their face") }}</label>
      <h3>{{ $t("Reading the videos") }}</h3>
      <div class="grid">
        <label class="switch"><input v-model="cfg.scan_new" type="checkbox">{{ $t("Read new videos by themselves (after their seekbar previews / frames are made)") }}</label>
        <label class="switch"><input v-model="cfg.use_storyboards" type="checkbox">{{ $t("Read the seekbar previews") }}</label>
        <label class="switch"><input v-model="cfg.use_thumbnail" type="checkbox">{{ $t("Read the thumbnail") }}</label>
        <label class="switch"><input v-model="cfg.use_frames" type="checkbox">{{ $t("Read the frames under the player") }}</label>
        <label class="switch"><input v-model="cfg.upscale_small" type="checkbox">{{ $t("Enlarge small pictures before reading (finds smaller faces, slower)") }}</label>
        <label>{{ $t("Seekbar previews: every") }}<span><input v-model.number="cfg.storyboard_every" class="input input-num" type="number" min="1" max="20"> {{ $t("th picture (1 = all)") }}</span></label>
        <label>{{ $t("Faces smaller than") }}<span><input v-model.number="cfg.min_face_px" class="input input-num" type="number" min="16" max="256"> {{ $t("pixels are left out") }}</span></label>
        <label>{{ $t("Face found when the detector is at least") }}<span><input v-model.number="cfg.min_quality" class="input input-num" type="number" min="0.5" max="0.99" step="0.01"> {{ $t("sure (0.5–0.99)") }}</span></label>
        <label>{{ $t("At most") }}<span><input v-model.number="cfg.max_faces_per_video" class="input input-num" type="number" min="5" max="200"> {{ $t("different faces kept per video") }}</span></label>
      </div>
      <h3>{{ $t("Performer photos") }}</h3>
      <div class="grid">
        <label>{{ $t("Faces in photos smaller than") }}<span><input v-model.number="cfg.photo_min_face_px" class="input input-num" type="number" min="16" max="512"> {{ $t("pixels are left out") }}</span></label>
        <label>{{ $t("Photos read per performer") }}<span><input v-model.number="cfg.max_photos_per_performer" class="input input-num" type="number" min="0" max="50"> {{ $t("(0 = all)") }}</span></label>
      </div>
      <h3>{{ $t("Matching") }}</h3>
      <div class="grid">
        <label>{{ $t("Suggest from a score of") }}<span><input v-model.number="cfg.suggest_score" class="input input-num" type="number" min="20" max="100"> {{ $t("(waiting ones below are removed)") }}</span></label>
        <label>{{ $t("A face counts for a performer when it's") }}<span><input v-model.number="cfg.margin" class="input input-num" type="number" min="0" max="30"> {{ $t("points closer to them than to anyone else") }}</span></label>
        <label>{{ $t("At most") }}<span><input v-model.number="cfg.max_per_video" class="input input-num" type="number" min="1" max="50"> {{ $t("suggestions per video (the best ones)") }}</span></label>
        <label>{{ $t("At least") }}<span><input v-model.number="cfg.min_support" class="input input-num" type="number" min="1" max="20"> {{ $t("faces in the video that look like them") }}</span></label>
        <label>{{ $t("Approve by itself from a score of") }}<span><input v-model.number="cfg.auto_approve_score" class="input input-num" type="number" min="20" max="100"> {{ $t("(100 = never; adds the performer to the video)") }}</span></label>
      </div>
      <p class="muted small">{{ $t("The score is how alike two faces are (0–100; 36 is where OpenCV says “the same person”). Faces from the same photo shoot score in the 60s and up; small, blurred or turned faces score lower. Each face counts for one performer only — the one it looks most like — and a face of a performer the video already lists suggests nobody else. Changing what's read reads every video again at the next scan; changing the matching only compares again. Not them is never asked again for that video.") }}</p>
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
.block h3 { font-size: 0.9rem; margin: 0.5rem 0 0; }
.block p { margin: 0; }
.warn { border-left: 3px solid var(--warning, #c90); }
.warn code { font-size: 0.85rem; }
.stats { display: flex; gap: 1.75rem; flex-wrap: wrap; }
.stats div { display: flex; flex-direction: column; }
.stats strong { font-size: 1.35rem; }
.stats span { font-size: 0.78rem; color: var(--text-2); }
.seg { display: flex; gap: 0.35rem; flex-wrap: wrap; }
.acts, .toolbar { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
.toolbar label, .bulk label { display: flex; align-items: center; gap: 0.35rem; }
.toolbar .search { flex: 1 1 14rem; max-width: 22rem; }
.toolbar .select { width: auto; min-width: 8rem; }
.input-num { width: 4.75rem; }
.grp { display: inline-flex; align-items: center; gap: 0.4rem; }
.sep { width: 1px; align-self: stretch; background: var(--border); margin: 0 0.25rem; }
.rows { display: flex; flex-direction: column; gap: 0.5rem; }
.rows > p { margin: 0; }
.row { display: flex; align-items: center; gap: 0.9rem; padding: 0.6rem 0.75rem; flex-wrap: wrap; }
.pics { display: flex; gap: 0.5rem; }
figure { margin: 0; display: flex; flex-direction: column; align-items: center; gap: 0.2rem; }
figcaption { font-size: 0.7rem; color: var(--text-2); max-width: var(--face); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.face { width: var(--face); height: var(--face); border-radius: 0.5rem; object-fit: cover; background: var(--panel-2); display: block; }
.face.photo { object-position: 50% 20%; }
.pic-btn { padding: 0; border: 0; background: none; cursor: zoom-in; border-radius: 0.5rem; }
.txt { flex: 1; min-width: 12rem; display: flex; flex-direction: column; gap: 0.15rem; }
.who { font-weight: 700; color: var(--text); }
.score { font-size: 1rem; }
.thumb img { width: calc(var(--face) * 1.1); max-width: 12rem; aspect-ratio: 16 / 9; object-fit: cover; border-radius: var(--radius); display: block; background: var(--panel-hover); }
.btns { display: flex; gap: 0.4rem; flex-wrap: wrap; margin-top: 0.4rem; }
.pager { display: flex; gap: 0.4rem; align-items: center; justify-content: center; flex-wrap: wrap; margin-top: 0.25rem; }
.big-overlay { position: fixed; inset: 0; z-index: 100; background: rgba(0, 0, 0, 0.7); display: flex; align-items: center; justify-content: center; padding: 1rem; }
.big { padding: 0.75rem; display: flex; flex-direction: column; gap: 0.5rem; align-items: center; max-width: min(64rem, 100%); max-height: 100%; overflow: auto; }
.big img { max-width: 100%; max-height: 75vh; border-radius: var(--radius); }
.big p { margin: 0; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(17rem, 1fr)); gap: 0.6rem 1.25rem; }
.grid label { display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.84rem; font-weight: 600; }
.grid label span { display: flex; align-items: center; gap: 0.4rem; font-weight: 400; color: var(--text-2); }
.switch { display: flex; flex-direction: row !important; align-items: center; gap: 0.4rem; font-size: 0.84rem; font-weight: 600; }
@media (max-width: 40rem) { .face { width: min(var(--face), 38vw); height: min(var(--face), 38vw); } figcaption { max-width: 38vw; } }
</style>
