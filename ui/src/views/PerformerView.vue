<script setup>
// A performer's profile (/performer/<any of their names>): photo, details and
// bio from ThePornDB, the names they have in the library (aliases), their videos
// and similar performers you have. For the admin: pick or change the
// ThePornDB profile, split a wrongly merged name off, refresh.
import { t } from "../lib/i18n.js";
import { askConfirm } from "../stores/confirm.js";
import { ref, computed, watch, onBeforeUnmount } from "vue";
import { useRouter } from "vue-router";
import Icon from "../components/Icon.vue";
import VideoCard from "../components/VideoCard.vue";
import PerformerPicker from "../components/PerformerPicker.vue";
import PhotoSlideshow from "../components/PhotoSlideshow.vue";
import PerformerPhotos from "../components/PerformerPhotos.vue";
import PerformerDetailsForm from "../components/PerformerDetailsForm.vue";
import StashBoxPanel from "../components/StashBoxPanel.vue";
import PerformerPlaceholder from "../components/PerformerPlaceholder.vue";
import { api, qs } from "../lib/api.js";
import { hasPermission } from "../stores/session.js";
import { onLibraryChange } from "../stores/live.js";
import { setPageTitle } from "../lib/title.js";
import { toast } from "../stores/toast.js";
import { isFavorite, setFavorite, loadBadges } from "../stores/performerBadges.js";

const props = defineProps({ name: String });
const router = useRouter();
const prof = ref(null);
const videos = ref(null);
const error = ref("");
const changing = ref(false);
const busy = ref("");
const showBio = ref(false);
const showPhotos = ref(false); // t("Your photos") panel (2.16.0); always shown once there are some
const canManage = computed(() => hasPermission("performers.manage"));

async function load() {
  try {
    prof.value = await api(`/api/performers/profile?${qs({ name: props.name })}`);
    error.value = "";
    const names = prof.value.names.map((n) => n.name);
    const r = await api(`/api/library?${qs({ statuses: "done", performers: names.length ? names : [props.name], page_size: 60, sort_key: "download_datetime", sort_dir: "desc" })}`);
    videos.value = r;
  } catch (err) { error.value = err.message || t("Could not load this performer"); }
}
watch(() => props.name, () => { prof.value = null; videos.value = null; changing.value = false; load(); }, { immediate: true });
watch(prof, (p) => setPageTitle(`/performer/${props.name}`, p?.name || props.name));
const stop = onLibraryChange((changed) => { if (!changed || changed.some((c) => c === "performers" || c.startsWith("performer:"))) load(); });
onBeforeUnmount(stop);

const p = computed(() => prof.value?.performer);
// Whose photos: the ThePornDB profile, or -- not on ThePornDB (2.17.0) -- the
// name's own (photos added by hand, placeholder gender).
const owner = computed(() => p.value || prof.value?.local || null);
// 3.9.0: a performer not on ThePornDB can have details typed in (PerformerDetailsForm)
const local = computed(() => (!p.value && prof.value?.local) || null);
const editingDetails = ref(false);
const shownAliases = computed(() => p.value?.aliases || local.value?.aliases || []);
const shownBio = computed(() => p.value?.bio || local.value?.bio || "");
const localLinks = computed(() => local.value?.links || p.value?.more_links || []);
const findingSb = ref(false); // 3.9.0: FansDB / StashDB panel (StashBoxPanel)
const sbLinked = computed(() => prof.value?.stashbox || []);
async function setGender(gender) {
  busy.value = "gender";
  try { await api("/api/performers/gender", { method: "POST", body: { name: props.name, gender } }); await load(); loadBadges(true); }
  catch (err) { toast(err.message || t("That didn't work"), { type: "error" }); }
  finally { busy.value = ""; }
}
const LABELS = {
  gender: t("Gender"), birthday: t("Born"), deathday: t("Died"), birthplace: t("Birthplace"), nationality: t("Nationality"),
  ethnicity: t("Ethnicity"), astrology: t("Star sign"), height: t("Height"), weight: t("Weight"), measurements: t("Measurements"),
  cupsize: t("Cup size"), fake_boobs: t("Enhanced"), hair_colour: t("Hair"), eye_colour: t("Eyes"), tattoos: t("Tattoos"),
  piercings: t("Piercings"),
};
const facts = computed(() => {
  const src = p.value || local.value;
  const e = src?.extras || {};
  const out = [];
  if (src?.age != null) out.push([t("Age"), e.deathday ? t("{age} (at death)", { age: src.age }) : String(src.age)]);
  for (const [k, label] of Object.entries(LABELS)) {
    const v = e[k];
    if (v === null || v === undefined || v === "") continue;
    out.push([label, typeof v === "boolean" ? (v ? t("Yes") : t("No")) : v]);
  }
  if (e.career_start_year) out.push([t("Active"), e.career_end_year ? `${e.career_start_year}–${e.career_end_year}` : t("{from} – now", { from: e.career_start_year })]);
  return out;
});
const otherNames = computed(() => (prof.value?.names || []).filter((n) => !n.main));
// 3.5.0: favourite performer -- all their names in the library count.
const allNames = computed(() => [...new Set([props.name, prof.value?.name, ...(prof.value?.names || []).map((n) => n.name)].filter(Boolean))]);
const favorite = computed(() => allNames.value.some((n) => isFavorite(n)));
const longBio = computed(() => shownBio.value.length > 420);

