<script setup>
// Management -> Users: roles, groups and each person's access (2.12.0).
// Someone's permissions = their role + their groups + what's given to them
// directly. Changes apply on their next click (no new sign-in).
import { askConfirm } from "../../stores/confirm.js";
import { ref, computed, watch } from "vue";
import Icon from "../Icon.vue";
import ModalDialog from "../ModalDialog.vue";
import PermissionPicker from "./PermissionPicker.vue";
import { api } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";
import { onLibraryChange } from "../../stores/live.js";

const props = defineProps({ reloadKey: Number });
const emit = defineEmits(["changed"]);
const data = ref(null);
const editing = ref(null); // { kind: "role" | "group" | "user", ... }
const busy = ref(false);
const err = ref("");

async function load() {
  try { data.value = await api("/api/access"); }
  catch (e) { err.value = e.message || "Could not load roles and groups"; }
}
load();
watch(() => props.reloadKey, load);
onLibraryChange((changed) => { if (changed?.includes("permissions")) load(); });

const label = (key) => data.value?.catalog.find((p) => p.key === key)?.label || key;
const summary = (list) => (list.includes("*") ? "Everything" : list.length ? list.filter((k) => k !== "library.manage").map(label).join(", ") + (list.includes("library.manage") ? (list.length > 1 ? ", " : "") + "managing the library" : "") : "Nothing");
const userName = (id) => data.value?.users.find((u) => u.id === id)?.username || "?";
const groupName = (id) => data.value?.groups.find((g) => g.id === id)?.name || "?";
const roleOf = (name) => data.value?.roles.find((r) => r.name === name);

function editRole(r) { err.value = ""; editing.value = { kind: "role", isNew: !r, name: r?.name || "", permissions: [...(r?.permissions || [])] }; }
function editGroup(g) { err.value = ""; editing.value = { kind: "group", id: g?.id, name: g?.name || "", description: g?.description || "", permissions: [...(g?.permissions || [])], members: [...(g?.members || [])] }; }
function editUser(u) { err.value = ""; editing.value = { kind: "user", id: u.id, username: u.username, role: u.role, groups: [...u.groups], permissions: [...u.permissions] }; }

// What the edited person / group would end up with from elsewhere (shown greyed in the picker).
const inherited = computed(() => {
  const e = editing.value;
  if (e?.kind !== "user") return [];
  const out = new Set(roleOf(e.role)?.permissions || []);
  for (const g of data.value.groups) if (e.groups.includes(g.id)) g.permissions.forEach((p) => out.add(p));
  return [...out];
});

async function save() {
  const e = editing.value;
  busy.value = true;
  err.value = "";
  try {
    if (e.kind === "role") {
      await api(e.isNew ? "/api/roles" : `/api/roles/${encodeURIComponent(e.name)}`, { method: e.isNew ? "POST" : "PUT", body: { name: e.name, permissions: e.permissions } });
    } else if (e.kind === "group") {
      const body = { name: e.name, description: e.description, permissions: e.permissions, members: e.members };
      await api(e.id ? `/api/groups/${e.id}` : "/api/groups", { method: e.id ? "PUT" : "POST", body });
    } else {
      await api(`/api/users/${e.id}/access`, { method: "PUT", body: { role: e.role, groups: e.groups, permissions: e.permissions } });
    }
    toast("Saved — applies on their next click", { type: "success" });
    editing.value = null;
    await load();
    emit("changed");
  } catch (x) { err.value = x.message || "Could not save"; }
  finally { busy.value = false; }
}
async function removeRole(r) {
  if (!(await askConfirm(`Delete the role “${r.name}”?`, { ok: "Delete", danger: true }))) return;
  try { await api(`/api/roles/${encodeURIComponent(r.name)}`, { method: "DELETE" }); await load(); }
  catch (x) { toast(x.message || "Could not delete", { type: "error" }); }
}
async function removeGroup(g) {
  if (!(await askConfirm(`Delete the group “${g.name}”? Its members lose what it gave them.`, { ok: "Delete", danger: true }))) return;
  try { await api(`/api/groups/${g.id}`, { method: "DELETE" }); await load(); }
  catch (x) { toast(x.message || "Could not delete", { type: "error" }); }
}
function toggleIn(list, id) { const i = list.indexOf(id); i >= 0 ? list.splice(i, 1) : list.push(id); }
defineExpose({ editUser: (id) => { const u = data.value?.users.find((x) => x.id === id); if (u) editUser(u); }, roles: computed(() => data.value?.roles || []) });
</script>

