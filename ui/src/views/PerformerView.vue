<script setup>
// A performer's profile (/performer/<any of her names>): photo, details and
// bio from ThePornDB, the names she has in the library (aliases), her videos
// and similar performers you have. For the admin: pick or change her
// ThePornDB profile, split a wrongly merged name off, refresh.
import { askConfirm } from "../stores/confirm.js";
import { ref, computed, watch, onBeforeUnmount } from "vue";
import { useRouter } from "vue-router";
import Icon from "../components/Icon.vue";
import VideoCard from "../components/VideoCard.vue";
import PerformerPicker from "../components/PerformerPicker.vue";
import PhotoSlideshow from "../components/PhotoSlideshow.vue";
import PerformerPhotos from "../components/PerformerPhotos.vue";
import PerformerPlaceholder from "../components/PerformerPlaceholder.vue";
import { api, qs } from "../lib/api.js";
import { hasPermission } from "../stores/session.js";
import { onLibraryChange } from "../stores/live.js";
import { setPageTitle } from "../lib/title.js";
import { toast } from "../stores/toast.js";

const props = defineProps({ name: String });
const router = useRouter();
const prof = ref(null);
const videos = ref(null);
const error = ref("");
const changing = ref(false);
const busy = ref("");
const showBio = ref(false);
const showPhotos = ref(false); // "Your photos" panel (2.16.0); always shown once she has some
const canManage = computed(() => hasPermission("performers.manage"));

async function load() {
  try {
    prof.value = await api(`/api/performers/profile?${qs({ name: props.name })}`);
    error.value = "";
    const names = prof.value.names.map((n) => n.name);
    const r = await api(`/api/library?${qs({ statuses: "done", performers: names.length ? names : [props.name], page_size: 60, sort_key: "download_datetime", sort_dir: "desc" })}`);
    videos.value = r;
  } catch (err) { error.value = err.message || "Could not load this performer"; }
}
watch(() => props.name, () => { prof.value = null; videos.value = null; changing.value = false; load(); }, { immediate: true });
watch(prof, (p) => setPageTitle(`/performer/${props.name}`, p?.name || props.name));
const stop = onLibraryChange((changed) => { if (!changed || changed.some((c) => c === "performers" || c.startsWith("performer:"))) load(); });
onBeforeUnmount(stop);

const p = computed(() => prof.value?.performer);
// Whose photos: her ThePornDB profile, or -- not on ThePornDB (2.17.0) -- the
// name's own (photos added by hand, placeholder gender).
const owner = computed(() => p.value || prof.value?.local || null);
async function setGender(gender) {
  busy.value = "gender";
  try { await api("/api/performers/gender", { method: "POST", body: { name: props.name, gender } }); await load(); }
  catch (err) { toast(err.message || "That didn't work", { type: "error" }); }
  finally { busy.value = ""; }
}
const LABELS = {
  gender: "Gender", birthday: "Born", deathday: "Died", birthplace: "Birthplace", nationality: "Nationality",
  ethnicity: "Ethnicity", astrology: "Star sign", height: "Height", weight: "Weight", measurements: "Measurements",
  cupsize: "Cup size", fake_boobs: "Enhanced", hair_colour: "Hair", eye_colour: "Eyes", tattoos: "Tattoos",
  piercings: "Piercings",
};
const facts = computed(() => {
  const e = p.value?.extras || {};
  const out = [];
  if (p.value?.age != null) out.push(["Age", `${p.value.age}${e.deathday ? " (at death)" : ""}`]);
  for (const [k, label] of Object.entries(LABELS)) {
    const v = e[k];
    if (v === null || v === undefined || v === "") continue;
    out.push([label, typeof v === "boolean" ? (v ? "Yes" : "No") : v]);
  }
  if (e.career_start_year) out.push(["Active", `${e.career_start_year}–${e.career_end_year || "now"}`]);
  return out;
});
const otherNames = computed(() => (prof.value?.names || []).filter((n) => !n.main));
const longBio = computed(() => (p.value?.bio || "").length > 420);