function inLibrary() { router.push({ name: "library", query: { performer: prof.value.names.map((n) => n.name) } }); }
async function split(n) {
  if (!(await askConfirm(t("“{name}” isn't {performer}? It stops being shown as {performer}, won't be merged into that profile again, and is looked up on its own.", { name: n.name, performer: p.value.name }), { ok: t("Someone else") }))) return;
  busy.value = n.name;
  try { await api("/api/performers/split", { method: "POST", body: { name: n.name } }); toast(t("“{name}” split off", { name: n.name })); await load(); }
  catch (err) { toast(err.message || t("Could not split"), { type: "error" }); }
  finally { busy.value = ""; }
}
async function refresh() {
  busy.value = "refresh";
  try { await api(`/api/performers/${p.value.id}/refresh`, { method: "POST" }); toast(t("Profile refreshed"), { type: "success" }); await load(); }
  catch (err) { toast(err.message || t("Could not refresh"), { type: "error" }); }
  finally { busy.value = ""; }
}
const STATUS_TEXT = {
  review: t("Several people on ThePornDB could be this performer — pick the right one."),
  none: t("Not found on ThePornDB."), skipped: t("Left without a ThePornDB profile."),
  unchecked: t("Not looked up on ThePornDB yet."), unknown: t("No videos with this performer."),
};
</script>

