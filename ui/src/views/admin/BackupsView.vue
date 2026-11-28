<script setup>
// Admin -> Backups: zip files of the library database and settings (plus,
// optionally, the thumbnail/preview cache and the secrets). A restore is
// staged here and applied the next time the app starts. 2.25.0: a backup password
// (new backups are AES-256 encrypted zips); protected backups ask for it when the
// saved one doesn't open them.
import { t, N_, formatNumber } from "../../lib/i18n.js";
import { askConfirm, askPassword } from "../../stores/confirm.js";
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import Icon from "../../components/Icon.vue";
import FolderPicker from "../../components/admin/FolderPicker.vue";
import { api } from "../../lib/api.js";
import { formatBytes, formatDateTime } from "../../lib/format.js";
import { toast } from "../../stores/toast.js";
import { onLibraryChange } from "../../stores/live.js";

const data = ref(null);
const error = ref("");
const creating = ref(false);
// 3.7.0: what a new backup holds, part by part (counted by the server), and the live free space.
const plan = ref(null);
const chosen = ref(new Set());
const space = ref(null);

async function load() {
  try {
    data.value = await api("/api/backups"); error.value = "";
    if (splitGb.value === null) splitGb.value = data.value.split_gb || 0;
  } catch (err) { error.value = err.message || t("Could not load the backups"); }
}
// (3.7.0) A backup bigger than this is written as parts (name.zip.001, .002, ...): 0 = one file.
const SPLIT_MIN = 1.9, SPLIT_MAX = 50;
const splitGb = ref(null);
const splitText = ref("");
const splitError = ref("");
async function saveSplit(gb) {
  splitError.value = "";
  if (gb && !(gb >= SPLIT_MIN && gb <= SPLIT_MAX)) { splitError.value = t("Parts can be 1.9 to 50 GB (or 0: one file)"); return; }
  try {
    const r = await api("/api/backups/settings", { method: "POST", body: { split_gb: gb } });
    splitGb.value = r.split_gb; splitText.value = r.split_gb ? String(r.split_gb) : "";
  } catch (err) { splitError.value = err.message || t("That didn't work"); }
}
function setSplitOn(on) { if (on) saveSplit(Number(splitText.value) || SPLIT_MIN); else saveSplit(0); }
const partsCount = computed(() => (splitGb.value ? Math.max(1, Math.ceil(estimate.value / (splitGb.value * 1024 ** 3))) : 1));
// (3.7.0) Where this backup goes: the backups folder, or another one (a bigger or external
// drive). The server remembers folders backups were saved to and lists their backups too.
const DEST_KEY = "pmv-backup-dest";
const dest = ref((() => { try { return localStorage.getItem(DEST_KEY) || ""; } catch { return ""; } })());
const picking = ref(false);
const destError = ref("");
function setDest(folder) {
  dest.value = folder || "";
  try { if (dest.value) localStorage.setItem(DEST_KEY, dest.value); else localStorage.removeItem(DEST_KEY); } catch { /* ignore */ }
  picking.value = false;
  loadPlan();
}
async function loadPlan() {
  destError.value = "";
  try {
    plan.value = await api(`/api/backups/plan?${new URLSearchParams({ dir: dest.value })}`);
    space.value = { free_bytes: plan.value.free_bytes, dir: plan.value.dir };
  } catch (err) {
    if (dest.value && err.status === 400) { destError.value = err.message; setDest(""); return; }
    plan.value = null;
  }
}
// 3.8.0: where the scheduled backup jobs copy and upload their backups (GET /api/backups/offsite).
const offsite = ref(null);
const offsiteError = ref("");
const checking = ref(false);
async function loadOffsite(live = false) {
  if (live) checking.value = true;
  try {
    offsite.value = await api(`/api/backups/offsite?${new URLSearchParams({ live: live ? "1" : "0" })}`);
    offsiteError.value = offsite.value.filester?.error || "";
    if (live && !offsiteError.value) {
      const who = offsite.value.filester.account?.username;
      toast(who ? t("Connected to Filester as {name}", { name: who }) : t("Connected to Filester"), { type: "success" });
    }
  } catch (err) { offsiteError.value = err.message || t("That didn't work"); }
  finally { checking.value = false; }
}
const uploadJobs = computed(() => (offsite.value?.jobs || []).filter((j) => j.upload));
const copyJobs = computed(() => (offsite.value?.jobs || []).filter((j) => j.copy_to.length || j.upload));
async function loadSpace() {
  if (document.hidden) return;
  try { space.value = await api(`/api/backups/space?${new URLSearchParams({ dir: dest.value })}`); } catch { /* keep the last */ }
}
let timer = null, stop = null;
onMounted(() => {
  load().then(() => { splitText.value = splitGb.value ? String(splitGb.value) : ""; inst.value.days = String(data.value?.installer_keep_days ?? 90); });
  loadPlan(); loadOffsite();
  timer = setInterval(loadSpace, 3000);
  stop = onLibraryChange((c) => { if (!c || c.includes("activity")) { load(); loadOffsite(); } });  // a backup finished
});
onBeforeUnmount(() => { clearInterval(timer); stop?.(); });

