<script setup>
// Server settings -> Credentials: every account and key the app uses,
// grouped as the server describes them (GET /api/credentials). Secrets are
// never sent back to the browser: an empty secret box keeps the saved value.
import { askConfirm } from "../../stores/confirm.js";
import { ref, onMounted } from "vue";
import { api } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";

const data = ref(null);
const error = ref("");
const values = ref({}); // name -> typed value
const revealed = ref({}); // name -> text shown under the field
const results = ref({}); // group key -> { ok, message }
const busy = ref("");

async function load() {
  try {
    data.value = await api("/api/credentials");
    const v = {};
    for (const g of data.value.groups) for (const f of g.fields) v[f.name] = f.kind === "secret" ? "" : (f.value || "");
    values.value = v;
    error.value = "";
  } catch (err) {
    error.value = err.message || "Could not load credentials";
  }
}
onMounted(load);

// A toggle that's on ("Use the PMVHaven account") hides the rest of its group.
function visibleFields(group) {
  const toggle = group.fields.find((f) => f.kind === "toggle");
  return toggle && values.value[toggle.name] === "1" ? [toggle] : group.fields;
}

function changes(group) {
  const out = {};
  for (const f of group.fields) {
    const v = (values.value[f.name] || "").trim();
    if (f.kind === "secret") { if (v) out[f.name] = v; } // empty = keep the saved secret
    else if (v !== (f.value || "")) out[f.name] = v;
  }
  return out;
}
async function save(group) {
  const vals = changes(group);
  if (!Object.keys(vals).length) { toast("Nothing changed"); return; }
  busy.value = group.key;
  try {
    await api("/api/credentials", { method: "PUT", body: { values: vals } });
    toast("Saved", { type: "success" });
    await load();
  } catch (err) { toast(err.message || "Could not save", { type: "error" }); }
  finally { busy.value = ""; }
}
async function clear(field) {
  if (!(await askConfirm(`Remove the saved ${field.label}?`, { ok: "Remove", danger: true }))) return;
  try {
    await api("/api/credentials", { method: "PUT", body: { values: { [field.name]: "" } } });
    toast("Removed");
    revealed.value = { ...revealed.value, [field.name]: "" };
    await load();
  } catch (err) { toast(err.message || "Could not remove", { type: "error" }); }
}
async function reveal(field) {
  if (revealed.value[field.name]) { revealed.value = { ...revealed.value, [field.name]: "" }; return; }
  try {
    const { value } = await api(`/api/credentials/${field.name}/reveal`);
    revealed.value = { ...revealed.value, [field.name]: value || "" };
  } catch (err) { toast(err.message || "Could not show it", { type: "error" }); }
}
async function generate(field) {
  if (!(await askConfirm("Generate a new key? Anything using the current key (e.g. the userscript) must be updated.", { ok: "Generate" }))) return;
  try {
    const { value } = await api(`/api/credentials/${field.name}/generate`, { method: "POST" });
    await load();
    revealed.value = { ...revealed.value, [field.name]: value };
    toast("New key generated — copy it now", { type: "success" });
  } catch (err) { toast(err.message || "Could not generate a key", { type: "error" }); }
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied"); } catch { toast("Copy it by hand (the browser blocked copying)"); }
}
// Groups with a "Test key" button: the field typed in, and the test endpoint.
const KEY_TESTS = {
  pmvhaven_api: { field: "pmvhaven_api_key", path: "pmvhaven-api" },
  virustotal: { field: "virustotal_api_key", path: "virustotal" },
  gemini: { field: "gemini_api_key", path: "gemini" },
};
async function test(group) {
  results.value = { ...results.value, [group.key]: { message: "Testing…" } };
  try {
    const t = KEY_TESTS[group.key];
    const typed = (values.value[t.field] || "").trim();
    const r = await api(`/api/credentials/test/${t.path}`, { method: "POST", body: { api_key: typed || null } });
    results.value = { ...results.value, [group.key]: r };
  } catch (err) {
    results.value = { ...results.value, [group.key]: { ok: false, message: err.message } };
  }
}
</script>

<template>
  <p v-if="error" class="error-text">{{ error }}</p>
  <div v-else-if="!data" class="skeleton" style="height: 16rem; border-radius: var(--radius-lg)" />
  <template v-else>
    <section v-for="g in data.groups" :key="g.key" class="card panel">
      <div class="panel-head-text">
        <h2>{{ g.label }}</h2>
        <p class="help-text">{{ g.help }}</p>
      </div>
      <div v-for="f in visibleFields(g)" :key="f.name" class="form-row">
        <span v-if="f.kind === 'toggle'" class="label">{{ f.label }}</span>
        <label v-if="f.kind === 'toggle'" class="switch">
          <input :id="`cred-${f.name}`" type="checkbox" :checked="values[f.name] === '1'" @change="values[f.name] = $event.target.checked ? '1' : '0'">
          {{ values[f.name] === "1" ? "Yes — same email and password as PMVHaven" : "No — this site has its own account" }}
        </label>
        <label v-if="f.kind !== 'toggle'" class="label" :for="`cred-${f.name}`">
          <span>{{ f.label }}
            <span v-if="f.kind === 'secret'" class="pill" :class="f.is_set ? 'pill-ok' : ''">{{ f.is_set ? "Saved" : "Not set" }}</span>
          </span>
        </label>
        <div v-if="f.kind !== 'toggle'" class="field-col">
          <div class="inline-input">
            <input :id="`cred-${f.name}`" v-model="values[f.name]" class="input" :type="f.kind === 'secret' ? 'password' : 'text'"
                   :inputmode="f.kind === 'number' ? 'numeric' : undefined" autocomplete="new-password"
                   :placeholder="f.kind === 'secret' ? (f.is_set ? 'Saved — type to replace' : 'Not set') : ''">
            <button v-if="f.revealable && f.is_set" class="btn btn-ghost btn-sm" @click="reveal(f)">{{ revealed[f.name] ? "Hide" : "Show" }}</button>
            <button v-if="f.generate" class="btn btn-ghost btn-sm" @click="generate(f)">Generate</button>
            <button v-if="f.is_set" class="btn btn-ghost btn-sm btn-danger" @click="clear(f)">Clear</button>
          </div>
          <div v-if="revealed[f.name]" class="revealed">
            <code class="mono">{{ revealed[f.name] }}</code>
            <button class="btn btn-ghost btn-sm" @click="copy(revealed[f.name])">Copy</button>
          </div>
        </div>
      </div>
      <div class="form-actions">
        <button class="btn btn-primary btn-sm" :disabled="busy === g.key" @click="save(g)">{{ busy === g.key ? "Saving…" : "Save" }}</button>
        <button v-if="KEY_TESTS[g.key]" class="btn btn-sm" @click="test(g)">Test key</button>
        <span v-if="results[g.key]" :class="results[g.key].ok ? 'ok-text' : results[g.key].ok === false ? 'error-text' : 'help-text'">{{ results[g.key].message }}</span>
      </div>
    </section>
    <p class="help-text">Stored encrypted in <code class="mono">{{ data.storage }}</code>. Keep the <code class="mono">data</code> folder private.</p>
  </template>
</template>

<style scoped>
.panel-head-text h2 { font-size: 1rem; margin-bottom: 0.25rem; }
.field-col { display: flex; flex-direction: column; gap: 0.375rem; min-width: 0; }
.revealed { display: flex; align-items: center; gap: 0.5rem; padding: 0.375rem 0.5rem; background: var(--panel-2); border-radius: var(--radius-sm); }
.revealed code { flex: 1; min-width: 0; }
</style>