<template>
  <div class="page perf">
    <p v-if="error" class="empty"><span class="empty-icon">👤</span>{{ error }}</p>
    <div v-else-if="!prof" class="skeleton" style="height: 18rem; border-radius: var(--radius-lg)" />
    <template v-else>
      <header class="head card">
        <PhotoSlideshow :photos="owner?.photos?.length ? owner.photos : owner?.photo ? [owner.photo] : []" :alt="prof.name"
                        :gender="owner?.gender || 'unknown'" />
        <div class="head-text">
          <h1>{{ prof.name }}<span v-if="p?.disambiguation" class="muted dis"> ({{ p.disambiguation }})</span></h1>
          <p v-if="shownAliases.length" class="muted small">{{ $t("Also known as {names}", { names: shownAliases.join(", ") }) }}</p>
          <p v-if="!p" class="muted">{{ STATUS_TEXT[prof.status] || "" }}<template v-if="!prof.configured">{{ " " + $t("Add a ThePornDB API key in Server Settings → Credentials to see profiles.") }}</template></p>
          <dl v-if="facts.length" class="facts">
            <div v-for="[k, v] in facts" :key="k" class="fact"><dt>{{ k }}</dt><dd>{{ v }}</dd></div>
          </dl>
          <div class="actions">
            <button class="btn btn-sm btn-primary" @click="inLibrary"><Icon name="library" :size="15" />{{ $t("Videos in the library") }}</button>
            <button class="btn btn-sm fav-btn" :class="{ on: favorite }" :aria-pressed="favorite"
                    :title="favorite ? $t('Remove from your favourite performers') : $t('Add to your favourite performers (♥ on their badges; Library → sort Favourite performers)')"
                    @click="setFavorite(allNames, !favorite, prof.name)"><Icon name="heart" :size="14" />{{ favorite ? $t("Favourite") : $t("Add to favourites") }}</button>
            <RouterLink :to="{ name: 'wall', query: { p: prof.name } }" class="btn btn-sm" :title="$t('Them and their videos, as a graph')"><Icon name="share" :size="14" />{{ $t("On The Wall") }}</RouterLink>
            <a v-if="p?.url" :href="p.url" target="_blank" rel="noopener noreferrer" class="btn btn-sm"><Icon name="external" :size="14" />ThePornDB</a>
            <a v-for="(url, site) in p?.links || {}" :key="site" :href="url" target="_blank" rel="noopener noreferrer" class="btn btn-ghost btn-sm">{{ site }}</a>
            <a v-for="l in sbLinked" :key="l.box" :href="l.url" target="_blank" rel="noopener noreferrer" class="btn btn-sm"><Icon name="external" :size="14" />{{ l.label }}</a>
            <a v-for="l in localLinks" :key="l.url" :href="l.url" target="_blank" rel="noopener noreferrer nofollow" class="btn btn-ghost btn-sm"><Icon name="external" :size="13" />{{ l.label }}</a>
            <button v-if="canManage && local" class="btn btn-ghost btn-sm" @click="editingDetails = !editingDetails"><Icon name="edit" :size="14" />{{ $t("Edit details") }}</button>
            <button v-if="canManage && owner && !owner.custom_photos?.length" class="btn btn-ghost btn-sm" @click="showPhotos = !showPhotos">
              <Icon name="plus" :size="14" />{{ $t("Add your photo") }}</button>
            <span v-if="canManage && !p && prof.local" class="gender" role="group" :aria-label="$t('Gender')">
              <span class="muted small" :title="$t('Badge colour, and the placeholder picture without a photo')">{{ $t("Gender") }}</span>
              <button v-for="g in ['female', 'male', 'trans']" :key="g" class="btn btn-sm" :class="{ 'btn-primary': prof.local.gender === g }"
                      :disabled="busy === 'gender'" @click="setGender(g)">{{ { female: $t("Female"), male: $t("Male"), trans: $t("Trans") }[g] }}</button>
            </span>
            <template v-if="canManage && prof.configured">
              <button v-if="p" class="btn btn-ghost btn-sm" :disabled="busy === 'refresh'" @click="refresh">{{ busy === "refresh" ? $t("Refreshing…") : $t("Refresh") }}</button>
              <button class="btn btn-ghost btn-sm" @click="changing = !changing">{{ p ? $t("Wrong person?") : $t("Find on ThePornDB") }}</button>
            </template>
            <button v-if="canManage && prof.status !== 'unknown'" class="btn btn-ghost btn-sm" @click="findingSb = !findingSb">{{ $t("FansDB / StashDB") }}</button>
          </div>
        </div>
      </header>

      <PerformerDetailsForm v-if="editingDetails && local" :name="props.name" :details="local.details" @saved="editingDetails = false; load()" @cancel="editingDetails = false" />
      <StashBoxPanel v-if="canManage && findingSb" :name="props.name" :sites="prof.stashbox_sites || []"
                     :linked="sbLinked" :on-tpdb="!!p" @changed="load" />
      <PerformerPhotos v-if="canManage && owner && (showPhotos || owner.custom_photos?.length)" :performer="owner" :several="!p" @changed="load" />

      <section v-if="canManage && prof.configured && (changing || (!p && prof.status === 'review'))" class="card panel">
        <h3 class="sub">{{ p ? $t("Pick the right profile for “{name}”", { name: props.name }) : $t("Which profile is it?") }}</h3>
        <PerformerPicker :name="props.name" :candidates="prof.candidates" @done="changing = false; load()" />
      </section>

      <section v-if="shownBio" class="card panel">
        <h3 class="sub">{{ $t("Bio") }}</h3>
        <p class="bio" :class="{ clamp: longBio && !showBio }">{{ shownBio }}</p>
        <button v-if="longBio" class="btn btn-ghost btn-sm more" @click="showBio = !showBio">{{ showBio ? $t("Show less") : $t("Show more") }}</button>
      </section>

      <section v-if="otherNames.length" class="card panel">
        <h3 class="sub">{{ $t("Names in your library") }}</h3>
        <p class="muted small">{{ prof.merge_aliases ? $t("These names are shown as {name}. If one is someone else, split it off.", { name: prof.name })
          : $t("These names belong to {name} (merging aliases is off in Server Settings → Performers, so they keep their own names). If one is someone else, split it off.", { name: prof.name }) }}</p>
        <ul class="names">
          <li v-for="n in prof.names" :key="n.name">
            <span>{{ n.name }}</span><span class="muted small">{{ $tn("{n} video", "{n} videos", n.videos) }}{{ n.main ? " · " + $t("main name") : n.source === "manual" ? " · " + $t("linked by hand") : "" }}</span>
            <button v-if="canManage && !n.main" class="btn btn-ghost btn-sm" :disabled="busy === n.name" @click="split(n)">{{ $t("Someone else — split off") }}</button>
          </li>
        </ul>
      </section>

      <section v-if="prof.similar.length" class="card panel">
        <h3 class="sub">{{ $t("Similar performers you have") }}</h3>
        <div class="similar">
          <RouterLink v-for="s in prof.similar" :key="s.name" :to="{ name: 'performer', params: { name: s.name } }" class="sim">
            <img v-if="s.photo" :src="s.photo" alt="" loading="lazy" @error="s.photo = null"><span v-else class="small-ph"><PerformerPlaceholder :gender="s.gender" /></span>
            <span>{{ s.name }}</span>
          </RouterLink>
        </div>
      </section>

      <section class="vids">
        <h3 class="sub">{{ $t("Videos ({n})", { n: videos?.total ?? "…" }) }}</h3>
        <div v-if="videos?.items?.length" class="grid"><VideoCard v-for="v in videos.items" :key="v.id" :item="v" /></div>
        <p v-else-if="videos" class="muted">{{ $t("No downloaded videos with this performer yet.") }}</p>
        <button v-if="videos && videos.total > videos.items.length" class="btn btn-sm" @click="inLibrary">{{ $t("All {n} in the library", { n: videos.total }) }}</button>
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
.gender { display: inline-flex; align-items: center; gap: 0.3rem; margin-inline-start: 0.25rem; }
.vids { display: flex; flex-direction: column; gap: 0.75rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(15.625rem, 1fr)); gap: 1.375rem 1rem; }
@media (max-width: 40rem) {
  .head { flex-direction: column; align-items: center; text-align: center; }
  .actions { justify-content: center; }
  .facts { width: 100%; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .grid { grid-template-columns: repeat(auto-fill, minmax(min(100%, 13.75rem), 1fr)); }
}
.fav-btn.on { color: var(--chip-female, #ec4899); border-color: currentColor; }
</style>
