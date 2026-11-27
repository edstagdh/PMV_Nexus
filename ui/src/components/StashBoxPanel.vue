<script setup>
// 3.9.0: find a performer on FansDB or StashDB by hand (ThePornDB stays the automatic match).
// Not on ThePornDB: linking fills the details nobody typed in, adds their links and saves a
// few of their photos. On ThePornDB: their page and links are added, and what ThePornDB leaves
// empty. GET /api/performers/stashbox/search, POST .../link | .../unlink (backend/stashbox.py).
import { t } from "../lib/i18n.js";
import { ref, computed } from "vue";
import Icon from "./Icon.vue";
import { api, qs } from "../lib/api.js";
import { toast } from "../stores/toast.js";
import { askConfirm } from "../stores/confirm.js";

const props = defineProps({ name: String, sites: { type: Array, default: () => [] }, linked: { type: Array, default: () => [] },
  onTpdb: Boolean });
const emit = defineEmits(["changed"]);
const ready = computed(() => props.sites.filter((s) => s.configured));
const box = ref(ready.value[0]?.box || "fansdb");
const q = ref(props.name);
const results = ref(null);
const busy = ref("");
const withPhotos = ref(true);
const label = (b) => props.sites.find((s) => s.box === b)?.label || b;

async function search() {
  busy.value = "search"; results.value = null;
  try { results.value = (await api(`/api/performers/stashbox/search?${qs({ box: box.value, q: q.value.trim() })}`)).results; }
  catch (err) { toast(err.message || t("Search failed"), { type: "error" }); }
  finally { busy.value = ""; }
}
async function link(b, id, photos) {
  busy.value = `${b}:${id}`;
  try {
    const r = await api("/api/performers/stashbox/link", { method: "POST", body: { name: props.name, box: b, id, photos } });
    toast(r.on_tpdb ? t("Linked to {site}", { site: label(b) })
      : t("Linked to {site}: {n} details filled in, {p} photos saved", { site: label(b), n: r.filled, p: r.photos }), { type: "success" });
    results.value = null;
    emit("changed");
  } catch (err) { toast(err.message || t("Could not link"), { type: "error" }); }
  finally { busy.value = ""; }
}
async function unlink(l) {
  if (!(await askConfirm(t("Unlink {name} from {site}? Details and photos already copied stay; edit or remove them by hand.", { name: props.name, site: l.label }), { ok: t("Unlink") }))) return;
  busy.value = `un:${l.box}`;
  try { await api("/api/performers/stashbox/unlink", { method: "POST", body: { name: props.name, box: l.box } }); emit("changed"); }
  catch (err) { toast(err.message || t("That didn't work"), { type: "error" }); }
  finally { busy.value = ""; }
}
</script>

<template>
  <section class="card panel sb">
    <h3 class="sub">{{ $t("FansDB / StashDB") }}</h3>
    <ul v-if="linked.length" class="linked">
      <li v-for="l in linked" :key="l.box">
        <strong>{{ l.label }}</strong><span>{{ l.name }}</span>
        <a v-if="l.url" :href="l.url" target="_blank" rel="noopener noreferrer" class="btn btn-ghost btn-icon btn-sm" :title="$t('Open on {site}', { site: l.label })"><Icon name="external" :size="14" /></a>
        <button class="btn btn-ghost btn-sm" :disabled="!!busy" :title="$t('Fetch the profile again (fills only what is still empty)')" @click="link(l.box, l.id, false)">{{ busy === `${l.box}:${l.id}` ? $t("Refreshing…") : $t("Refresh") }}</button>
        <button class="btn btn-ghost btn-sm" :disabled="!!busy" @click="unlink(l)">{{ $t("Unlink") }}</button>
      </li>
    </ul>
    <p v-if="!ready.length" class="muted small">{{ $t("Add a FansDB or StashDB API key in Server Settings → Credentials to search them.") }}</p>
    <template v-else>
      <form class="search" @submit.prevent="search">
        <select v-model="box" class="select" :aria-label="$t('Site')">
          <option v-for="s in ready" :key="s.box" :value="s.box">{{ s.label }}</option>
        </select>
        <input v-model="q" class="input" maxlength="100" :placeholder="$t('Name to search')" :aria-label="$t('Name to search')">
        <button class="btn btn-sm" :disabled="!!busy || !q.trim()">{{ busy === "search" ? $t("Searching…") : $t("Search") }}</button>
      </form>
      <label v-if="!onTpdb" class="photos-opt"><input v-model="withPhotos" type="checkbox"> {{ $t("Also save up to 5 of their photos") }}</label>
      <ul v-if="results?.length" class="cands">
        <li v-for="c in results" :key="c.id">
          <img v-if="c.image" :src="c.image" alt="" loading="lazy" referrerpolicy="no-referrer">
          <span v-else class="noimg"><Icon name="user" :size="20" /></span>
          <span class="info">
            <strong>{{ c.name }}</strong><span v-if="c.disambiguation" class="muted"> ({{ c.disambiguation }})</span>
            <span class="muted small">{{ [c.gender, c.birthday, c.country, c.scenes != null ? $tn("{n} scene", "{n} scenes", c.scenes) : ""].filter(Boolean).join(" · ") }}</span>
            <span v-if="c.aliases?.length" class="muted small">{{ $t("Also: {names}", { names: c.aliases.slice(0, 6).join(", ") }) }}</span>
          </span>
          <a :href="c.url" target="_blank" rel="noopener noreferrer" class="btn btn-ghost btn-icon btn-sm" :title="$t('Open on {site}', { site: label(c.box) })"><Icon name="external" :size="14" /></a>
          <button class="btn btn-sm btn-primary" :disabled="!!busy" @click="link(c.box, c.id, withPhotos && !onTpdb)">{{ busy === `${c.box}:${c.id}` ? $t("Linking…") : $t("This is the one") }}</button>
        </li>
      </ul>
      <p v-else-if="results" class="muted small">{{ $t("Nothing found on {site}.", { site: label(box) }) }}</p>
    </template>
  </section>
</template>

<style scoped>
.sb { display: flex; flex-direction: column; gap: 0.6rem; }
.small { font-size: 0.8rem; margin: 0; }
.linked, .cands { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.4rem; }
.linked li { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; font-size: 0.875rem; }
.search { display: flex; gap: 0.4rem; flex-wrap: wrap; }
.search .input { flex: 1; min-width: 10rem; }
.photos-opt { font-size: 0.85rem; display: flex; gap: 0.4rem; align-items: center; }
.cands li { display: flex; align-items: center; gap: 0.75rem; }
.cands img, .noimg { width: 3rem; height: 3rem; border-radius: 50%; object-fit: cover; flex: none; }
.noimg { display: grid; place-items: center; background: var(--panel-2); color: var(--text-dim); }
.info { flex: 1; min-width: 0; display: flex; flex-direction: column; }
@media (max-width: 40rem) { .cands li { flex-wrap: wrap; } }
</style>
