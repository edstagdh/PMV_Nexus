<script setup>
// Server settings -> Proxy: the proxy's address (a credential), where it's
// used (the sites: off / split / all; each share destination), and a test
// that compares it with the direct connection (does it work, which address
// the sites see, latency, speed) -- run it before relying on the proxy.
import { askConfirm } from "../../stores/confirm.js";
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { api } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";

const props = defineProps({ settings: Object });
const emit = defineEmits(["saved"]);

const addr = ref({ url: "", username: "", password: "" });
const cfg = ref({});
const status = ref(null);
const test = ref(null);
const busy = ref("");
let timer = null;

function fromSettings() {
  const p = props.settings?.proxy || {};
  addr.value = { url: p.url || "", username: p.username || "", password: "" };
  cfg.value = { site_mode: p.site_mode || "off", proxy_downloads: p.proxy_downloads || 1, telegram: !!p.telegram,
                gofile: !!p.gofile, pixeldrain: !!p.pixeldrain, check_before_use: p.check_before_use !== false };
}
async function loadStatus() {
  try { status.value = await api("/api/settings/proxy/status"); test.value = test.value || status.value.last_test; } catch { /* keep */ }
}
onMounted(() => { fromSettings(); loadStatus(); timer = setInterval(loadStatus, 5000); });
onBeforeUnmount(() => clearInterval(timer));

const direct = computed(() => props.settings?.download?.max_concurrent_downloads || 1);
const total = computed(() => (cfg.value.site_mode === "split" ? direct.value + cfg.value.proxy_downloads : direct.value));
const socks = computed(() => /^socks/i.test(addr.value.url.trim()));
const passwordSet = computed(() => !!props.settings?.proxy?.password_set);

