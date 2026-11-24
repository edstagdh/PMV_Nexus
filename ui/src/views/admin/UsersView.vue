<script setup>
// Admin -> Users. There is exactly one admin (the account signed in here);
// every other account is a regular user: library viewing and playback only.
import { t } from "../../lib/i18n.js";
import AccessPanel from "../../components/admin/AccessPanel.vue";
import { ref, onMounted } from "vue";
import Icon from "../../components/Icon.vue";
import ModalDialog from "../../components/ModalDialog.vue";
import PasswordChecklist from "../../components/PasswordChecklist.vue";
import { api } from "../../lib/api.js";
import { formatDateTime } from "../../lib/format.js";
import { session } from "../../stores/session.js";
import { toast } from "../../stores/toast.js";

const users = ref(null);
const access = ref(null);  // AccessPanel: roles, groups, per-person access
const reloadKey = ref(0);
const error = ref("");
const dialog = ref(null); // { mode: "create" | "edit" | "delete", user? }
const form = ref({});
const formError = ref("");
const busy = ref(false);

async function load() {
  try { users.value = await api("/api/users"); error.value = ""; }
  catch (err) { error.value = err.message || t("Could not load the users"); }
}
onMounted(load);

function openCreate() { form.value = { username: "", password: "", role: "user" }; formError.value = ""; dialog.value = { mode: "create" }; }
function openEdit(u) { form.value = { username: u.username, password: "", is_active: u.is_active }; formError.value = ""; dialog.value = { mode: "edit", user: u }; }
function openDelete(u) { formError.value = ""; dialog.value = { mode: "delete", user: u }; }
const close = () => { if (!busy.value) dialog.value = null; };