// 3.8.0: "Also upload to Filester" -- never with the credentials or the videos (as a job's upload)
const uploadToo = ref(false);
const NOT_UPLOADED = ["secrets", "videos"];
const canUpload = computed(() => !!offsite.value?.filester?.configured && !!data.value?.password_set);
function setUpload(on) {
  uploadToo.value = on;
  if (on) chosen.value = new Set([...chosen.value].filter((k) => !NOT_UPLOADED.includes(k)));
}
function toggle(key, on) {
  const next = new Set(chosen.value);
  if (on) next.add(key); else next.delete(key);
  chosen.value = next;
}
const partsChosen = computed(() => (plan.value?.parts || []).filter((p) => chosen.value.has(p.key)));
const estimate = computed(() => {
  const a = plan.value?.always;
  if (!a) return 0;
  return a.bytes - a.database_bytes + Math.floor(a.database_bytes / 3) + partsChosen.value.reduce((n, p) => n + p.bytes, 0);
});
const needed = computed(() => estimate.value + (plan.value?.margin_bytes || 0));
const enough = computed(() => space.value?.free_bytes == null || space.value.free_bytes >= needed.value);
const PART_HELP = {
  secrets: N_("keep such backups private"),
  performer_photos: N_("can be downloaded again"),
  thumbnails: N_("can be made again, but it takes a while"),
  frames: N_("can be made again"),
  storyboards: N_("can be made again"),
  videos: N_("needs as much space as the library itself"),
  derived: N_("can be made again; your face approvals are in the database anyway"),
};

