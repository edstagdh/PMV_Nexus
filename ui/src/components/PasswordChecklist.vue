<script setup>
// Live checklist of the password policy (rules come from the server,
// GET /api/auth/password-policy; the server checks again on save).
import { ref, onMounted } from "vue";
import { api } from "../lib/api.js";

const props = defineProps({ password: String, username: String });
const policy = ref(null);
onMounted(async () => { try { policy.value = await api("/api/auth/password-policy"); } catch { /* server still checks */ } });

function simpleRun(p) {
  const s = p.toLowerCase();
  if (new Set(s).size === 1) return true;
  const steps = new Set([...s].slice(1).map((c, i) => c.charCodeAt(0) - s.charCodeAt(i)));
  return steps.size === 1 && (steps.has(1) || steps.has(-1));
}
// true = met, false = not met, null = not judged yet (empty box / checked by the server)
function state(id) {
  const p = props.password || "";
  if (!p) return null;
  const pol = policy.value;
  if (id === "length") return p.length >= pol.min_length && p.length <= pol.max_length;
  if (id === "mix") return /\p{L}/u.test(p) && /\d/.test(p);
  if (id === "username") { const u = (props.username || "").trim().toLowerCase(); return !(u.length >= 3 && p.toLowerCase().includes(u)); }
  if (id === "common") return simpleRun(p) ? false : null;
  return null;
}
</script>

<template>
  <ul v-if="policy" class="checklist" aria-label="Password rules">
    <li v-for="r in policy.rules" :key="r.id" :class="{ ok: state(r.id) === true, bad: state(r.id) === false }">
      <span class="mark" aria-hidden="true">{{ state(r.id) === true ? "✓" : state(r.id) === false ? "✗" : "•" }}</span>{{ r.text }}
    </li>
    <li class="note">{{ policy.note }}</li>
  </ul>
</template>

<style scoped>
.checklist { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.2rem; font-size: 0.78rem; color: var(--text-2); }
.mark { display: inline-block; width: 1.1rem; font-weight: 700; color: var(--text-dim); }
.ok { color: var(--success); }
.ok .mark { color: var(--success); }
.bad { color: var(--danger); }
.bad .mark { color: var(--danger); }
.note { color: var(--text-dim); margin-top: 0.2rem; }
</style>