<template>
  <p v-if="err && !editing" class="error-text">{{ err }}</p>
  <template v-if="data">
    <section class="card block">
      <div class="head"><h2>Roles</h2><button class="btn btn-sm" @click="editRole(null)"><Icon name="plus" :size="15" />New role</button></div>
      <p class="muted small">Everyone has one role. <strong>admin</strong> can do everything (there's exactly one admin); <strong>user</strong> is the default for new accounts.</p>
      <ul class="rows">
        <li v-for="r in data.roles" :key="r.name">
          <span class="nm">{{ r.name }}</span><span class="muted small">{{ r.users }} {{ r.users === 1 ? "person" : "people" }}</span>
          <span class="sum small">{{ summary(r.permissions) }}</span>
          <span class="acts">
            <button v-if="r.name !== 'admin'" class="btn btn-ghost btn-sm" @click="editRole(r)">Edit</button>
            <button v-if="!r.builtin" class="btn btn-ghost btn-sm btn-danger" @click="removeRole(r)">Delete</button>
          </span>
        </li>
      </ul>
    </section>

    <section class="card block">
      <div class="head"><h2>Groups</h2><button class="btn btn-sm" @click="editGroup(null)"><Icon name="plus" :size="15" />New group</button></div>
      <p class="muted small">A group gives its permissions to everyone in it — e.g. <em>Info helpers</em> with Names, or <em>Moderators</em> with Edit details.</p>
      <p v-if="!data.groups.length" class="muted small">No groups yet.</p>
      <ul class="rows">
        <li v-for="g in data.groups" :key="g.id">
          <span class="nm">{{ g.name }}</span>
          <span class="muted small">{{ g.members.length ? g.members.map(userName).join(", ") : "no members" }}</span>
          <span class="sum small">{{ summary(g.permissions) }}</span>
          <span class="acts">
            <button class="btn btn-ghost btn-sm" @click="editGroup(g)">Edit</button>
            <button class="btn btn-ghost btn-sm btn-danger" @click="removeGroup(g)">Delete</button>
          </span>
        </li>
      </ul>
    </section>
  </template>

  <ModalDialog v-if="editing" :title="editing.kind === 'role' ? (editing.isNew ? 'New role' : `Role: ${editing.name}`) : editing.kind === 'group' ? (editing.id ? `Group: ${editing.name}` : 'New group') : `Access: ${editing.username}`"
               width="54rem" @close="editing = null">
    <div class="form">
      <template v-if="editing.kind === 'role'">
        <label v-if="editing.isNew" class="field">Name<input v-model="editing.name" class="input" maxlength="40" placeholder="e.g. moderator"></label>
      </template>
      <template v-else-if="editing.kind === 'group'">
        <label class="field">Name<input v-model="editing.name" class="input" maxlength="60" placeholder="e.g. Info helpers"></label>
        <label class="field">Description<input v-model="editing.description" class="input" maxlength="300"></label>
        <div class="field">Members
          <div class="chips"><label v-for="u in data.users.filter((x) => x.role !== 'admin')" :key="u.id" class="chip-check">
            <input type="checkbox" :checked="editing.members.includes(u.id)" @change="toggleIn(editing.members, u.id)">{{ u.username }}</label></div>
        </div>
      </template>
      <template v-else>
        <label class="field">Role
          <select v-model="editing.role" class="select" :disabled="editing.role === 'admin'">
            <option v-for="r in data.roles.filter((x) => x.name !== 'admin' || editing.role === 'admin')" :key="r.name" :value="r.name">{{ r.name }}</option>
          </select></label>
        <div class="field">Groups
          <div class="chips"><label v-for="g in data.groups" :key="g.id" class="chip-check">
            <input type="checkbox" :checked="editing.groups.includes(g.id)" @change="toggleIn(editing.groups, g.id)">{{ g.name }}</label>
            <span v-if="!data.groups.length" class="muted small">No groups yet</span></div>
        </div>
        <p class="muted small">Ticked and greyed: already given by the role or a group. Tick more to give them to {{ editing.username }} only.</p>
      </template>
      <PermissionPicker v-if="editing.kind !== 'user' || editing.role !== 'admin'" v-model="editing.permissions" :catalog="data.catalog" :inherited="inherited" />
      <p v-else class="muted">The admin can do everything.</p>
      <p v-if="err" class="error-text">{{ err }}</p>
    </div>
    <template #footer>
      <button class="btn" @click="editing = null">Cancel</button>
      <button class="btn btn-primary" :disabled="busy || (editing.kind !== 'user' && !editing.name.trim())" @click="save">{{ busy ? "Saving…" : "Save" }}</button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.block { padding: 1rem 1.125rem; display: flex; flex-direction: column; gap: 0.5rem; }
.head { display: flex; justify-content: space-between; align-items: center; gap: 1rem; }
.head h2 { font-size: 1.05rem; margin: 0; }
.block p { margin: 0; }
.small { font-size: 0.8rem; }
.rows { list-style: none; margin: 0; padding: 0; }
.rows li { display: grid; grid-template-columns: minmax(8rem, 12rem) minmax(6rem, 14rem) 1fr auto; gap: 0.75rem; align-items: center; padding: 0.5rem 0.25rem; border-top: 1px solid var(--border); }
.nm { font-weight: 650; }
.sum { color: var(--text-2); }
.acts { display: flex; gap: 0.25rem; }
@media (max-width: 48rem) { .rows li { grid-template-columns: 1fr auto; } .rows li .sum, .rows li .muted { grid-column: 1 / -1; } }
.form { display: flex; flex-direction: column; gap: 0.875rem; }
.field { display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.84rem; font-weight: 600; }
.chips { display: flex; flex-wrap: wrap; gap: 0.375rem; font-weight: 400; }
.chip-check { display: inline-flex; gap: 0.35rem; align-items: center; padding: 0.25rem 0.6rem; border: 1px solid var(--border); border-radius: 999px; font-size: 0.8rem; cursor: pointer; }
</style>