async function create() {
  const a = plan.value.always;
  const lines = [t("This backup will hold:"),
    "• " + t("the database ({size}) and your settings, installed languages, own performer photos and playlist covers ({n} files)",
      { size: formatBytes(a.database_bytes), n: formatNumber(a.files - 1) }),
    ...partsChosen.value.map((p) => "• " + t("{part}: {n} files, {size}", { part: p.label, n: formatNumber(p.files), size: formatBytes(p.bytes) })),
    "", t("About {size} in all; {free} free in {folder}.", { size: formatBytes(estimate.value), free: formatBytes(space.value?.free_bytes ?? 0), folder: space.value?.dir || plan.value.dir })];
  if (uploadToo.value) lines.push(t("Then also uploaded to your private Filester folder (encrypted, in parts of up to {gb} GB).",
    { gb: offsite.value.filester.max_part_gb }));
  if (partsCount.value > 1) lines.push(t("Written as about {n} parts of at most {gb} GB each.", { n: formatNumber(partsCount.value), gb: splitGb.value }));
  if (!(await askConfirm(lines.join("\n"), { title: t("Create the backup?"), ok: t("Create backup") }))) return;
  const videos = partsChosen.value.find((p) => p.key === "videos");
  if (videos && !(await askConfirm(t("Include all {n} videos ({size})? The backup needs that much free space, takes a long time to make, and stays as big as long as it's kept.",
    { n: formatNumber(videos.files), size: formatBytes(videos.bytes) }), { title: t("Back up the videos too?"), ok: t("Include the videos"), danger: true }))) return;
  creating.value = true;
  try {
    await api("/api/backups", { method: "POST", body: { include: [...chosen.value], confirm_videos: !!videos, dir: dest.value || null, upload: uploadToo.value } });
    toast(t("Backup started — follow it on Tasks; a notification says how it went"), { type: "success", timeout: 7000, link: "/tasks" });
  } catch (err) { toast(err.message || t("The backup failed"), { type: "error", timeout: 9000 }); }
  finally { creating.value = false; loadPlan(); }
}
async function act(fn, ok) {
  try { await fn(); toast(ok); load(); } catch (err) { toast(err.message || t("That didn't work"), { type: "error" }); }
}
const enc = encodeURIComponent;
async function remove(b) {
  if (!(await askConfirm(t("Delete the backup {name}? This can't be undone.", { name: b.name }), { ok: t("Delete"), danger: true }))) return;
  act(() => api(`/api/backups/${enc(b.name)}`, { method: "DELETE" }), t("Backup deleted"));
}
// Calls a backup route; when the backup's password is needed (423), asks for it and tries again.
async function withPassword(b, path) {
  let password = null;
  for (;;) {
    try { return await api(path, { method: "POST", body: password ? { password } : {} }); }
    catch (err) {
      if (err.status !== 423) throw err;
      password = await askPassword(data.value?.password_set ? t("{name} is protected with a password (not the one saved here).", { name: b.name }) : t("{name} is protected with a password.", { name: b.name }),
        { title: t("Backup password"), ok: t("Open"), error: password ? t("Wrong password — try again.") : "" });
      if (!password) return null;
    }
  }
}
async function restore(b) {
  if (!(await askConfirm(t("Restore this backup?") + "\n\n"
    + t("It's applied the next time the app is started (start.bat / run.py). The current database and settings are kept next to the originals as *.pre-restore files.") + "\n\n"
    + t("At that start the console window lists the folders from the backup's settings (videos, cache…) — useful when the backup comes from another computer: point them at this computer's folders there."), { ok: t("Restore") }))) return;
  act(async () => { if (!(await withPassword(b, `/api/backups/${enc(b.name)}/restore`))) throw new Error(t("Restore cancelled")); },
    t("Restore staged — restart the app to apply it"));
}
// The backup password (2.25.0)
const pw = ref({ editing: false, value: "", again: "", saving: false });
const pwProblem = () => (pw.value.value.length < 8 ? t("Use at least 8 characters") : pw.value.value !== pw.value.again ? t("The passwords don't match") : "");
async function savePassword(value) {
  pw.value.saving = true;
  try {
    await api("/api/backups-password", { method: "PUT", body: { password: value } });
    toast(value ? t("Backup password saved — new backups are protected with it") : t("Backup password removed — new backups aren't protected"),
      { type: "success", timeout: 6000 });
    pw.value = { editing: false, value: "", again: "", saving: false };
    load();
  } catch (err) { toast(err.message || t("Could not save it"), { type: "error" }); pw.value.saving = false; }
}
async function removePassword() {
  if (!(await askConfirm(t("Remove the backup password? New backups won't be protected. Backups already made keep their password."),
    { ok: t("Remove"), danger: true }))) return;
  savePassword("");
}
// Restore test: opens the backup like a restore would, changes nothing.
const testing = ref("");
const report = ref(null);
async function test(b) {
  testing.value = b.name;
  try {
    const r = await withPassword(b, `/api/backups/${enc(b.name)}/test`);
    if (!r) return;
    report.value = r;
    toast(r.ok ? t("Restore test passed") : t("Restore test failed"), { type: r.ok ? "success" : "error" });
    load();
  } catch (err) { toast(err.message || t("Could not test it"), { type: "error" }); }
  finally { testing.value = ""; }
}
const cancelRestore = () => act(() => api("/api/backups-restore", { method: "DELETE" }), t("Restore cancelled"));
const TRIGGERS = { scheduled: t("scheduled"), manual: t("by hand"), auto: t("automatic"), restore: t("before a restore"),
  installer: t("before installing an update") };
