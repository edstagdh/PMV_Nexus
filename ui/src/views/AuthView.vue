<script setup>
import { ref } from "vue";
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
    error.value = "Please accept the terms of use first";
    return;
  }
  if (session.setupRequired && password.value !== password2.value) {
    error.value = "The passwords do not match";
    return;
  }
  busy.value = true;
  try {
    if (session.setupRequired) await completeSetup(setupCode.value, username.value, password.value);
    else await login(username.value, password.value);
  } catch (err) {
    error.value = err.message || "Sign-in failed";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="auth">
    <form class="auth-card card" @submit.prevent="submit">
      <div class="auth-brand">
        <AppLogo size="2.75rem" />
        <div>
          <h1>PMV Nexus</h1>
          <p class="muted">{{ session.setupRequired ? "First-run setup" : "Sign in to continue" }}</p>
        </div>
      </div>
      <p v-if="session.setupRequired" class="hint">
        No accounts exist yet. Create the administrator account. The setup code is shown in the window
        where the app is running (and in <code>data/logs</code>).
      </p>
      <label v-if="session.setupRequired" class="field">
        <span>Setup code</span>
        <input v-model="setupCode" class="input" autocomplete="off" placeholder="XXXX-XXXX-XXXX" required>
      </label>
      <label class="field">
        <span>Username</span>
        <input v-model="username" class="input" autocomplete="username" required autofocus>
      </label>
      <label class="field">
        <span>Password</span>
        <input v-model="password" class="input" type="password" :autocomplete="session.setupRequired ? 'new-password' : 'current-password'" required>
      </label>
      <label v-if="session.setupRequired" class="field">
        <span>Confirm password</span>
        <input v-model="password2" class="input" type="password" autocomplete="new-password" required>
      </label>
      <PasswordChecklist v-if="session.setupRequired" :password="password" :username="username" />
      <div v-if="session.setupRequired" class="terms">
        <p><strong>Terms of use.</strong> PMV Nexus is an independent project, not affiliated with PMVHaven or any other
          site it works with. It's for adults only (18+, or the age of majority where you live). You're responsible for
          how you use it: follow each site's terms (download only what a site offers for download, for your own personal
          use), respect copyright, and don't redistribute content you have no rights to. Provided as is, without warranty.
          Full text: <code>DISCLAIMER.md</code> in the app folder, and Help → Terms of use.</p>
        <label class="switch"><input v-model="accepted" type="checkbox" required>I'm 18 or older and I accept these terms</label>
      </div>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button class="btn btn-primary submit" :disabled="busy">
        {{ busy ? "Please wait…" : session.setupRequired ? "Create admin account" : "Sign in" }}
      </button>
    </form>
  </div>
</template>

<style scoped>
.auth { min-height: 100vh; display: grid; place-items: center; padding: 1rem;
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
