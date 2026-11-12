<script setup>
// Admin -> Users. There is exactly one admin (the account signed in here);
// every other account is a regular user: library viewing and playback only.
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
  catch (err) { error.value = err.message || "Could not load the users"; }
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
      toast(`Created ${form.value.username.trim()}`, { type: "success" });
    } else if (d.mode === "edit") {
      const u = d.user;
      const patch = {};
      if (form.value.username.trim() !== u.username) patch.username = form.value.username.trim();
      if (form.value.is_active !== u.is_active) patch.is_active = form.value.is_active;
      if (Object.keys(patch).length) await api(`/api/users/${u.id}`, { method: "PATCH", body: patch });
      if (form.value.password) await api(`/api/users/${u.id}/reset-password`, { method: "POST", body: { new_password: form.value.password } });
      toast("Saved", { type: "success" });
    } else {
      await api(`/api/users/${d.user.id}`, { method: "DELETE" });
      toast(`Deleted ${d.user.username}`);
    }
    dialog.value = null;
    load();
  } catch (err) {
    formError.value = err.message || "That didn't work";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>Users</h1>
        <p>What each person may do comes from their role, the groups they're in, and anything given to them directly (<strong>Access</strong>).
          The admin can do everything.</p>
      </div>
      <button class="btn btn-primary btn-sm" @click="openCreate"><Icon name="plus" />Create user</button>
    </header>

    <p v-if="error" class="error-text">{{ error }}</p>
    <div v-else-if="!users" class="skeleton" style="height: 10rem; border-radius: var(--radius-lg)" />
    <section v-else class="card table-wrap">
      <table class="data-table">
        <thead><tr><th>User</th><th>Role</th><th>Status</th><th>Last sign-in</th><th /></tr></thead>
        <tbody>
          <tr v-for="u in users" :key="u.id">
            <td><strong>{{ u.username }}</strong> <span v-if="u.id === session.user?.id" class="muted">(you)</span></td>
            <td><span class="pill" :class="{ 'pill-accent': u.role === 'admin' }">{{ u.role }}</span></td>
            <td><span class="pill" :class="u.is_active ? 'pill-ok' : 'pill-bad'">{{ u.is_active ? "Active" : "Disabled" }}</span></td>
            <td class="muted nowrap">{{ u.last_login_at ? formatDateTime(u.last_login_at) : "Never" }}</td>
            <td class="actions">
              <button class="btn btn-ghost btn-sm" @click="openEdit(u)"><Icon name="edit" />Edit</button>
              <button v-if="u.role !== 'admin'" class="btn btn-ghost btn-sm" @click="access?.editUser(u.id)"><Icon name="lock" />Access</button>
              <button v-if="u.role !== 'admin'" class="btn btn-ghost btn-sm btn-danger" @click="openDelete(u)"><Icon name="trash" />Delete</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <AccessPanel ref="access" :reload-key="reloadKey" @changed="load" />

    <ModalDialog v-if="dialog && dialog.mode !== 'delete'" :title="dialog.mode === 'create' ? 'Create user' : `Edit ${dialog.user.username}`" @close="close">
      <form id="user-form" class="form" @submit.prevent="submit">
        <label class="field">Username
          <input v-model="form.username" class="input" autocomplete="off" required minlength="3">
        </label>
        <label class="field">{{ dialog.mode === "create" ? "Password" : "New password" }}
          <input v-model="form.password" class="input" type="password" autocomplete="new-password" :required="dialog.mode === 'create'"
                 :placeholder="dialog.mode === 'edit' ? 'Leave empty to keep the current password' : ''">
        </label>
        <PasswordChecklist v-if="form.password || dialog.mode === 'create'" :password="form.password" :username="form.username" />
        <label v-if="dialog.mode === 'create'" class="field">Role
          <select v-model="form.role" class="select">
            <option v-for="r in (access?.roles || []).filter((x) => x.name !== 'admin')" :key="r.name" :value="r.name">{{ r.name }}</option>
          </select>
        </label>
        <p v-if="dialog.mode === 'edit' && form.password" class="help-text">Setting a new password signs this user out everywhere.</p>
        <label v-if="dialog.mode === 'edit' && dialog.user.role !== 'admin'" class="switch">
          <input v-model="form.is_active" type="checkbox">Account enabled
        </label>
        <p v-if="formError" class="error-text">{{ formError }}</p>
      </form>
      <template #footer>
        <button class="btn" type="button" @click="close">Cancel</button>
        <button class="btn btn-primary" type="submit" form="user-form" :disabled="busy">{{ busy ? "Saving…" : dialog.mode === "create" ? "Create" : "Save" }}</button>
      </template>
    </ModalDialog>

    <ModalDialog v-if="dialog?.mode === 'delete'" :title="`Delete ${dialog.user.username}?`" @close="close">
      <p>The account is signed out and removed for good. Their settings go with it; the library is not affected.</p>
      <p v-if="formError" class="error-text">{{ formError }}</p>
      <template #footer>
        <button class="btn" @click="close">Cancel</button>
        <button class="btn btn-danger" :disabled="busy" @click="submit">{{ busy ? "Deleting…" : "Delete" }}</button>
      </template>
    </ModalDialog>
  </div>
</template>

<style scoped>
.actions { text-align: right; white-space: nowrap; }
.nowrap { white-space: nowrap; }
.form { display: flex; flex-direction: column; gap: 0.75rem; }
.field { display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.84rem; font-weight: 600; }
.field .input { font-weight: 400; }
</style>