// 3.13.0: the backup Setup / the install scripts make before an update, kept for N days
const inst = ref({ saving: false, error: "", days: "" });
async function saveInstaller(body) {
  inst.value.saving = true; inst.value.error = "";
  try {
    const r = await api("/api/backups/settings", { method: "POST", body });
    data.value = { ...data.value, installer_backup: r.installer_backup, installer_keep_days: r.installer_keep_days };
    inst.value.days = String(r.installer_keep_days);
  } catch (err) { inst.value.error = err.message || t("That didn't work"); }
  finally { inst.value.saving = false; }
}
function saveInstallerDays() {
  const d = Math.round(Number(inst.value.days));
  if (!(d >= 1 && d <= 3650)) { inst.value.error = t("1 to 3650 days"); return; }
  saveInstaller({ installer_keep_days: d });
}
const PART_SHORT = { secrets: N_("secrets"), performer_photos: N_("performer photos"), thumbnails: N_("thumbnails"),
  frames: N_("frames"), storyboards: N_("seekbar previews"), videos: N_("videos"), derived: N_("rebuildable data") };
const contents = (b) => [t("database + settings"),
  ...(b.include ? b.include.map((k) => t(PART_SHORT[k] || k)) : [b.include_cache && t("cache"), b.include_secrets && t("secrets")])]
  .filter(Boolean).join(" + ");