function inLibrary() { router.push({ name: "library", query: { performer: prof.value.names.map((n) => n.name) } }); }
async function split(n) {
  if (!(await askConfirm(`“${n.name}” isn't ${p.value.name}? It stops being shown as her, won't be merged into her again, and is looked up on its own.`, { ok: "Not her" }))) return;
  busy.value = n.name;
  try { await api("/api/performers/split", { method: "POST", body: { name: n.name } }); toast(`“${n.name}” split off`); await load(); }
  catch (err) { toast(err.message || "Could not split", { type: "error" }); }
  finally { busy.value = ""; }
}
async function refresh() {
  busy.value = "refresh";
  try { await api(`/api/performers/${p.value.id}/refresh`, { method: "POST" }); toast("Profile refreshed", { type: "success" }); await load(); }
  catch (err) { toast(err.message || "Could not refresh", { type: "error" }); }
  finally { busy.value = ""; }
}
const STATUS_TEXT = {
  review: "Several people on ThePornDB could be her — pick the right one.",
  none: "Not found on ThePornDB.", skipped: "Left without a ThePornDB profile.",
  unchecked: "Not looked up on ThePornDB yet.", unknown: "No videos with this performer.",
};
</script>

<template>
  <div class="page perf">
    <p v-if="error" class="empty"><span class="empty-icon">👤</span>{{ error }}</p>
    <div v-else-if="!prof" class="skeleton" style="height: 18rem; border-radius: var(--radius-lg)" />
    <template v-else>
      <header class="head card">
        <PhotoSlideshow :photos="owner?.photos?.length ? owner.photos : owner?.photo ? [owner.photo] : []" :alt="prof.name"
                        :gender="owner?.gender || 'female'" />
        <div class="head-text">
          <h1>{{ prof.name }}<span v-if="p?.disambiguation" class="muted dis"> ({{ p.disambiguation }})</span></h1>
          <p v-if="p?.aliases?.length" class="muted small">Also known as {{ p.aliases.join(", ") }}</p>
          <p v-if="!p" class="muted">{{ STATUS_TEXT[prof.status] || "" }}<template v-if="!prof.configured"> Add a ThePornDB API key in Server Settings → Credentials to see profiles.</template></p>
          <dl v-if="facts.length" class="facts">
            <div v-for="[k, v] in facts" :key="k" class="fact"><dt>{{ k }}</dt><dd>{{ v }}</dd></div>
          </dl>
          <div class="actions">
            <button class="btn btn-sm btn-primary" @click="inLibrary"><Icon name="library" :size="15" />Her videos in the library</button>
            <a v-if="p?.url" :href="p.url" target="_blank" rel="noopener noreferrer" class="btn btn-sm"><Icon name="external" :size="14" />ThePornDB</a>
            <a v-for="(url, site) in p?.links || {}" :key="site" :href="url" target="_blank" rel="noopener noreferrer" class="btn btn-ghost btn-sm">{{ site }}</a>
            <button v-if="canManage && owner && !owner.custom_photos?.length" class="btn btn-ghost btn-sm" @click="showPhotos = !showPhotos">
              <Icon name="plus" :size="14" />Add your photo</button>
            <span v-if="canManage && !p && prof.local && !prof.local.photos.length" class="gender" role="group" aria-label="Placeholder">
              <span class="muted small">Placeholder</span>
              <button v-for="g in ['female', 'male']" :key="g" class="btn btn-sm" :class="{ 'btn-primary': prof.local.gender === g }"
                      :disabled="busy === 'gender'" @click="setGender(g)">{{ g === "female" ? "Female" : "Male" }}</button>
            </span>
            <template v-if="canManage && prof.configured">
              <button v-if="p" class="btn btn-ghost btn-sm" :disabled="busy === 'refresh'" @click="refresh">{{ busy === "refresh" ? "Refreshing…" : "Refresh" }}</button>
              <button class="btn btn-ghost btn-sm" @click="changing = !changing">{{ p ? "Wrong person?" : "Find her on ThePornDB" }}</button>
            </template>
          </div>
        </div>
      </header>

      <PerformerPhotos v-if="canManage && owner && (showPhotos || owner.custom_photos?.length)" :performer="owner" @changed="load" />

      <section v-if="canManage && prof.configured && (changing || (!p && prof.status === 'review'))" class="card panel">
        <h3 class="sub">{{ p ? `Pick the right profile for “${props.name}”` : "Which one is she?" }}</h3>
        <PerformerPicker :name="props.name" :candidates="prof.candidates" @done="changing = false; load()" />
      </section>

      <section v-if="p?.bio" class="card panel">
        <h3 class="sub">Bio</h3>
        <p class="bio" :class="{ clamp: longBio && !showBio }">{{ p.bio }}</p>
        <button v-if="longBio" class="btn btn-ghost btn-sm more" @click="showBio = !showBio">{{ showBio ? "Show less" : "Show more" }}</button>
      </section>

      <section v-if="otherNames.length" class="card panel">
        <h3 class="sub">Names in your library</h3>
        <p class="muted small">{{ prof.merge_aliases ? "These names are shown as" : "These names belong to" }} {{ prof.name }}<template v-if="!prof.merge_aliases"> (merging aliases is off in Server Settings → Performers, so they keep their own names)</template>.
          If one is someone else, split it off.</p>
        <ul class="names">
          <li v-for="n in prof.names" :key="n.name">
            <span>{{ n.name }}</span><span class="muted small">{{ n.videos }} {{ n.videos === 1 ? "video" : "videos" }}{{ n.main ? " · main name" : n.source === "manual" ? " · linked by hand" : "" }}</span>
            <button v-if="canManage && !n.main" class="btn btn-ghost btn-sm" :disabled="busy === n.name" @click="split(n)">Not her — split off</button>
          </li>
        </ul>
      </section>

      <section v-if="prof.similar.length" class="card panel">
        <h3 class="sub">Similar performers you have</h3>
        <div class="similar">
          <RouterLink v-for="s in prof.similar" :key="s.name" :to="{ name: 'performer', params: { name: s.name } }" class="sim">
            <img v-if="s.photo" :src="s.photo" alt="" loading="lazy" @error="s.photo = null"><span v-else class="small-ph"><PerformerPlaceholder :gender="s.gender" /></span>
            <span>{{ s.name }}</span>
          </RouterLink>
        </div>
      </section>

      <section class="vids">
        <h3 class="sub">Videos ({{ videos?.total ?? "…" }})</h3>
        <div v-if="videos?.items?.length" class="grid"><VideoCard v-for="v in videos.items" :key="v.id" :item="v" /></div>
        <p v-else-if="videos" class="muted">No downloaded videos with her yet.</p>
        <button v-if="videos && videos.total > videos.items.length" class="btn btn-sm" @click="inLibrary">All {{ videos.total }} in the library</button>
      </section>
    </template>
  </div>
