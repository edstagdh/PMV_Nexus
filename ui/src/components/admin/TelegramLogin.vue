<script setup>
// Server settings -> Connections -> Telegram: sign the app in to Telegram for
// sharing (phone -> code -> password when two-step verification is on), check
// the saved login, or sign in again when it has expired. See backend/telegram_login.py.
import { askConfirm } from "../../stores/confirm.js";
import { ref, onMounted, onBeforeUnmount } from "vue";
import { RouterLink } from "vue-router";
import { api } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";

const st = ref(null);
const step = ref(""); // "" | phone | code | password
const form = ref({ phone: "", code: "", password: "", email: "", emailCode: "" });
const info = ref("");
const busy = ref(false);

async function load(check = false) {
  try {
    st.value = await api(`/api/telegram/status${check ? "?check=true" : ""}`);
    if (st.value.login_step && !step.value) step.value = st.value.login_step;
  } catch (err) { toast(err.message || "Could not check Telegram", { type: "error" }); }
}
onMounted(() => load(false));

async function call(path, body, after) {
  busy.value = true;
  try { const r = await api(path, { method: "POST", body }); await after(r); }
  catch (err) { toast(err.message || "That didn't work", { type: "error" }); }
  finally { busy.value = false; }
}
// Where the code went, and (after Telegram's waiting time) another way to get it.
const sentInfo = ref(null);
const now = ref(Date.now());
let tick = null;
function codeSent(r) {
  if (r.step === "email") { step.value = "email"; info.value = ""; return; }
  step.value = "code"; info.value = "";
  sentInfo.value = { text: r.sent, next: r.next, type: r.type, at: Date.now() + (r.resend_in || 0) * 1000 };
  clearInterval(tick); tick = setInterval(() => { now.value = Date.now(); }, 1000);
}
const waitLeft = () => Math.max(0, Math.ceil(((sentInfo.value?.at || 0) - now.value) / 1000));
const start = () => call("/api/telegram/login/start", { phone: form.value.phone }, codeSent);
const sendEmail = () => call("/api/telegram/login/email", { email: form.value.email }, (r) => { step.value = "email_code"; info.value = `Telegram sent a code ${r.sent}.`; });
const sendEmailCode = () => call("/api/telegram/login/email-code", { code: form.value.emailCode }, codeSent);
const resend = () => call("/api/telegram/login/resend", {}, (r) => { codeSent(r); toast("Telegram is sending the code another way"); });
const sendCode = () => call("/api/telegram/login/code", { code: form.value.code }, finish);
const sendPassword = () => call("/api/telegram/login/password", { password: form.value.password }, finish);
async function finish(r) {
  if (r.step === "password") { step.value = "password"; info.value = r.hint ? `Password hint: ${r.hint}` : "Two-step verification is on."; return; }
  toast(`Signed in to Telegram as ${r.account}`, { type: "success" });
  step.value = ""; form.value = { phone: "", code: "", password: "", email: "", emailCode: "" }; info.value = "";
  await load(true);
}
onBeforeUnmount(() => clearInterval(tick));
async function cancel() {
  await api("/api/telegram/login/cancel", { method: "POST" }).catch(() => {});
  step.value = ""; info.value = "";
  load(false);
}
async function signOut() {
  if (!(await askConfirm("Sign the app out of Telegram? Sharing to Telegram stops until you sign in again.", { ok: "Sign out", danger: true }))) return;
  await api("/api/telegram/logout", { method: "POST" }).catch(() => {});
  load(false);
}
async function check() { busy.value = true; await load(true); busy.value = false; }
</script>