const fileName = (p) => p.split(/[\\/]/).pop();
const pendingName = (p) => p.name || fileName(p.zip);
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>{{ $t("Backups") }}</h1>
        <p><Tx msg="A backup always holds the library database and your settings (config.yaml and the changes made in the app). Regular backups are Backup jobs on the {link1} page (a daily and a weekly one are built in)."><template #link1><RouterLink to="/admin/scheduler">{{ $t("Scheduler") }}</RouterLink></template></Tx></p>
      </div>
    </header>

    <section class="card panel">
      <h2>{{ $t("New backup") }}</h2>
      <p v-if="plan" class="help-text">{{ $t("Always included: the database ({size}) and your settings, installed languages, own performer photos and playlist covers ({n} files).",
        { size: formatBytes(plan.always.database_bytes), n: formatNumber(plan.always.files - 1) }) }}</p>
      <div v-if="plan" class="toggles">
        <label v-for="p in plan.parts" :key="p.key" class="switch part" :class="{ big: p.key === 'videos' }">
          <input type="checkbox" :checked="chosen.has(p.key)" :disabled="!p.files || (uploadToo && NOT_UPLOADED.includes(p.key))"
            :title="uploadToo && NOT_UPLOADED.includes(p.key) ? $t('Never uploaded to Filester') : ''" @change="toggle(p.key, $event.target.checked)">
          <span><strong>{{ p.label }}</strong>
            <span class="muted"> · {{ p.files ? $tn("{n} file", "{n} files", p.files) + " · " + formatBytes(p.bytes) : $t("nothing yet") }} · {{ $t(PART_HELP[p.key]) }}</span></span>
        </label>
      </div>
      <div v-else class="skeleton" style="height: 6rem" />
      <div class="dest">
        <span class="muted">{{ $t("Save to") }}</span>
        <code class="mono">{{ space?.dir || plan?.dir || data?.dir }}</code>
        <button class="btn btn-sm" @click="picking = true"><Icon name="disk" />{{ $t("Choose a folder…") }}</button>
        <button v-if="dest" class="btn btn-ghost btn-sm" @click="setDest('')">{{ $t("Back to the backups folder") }}</button>
      </div>
      <p v-if="destError" class="error-text small">{{ destError }}</p>
      <p v-if="plan && space" class="space" :class="enough ? 'muted' : 'error-text'">
        {{ $t("About {size} · {free} free in {folder}", { size: formatBytes(estimate), free: space.free_bytes == null ? "?" : formatBytes(space.free_bytes), folder: space.dir }) }}
        <template v-if="!enough"> — {{ $t("not enough: it needs about {needed}. Choose a folder on a bigger drive, or leave something out.", { needed: formatBytes(needed) }) }}</template>
      </p>
      <p v-if="dest" class="help-text">{{ $t("Backups saved to another folder are listed and can be tested and restored like the others; only the backups folder keeps just the newest ones.") }}</p>
      <FolderPicker v-if="picking" :start="dest || data?.dir" :title="$t('Where this backup goes')" @close="picking = false" @pick="setDest" />
      <div v-if="splitGb !== null" class="split">
        <label class="switch"><input type="checkbox" :checked="!!splitGb" @change="setSplitOn($event.target.checked)">{{ $t("Split big backups into parts") }}</label>
        <span v-if="splitGb" class="inline-input"><input v-model="splitText" class="input input-num" type="number" :min="SPLIT_MIN" :max="SPLIT_MAX" step="0.1"
          :aria-label="$t('Largest part, in GB')" @change="saveSplit(Number(splitText))"> GB</span>
        <p class="help-text">{{ $t("A backup bigger than this is written as numbered parts (name.zip.001, .002, …) of at most this size: 1.9 to 50 GB. 7-Zip opens them from the .001 part; this app restores them together. Scheduled backups too.") }}
          <template v-if="splitGb && partsCount > 1"> {{ $t("This one: about {n} parts.", { n: formatNumber(partsCount) }) }}</template></p>
        <p v-if="splitError" class="error-text small">{{ splitError }}</p>
      </div>
      <p v-if="data" class="help-text">{{ data.password_set ? $t("🔒 Protected with the backup password (below).") : $t("Not password-protected — set a backup password below.") }}</p>
      <div v-if="offsite" class="upload-too">
        <label class="switch"><input type="checkbox" :checked="uploadToo" :disabled="!canUpload" @change="setUpload($event.target.checked)">{{ $t("Also upload to Filester (a private folder)") }}</label>
        <p class="help-text">{{ !offsite.filester.configured ? $t("Needs the Filester API key: Server Settings → Credentials → Filester.")
          : !data?.password_set ? $t("Needs a backup password (below): only encrypted backups are uploaded.")
          : $t("Encrypted, never with the credentials or the videos, in parts of up to {gb} GB.", { gb: offsite.filester.max_part_gb }) }}</p>
      </div>
      <div class="form-actions"><button class="btn btn-primary btn-sm" :disabled="creating || !plan || !enough" @click="create"><Icon name="download" />{{ creating ? $t("Starting…") : $t("Create backup now") }}</button></div>
    </section>

    <section v-if="data" class="card panel">
      <h2>{{ $t("Backup before installing an update") }}</h2>
      <p class="help-text">{{ $t("Setup.exe, install.sh and install.bat make a backup of the database, the settings, your own performer photos and the playlist covers before they install an update — so if an update goes wrong, the library can be put back as it was (Restore), and the rest (thumbnails, previews, frames) made again by Repair/Update. It's encrypted when a backup password is set, and listed here as “before installing an update”.") }}</p>
      <label class="switch"><input type="checkbox" :checked="data.installer_backup" :disabled="inst.saving" @change="saveInstaller({ installer_backup: $event.target.checked })">{{ $t("Make a backup before installing an update") }}</label>
      <div class="form-actions">
        <span>{{ $t("Delete them after") }}</span>
        <input v-model="inst.days" class="input input-num" type="number" min="1" max="3650" :placeholder="String(data.installer_keep_days)"
               :aria-label="$t('Days to keep backups made before installing')" @change="saveInstallerDays">
        <span>{{ $t("days") }}</span>
      </div>
      <small v-if="inst.error" class="error-text">{{ inst.error }}</small>
      <small class="muted">{{ $t("Only these backups are deleted by this, and only from the backups folder; the others follow their own rules.") }}</small>
    </section>

    <section v-if="data" class="card panel">
      <h2>{{ $t("Backup password") }}</h2>
      <p class="help-text"><Tx msg="While a password is set, every new backup — made here or by the Scheduler — is an encrypted zip (AES-256). Without the password nobody can open it or restore it: not this app, not 7-Zip. Store it somewhere safe (e.g. a password manager); {b1}. Backups made before keep the password they had."><template #b1><strong>{{ $t("it can't be recovered") }}</strong></template></Tx></p>
      <template v-if="pw.editing">
        <div class="pw-fields">
          <input v-model="pw.value" class="input" type="password" autocomplete="new-password" :placeholder="$t('New password')" :aria-label="$t('New backup password')">
          <input v-model="pw.again" class="input" type="password" autocomplete="new-password" :placeholder="$t('Again')" :aria-label="$t('Backup password again')">
        </div>
        <small v-if="pw.value && pwProblem()" class="muted">{{ pwProblem() }}</small>
        <div class="form-actions">
          <button class="btn btn-primary btn-sm" :disabled="!!pwProblem() || pw.saving" @click="savePassword(pw.value)">{{ $t("Save password") }}</button>
          <button class="btn btn-ghost btn-sm" @click="pw = { editing: false, value: '', again: '', saving: false }">{{ $t("Cancel") }}</button>
        </div>
      </template>
      <div v-else class="form-actions">
        <span class="pill" :class="data.password_set ? 'pill-ok' : ''">{{ data.password_set ? $t("🔒 Set") : $t("Not set") }}</span>
        <button class="btn btn-sm" @click="pw.editing = true">{{ data.password_set ? $t("Change") : $t("Set a password") }}</button>
        <button v-if="data.password_set" class="btn btn-ghost btn-sm btn-danger" @click="removePassword">{{ $t("Remove") }}</button>
      </div>
    </section>

    <section v-if="offsite" class="card panel">
      <h2>{{ $t("Copies elsewhere") }}</h2>
      <p class="help-text">{{ $t("A scheduled backup job can copy each backup to other folders (an external drive, a network share) and upload it to Filester — so one failing or wiped disk doesn't take the backups with it. Set it in each backup job: Admin → Scheduler → Edit.") }}</p>
      <p v-if="!copyJobs.length" class="muted small">{{ $t("No backup job copies or uploads its backups yet.") }}</p>
      <ul v-else class="offsite-jobs">
        <li v-for="j in copyJobs" :key="j.id">
          <strong>{{ j.name }}</strong><span v-if="!j.enabled" class="pill">{{ $t("Disabled") }}</span>
          <span class="muted"> · {{ $t("keeps {n}", { n: j.keep }) }}</span>
          <div v-for="c in j.copy_to" :key="c" class="mono small">→ {{ c }}</div>
          <div v-if="j.upload" class="small">→ {{ $t("Filester (private folder)") }}</div>
        </li>
      </ul>
      <template v-if="uploadJobs.length || offsite.filester.uploads.length || offsite.filester.configured">
        <h3>{{ $t("Filester") }}</h3>
        <p v-if="!offsite.filester.configured" class="error-text small">{{ $t("No API key: add it under Server Settings → Credentials → Filester.") }}</p>
        <p v-if="uploadJobs.length && !data?.password_set" class="error-text small">{{ $t("Set a backup password above: without it nothing is uploaded.") }}</p>
        <p v-if="offsite.filester.account" class="ok-text small">
          {{ offsite.filester.account.username ? $t("Connected as {name}.", { name: offsite.filester.account.username }) : $t("Connected.") }}</p>
        <p v-if="offsiteError" class="error-text small">{{ offsiteError }}</p>
        <div class="form-actions">
          <button class="btn btn-sm" :disabled="!offsite.filester.configured || checking" @click="loadOffsite(true)">{{ checking ? $t("Checking…") : $t("Check Filester") }}</button>
        </div>
        <div v-if="offsite.filester.uploads.length" class="table-scroll"><table class="data-table">
          <thead><tr><th>{{ $t("Uploaded backup") }}</th><th>{{ $t("Job") }}</th><th>{{ $t("Size") }}</th><th>{{ $t("Uploaded") }}</th></tr></thead>
          <tbody>
            <tr v-for="u in offsite.filester.uploads" :key="u.backup + u.uploaded_at">
              <td class="mono">{{ u.backup }}<span v-if="u.parts > 1" class="muted"> · {{ $tn("{n} part", "{n} parts", u.parts) }}</span></td>
              <td>{{ u.job_name || "—" }}</td>
              <td class="nowrap">{{ formatBytes(u.size || 0) }}</td>
              <td class="nowrap">{{ formatDateTime(u.uploaded_at) }}</td>
            </tr>
          </tbody>
        </table></div>
        <p class="muted small">{{ $t("To restore one, download its file(s) from your Filester account and choose them in Setup (or put them in the backups folder). Filester deletes files nobody downloads for 45 days.") }}</p>
      </template>
    </section>

    <p v-if="error" class="error-text">{{ error }}</p>
    <div v-else-if="!data" class="skeleton" style="height: 8rem; border-radius: var(--radius-lg)" />
    <template v-else>
      <div v-if="data.pending_restore" class="card panel staged">
        <p class="help-text"><Tx msg="A restore of {b1} is staged. It's applied the next time the app is started with {code1} / {code2}."><template #b1><strong>{{ pendingName(data.pending_restore) }}</strong></template><template #code1><code>start.bat</code></template><template #code2><code>run.py</code></template></Tx></p>
        <div><button class="btn btn-sm" @click="cancelRestore">{{ $t("Cancel the restore") }}</button></div>
      </div>
      <section v-if="report" class="card report">
        <div class="report-head"><strong>{{ report.ok ? $t("Restore test of {name}: passed", { name: report.name }) : $t("Restore test of {name}: FAILED", { name: report.name }) }}</strong>
          <button class="btn btn-ghost btn-sm" @click="report = null">{{ $t("Close") }}</button></div>
        <ul>
          <li v-for="c in report.checks" :key="c.name" :class="c.ok ? 'ok-text' : 'error-text'">{{ c.ok ? "✓" : "✗" }} {{ c.name }}<span v-if="c.detail" class="muted"> — {{ c.detail }}</span></li>
        </ul>
      </section>
      <section class="card table-wrap">
        <table class="data-table">
          <thead><tr><th>{{ $t("Created") }}</th><th>{{ $t("Contents") }}</th><th>{{ $t("Size") }}</th><th /></tr></thead>
          <tbody>
            <tr v-for="b in data.backups" :key="b.name">
              <td>
                <div class="nowrap">{{ formatDateTime(b.created_at) }}</div>
                <div class="muted small">{{ b.job === "installer" ? TRIGGERS.installer : TRIGGERS[b.trigger] || b.trigger || $t("unknown") }}<template v-if="b.app_version"> · v{{ b.app_version }}</template></div>
                <div v-if="b.job === 'installer' && b.default" class="muted small">{{ $t("deleted {days} days after it was made", { days: data.installer_keep_days }) }}</div>
              </td>
              <td>
                <div>{{ contents(b) }}<span v-if="b.encrypted" class="lock" :title="$t('Protected with a password (AES-256)')"> 🔒</span></div>
                <div class="muted small mono">{{ b.name }}</div>
                <div v-if="!b.default" class="muted small">{{ $t("in {folder}", { folder: b.dir }) }}</div>
                <span v-if="!b.valid" class="pill pill-bad">{{ $t("Unreadable") }}</span>
                <div v-if="b.test" class="small" :class="b.test.ok ? 'ok-text' : 'error-text'">
                  {{ b.test.ok ? $t("✓ Restore test passed") : $t("✗ Restore test failed: {checks}", { checks: b.test.failed.join(", ") }) }}
                  <span class="muted">· {{ formatDateTime(b.test.tested_at) }}</span></div>
                <div v-else class="muted small">{{ $t("Not tested yet") }}</div>
              </td>
              <td class="nowrap">{{ formatBytes(b.size_bytes) }}<div v-if="b.parts" class="muted small">{{ $tn("{n} part", "{n} parts", b.parts) }}</div></td>
              <td class="actions">
                <details v-if="b.parts" class="parts">
                  <summary class="btn btn-ghost btn-sm"><Icon name="download" />{{ $t("Download") }}</summary>
                  <div class="part-links">
                    <a v-for="(size, i) in b.part_sizes" :key="i" class="btn btn-ghost btn-sm" :href="`/api/backups/${enc(b.name)}/download?part=${i + 1}`">
                      {{ $t("Part {n} ({size})", { n: i + 1, size: formatBytes(size) }) }}</a>
                  </div>
                </details>
                <a v-else class="btn btn-ghost btn-sm" :href="`/api/backups/${enc(b.name)}/download`"><Icon name="download" />{{ $t("Download") }}</a>
                <button class="btn btn-ghost btn-sm" :disabled="!!testing" :title="$t('Open it like a restore would, without changing anything')" @click="test(b)">
                  <Icon name="check" />{{ testing === b.name ? $t("Testing…") : $t("Test") }}</button>
                <button v-if="b.valid" class="btn btn-ghost btn-sm" @click="restore(b)">{{ $t("Restore") }}</button>
                <button class="btn btn-ghost btn-sm btn-danger" @click="remove(b)"><Icon name="trash" />{{ $t("Delete") }}</button>
              </td>
            </tr>
            <tr v-if="!data.backups.length"><td colspan="4" class="muted">{{ $t("No backups yet.") }}</td></tr>
          </tbody>
        </table>
      </section>
      <Tx tag="p" class="help-text" msg="Stored in {folder} — the newest {n} are kept." :params="{ n: data.keep }"><template #folder><code class="mono">{{ data.dir }}</code></template></Tx>
    </template>
  </div>
