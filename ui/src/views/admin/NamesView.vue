<script setup>
// Management -> Names: every tag / performer / music name with its video
// count. Rename one, or merge a misspelled one into the right one
// ("aurora vael" -> "aurora vale"): every video using it is changed,
// and the fix can be remembered for new videos (backend/library_mixins/names.py).
// Also: likely typos (names a letter or two apart), the remembered fixes,
// and upper/lower case spellings (SpellingsPanel, moved here from Maintenance).
import { askConfirm } from "../../stores/confirm.js";
import { ref, computed, watch } from "vue";
import Icon from "../../components/Icon.vue";
import SpellingsPanel from "../../components/admin/SpellingsPanel.vue";
import { api, qs } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";

const FIELDS = [{ key: "tags", label: "Tags", one: "tag" }, { key: "performers", label: "Performers", one: "performer" }, { key: "music", label: "Music", one: "music name" }];
const field = ref("tags");
const fieldInfo = computed(() => FIELDS.find((f) => f.key === field.value));
const data = ref(null);
const typos = ref(null);
const error = ref("");
const q = ref("");
const shown = ref(150);
const editing = ref(null); // value being renamed
const target = ref("");
const remember = ref(true);
const busy = ref(false);

// "Not the same" answers for suggested typos stay in this browser.
const DISMISS_KEY = "pmv-names-not-typos";
function loadDismissed() { try { return new Set(JSON.parse(localStorage.getItem(DISMISS_KEY) || "[]")); } catch { return new Set(); } }
const dismissed = ref(loadDismissed());
const pairKey = (p) => `${field.value}|${[p.a, p.b].map((s) => s.toLowerCase()).sort().join("|")}`;
function dismiss(p) {
  dismissed.value = new Set([...dismissed.value, pairKey(p)]);
  try { localStorage.setItem(DISMISS_KEY, JSON.stringify([...dismissed.value])); } catch { /* private mode */ }
}

async function load() {
  error.value = "";
  try {
    const [n, t] = await Promise.all([
      api(`/api/names?${qs({ field: field.value })}`),
      api(`/api/names/typos?${qs({ field: field.value })}`),
    ]);
    data.value = n;
    typos.value = t.pairs;
  } catch (err) { error.value = err.message || "Could not load the names"; }
}
watch(field, () => { data.value = null; typos.value = null; editing.value = null; q.value = ""; shown.value = 150; load(); }, { immediate: true });

const filtered = computed(() => {
  const s = q.value.trim().toLowerCase();
  const all = data.value?.names || [];
  return s ? all.filter((n) => n.value.toLowerCase().includes(s) || n.shown.toLowerCase().includes(s)) : all;
});
const visibleTypos = computed(() => (typos.value || []).filter((p) => !dismissed.value.has(pairKey(p))));
const countOf = (value) => data.value?.names.find((n) => n.value.toLowerCase() === value.trim().toLowerCase())?.count || 0;

function startEdit(n) { editing.value = n.value; target.value = n.shown !== n.value ? n.shown : n.value; remember.value = true; }

async function rename(old, to, rememberIt = remember.value) {
  to = to.trim();
  if (!to || to === old) return;
  const into = countOf(to);
  const msg = into
    ? `Merge “${old}” into “${to}”? Every video with “${old}” gets “${to}” instead (${to} already has ${into} ${into === 1 ? "video" : "videos"}).`
    : `Rename “${old}” to “${to}” in every video?`;
  if (!(await askConfirm(msg, { ok: into ? "Merge" : "Rename" }))) return;
  busy.value = true;
  try {
    const r = await api("/api/names/rename", { method: "POST", body: { field: field.value, old, new: to, remember: rememberIt } });
    toast(`“${old}” → “${to}” in ${r.videos} ${r.videos === 1 ? "video" : "videos"}`, { type: "success" });
    editing.value = null;
    await load();
  } catch (err) { toast(err.message || "Could not rename", { type: "error" }); }
  finally { busy.value = false; }
}
async function forget(rule) {
  try {
    await api("/api/names/rules/delete", { method: "POST", body: { field: field.value, old: rule.old } });
    toast("Fix forgotten — videos keep their current names");
    await load();
  } catch (err) { toast(err.message || "That didn't work", { type: "error" }); }
}
</script>

