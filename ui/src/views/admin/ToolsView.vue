<script setup>
// Admin -> Tools: whether Python, ffmpeg/ffprobe/MediaInfo and the Python
// packages the app needs are installed (GET /api/system/tools).
import { ref, onMounted } from "vue";
import { api } from "../../lib/api.js";

const data = ref(null);
const error = ref("");
const checking = ref(false);

async function load() {
  checking.value = true;
  try { data.value = await api("/api/system/tools"); error.value = ""; }
  catch (err) { error.value = err.message || "Could not check the tools"; }
  finally { checking.value = false; }
}
onMounted(load);
const ver = (v) => (v ? `v${String(v).replace(/^v/, "")}` : "");
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div><h1>Tools</h1><p>Programs and Python packages the app relies on.</p></div>
      <button class="btn btn-sm" :disabled="checking" @click="load">{{ checking ? "Checking…" : "Check again" }}</button>
    </header>

    <p v-if="error" class="error-text">{{ error }}</p>
    <div v-else-if="!data" class="skeleton" style="height: 16rem; border-radius: var(--radius-lg)" />
    <template v-else>
      <div class="card panel summary" :class="data.all_ok ? 'ok' : 'bad'">
        {{ data.all_ok ? "✅ Everything required is installed." : "⚠️ Some required components are missing or outdated — see the hints below." }}
      </div>
      <section v-for="group in [
        { title: 'Runtime', rows: [{ name: 'Python', ok: data.python.ok, version: data.python.version, path: data.python.executable, hint: data.python.ok ? null : 'Python 3.11 or newer is recommended' }] },
        { title: 'Programs', rows: data.tools },
        { title: 'Python packages', rows: data.packages },
      ]" :key="group.title" class="card panel">
        <h2>{{ group.title }}</h2>
        <div class="table-wrap">
          <table class="data-table">
            <tbody>
              <tr v-for="t in group.rows" :key="t.name">
                <td class="name"><strong>{{ t.name }}</strong></td>
                <td>
                  <span>{{ ver(t.version) || "—" }}</span>
                  <span v-if="t.required" class="muted"> · needs {{ t.required }}</span>
                  <div v-if="t.path" class="mono muted">{{ t.path }}</div>
                  <div v-if="t.hint && !t.ok" class="hint mono">{{ t.hint }}</div>
                </td>
                <td class="state"><span class="pill" :class="t.ok ? 'pill-ok' : 'pill-bad'">{{ t.ok ? "OK" : "Missing" }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="group.title === 'Runtime'" class="help-text">{{ data.platform }}</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.summary { font-size: 0.9rem; font-weight: 600; }
.summary.ok { border-color: var(--success); }
.summary.bad { border-color: var(--danger); }
.name { width: 12rem; }
.state { width: 5rem; text-align: right; }
.hint { margin-top: 0.25rem; padding: 0.3rem 0.5rem; background: var(--panel-2); border-radius: var(--radius-sm); color: var(--text); }
</style>