</template>

<style scoped>
.upload-too { margin-top: 0.5rem; }
.offsite-jobs { margin: 0; padding-inline-start: 1.1rem; display: flex; flex-direction: column; gap: 0.4rem; font-size: 0.86rem; }
.offsite-jobs .pill { margin-inline-start: 0.4rem; }
.table-scroll { overflow-x: auto; }
h3 { margin: 0.5rem 0 0; font-size: 0.95rem; }
.toggles { display: flex; flex-direction: column; gap: 0.625rem; }
.part { align-items: flex-start; }
.part.big strong { color: var(--warning); }
.space { margin: 0.75rem 0 0; font-size: 0.85rem; }
.split { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 0.75rem; margin-top: 1rem; }
.split .help-text { flex-basis: 100%; margin: 0; }
.dest { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; margin-top: 1rem; }
.dest code { overflow-wrap: anywhere; }
.parts { display: inline-block; position: relative; }
.parts summary { list-style: none; }
.parts summary::-webkit-details-marker { display: none; }
.part-links { position: absolute; inset-inline-end: 0; z-index: 5; display: flex; flex-direction: column; align-items: stretch;
  background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); padding: 0.25rem; max-height: 16rem; overflow-y: auto; }
.staged { border-color: var(--warning); }
.actions { text-align: end; white-space: nowrap; }
.nowrap { white-space: nowrap; }
.small { font-size: 0.76rem; }
.report { padding: 0.875rem 1rem; margin-bottom: 1rem; }
.report-head { display: flex; justify-content: space-between; align-items: center; gap: 1rem; }
.report ul { list-style: none; margin: 0.5rem 0 0; padding: 0; font-size: 0.84rem; display: flex; flex-direction: column; gap: 0.2rem; }
.ok-text { color: var(--success); }
.pw-fields { display: flex; flex-wrap: wrap; gap: 0.5rem; }
.pw-fields .input { flex: 1 1 12rem; max-width: 18rem; }
</style>