<template>
  <section class="card panel">
    <div class="tg-head">
      <h2>Telegram</h2>
      <template v-if="st">
        <span v-if="!st.configured" class="pill pill-bad">API keys missing</span>
        <span v-else-if="st.ok === true" class="pill pill-ok">Signed in</span>
        <span v-else-if="st.ok === false" class="pill pill-bad">{{ st.expired ? "Login expired" : "Not working" }}</span>
        <span v-else class="pill" :class="st.signed_in ? '' : 'pill-bad'">{{ st.signed_in ? "Login saved" : "Not signed in" }}</span>
      </template>
    </div>
    <p class="help-text">Sharing to Telegram uses your own Telegram account (not a bot), signed in once here.
      Telegram ends saved logins after long inactivity — then just sign in again.</p>
    <p v-if="st && !st.configured" class="error-text">Save the Telegram API ID and hash in the
      <RouterLink :to="{ query: { tab: 'credentials' } }">Credentials</RouterLink> tab first (from my.telegram.org).</p>
    <p v-if="st?.account" class="small">Account: <strong>{{ st.account }}</strong></p>
    <p v-if="st?.ok === false" class="error-text small">{{ st.error }}</p>

    <form v-if="step === 'phone'" class="tg-form" @submit.prevent="start">
      <input v-model="form.phone" class="input" type="tel" placeholder="+44 7700 900123" autocomplete="tel" aria-label="Phone number" required>
      <button class="btn btn-primary btn-sm" :disabled="busy">{{ busy ? "Sending…" : "Send the code" }}</button>
      <button class="btn btn-sm" type="button" @click="cancel">Cancel</button>
    </form>
    <form v-else-if="step === 'code'" class="tg-form" @submit.prevent="sendCode">
      <input v-model="form.code" class="input" inputmode="numeric" placeholder="12345" autocomplete="one-time-code" aria-label="Login code" required>
      <button class="btn btn-primary btn-sm" :disabled="busy">{{ busy ? "Checking…" : "Sign in" }}</button>
      <button class="btn btn-sm" type="button" @click="cancel">Cancel</button>
    </form>
    <template v-else-if="step === 'email'">
      <p class="small sent">Telegram wants a login email on your account before it sends a login code (it does this for some accounts
        when signing in to a new app). Enter an email address — Telegram sends a code to it, and then the login code goes there too.</p>
      <form class="tg-form" @submit.prevent="sendEmail">
        <input v-model="form.email" class="input" type="email" placeholder="you@example.com" autocomplete="email" aria-label="Login email" required>
        <button class="btn btn-primary btn-sm" :disabled="busy">{{ busy ? "Sending…" : "Send the email code" }}</button>
        <button class="btn btn-sm" type="button" @click="cancel">Cancel</button>
      </form>
    </template>
    <form v-else-if="step === 'email_code'" class="tg-form" @submit.prevent="sendEmailCode">
      <input v-model="form.emailCode" class="input" inputmode="numeric" placeholder="Code from the email" autocomplete="one-time-code" aria-label="Email code" required>
      <button class="btn btn-primary btn-sm" :disabled="busy">{{ busy ? "Checking…" : "Confirm the email" }}</button>
      <button class="btn btn-sm" type="button" @click="cancel">Cancel</button>
    </form>
    <form v-else-if="step === 'password'" class="tg-form" @submit.prevent="sendPassword">
      <input v-model="form.password" class="input" type="password" placeholder="Cloud password" autocomplete="current-password" aria-label="Two-step verification password" required>
      <button class="btn btn-primary btn-sm" :disabled="busy">{{ busy ? "Checking…" : "Sign in" }}</button>
      <button class="btn btn-sm" type="button" @click="cancel">Cancel</button>
    </form>
    <p v-if="step === 'code' && sentInfo" class="small sent">Telegram sent the code {{ sentInfo.text }}
      <span v-if="sentInfo.type" class="muted"> (Telegram's answer: {{ sentInfo.type }})</span></p>
    <p v-if="step === 'code' && sentInfo?.next" class="small">Didn't get it?
      <button class="btn btn-ghost btn-sm" :disabled="busy || waitLeft() > 0" @click="resend">Send it {{ sentInfo.next }}</button>
      <span v-if="waitLeft() > 0" class="muted">in {{ waitLeft() }} s</span></p>
    <p v-else-if="step === 'code' && sentInfo" class="muted small">No code after a few minutes? Cancel and start again, and check the
      number has its country code.</p>
    <p v-if="info && step" class="muted small">{{ info }}</p>

    <div v-if="!step && st?.configured" class="form-actions">
      <button class="btn btn-sm" :class="{ 'btn-primary': !st.signed_in || st.ok === false }" @click="step = 'phone'">
        {{ st.signed_in ? "Sign in again" : "Sign in" }}</button>
      <button v-if="st.signed_in" class="btn btn-sm" :disabled="busy" @click="check">{{ busy ? "Checking…" : "Check the login" }}</button>
      <button v-if="st.signed_in" class="btn btn-ghost btn-sm" @click="signOut">Sign out</button>
    </div>
  </section>
</template>

<style scoped>
.tg-head { display: flex; align-items: center; gap: 0.625rem; }
.tg-head h2 { font-size: 1rem; margin: 0; }
.tg-form { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
.tg-form .input { max-width: 16rem; height: 2.25rem; }
.small { font-size: 0.84rem; }
.sent { padding: 0.5rem 0.75rem; border-radius: var(--radius); background: var(--panel-2); border-left: 3px solid var(--accent); }
p { margin: 0; }
</style>