</template>

<style scoped>
.head { display: flex; gap: 1.25rem; padding: 1.125rem; align-items: flex-start; }
.nophoto { display: grid; place-items: center; color: var(--text-dim); background: var(--panel-2); }
.head-text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 0.5rem; }
.head-text h1 { font-size: 1.6rem; }
.dis { font-size: 1rem; font-weight: 400; }
.head-text p { margin: 0; }
.small { font-size: 0.8rem; }
.facts { display: grid; grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr)); gap: 0.375rem 1rem; margin: 0.25rem 0; font-size: 0.84rem; }
.facts dt { color: var(--text-dim); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; }
.facts dd { margin: 0; overflow-wrap: anywhere; }
.fact { display: flex; flex-direction: column; gap: 0.1rem; min-width: 0; }
.actions { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.25rem; }
.sub { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); margin: 0; }
.bio { margin: 0; white-space: pre-line; font-size: 0.875rem; color: var(--text-2); }
.bio.clamp { display: -webkit-box; -webkit-line-clamp: 5; -webkit-box-orient: vertical; overflow: hidden; }
.more { align-self: flex-start; }
.names { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.25rem; }
.names li { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; font-size: 0.875rem; }
.similar { display: flex; gap: 0.75rem; flex-wrap: wrap; }
.sim { display: flex; flex-direction: column; align-items: center; gap: 0.25rem; width: 6rem; text-align: center; font-size: 0.8rem; color: var(--text); text-decoration: none; }
.sim img, .small-ph { width: 4.5rem; height: 4.5rem; border-radius: 50%; object-fit: cover; }
.small-ph { display: block; overflow: hidden; }
.gender { display: inline-flex; align-items: center; gap: 0.3rem; margin-left: 0.25rem; }
.vids { display: flex; flex-direction: column; gap: 0.75rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(15.625rem, 1fr)); gap: 1.375rem 1rem; }
@media (max-width: 40rem) {
  .head { flex-direction: column; align-items: center; text-align: center; }
  .actions { justify-content: center; }
  .facts { width: 100%; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .grid { grid-template-columns: repeat(auto-fill, minmax(min(100%, 13.75rem), 1fr)); }
}
</style>