<template>
  <div class="page names">
    <div class="top">
      <p class="muted small intro">Rename a {{ fieldInfo.one }}, or merge a misspelled one into the right one: every video that has it is changed.
        With <strong>Remember</strong>, the same misspelling is shown fixed on videos added later too.</p>
      <div class="seg" role="tablist">
        <button v-for="f in FIELDS" :key="f.key" type="button" class="btn btn-sm" :class="{ 'btn-primary': field === f.key }" @click="field = f.key">{{ f.label }}</button>
      </div>
    </div>
    <p v-if="error" class="error-text">{{ error }}</p>

    <section v-if="visibleTypos.length" class="card block">
      <h2>Possible typos <span class="count">{{ visibleTypos.length }}</span></h2>
      <p class="muted small">Names only a letter or two apart. Merge the wrong one into the right one, or mark them as different.</p>
      <ul class="pairs">
        <li v-for="p in visibleTypos.slice(0, 50)" :key="pairKey(p)">
          <span class="pair"><span class="nm">{{ p.b }}</span><span class="muted">{{ p.b_count }}</span>
            <Icon name="right" :size="14" /><span class="nm good">{{ p.a }}</span><span class="muted">{{ p.a_count }}</span></span>
          <span class="acts">
            <button class="btn btn-sm btn-primary" :disabled="busy" @click="rename(p.b, p.a, true)">Merge into “{{ p.a }}”</button>
            <button class="btn btn-ghost btn-sm" :disabled="busy" @click="rename(p.a, p.b, true)">Other way</button>
            <button class="btn btn-ghost btn-sm" @click="dismiss(p)">Not the same</button>
          </span>
        </li>
      </ul>
    </section>

    <section class="card block">
      <div class="block-head">
        <h2>All {{ fieldInfo.label.toLowerCase() }} <span v-if="data" class="count">{{ data.names.length }}</span></h2>
        <input v-model="q" class="input search" type="search" :placeholder="`Find a ${fieldInfo.one}`" :aria-label="`Find a ${fieldInfo.one}`">
      </div>
      <div v-if="!data" class="skeleton" style="height: 6rem; border-radius: var(--radius)" />
      <p v-else-if="!filtered.length" class="muted small">Nothing matches.</p>
      <ul v-else class="list">
        <li v-for="n in filtered.slice(0, shown)" :key="n.value">
          <template v-if="editing === n.value">
            <form class="edit" @submit.prevent="rename(n.value, target)">
              <span class="nm">{{ n.value }}</span><Icon name="right" :size="14" />
              <input v-model="target" class="input" list="names-all" maxlength="120" aria-label="New name" autofocus>
              <label class="switch small"><input v-model="remember" type="checkbox">Remember</label>
              <button class="btn btn-sm btn-primary" :disabled="busy || !target.trim() || target.trim() === n.value">
                {{ countOf(target) && target.trim().toLowerCase() !== n.value.toLowerCase() ? "Merge" : "Rename" }}</button>
              <button type="button" class="btn btn-ghost btn-sm" @click="editing = null">Cancel</button>
            </form>
          </template>
          <template v-else>
            <span class="nm">{{ n.value }}</span>
            <span v-if="n.shown !== n.value" class="muted small">shown as “{{ n.shown }}”</span>
            <span class="muted small cnt">{{ n.count }} {{ n.count === 1 ? "video" : "videos" }}</span>
            <button class="btn btn-ghost btn-sm" @click="startEdit(n)"><Icon name="edit" :size="14" />Rename / merge</button>
          </template>
        </li>
      </ul>
      <datalist id="names-all"><option v-for="n in (data?.names || []).slice(0, 2000)" :key="n.value" :value="n.value" /></datalist>
      <button v-if="filtered.length > shown" class="btn btn-sm more" @click="shown += 300">Show more ({{ filtered.length - shown }} left)</button>
    </section>

    <section v-if="data?.rules?.length" class="card block">
      <h2>Remembered fixes <span class="count">{{ data.rules.length }}</span></h2>
      <p class="muted small">These misspellings are shown fixed wherever they turn up (new downloads, syncs). Forgetting one doesn't change any video.</p>
      <ul class="list">
        <li v-for="r in data.rules" :key="r.old">
          <span class="nm">{{ r.old }}</span><Icon name="right" :size="14" /><span class="nm good">{{ r.new }}</span>
          <button class="btn btn-ghost btn-sm" style="margin-left: auto" @click="forget(r)">Forget</button>
        </li>
      </ul>
    </section>

    <SpellingsPanel />
  </div>
</template>

<style scoped>
.top { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; flex-wrap: wrap; }
.intro { margin: 0; max-width: 46rem; }
.seg { display: flex; gap: 0.375rem; }
.block { padding: 1rem 1.125rem; display: flex; flex-direction: column; gap: 0.625rem; }
.block h2 { font-size: 1.05rem; display: flex; align-items: center; gap: 0.5rem; }
.block p { margin: 0; }
.block-head { display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; }
.search { max-width: 18rem; height: 2.25rem; }
.small { font-size: 0.8rem; }
.count { font-size: 0.72rem; font-weight: 700; background: var(--panel-hover); border-radius: 999px; padding: 0.05rem 0.5rem; color: var(--text-2); }
.list, .pairs { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; max-height: 34rem; overflow-y: auto; }
.list li, .pairs li { display: flex; align-items: center; gap: 0.625rem; padding: 0.4rem 0.5rem; border-bottom: 1px solid var(--border); flex-wrap: wrap; min-height: 2.6rem; }
.nm { font-weight: 600; overflow-wrap: anywhere; }
.nm.good { color: var(--success); }
.cnt { margin-left: auto; }
.pair { display: inline-flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; flex: 1; min-width: 14rem; }
.acts { display: flex; gap: 0.375rem; flex-wrap: wrap; }
.edit { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; width: 100%; }
.edit .input { flex: 1 1 14rem; height: 2.1rem; }
.more { align-self: flex-start; }
</style>