async function submit() {
  busy.value = true;
  formError.value = "";
  const d = dialog.value;
  try {
    if (d.mode === "create") {
      await api("/api/users", { method: "POST", body: { username: form.value.username.trim(), password: form.value.password, role: form.value.role || "user" } });
      toast(t("Created {name}", { name: form.value.username.trim() }), { type: "success" });
    } else if (d.mode === "edit") {
      const u = d.user;
      const patch = {};
      if (form.value.username.trim() !== u.username) patch.username = form.value.username.trim();
      if (form.value.is_active !== u.is_active) patch.is_active = form.value.is_active;
      if (Object.keys(patch).length) await api(`/api/users/${u.id}`, { method: "PATCH", body: patch });
      if (form.value.password) await api(`/api/users/${u.id}/reset-password`, { method: "POST", body: { new_password: form.value.password } });
      toast(t("Saved"), { type: "success" });
    } else {
      await api(`/api/users/${d.user.id}`, { method: "DELETE" });
      toast(t("Deleted {name}", { name: d.user.username }));
    }
    dialog.value = null;
    load();
  } catch (err) {
    formError.value = err.message || t("That didn't work");
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>{{ $t("Users") }}</h1>
        <p><Tx msg="What each person may do comes from their role, the groups they're in, and anything given to them directly ({b1}). The admin can do everything."><template #b1><strong>{{ $t("Access") }}</strong></template></Tx></p>
      </div>
      <button class="btn btn-primary btn-sm" @click="openCreate"><Icon name="plus" />{{ $t("Create user") }}</button>
    </header>

    <p v-if="error" class="error-text">{{ error }}</p>
    <div v-else-if="!users" class="skeleton" style="height: 10rem; border-radius: var(--radius-lg)" />
    <section v-else class="card table-wrap">
      <table class="data-table">
        <thead><tr><th>{{ $t("User") }}</th><th>{{ $t("Role") }}</th><th>{{ $t("Status") }}</th><th>{{ $t("Last sign-in") }}</th><th /></tr></thead>
        <tbody>
          <tr v-for="u in users" :key="u.id">
            <td><strong>{{ u.username }}</strong> <span v-if="u.id === session.user?.id" class="muted">{{ $t("(you)") }}</span></td>
            <td><span class="pill" :class="{ 'pill-accent': u.role === 'admin' }">{{ u.role }}</span></td>
            <td><span class="pill" :class="u.is_active ? 'pill-ok' : 'pill-bad'">{{ u.is_active ? $t("Active") : $t("Disabled") }}</span></td>
            <td class="muted nowrap">{{ u.last_login_at ? formatDateTime(u.last_login_at) : $t("Never") }}</td>
            <td class="actions">
              <button class="btn btn-ghost btn-sm" @click="openEdit(u)"><Icon name="edit" />{{ $t("Edit") }}</button>
              <button v-if="u.role !== 'admin'" class="btn btn-ghost btn-sm" @click="access?.editUser(u.id)"><Icon name="lock" />{{ $t("Access") }}</button>
              <button v-if="u.role !== 'admin'" class="btn btn-ghost btn-sm btn-danger" @click="openDelete(u)"><Icon name="trash" />{{ $t("Delete") }}</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <AccessPanel ref="access" :reload-key="reloadKey" @changed="load" />

    <ModalDialog v-if="dialog && dialog.mode !== 'delete'" :title="dialog.mode === 'create' ? $t('Create user') : $t('Edit {name}', { name: dialog.user.username })" @close="close">
      <form id="user-form" class="form" @submit.prevent="submit">
        <label class="field">{{ $t("Username") }}
          <input v-model="form.username" class="input" autocomplete="off" required minlength="3">
        </label>
        <label class="field">{{ dialog.mode === "create" ? $t("Password") : $t("New password") }}
          <input v-model="form.password" class="input" type="password" autocomplete="new-password" :required="dialog.mode === 'create'"
                 :placeholder="dialog.mode === 'edit' ? $t('Leave empty to keep the current password') : ''">
        </label>
        <PasswordChecklist v-if="form.password || dialog.mode === 'create'" :password="form.password" :username="form.username" />
        <label v-if="dialog.mode === 'create'" class="field">{{ $t("Role") }}
          <select v-model="form.role" class="select">
            <option v-for="r in (access?.roles || []).filter((x) => x.name !== 'admin')" :key="r.name" :value="r.name">{{ r.name }}</option>
          </select>
        </label>
        <p v-if="dialog.mode === 'edit' && form.password" class="help-text">{{ $t("Setting a new password signs this user out everywhere.") }}</p>
        <label v-if="dialog.mode === 'edit' && dialog.user.role !== 'admin'" class="switch">
          <input v-model="form.is_active" type="checkbox">{{ $t("Account enabled") }}
        </label>
        <p v-if="formError" class="error-text">{{ formError }}</p>
      </form>
      <template #footer>
        <button class="btn" type="button" @click="close">{{ $t("Cancel") }}</button>
        <button class="btn btn-primary" type="submit" form="user-form" :disabled="busy">{{ busy ? $t("Saving…") : dialog.mode === "create" ? $t("Create") : $t("Save") }}</button>
      </template>
    </ModalDialog>

    <ModalDialog v-if="dialog?.mode === 'delete'" :title="$t('Delete {name}?', { name: dialog.user.username })" @close="close">
      <p>{{ $t("The account is signed out and removed for good. Their settings go with it; the library is not affected.") }}</p>
      <p v-if="formError" class="error-text">{{ formError }}</p>
      <template #footer>
        <button class="btn" @click="close">{{ $t("Cancel") }}</button>
        <button class="btn btn-danger" :disabled="busy" @click="submit">{{ busy ? $t("Deleting…") : $t("Delete") }}</button>
      </template>
    </ModalDialog>
  </div>
</template>

<style scoped>
.actions { text-align: end; white-space: nowrap; }
.nowrap { white-space: nowrap; }
.form { display: flex; flex-direction: column; gap: 0.75rem; }
.field { display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.84rem; font-weight: 600; }
.field .input { font-weight: 400; }
</style>
