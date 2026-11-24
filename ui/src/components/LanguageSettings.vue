<script setup>
// My Settings -> Language (3.7.0). Everyone picks the interface language from the installed ones;
// people who manage the server also get languages from GitHub (checked against its list of
// SHA-256 hashes by the server) or from a file, remove them, and choose the default.
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { api, ApiError, languageHeaders } from "../lib/api.js";
import { i18n, t, loadBoot, setLanguage, pickLanguage, formatNumber } from "../lib/i18n.js";
import { session, savePref, hasPermission } from "../stores/session.js";
import { askConfirm } from "../stores/confirm.js";
import { toast } from "../stores/toast.js";
import { onLibraryChange } from "../stores/live.js";
import { formatDateTime } from "../lib/format.js";

const boot = ref(null);
const manager = computed(() => hasPermission("settings.manage"));
const available = ref(null);
const busy = ref("");
const error = ref("");
const report = ref(null);
const fileInput = ref(null);

async function refresh() { boot.value = await loadBoot(true); }
let stop = null;
onMounted(() => { refresh(); stop = onLibraryChange((c) => { if (!c || c.includes("languages")) refresh(); }); });
onBeforeUnmount(() => stop?.());

const installed = computed(() => boot.value?.languages || []);
const defaultCode = computed(() => boot.value?.default || "en");
const nameOf = (code) => (code === "en" ? "English" : installed.value.find((l) => l.code === code)?.native || code);  // i18n-ignore
const pct = (l) => (l.total ? Math.floor((l.translated / l.total) * 100) : 0);

// Picking a language reloads the page, so every part of it (menus made earlier too) shows it.
async function choose(code) {
  if (code === session.prefs.language) return;
  savePref("language", code);
  busy.value = "choose";
  const shown = await pickLanguage(code);
  await setLanguage(shown);
  setTimeout(() => window.location.reload(), 450);  // after the setting is saved
}

async function setDefault(code) {
  try {
    await api("/api/settings/language", { method: "POST", body: { default: code } });
    await refresh();
    toast(t("Default language saved"), { type: "success" });
  } catch (err) { toast(err.message || t("That didn't work"), { type: "error" }); }
}

async function showAvailable() {
  busy.value = "list"; error.value = "";
  try { available.value = (await api("/api/i18n/available")).languages; }
  catch (err) { error.value = err.message || t("Couldn't reach GitHub"); available.value = null; }
  finally { busy.value = ""; }
}

function done(r) {
  report.value = r;
  const l = r.language;
  toast(t("Installed {name}: {done} of {total} texts translated", { name: l.native || l.name, done: formatNumber(r.report.translated), total: formatNumber(r.report.total) }), { type: "success" });
  refresh();
  if (available.value) showAvailable();
  if (l.code === i18n.code) setTimeout(() => window.location.reload(), 600);  // the one showing was updated
}

async function install(code) {
  busy.value = `install:${code}`; error.value = "";
  try { done(await api("/api/i18n/install", { method: "POST", body: { code } })); }
  catch (err) { error.value = err.message || t("That didn't work"); }
  finally { busy.value = ""; }
}

async function fromFile(e) {
  const file = e.target.files?.[0];
  e.target.value = "";
  if (!file) return;
  if (file.size > 1_000_000) { error.value = t("The language file is too big (over {mb} MB)", { mb: 1 }); return; }
  busy.value = "file"; error.value = "";
  try {
    const resp = await fetch("/api/i18n/upload", {
      method: "PUT", body: file, credentials: "same-origin",
      headers: languageHeaders({ "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" }),
    });
    const data = await resp.json().catch(() => null);
    if (!resp.ok) throw new ApiError(data?.detail || t("Request failed ({status})", { status: resp.status }), resp.status);
    done(data);
  } catch (err) { error.value = err.message || t("That didn't work"); }
  finally { busy.value = ""; }
}

async function remove(l) {
  if (!(await askConfirm(t("Remove {name}? People using it see English (or the default) until it's installed again.", { name: l.native || l.name }),
    { ok: t("Remove"), danger: true }))) return;
  try {
    await api(`/api/i18n/${encodeURIComponent(l.code)}`, { method: "DELETE" });
    await refresh();
    if (l.code === i18n.code) window.location.reload();
  } catch (err) { toast(err.message || t("That didn't work"), { type: "error" }); }
}

const STATUS = { new: t("Not installed"), installed: t("Installed"), update: t("Update available") };
</script>

