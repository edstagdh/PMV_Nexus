<script setup>
// Upload a video from this computer, as a few steps:
//  1. pick the file and fill in its details; the details go first
//     (POST /api/library/local), then the file itself streamed as the body
//     of PUT /api/library/local/{id}, with progress;
//  2. the server checks it (GET /api/library/local/{id}, polled): kept as it
//     is, re-packed into MP4, or -- when browsers can't play it -- converted
//     with the codec picked here (ConvertStep), unless Server settings say to
//     do that automatically. Only then is it added to the library;
//  3. choose the thumbnail (ThumbnailStep) and 4. the preview clips (PreviewStep).
import { askConfirm } from "../stores/confirm.js";
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import Icon from "../components/Icon.vue";
import TagInput from "../components/TagInput.vue";
import ConvertStep from "../components/upload/ConvertStep.vue";
import ThumbnailStep from "../components/upload/ThumbnailStep.vue";
import PreviewStep from "../components/upload/PreviewStep.vue";
import { api, qs } from "../lib/api.js";
import { formatBytes } from "../lib/format.js";
import { toast } from "../stores/toast.js";

// Only these: anything else is refused here before a byte is sent (and again by
// the server, which also checks the file's contents -- backend/upload_safety.py).
const EXTENSIONS = [".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".wmv", ".ts"];
const ACCEPT = EXTENSIONS.join(",");
const emptyForm = () => ({ title: "", creator: "", upload_date: "", description: "", performers: [], tags: [], music: [], flags: [] });

const file = ref(null);
const form = ref(emptyForm());
const creators = ref([]);
const dragOver = ref(false);
// idle | uploading | processing (server checks/converts) | convert (needs the codec choice)
// | thumbnail | preview | done | failed
const state = ref("idle");
const status = ref(null); // GET /api/library/local/{id}
const plan = ref(null);   // preview-plan: duration + suggested clip times
const progress = ref({ loaded: 0, total: 0, speed: 0 });
const error = ref("");
const result = ref(null);
let xhr = null;
let uploadId = null;

onMounted(async () => {
  try { creators.value = (await api(`/api/library/suggest?${qs({ field: "creators", q: "", limit: 500 })}`)).items.map((i) => i.value); } catch { /* no suggestions */ }
});

function choose(f) {
  if (!f) return;
  const ext = (f.name.match(/\.[^.]+$/)?.[0] || "").toLowerCase();
  if (!EXTENSIONS.includes(ext)) {
    file.value = null;
    error.value = `“${f.name}” can't be uploaded: only ${EXTENSIONS.join(", ")} video files are allowed.`;
    toast(error.value, { type: "error", timeout: 6000 });
    return;
  }
  if (!f.size) {
    file.value = null;
    error.value = `“${f.name}” is empty.`;
    return;
  }
  file.value = f;
  if (!form.value.title) form.value.title = f.name.replace(/\.[^.]+$/, "").replace(/[_.]+/g, " ").trim();
  error.value = "";
}
function onDrop(e) { dragOver.value = false; choose(e.dataTransfer?.files?.[0]); }

const percent = computed(() => (progress.value.total ? Math.floor((progress.value.loaded / progress.value.total) * 100) : 0));
const eta = computed(() => {
  const { loaded, total, speed } = progress.value;
  if (!speed) return "";
  const s = Math.round((total - loaded) / speed);
  return s > 90 ? `${Math.round(s / 60)} min left` : `${s}s left`;
});
const canSend = computed(() => file.value && form.value.title.trim() && state.value !== "uploading");

async function upload() {
  if (!canSend.value) return;
  error.value = "";
  const f = file.value;
  try {
    ({ upload_id: uploadId } = await api("/api/library/local", {
      method: "POST",
      body: { ...form.value, upload_date: form.value.upload_date || null, filename: f.name, size: f.size },
    }));
  } catch (err) { error.value = err.message || "The upload couldn't start"; return; }

  state.value = "uploading";
  progress.value = { loaded: 0, total: f.size, speed: 0 };
  const started = Date.now();
  xhr = new XMLHttpRequest();
  xhr.open("PUT", `/api/library/local/${uploadId}`);
  xhr.setRequestHeader("Content-Type", "application/octet-stream");
  xhr.upload.onprogress = (e) => {
    const secs = (Date.now() - started) / 1000;
    progress.value = { loaded: e.loaded, total: e.total || f.size, speed: secs > 0.5 ? e.loaded / secs : 0 };
  };
  xhr.onload = () => {
    let data = null;
    try { data = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
    if (xhr.status >= 200 && xhr.status < 300) {
      onStatus(data);
    } else {
      state.value = "failed";
      error.value = data?.detail || `The upload failed (${xhr.status})`;
    }
    xhr = null;
  };
  xhr.onerror = () => { state.value = "failed"; error.value = "The connection was lost during the upload"; xhr = null; };
  xhr.onabort = () => { state.value = "idle"; error.value = "Upload cancelled"; xhr = null; api(`/api/library/local/${uploadId}`, { method: "DELETE" }).catch(() => {}); };
  xhr.send(f);
}
// ---- after the file arrived: follow the server's check / conversion ----
let poll = null;
const checks = ref([]); // safety checks the server ran (virus scan, VirusTotal) -- shown when it's done
const PROCESSING = ["queued", "checking", "scanning", "remuxing", "converting", "finishing"];
async function onStatus(s) {
  status.value = s;
  clearTimeout(poll);
  if (PROCESSING.includes(s.state)) {
    state.value = "processing";
    poll = setTimeout(refresh, 1000);
  } else if (s.state === "needs_conversion") {
    state.value = "convert";
  } else if (s.state === "done") {
    result.value = s.item;
    checks.value = s.checks || [];
    toast(`“${s.item.title}” is in the library`, { type: "success" });
    try { plan.value = await api(`/api/library/${encodeURIComponent(s.item.id)}/preview-plan`); } catch { plan.value = { duration: 0, timestamps: [] }; }
    try { result.value = await api(`/api/library/${encodeURIComponent(s.item.id)}/item`); } catch { /* keep what we have */ }
    state.value = "thumbnail";
  } else {
    state.value = "failed";
    error.value = s.message || "The upload failed";
  }
}
async function refresh() {
  try { onStatus(await api(`/api/library/local/${uploadId}`)); }
  catch (err) { state.value = "failed"; error.value = err.message || "Lost track of the upload"; }
}
const busy = ref(false);
async function convert(choice) {
  busy.value = true;
  try { onStatus(await api(`/api/library/local/${uploadId}/convert`, { method: "POST", body: choice })); }
  catch (err) { toast(err.message || "Could not start the conversion", { type: "error" }); }
  finally { busy.value = false; }
}
async function cancelProcessing() {
  if (!(await askConfirm("Cancel this upload? The uploaded file is deleted.", { ok: "Yes, cancel", danger: true }))) return;
  clearTimeout(poll);
  try { await api(`/api/library/local/${uploadId}`, { method: "DELETE" }); } catch { /* gone already */ }
  state.value = "idle";
  status.value = null;
  error.value = "Upload cancelled";
}
const stepLabel = computed(() => ({
  queued: "Waiting in line…", checking: "Checking the file…", scanning: "Scanning for viruses…", remuxing: "Re-packing into MP4…",
  converting: "Converting…", finishing: "Making the thumbnail and preview…",
}[status.value?.state] || "Working…"));

function cancel() { xhr?.abort(); }
function another() {
  file.value = null; form.value = emptyForm(); state.value = "idle"; result.value = null; error.value = "";
  status.value = null; plan.value = null; uploadId = null;
}

// Leaving the page mid-upload would cancel it: ask first.
function beforeUnload(e) { if (state.value === "uploading") { e.preventDefault(); e.returnValue = ""; } }
onMounted(() => window.addEventListener("beforeunload", beforeUnload));
onBeforeUnmount(() => { window.removeEventListener("beforeunload", beforeUnload); xhr?.abort(); clearTimeout(poll); });
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>Upload</h1>
        <p>Add a video from this computer to the library. A file browsers can't play is re-packed into MP4, or converted if it has to be;
          then you choose its thumbnail and preview clip. Uploaded videos aren't linked to PMVHaven, so they're never synced with it.</p>
      </div>
    </header>

    <section v-if="state === 'processing'" class="card panel">
      <h2>{{ stepLabel }}</h2>
      <p class="help-text">{{ status?.message }}</p>
      <div class="progress-box">
        <div class="bar" :class="{ indeterminate: status?.progress == null }"><div :style="{ width: `${status?.progress ?? 30}%` }" /></div>
      </div>
      <p class="muted small">You can leave this page: the work carries on and the video appears in the library when it's ready
        (the thumbnail and preview steps are then skipped — use the video's ⋮ menu for the thumbnail).</p>
      <ul v-if="status?.checks?.length" class="checks"><li v-for="c in status.checks" :key="c.name" :class="c.ok ? 'ok-text' : 'muted'">{{ c.ok ? "✓" : "–" }} {{ c.name }}: {{ c.result }}</li></ul>
      <p v-if="status?.state === 'queued'" class="muted small">Uploads are checked one at a time, in the order they arrived.</p>
      <div v-if="['queued', 'converting', 'checking', 'scanning', 'remuxing'].includes(status?.state)" class="form-actions">
        <button class="btn btn-sm" @click="cancelProcessing">Cancel</button></div>
    </section>

    <ConvertStep v-else-if="state === 'convert'" :status="status" :busy="busy" @convert="convert" @cancel="cancelProcessing" />

    <ThumbnailStep v-else-if="state === 'thumbnail' && result" :item="result" :duration="plan?.duration || 0"
                   @update="result = $event" @next="state = 'preview'" />

    <PreviewStep v-else-if="state === 'preview' && result" :item="result" :plan="plan"
                 @update="result = $event" @back="state = 'thumbnail'" @finish="state = 'done'" />

    <section v-else-if="state === 'done' && result" class="card panel done">
      <h2>✅ “{{ result.title }}” is in the library</h2>
      <ul v-if="checks.length" class="checks"><li v-for="c in checks" :key="c.name" :class="c.ok ? 'ok-text' : 'muted'">{{ c.ok ? "✓" : "–" }} {{ c.name }}: {{ c.result }}</li></ul>
      <div class="form-actions">
        <RouterLink :to="{ name: 'video', params: { id: result.id } }" class="btn btn-primary btn-sm"><Icon name="play" />Open it</RouterLink>
        <button class="btn btn-sm" @click="another">Upload another</button>
      </div>
    </section>

    <form v-else class="card panel" @submit.prevent="upload">
      <label class="drop" :class="{ over: dragOver, has: file }" @dragover.prevent="dragOver = true" @dragleave="dragOver = false" @drop.prevent="onDrop">
        <input type="file" :accept="ACCEPT" class="sr-only" :disabled="state === 'uploading'" @change="choose($event.target.files[0])">
        <Icon name="upload" :size="28" class="drop-icon" />
        <span v-if="file"><strong>{{ file.name }}</strong> · {{ formatBytes(file.size) }}<br><span class="muted">Click or drop to pick another file</span></span>
        <span v-else><strong>Choose a video file</strong> or drop it here<br><span class="muted">Only {{ EXTENSIONS.map((e) => e.slice(1)).join(", ") }}</span></span>
      </label>

      <div class="form-row">
        <label class="label" for="up-title">Title</label>
        <input id="up-title" v-model="form.title" class="input" maxlength="300" required>
      </div>
      <div class="form-row">
        <label class="label" for="up-creator">Creator<small>Also the folder it's saved in.</small></label>
        <input id="up-creator" v-model="form.creator" class="input" list="up-creators" placeholder="Unknown" maxlength="120">
        <datalist id="up-creators"><option v-for="c in creators" :key="c" :value="c" /></datalist>
      </div>
      <div class="form-row">
        <label class="label" for="up-date">Upload date<small>Optional — when it was published.</small></label>
        <input id="up-date" v-model="form.upload_date" class="input date" type="date">
      </div>
      <div class="form-row top">
        <label class="label" for="up-desc">Description</label>
        <textarea id="up-desc" v-model="form.description" class="input area" rows="3" maxlength="10000" />
      </div>
      <div class="form-row"><span class="label">Performers</span><TagInput keep-open show-all v-model="form.performers" field="performers" chip-class="chip-performer" placeholder="Type a name…" /></div>
      <div class="form-row"><span class="label">Tags</span><TagInput keep-open show-all v-model="form.tags" field="tags" chip-class="chip-tag" placeholder="Type a tag…" /></div>
      <div class="form-row"><span class="label">Music</span><TagInput keep-open show-all v-model="form.music" field="music" chip-class="chip-music" placeholder="Song — Artist" /></div>
      <div class="form-row"><span class="label">Content flags</span><TagInput keep-open show-all v-model="form.flags" field="flags" placeholder="e.g. Explicit content" /></div>

      <div v-if="state === 'uploading'" class="progress-box">
        <div class="bar"><div :style="{ width: `${percent}%` }" /></div>
        <span class="muted small">{{ percent }}% · {{ formatBytes(progress.loaded) }} of {{ formatBytes(progress.total) }}
          <template v-if="progress.speed"> · {{ formatBytes(progress.speed) }}/s · {{ eta }}</template></span>
      </div>
      <p v-if="error" class="error-text">{{ error }}</p>
      <div class="form-actions">
        <button class="btn btn-primary" type="submit" :disabled="!canSend">{{ state === "uploading" ? "Uploading…" : "Upload" }}</button>
        <button v-if="state === 'uploading'" class="btn" type="button" @click="cancel">Cancel</button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.checks { list-style: none; margin: 0.25rem 0 0.5rem; padding: 0; display: flex; flex-direction: column; gap: 0.2rem; font-size: 0.84rem; }
.drop { display: flex; align-items: center; gap: 1rem; padding: 1.25rem; border: 2px dashed var(--border-strong); border-radius: var(--radius-lg);
  cursor: pointer; font-size: 0.9rem; transition: border-color 0.15s, background 0.15s; }
.drop:hover, .drop.over { border-color: var(--accent); background: var(--accent-soft); }
.drop.has { border-style: solid; }
.drop-icon { color: var(--accent); }
.form-row.top { align-items: start; }
.area { height: auto; padding: 0.5rem 0.75rem; resize: vertical; font: inherit; }
.date { max-width: 12rem; }
.progress-box { display: flex; flex-direction: column; gap: 0.375rem; }
.bar { height: 0.5rem; border-radius: 1rem; background: var(--panel-2); overflow: hidden; }
.bar div { height: 100%; background: var(--accent); transition: width 0.3s; }
.bar.indeterminate div { animation: slide 1.2s ease-in-out infinite; }
@keyframes slide { from { transform: translateX(-100%); } to { transform: translateX(340%); } }
h2 { font-size: 1.05rem; margin: 0; }
.small { font-size: 0.8rem; }
.done h2 { font-size: 1.05rem; }
</style>