async function saveAddress() {
  const values = { proxy_url: addr.value.url.trim(), proxy_username: addr.value.username.trim() };
  if (addr.value.password) values.proxy_password = addr.value.password;
  busy.value = "addr";
  try {
    await api("/api/credentials", { method: "PUT", body: { values } });
    toast("Proxy address saved", { type: "success" });
    addr.value.password = "";
    emit("saved");
    loadStatus();
  } catch (err) { toast(err.message || "Could not save", { type: "error" }); }
  finally { busy.value = ""; }
}
async function clearPassword() {
  if (!(await askConfirm("Remove the saved proxy password?", { ok: "Remove", danger: true }))) return;
  await api("/api/credentials", { method: "PUT", body: { values: { proxy_password: "" } } }).catch(() => {});
  emit("saved");
}
async function saveUse() {
  if (total.value > 2 && !(await askConfirm(`That's ${total.value} downloads at the same time. More than 2 often gets downloads refused or slowed down by the site. Save anyway?`, { ok: "Save anyway" }))) return;
  busy.value = "use";
  try {
    await api("/api/settings/proxy", { method: "POST", body: { ...cfg.value, proxy_downloads: Number(cfg.value.proxy_downloads) } });
    toast("Saved", { type: "success" });
    emit("saved");
    loadStatus();
  } catch (err) { toast(err.message || "Could not save", { type: "error" }); }
  finally { busy.value = ""; }
}
async function runTest() {
  busy.value = "test";
  try { test.value = await api("/api/settings/proxy/test", { method: "POST" }); loadStatus(); }
  catch (err) { toast(err.message || "The test failed", { type: "error" }); }
  finally { busy.value = ""; }
}
const fmtMs = (v) => (v == null ? "—" : `${v} ms`);
const ago = (ts) => (ts ? new Date(ts * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" }) : "");
</script>

<template>
  <section class="card panel">
    <p class="help-text">Send the app's connections through a proxy: downloads from PMVHaven / HMVHaven / PornTubeAI, and uploads to the
      share destinations you pick. Test it first — a slow proxy holds everything sent through it up.</p>

    <h3 class="sub">Proxy</h3>
    <div class="form-row">
      <label class="label" for="px-url">Address<small>socks5://host:port or http://host:port</small></label>
      <input id="px-url" v-model="addr.url" class="input" placeholder="socks5://192.168.1.10:1080" autocomplete="off" spellcheck="false">
    </div>
    <div class="form-row">
      <label class="label" for="px-user">Login<small>Only if the proxy asks for one.</small></label>
      <div class="inline-input">
        <input id="px-user" v-model="addr.username" class="input" placeholder="Username" autocomplete="off">
        <input v-model="addr.password" class="input" type="password" :placeholder="passwordSet ? 'Password (saved — type to change)' : 'Password'" autocomplete="new-password">
        <button v-if="passwordSet" class="btn btn-ghost btn-sm" type="button" @click="clearPassword">Remove</button>
      </div>
    </div>
    <p v-if="socks" class="muted small">SOCKS proxies need the <code>socksio</code> package (in requirements.txt). Videos streamed as HLS can't be
      downloaded through SOCKS (FFmpeg only supports HTTP proxies); in split mode they go through the direct connection.</p>
    <div class="form-actions"><button class="btn btn-sm" :disabled="busy === 'addr'" @click="saveAddress">Save address</button></div>

    <h3 class="sub">Test</h3>
    <div class="test-head">
      <button class="btn btn-sm btn-primary" :disabled="busy === 'test' || !status?.configured" @click="runTest">
        {{ busy === "test" ? "Testing… (up to a minute)" : "Test the proxy" }}</button>
      <span v-if="!status?.configured" class="muted small">Save an address first.</span>
      <span v-else-if="status?.last_check" class="small" :class="status.last_check.ok ? 'ok-text' : 'error-text'">
        Last check {{ ago(status.last_check.checked_at) }}: {{ status.last_check.ok ? "working" : status.last_check.error }}</span>
    </div>
    <template v-if="test && !test.error">
      <p class="verdict" :class="test.level">{{ test.verdict }}</p>
      <div class="table-wrap"><table class="data-table">
        <thead><tr><th /><th>Works</th><th>Address the sites see</th><th>Latency</th><th>PMVHaven</th><th>Speed</th></tr></thead>
        <tbody>
          <tr v-for="row in [['Direct', test.direct], ['Proxy', test.proxy]]" :key="row[0]">
            <td><strong>{{ row[0] }}</strong></td>
            <td><span class="pill" :class="row[1].ok ? 'pill-ok' : 'pill-bad'">{{ row[1].ok ? "Yes" : "No" }}</span></td>
            <td class="mono">{{ row[1].ip || "—" }}</td>
            <td>{{ fmtMs(row[1].latency_ms) }}</td>
            <td>{{ row[1].site_ms != null ? `${row[1].site_ms} ms (${row[1].site_status})` : "—" }}</td>
            <td>{{ row[1].mbps != null ? `${row[1].mbps} Mbit/s` : row[1].error ? row[1].error : "—" }}</td>
          </tr>
        </tbody>
      </table></div>
      <p class="muted small">Tested {{ ago(test.tested_at) }}. Speed = about 10 MB from a public speed-test server; the sites themselves may be slower.</p>
    </template>
    <p v-else-if="test?.error" class="error-text">{{ test.error }}</p>

    <h3 class="sub">Use it for</h3>
    <div class="form-row">
      <span class="label">PMVHaven, HMVHaven, PornTubeAI</span>
      <div class="radios">
        <label class="radio"><input v-model="cfg.site_mode" type="radio" value="off"><span><strong>Not used</strong>
          <span class="muted">Everything goes directly.</span></span></label>
        <label class="radio"><input v-model="cfg.site_mode" type="radio" value="split"><span><strong>Split downloads</strong>
          <span class="muted">Downloads also run through the proxy, at the same time as the direct ones — e.g. one direct and one through the
            proxy, twice as many at once. Pages and details still load directly.</span></span></label>
        <label class="radio"><input v-model="cfg.site_mode" type="radio" value="all"><span><strong>Everything</strong>
          <span class="muted">Every request to the sites — pages, details, downloads and sign-in — goes through the proxy.</span></span></label>
      </div>
    </div>
    <div v-if="cfg.site_mode === 'split'" class="form-row">
      <label class="label" for="px-n">Through the proxy at once<small>Plus {{ direct }} direct ("Downloads at once" in the Download tab).</small></label>
      <div class="field-col">
        <select id="px-n" v-model.number="cfg.proxy_downloads" class="select" style="max-width: 8rem">
          <option v-for="n in [1, 2, 3]" :key="n" :value="n">{{ n }}</option>
        </select>
        <small :class="total > 2 ? 'error-text' : 'help-text'">{{ total }} downloads at the same time{{ total > 2 ? " — more than 2 will most likely cause problems (refused or slowed downloads)." : "." }}</small>
      </div>
    </div>
    <div class="form-row">
      <span class="label">Share uploads</span>
      <div class="toggles">
        <label class="switch"><input v-model="cfg.telegram" type="checkbox">Telegram</label>
        <label class="switch"><input v-model="cfg.gofile" type="checkbox">Gofile</label>
        <label class="switch"><input v-model="cfg.pixeldrain" type="checkbox">Pixeldrain</label>
      </div>
    </div>
    <div class="form-row">
      <span class="label">Safety</span>
      <label class="switch"><input v-model="cfg.check_before_use" type="checkbox">Check the proxy before using it (every 5 minutes at most); while it
        doesn't answer, nothing is sent through it — downloads wait for the direct connection, uploads fail with a message</label>
    </div>
    <div class="form-actions"><button class="btn btn-primary btn-sm" :disabled="busy === 'use'" @click="saveUse">Save</button></div>

    <template v-if="status?.lanes && Object.keys(status.lanes).length">
      <h3 class="sub">Downloading now</h3>
      <ul class="lanes">
        <li v-for="(l, name) in status.lanes" :key="name"><span class="pill" :class="name.startsWith('proxy') ? 'pill-accent' : ''">{{ name.startsWith("proxy") ? "Proxy" : "Direct" }}</span>
          {{ l.title || (l.item_id ? l.item_id : "waiting") }}</li>
      </ul>
    </template>
  </section>
</template>

<style scoped>
.sub { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); margin: 0.5rem 0 0; }
.small { font-size: 0.8rem; }
.test-head { display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap; }
.verdict { margin: 0; padding: 0.5rem 0.75rem; border-radius: var(--radius); font-size: 0.875rem; background: var(--panel-2); }
.verdict.ok { border-left: 3px solid var(--success, #22c55e); }
.verdict.warn { border-left: 3px solid var(--warning, #f59e0b); }
.verdict.bad { border-left: 3px solid var(--danger); }
.radios { display: flex; flex-direction: column; gap: 0.5rem; }
.radio { display: flex; gap: 0.5rem; align-items: flex-start; cursor: pointer; }
.radio input { margin-top: 0.2rem; accent-color: var(--accent); }
.radio span { display: flex; flex-direction: column; gap: 0.125rem; font-size: 0.875rem; }
.field-col { display: flex; flex-direction: column; gap: 0.375rem; }
.lanes { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.84rem; }
.inline-input .input { min-width: 0; }
.toggles { display: flex; flex-direction: column; gap: 0.625rem; }
.switch { align-items: flex-start; }
</style>