<template>
  <section class="card group">
    <h2>{{ $t("Language") }}</h2>
    <div class="choices" role="radiogroup" :aria-label="$t('Language')">
      <label class="choice">
        <input type="radio" name="lang" :checked="!session.prefs.language" :disabled="busy === 'choose'" @change="choose('')">
        <span><strong>{{ $t("The server's default") }}</strong> <span class="muted">· {{ nameOf(defaultCode) }}</span></span>
      </label>
      <label class="choice">
        <input type="radio" name="lang" :checked="session.prefs.language === 'en'" :disabled="busy === 'choose'" @change="choose('en')">
        <span><strong lang="en" dir="ltr">English</strong></span>  <!-- i18n-ignore -->
      </label>
      <label v-for="l in installed" :key="l.code" class="choice">
        <input type="radio" name="lang" :checked="session.prefs.language === l.code" :disabled="busy === 'choose'" @change="choose(l.code)">
        <span><strong :lang="l.code" :dir="l.direction">{{ l.native }}</strong>
          <span class="muted"> · {{ l.name }} · {{ $t("{percent}% translated", { percent: pct(l) }) }}</span></span>
      </label>
    </div>
    <p class="muted small">{{ $t("Video titles, names, tags and descriptions stay as they are. Texts a language doesn't have yet show in English.") }}</p>

    <template v-if="manager">
      <h3 class="sub">{{ $t("Languages on this server") }}</h3>
      <p v-if="!installed.length" class="muted small">{{ $t("Only English so far.") }}</p>
      <ul v-else class="installed">
        <li v-for="l in installed" :key="l.code">
          <span class="who"><strong :lang="l.code" :dir="l.direction">{{ l.native }}</strong> <span class="muted">· {{ l.name }} · {{ l.code }}</span></span>
          <span class="muted small">{{ $t("{done} of {total} texts ({percent}%)", { done: formatNumber(l.translated), total: formatNumber(l.total), percent: pct(l) }) }}
            · {{ $t("version {version}", { version: l.version }) }} · {{ l.source === "github" ? $t("from GitHub") : $t("from a file") }}
            <template v-if="l.installed_at"> · {{ formatDateTime(l.installed_at) }}</template></span>
          <button class="btn btn-ghost btn-sm" @click="remove(l)">{{ $t("Remove") }}</button>
        </li>
      </ul>
      <div class="form-row">
        <label class="label" for="lang-default">{{ $t("Default language") }}<small>{{ $t("For the sign-in page and for accounts that keep “The server's default”.") }}</small></label>
        <select id="lang-default" class="select" :value="defaultCode" style="max-width: 20rem" @change="setDefault($event.target.value)">
          <option value="en">English</option>  <!-- i18n-ignore -->
          <option v-for="l in installed" :key="l.code" :value="l.code">{{ l.native }} ({{ l.name }})</option>
        </select>
      </div>
      <div class="acts">
        <button class="btn btn-sm btn-primary" :disabled="!!busy" @click="showAvailable">{{ busy === "list" ? $t("Asking GitHub…") : $t("Get languages from GitHub") }}</button>
        <button class="btn btn-sm" :disabled="!!busy" @click="fileInput.click()">{{ busy === "file" ? $t("Installing…") : $t("Install from a file…") }}</button>
        <input ref="fileInput" type="file" accept=".json,application/json" hidden @change="fromFile">
      </div>
      <p class="muted small">{{ $t("Language files come from the app's GitHub repository; the server checks each one's size and SHA-256 against GitHub's list and every text before installing it. A file you install yourself gets the same checks.") }}</p>
      <p v-if="error" class="error-text small">{{ error }}</p>
      <ul v-if="available" class="available">
        <li v-if="!available.length" class="muted small">{{ $t("No languages are published on GitHub yet") }}</li>
        <li v-for="a in available" :key="a.code">
          <span class="who"><strong :lang="a.code" :dir="a.direction">{{ a.native || a.name }}</strong> <span class="muted">· {{ a.name }} · {{ a.code }}</span></span>
          <span class="muted small"><template v-if="a.total">{{ $t("{percent}% translated", { percent: Math.floor((a.translated / a.total) * 100) }) }} · </template>{{ $t("version {version}", { version: a.version }) }} · {{ STATUS[a.status] }}</span>
          <button v-if="a.status !== 'installed'" class="btn btn-sm" :disabled="!!busy" @click="install(a.code)">
            {{ busy === `install:${a.code}` ? $t("Installing…") : a.status === "update" ? $t("Update") : $t("Install") }}</button>
        </li>
      </ul>
      <details v-if="report && report.report.rejected_count" class="report">
        <summary>{{ $tn("{n} text was left in English — why", "{n} texts were left in English — why", report.report.rejected_count) }}</summary>
        <ul><li v-for="r in report.report.rejected" :key="r.text"><code>{{ r.text }}</code> — {{ r.reason }}</li></ul>
      </details>
    </template>
  </section>
</template>

<style scoped>
.choices { display: flex; flex-direction: column; gap: 0.375rem; margin: 0.5rem 0 0.75rem; }
.choice { display: flex; align-items: center; gap: 0.625rem; padding: 0.5rem 0.75rem; border: 1px solid var(--border); border-radius: var(--radius); cursor: pointer; }
.choice:has(input:checked) { border-color: var(--accent); background: var(--accent-soft); }
.sub { font-size: 0.8rem; color: var(--text-dim); margin: 1.25rem 0 0.5rem; }
.installed, .available { list-style: none; margin: 0.5rem 0; padding: 0; display: flex; flex-direction: column; gap: 0.375rem; }
.installed li, .available li { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 0.75rem; padding: 0.5rem 0.75rem; border: 1px solid var(--border); border-radius: var(--radius); }
.who { flex: 1 1 14rem; min-width: 0; }
.acts { display: flex; gap: 0.5rem; flex-wrap: wrap; margin: 0.75rem 0 0.25rem; }
.report { margin-top: 0.75rem; font-size: 0.85rem; }
.report ul { margin: 0.5rem 0 0; padding-inline-start: 1.25rem; }
</style>
