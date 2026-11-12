<script setup>
// Server settings -> Connections: sign the app in to each site once; the
// saved login session is reused until it expires (GET /api/pmv/status,
// POST /api/pmv/login?site=...).
import { ref, onMounted } from "vue";
import { RouterLink } from "vue-router";
import TelegramLogin from "./TelegramLogin.vue";
import { api, qs } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";

const status = ref(null);
const error = ref("");
const busy = ref("");
const label = (key) => status.value?.[key]?.label || key;

async function load() {
  try { status.value = await api("/api/pmv/status"); error.value = ""; }
  catch (err) { error.value = err.message || "Could not check the connections"; }
}
onMounted(load);

async function connect(site) {
  busy.value = site;
  try {
    await api(`/api/pmv/login?${qs({ site })}`, { method: "POST" });
    toast(`Connected to ${label(site)}`, { type: "success" });
  } catch (err) {
    toast(err.message || `Could not connect to ${label(site)}`, { type: "error" });
  } finally {
    busy.value = "";
    load();
  }
}
</script>

<template>
  <div class="conn">
  <section class="card panel">
    <p class="help-text">
      Connect each site once so the app can read and download from it; the saved login session is reused until it expires.
      Each site signs in with the account set in the <RouterLink :to="{ query: { tab: 'credentials' } }">Credentials</RouterLink> tab
      (the sister sites use the PMVHaven account unless you give them their own).
    </p>
    <p v-if="error" class="error-text">{{ error }}</p>
    <div v-else-if="!status" class="skeleton" style="height: 5rem; border-radius: var(--radius)" />
    <div v-else class="table-wrap"><table class="data-table">
      <tbody>
        <tr v-for="(s, key) in status" :key="key">
          <td><strong>{{ label(key) }}</strong><div class="muted domain">{{ s.domain }}</div></td>
          <td><span class="pill" :class="s.logged_in ? 'pill-ok' : 'pill-bad'">{{ s.logged_in ? "Connected" : "Not connected" }}</span></td>
          <td style="text-align: right">
            <button class="btn btn-sm" :class="{ 'btn-primary': !s.logged_in }" :disabled="busy === key" @click="connect(key)">
              {{ busy === key ? "Connecting…" : s.logged_in ? "Reconnect" : "Connect" }}
            </button>
          </td>
        </tr>
      </tbody>
    </table></div>
  </section>
  <TelegramLogin />
  </div>
</template>

<style scoped>
.conn { display: flex; flex-direction: column; gap: 1rem; }
.domain { font-size: 0.76rem; overflow-wrap: anywhere; }
</style>
