<script setup>
import { t, i18n, loadBoot, setLanguage } from "../lib/i18n.js";
import { ref, onMounted } from "vue";
import AppLogo from "../components/AppLogo.vue";
import PasswordChecklist from "../components/PasswordChecklist.vue";
import { session, login, completeSetup } from "../stores/session.js";

const username = ref("");
const password = ref("");
const password2 = ref("");
const setupCode = ref("");
const accepted = ref(false);   // first run: the terms of use (DISCLAIMER.md), 2.28.0
const error = ref("");
const busy = ref(false);

async function submit() {
  error.value = "";
  if (session.setupRequired && !accepted.value) {
    error.value = t("Please accept the terms of use first");
    return;
  }
  if (session.setupRequired && password.value !== password2.value) {
    error.value = t("The passwords do not match");
    return;
  }
  busy.value = true;
  try {
    if (session.setupRequired) await completeSetup(setupCode.value, username.value, password.value);
    else await login(username.value, password.value);
  } catch (err) {
    error.value = err.message || t("Sign-in failed");
  } finally {
    busy.value = false;
  }
}

// 3.7.0: the sign-in page in another installed language (this browser only; after signing in,
// the account's choice in My Settings -> Language counts).
const langs = ref([]);
onMounted(async () => { langs.value = (await loadBoot())?.languages || []; });
async function pickLanguage(code) {
  await setLanguage(code);
  window.location.reload();
}
</script>

<template>
  <div class="auth">
    <form class="auth-card card" @submit.prevent="submit">
      <div class="auth-brand">
        <AppLogo size="2.75rem" />
        <div>
          <h1>PMV Nexus</h1>
          <p class="muted">{{ session.setupRequired ? $t("First-run setup") : $t("Sign in to continue") }}</p>
        </div>
      </div>
      <p v-if="session.setupRequired" class="hint"><Tx msg="No accounts exist yet. Create the administrator account. The setup code is shown in the window where the app is running (and in {folder})."><template #folder><code>data/logs</code></template></Tx></p>
      <label v-if="session.setupRequired" class="field">
        <span>{{ $t("Setup code") }}</span>
        <input v-model="setupCode" class="input" autocomplete="off" :placeholder="$t('XXXX-XXXX-XXXX')" required>
      </label>
      <label class="field">
        <span>{{ $t("Username") }}</span>
        <input v-model="username" class="input" autocomplete="username" required autofocus>
      </label>
      <label class="field">
        <span>{{ $t("Password") }}</span>
        <input v-model="password" class="input" type="password" :autocomplete="session.setupRequired ? 'new-password' : 'current-password'" required>
      </label>
      <label v-if="session.setupRequired" class="field">
        <span>{{ $t("Confirm password") }}</span>
        <input v-model="password2" class="input" type="password" autocomplete="new-password" required>
      </label>
      <PasswordChecklist v-if="session.setupRequired" :password="password" :username="username" />
      <div v-if="session.setupRequired" class="terms">
        <p><strong>{{ $t("Terms of use.") }}</strong> {{ $t("PMV Nexus is an independent project, not affiliated with PMVHaven or any other site it works with. It's for adults only (18+, or the age of majority where you live). You're responsible for how you use it: follow each site's terms (download only what a site offers for download, for your own personal use), respect copyright, and don't redistribute content you have no rights to. Provided as is, without warranty.") }}
          <Tx msg="Full text: {file} in the app folder, and Help → Terms of use."><template #file><code>DISCLAIMER.md</code></template></Tx></p>
        <label class="switch"><input v-model="accepted" type="checkbox" required>{{ $t("I'm 18 or older and I accept these terms") }}</label>
      </div>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button class="btn btn-primary submit" :disabled="busy">
        {{ busy ? $t("Please wait…") : session.setupRequired ? $t("Create admin account") : $t("Sign in") }}
      </button>
    </form>
    <label v-if="langs.length" class="auth-lang muted small">
      <span aria-hidden="true">🌐</span>
      <select class="select" :value="i18n.code" :aria-label="$t('Language')" @change="pickLanguage($event.target.value)">
        <option value="en" lang="en">English</option>  <!-- i18n-ignore -->
        <option v-for="l in langs" :key="l.code" :value="l.code" :lang="l.code">{{ l.native }}</option>
      </select>
    </label>
  </div>
</template>

<style scoped>
.auth-lang { display: flex; align-items: center; gap: 0.5rem; justify-content: center; margin-top: 1rem; }
.auth-lang .select { width: auto; }
.auth { min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 1rem;
  background: radial-gradient(75rem 37.5rem at 50% -10%, var(--accent-soft), transparent 60%), var(--bg); }
.auth-card { width: 100%; max-width: 23.75rem; padding: 1.5rem; display: flex; flex-direction: column; gap: 0.875rem; box-shadow: var(--shadow); }
.auth-brand { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.25rem; }
.auth-brand h1 { font-size: 1.125rem; }
.auth-brand p { margin: 0; font-size: 0.8125rem; }
.hint { margin: 0; font-size: 0.7812rem; color: var(--text-dim); }
.error { margin: 0; color: var(--danger); font-size: 0.8125rem; }
.submit { height: 2.5rem; }
.terms { font-size: 0.8rem; line-height: 1.45; color: var(--text-2); border: 1px solid var(--border); border-radius: var(--radius);
  padding: 0.6rem 0.75rem; display: flex; flex-direction: column; gap: 0.5rem; }
.terms p { margin: 0; }
.terms .switch { color: var(--text); font-size: 0.85rem; }
</style>
