<script setup>
// Admin -> Backups: zip files of the library database and settings (plus,
// optionally, the thumbnail/preview cache and the secrets). A restore is
// staged here and applied the next time the app starts. 2.25.0: a backup password
// (new backups are AES-256 encrypted zips); protected backups ask for it when the
// saved one doesn't open them.
import { askConfirm, askPassword } from "../../stores/confirm.js";
import { ref, onMounted } from "vue";
import Icon from "../../components/Icon.vue";
import { api } from "../../lib/api.js";
import { formatBytes, formatDateTime } from "../../lib/format.js";
import { toast } from "../../stores/toast.js";

const data = ref(null);
const error = ref("");
const opts = ref({ include_cache: false, include_secrets: false });
const creating = ref(false);

async function load() {
  try { data.value = await api("/api/backups"); error.value = ""; }
  catch (err) { error.value = err.message || "Could not load the backups"; }
}
onMounted(load);

async function create() {
  creating.value = true;
  try {
    const b = await api("/api/backups", { method: "POST", body: { ...opts.value } });
    toast(`Backup created (${formatBytes(b.size_bytes)})${b.test ? (b.test.ok ? " — restore test passed" : " — but it FAILED its restore test") : ""}`,
      { type: b.test && !b.test.ok ? "error" : "success", timeout: 7000 });
    load();
  } catch (err) { toast(err.message || "The backup failed", { type: "error", timeout: 8000 }); }
  finally { creating.value = false; }
}
async function act(fn, ok) {
  try { await fn(); toast(ok); load(); } catch (err) { toast(err.message || "That didn't work", { type: "error" }); }
}
const enc = encodeURIComponent;
async function remove(b) {
  if (!(await askConfirm(`Delete the backup ${b.name}? This can't be undone.`, { ok: "Delete", danger: true }))) return;
  act(() => api(`/api/backups/${enc(b.name)}`, { method: "DELETE" }), "Backup deleted");
}
// Calls a backup route; when the backup's password is needed (423), asks for it and tries again.
async function withPassword(b, path) {
  let password = null;
  for (;;) {
    try { return await api(path, { method: "POST", body: password ? { password } : {} }); }
    catch (err) {
      if (err.status !== 423) throw err;
      password = await askPassword(`${b.name} is protected with a password${data.value?.password_set ? " (not the one saved here)" : ""}.`,
        { title: "Backup password", ok: "Open", error: password ? "Wrong password — try again." : "" });
      if (!password) return null;
    }
  }
}
async function restore(b) {
  if (!(await askConfirm("Restore this backup?\n\nIt's applied the next time the app is started (start.bat / run.py). "
    + "The current database and settings are kept next to the originals as *.pre-restore files.\n\n"
    + "At that start the console window lists the folders from the backup's settings (videos, cache…) — "
    + "useful when the backup comes from another computer: point them at this computer's folders there.", { ok: "Restore" }))) return;
  act(async () => { if (!(await withPassword(b, `/api/backups/${enc(b.name)}/restore`))) throw new Error("Restore cancelled"); },
    "Restore staged — restart the app to apply it");
}
// The backup password (2.25.0)
const pw = ref({ editing: false, value: "", again: "", saving: false });
const pwProblem = () => (pw.value.value.length < 8 ? "Use at least 8 characters" : pw.value.value !== pw.value.again ? "The passwords don't match" : "");
async function savePassword(value) {
  pw.value.saving = true;
  try {
    await api("/api/backups-password", { method: "PUT", body: { password: value } });
    toast(value ? "Backup password saved — new backups are protected with it" : "Backup password removed — new backups aren't protected",
      { type: "success", timeout: 6000 });
    pw.value = { editing: false, value: "", again: "", saving: false };
    load();
  } catch (err) { toast(err.message || "Could not save it", { type: "error" }); pw.value.saving = false; }
}
async function removePassword() {
  if (!(await askConfirm("Remove the backup password? New backups won't be protected. Backups already made keep their password.",
    { ok: "Remove", danger: true }))) return;
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
    toast(r.ok ? "Restore test passed" : "Restore test failed", { type: r.ok ? "success" : "error" });
    load();
  } catch (err) { toast(err.message || "Could not test it", { type: "error" }); }
  finally { testing.value = ""; }
}
const cancelRestore = () => act(() => api("/api/backups-restore", { method: "DELETE" }), "Restore cancelled");
const contents = (b) => ["database + settings", b.include_cache && "cache", b.include_secrets && "secrets"].filter(Boolean).join(" + ");
const fileName = (p) => p.split(/[\\/]/).pop();
const pendingName = (p) => p.name || fileName(p.zip);
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>Backups</h1>
        <p>A backup always holds the library database and your settings (config.yaml and the changes made in the app).
          For regular backups, add a “Backup database + settings” job on the <RouterLink to="/admin/scheduler">Scheduler</RouterLink> page.</p>
      </div>
    </header>

    <section class="card panel">
      <h2>New backup</h2>
      <div class="toggles">
        <label class="switch"><input v-model="opts.include_cache" type="checkbox">Include the thumbnail / preview cache (can be large)</label>
        <label class="switch"><input v-model="opts.include_secrets" type="checkbox">Include secrets (credentials and site logins) — keep such backups private</label>
      </div>
      <p v-if="data" class="help-text">{{ data.password_set ? "🔒 Protected with the backup password (below)." : "Not password-protected — set a backup password below." }}</p>
      <div class="form-actions"><button class="btn btn-primary btn-sm" :disabled="creating" @click="create"><Icon name="download" />{{ creating ? "Creating…" : "Create backup now" }}</button></div>
    </section>

    <section v-if="data" class="card panel">
      <h2>Backup password</h2>
      <p class="help-text">While a password is set, every new backup — made here or by the Scheduler — is an encrypted zip
        (AES-256). Without the password nobody can open it or restore it: not this app, not 7-Zip. Store it somewhere safe
        (e.g. a password manager); <strong>it can't be recovered</strong>. Backups made before keep the password they had.</p>
      <template v-if="pw.editing">
        <div class="pw-fields">
          <input v-model="pw.value" class="input" type="password" autocomplete="new-password" placeholder="New password" aria-label="New backup password">
          <input v-model="pw.again" class="input" type="password" autocomplete="new-password" placeholder="Again" aria-label="Backup password again">
        </div>
        <small v-if="pw.value && pwProblem()" class="muted">{{ pwProblem() }}</small>
        <div class="form-actions">
          <button class="btn btn-primary btn-sm" :disabled="!!pwProblem() || pw.saving" @click="savePassword(pw.value)">Save password</button>
          <button class="btn btn-ghost btn-sm" @click="pw = { editing: false, value: '', again: '', saving: false }">Cancel</button>
        </div>
      </template>
      <div v-else class="form-actions">
        <span class="pill" :class="data.password_set ? 'pill-ok' : ''">{{ data.password_set ? "🔒 Set" : "Not set" }}</span>
        <button class="btn btn-sm" @click="pw.editing = true">{{ data.password_set ? "Change" : "Set a password" }}</button>
        <button v-if="data.password_set" class="btn btn-ghost btn-sm btn-danger" @click="removePassword">Remove</button>
      </div>
    </section>

    <p v-if="error" class="error-text">{{ error }}</p>
    <div v-else-if="!data" class="skeleton" style="height: 8rem; border-radius: var(--radius-lg)" />
    <template v-else>
      <div v-if="data.pending_restore" class="card panel staged">
        <p class="help-text">A restore of <strong>{{ pendingName(data.pending_restore) }}</strong> is staged. It's applied the next time
          the app is started with <code>start.bat</code> / <code>run.py</code>.</p>
        <div><button class="btn btn-sm" @click="cancelRestore">Cancel the restore</button></div>
      </div>
      <section v-if="report" class="card report">
        <div class="report-head"><strong>Restore test of {{ report.name }}: {{ report.ok ? "passed" : "FAILED" }}</strong>
          <button class="btn btn-ghost btn-sm" @click="report = null">Close</button></div>
        <ul>
          <li v-for="c in report.checks" :key="c.name" :class="c.ok ? 'ok-text' : 'error-text'">{{ c.ok ? "✓" : "✗" }} {{ c.name }}<span v-if="c.detail" class="muted"> — {{ c.detail }}</span></li>
        </ul>
      </section>
      <section class="card table-wrap">
        <table class="data-table">
          <thead><tr><th>Created</th><th>Contents</th><th>Size</th><th /></tr></thead>
          <tbody>
            <tr v-for="b in data.backups" :key="b.name">
              <td>
                <div class="nowrap">{{ formatDateTime(b.created_at) }}</div>
                <div class="muted small">{{ b.trigger || "unknown" }}<template v-if="b.app_version"> · v{{ b.app_version }}</template></div>
              </td>
              <td>
                <div>{{ contents(b) }}<span v-if="b.encrypted" class="lock" title="Protected with a password (AES-256)"> 🔒</span></div>
                <div class="muted small mono">{{ b.name }}</div>
                <span v-if="!b.valid" class="pill pill-bad">Unreadable</span>
                <div v-if="b.test" class="small" :class="b.test.ok ? 'ok-text' : 'error-text'">
                  {{ b.test.ok ? "✓ Restore test passed" : `✗ Restore test failed: ${b.test.failed.join(", ")}` }}
                  <span class="muted">· {{ formatDateTime(b.test.tested_at) }}</span></div>
                <div v-else class="muted small">Not tested yet</div>
              </td>
              <td class="nowrap">{{ formatBytes(b.size_bytes) }}</td>
              <td class="actions">
                <a class="btn btn-ghost btn-sm" :href="`/api/backups/${enc(b.name)}/download`"><Icon name="download" />Download</a>
                <button class="btn btn-ghost btn-sm" :disabled="!!testing" title="Open it like a restore would, without changing anything" @click="test(b)">
                  <Icon name="check" />{{ testing === b.name ? "Testing…" : "Test" }}</button>
                <button v-if="b.valid" class="btn btn-ghost btn-sm" @click="restore(b)">Restore</button>
                <button class="btn btn-ghost btn-sm btn-danger" @click="remove(b)"><Icon name="trash" />Delete</button>
              </td>
            </tr>
            <tr v-if="!data.backups.length"><td colspan="4" class="muted">No backups yet.</td></tr>
          </tbody>
        </table>
      </section>
      <p class="help-text">Stored in <code class="mono">{{ data.dir }}</code> — the newest {{ data.keep }} are kept.</p>
    </template>
  </div>
</template>

<style scoped>
.toggles { display: flex; flex-direction: column; gap: 0.625rem; }
.staged { border-color: var(--warning); }
.actions { text-align: right; white-space: nowrap; }
.nowrap { white-space: nowrap; }
.small { font-size: 0.76rem; }
.report { padding: 0.875rem 1rem; margin-bottom: 1rem; }
.report-head { display: flex; justify-content: space-between; align-items: center; gap: 1rem; }
.report ul { list-style: none; margin: 0.5rem 0 0; padding: 0; font-size: 0.84rem; display: flex; flex-direction: column; gap: 0.2rem; }
.ok-text { color: var(--success); }
.pw-fields { display: flex; flex-wrap: wrap; gap: 0.5rem; }
.pw-fields .input { flex: 1 1 12rem; max-width: 18rem; }
</style>
