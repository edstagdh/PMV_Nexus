<script setup>
import { t, tn, N_ } from "../lib/i18n.js";
import { ref, computed, watch, onBeforeUnmount } from "vue";
import { useRouter } from "vue-router";
import Icon from "../components/Icon.vue";
import MediaInfoDialog from "../components/dialogs/MediaInfoDialog.vue";
import PlayQueue from "../components/PlayQueue.vue";
import { player, openOnPage, leavePage, parkPlayer, seekPlayer } from "../stores/player.js";
import { setPageTitle } from "../lib/title.js";
import VideoCard from "../components/VideoCard.vue";
import RelatedList from "../components/RelatedList.vue";
import ActionBar from "../components/ActionBar.vue";
import FavoriteButton from "../components/FavoriteButton.vue";
import VideoFrames from "../components/VideoFrames.vue";
import FacesOnVideo from "../components/faces/FacesOnVideo.vue";
import { api, qs } from "../lib/api.js";
import { toast } from "../stores/toast.js";
import { normalizeMetadata } from "../lib/metadata.js";
import { thumbUrl, compactNumber, workStatus, formatDateTime, formatDuration } from "../lib/format.js";
import { onLibraryChange } from "../stores/live.js";
import { session, hasPermission } from "../stores/session.js";
import { openDialog } from "../stores/dialogs.js";
import { discover } from "../lib/discover.js";
import { perfClass, perfTitle } from "../stores/performerBadges.js";
import { makers, creatorText, uploadedBy } from "../lib/creators.js";

const props = defineProps({ id: String });
const router = useRouter();
const item = ref(null);
const meta = ref(null);
const syncInfo = ref({});
const error = ref("");
const related = ref([]);
const shared = ref([]); // 3.9.0: videos with the same footage (GET .../shared-scenes)
const sameCreator = ref([]);
const showFullDescription = ref(false);

async function load(full = true) {
  const id = props.id;
  try {
    const [it, md] = await Promise.all([
      api(`/api/library/${encodeURIComponent(id)}/item`),
      full ? api(`/api/library/${encodeURIComponent(id)}/metadata`).catch(() => null) : Promise.resolve(undefined),
    ]);
    if (id !== props.id) return;
    const statusChanged = item.value && item.value.status !== it.status;
    // Repair/Update, a sync or an edit rewrites the metadata file (views, likes, description...):
    // read it again whenever the video itself changed, so the page shows the new details (3.2.0).
    const changed = item.value && item.value.updated_at !== it.updated_at;
    item.value = it;
    if (md !== undefined) { meta.value = md; syncInfo.value = md?._site_sync || {}; }
    if (statusChanged && !full) load(true);
    else if (changed && !full) reloadMetadata(id);
    error.value = "";
    if (full) loadNeighbours(it);
  } catch (err) {
    if (id === props.id) error.value = err.status === 404 ? t("This video is not in the library.") : err.message;
  }
}

async function reloadMetadata(id) {
  const md = await api(`/api/library/${encodeURIComponent(id)}/metadata`).catch(() => null);
  if (id !== props.id) return;
  meta.value = md;
  syncInfo.value = md?._site_sync || {};
}

async function loadNeighbours(it) {
  const id = it.id;
  const [rel, same, sh] = await Promise.all([
    api(`/api/library/${encodeURIComponent(id)}/related?limit=${session.prefs.relatedCount || 1}&order=${session.prefs.relatedOrder || "best"}`).catch(() => ({ items: [] })),
    makers(it).length
      ? api(`/api/library?${qs({ statuses: "done", creators: makers(it), page_size: 9, sort_key: "download_datetime", sort_dir: "desc" })}`).catch(() => ({ items: [] }))
      : Promise.resolve({ items: [] }),
    api(`/api/library/${encodeURIComponent(id)}/shared-scenes`).catch(() => ({ items: [] })),
  ]);
  if (id !== props.id) return;
  related.value = rel.items;
  shared.value = sh.items || [];
  sameCreator.value = (same.items || []).filter((x) => x.id !== id).slice(0, 8);
}

// The app's one player (stores/player.js) is placed into `slot` when the
// video can play; leaving the page hands it to the mini-player.
const slot = ref(null);
watch(() => props.id, (id, old) => {
  if (old && player.mode === "page") parkPlayer(); // keep it out of the old page's DOM while the new one loads
  item.value = null; meta.value = null; related.value = []; shared.value = []; sameCreator.value = []; showFullDescription.value = false;
  load();
}, { immediate: true });
const stop = onLibraryChange((changed) => { if (!changed || changed.includes(props.id)) load(false); });
onBeforeUnmount(() => { stop(); leavePage(); });

// One column (phones, narrow windows): the playlist goes right under the
// player instead of after the details and the creator's other videos.
const oneColumn = window.matchMedia("(max-width: 75rem)");
const narrow = ref(oneColumn.matches);
const onColumns = (e) => { narrow.value = e.matches; };
oneColumn.addEventListener("change", onColumns);
onBeforeUnmount(() => oneColumn.removeEventListener("change", onColumns));

const view = computed(() => normalizeMetadata(meta.value));
const title = computed(() => item.value?.title || view.value?.title || item.value?.url || "");
// The library keeps the full vote-ranked tag list; the metadata file only has the site's top 5.
const tags = computed(() => (item.value?.tags?.length || edited("tags") ? item.value.tags : view.value?.tags || []));
const performers = computed(() => (item.value?.performers?.length || edited("performers") ? item.value.performers : view.value?.performers || []));
const music = computed(() => (item.value?.music?.length || edited("music") ? item.value.music : view.value?.music || []));
const flags = computed(() => item.value?.user_edits?.flags ?? view.value?.flags ?? []);
// Fields changed by hand (Edit details) -- kept through syncs and repairs.
function edited(field) { return !!item.value?.user_edits && field in item.value.user_edits; }
const canEdit = computed(() => hasPermission("videos.edit"));
const playable = computed(() => item.value?.status === "done" && item.value?.video_path && !item.value?.file_missing);
// 3.11.0: in the library, but its file is missing or empty
const fileMissing = computed(() => item.value?.status === "done" && item.value?.file_missing);
const canRedownload = computed(() => fileMissing.value && hasPermission("videos.add") && item.value.site !== "local" && item.value.site_status !== "removed");
const redownloading = ref(false);
async function redownload() {
  redownloading.value = true;
  try {
    await api(`/api/library/${encodeURIComponent(item.value.id)}/redownload`, { method: "POST", body: {} });
    toast(t("Downloading it again — see Tasks"), { type: "success" });
    load(false);
  } catch (err) {
    toast(err.message, { type: "error" });
  } finally {
    redownloading.value = false;
  }
}
// Hand the player this page's slot once the video can play; if it can't (still
// downloading, removed), a video playing elsewhere carries on in the mini-player.
watch([slot, () => item.value?.id, playable], () => {
  if (playable.value && slot.value) openOnPage(item.value, slot.value);
  else if (item.value && !playable.value && player.mode === "page") leavePage();
}, { flush: "post" });
watch(error, (e) => { if (e && player.mode === "page") leavePage(); });
const status = computed(() => (item.value ? workStatus(item.value) : null));
// Names of the fields kept as edited by hand (3.7.0: shown in the reader's language).
const FIELD_NAMES = { title: t("Title"), tags: t("Tags"), performers: t("Performers"), music: t("Music"), creators: t("Creators"), description: t("Description") };
const fieldName = (k) => FIELD_NAMES[k] || k;
const metaLine = computed(() => {
  const v = view.value || {};
  return [
    v.views != null ? tn("{n} view", "{n} views", Number(v.views)) : null,
    v.uploadDate ? t("Uploaded {date}", { date: v.uploadDate }) : null,
    v.rating || item.value?.ratings ? `★ ${v.rating || item.value.ratings}` : null,
    v.likes != null ? t("{n} likes", { n: compactNumber(v.likes) }) : null,
    v.favorites != null ? t("{n} favorites", { n: compactNumber(v.favorites) }) : null,
    item.value?.tech_duration || v.duration,
  ].filter(Boolean);
});
watch([title, error], ([name, e]) => setPageTitle(`/video/${props.id}`, e ? t("Video not found") : name), { immediate: true });
// Technical Details in sections (3.2.0): side by side when there's room, else one under the other.
// The keys are the server's English field names: they are matched, not shown translated.
const TECH_GENERAL = ["Duration", "File Size", "Overall Bitrate", "Container", "Format"];  // i18n-ignore
const TECH_GROUP_LABELS = { General: N_("General"), Video: N_("Video"), Audio: N_("Audio"), Other: N_("Other") };
const techGroups = computed(() => {
  const groups = { General: [], Video: [], Audio: [], Other: [] };
  for (const [k, v] of Object.entries(view.value?.technical || {})) {
    if (v === null || v === undefined || v === "") continue;
    const name = TECH_GENERAL.includes(k) ? "General"  // i18n-ignore
      : /^video\b|^resolution$|^fps$|^frame rate|^hdr|^bit depth|^pixel|^colou?r/i.test(k) ? "Video"  // i18n-ignore
      : /^audio\b|^sample rate|^channels?$/i.test(k) ? "Audio" : "Other";  // i18n-ignore
    groups[name].push([k, typeof v === "boolean" ? (v ? t("Yes") : t("No")) : v]);
  }
  // 3.9.0: the video's perceptual hash (16 hex digits) -- what the duplicate finder compares
  if (item.value?.local_phash) groups.General.push(["pHash", item.value.local_phash]);  // i18n-ignore
  return Object.entries(groups).filter(([, rows]) => rows.length).map(([name, rows]) => ({ name, label: TECH_GROUP_LABELS[name], rows }));
});
// 3.9.0: MediaInfo's full report of the file (who may run maintenance or edit videos)
const mediaInfoOpen = ref(false);
const canMediaInfo = computed(() => item.value?.status === "done" && (hasPermission("videos.maintain") || hasPermission("videos.edit")));
const longDescription = computed(() => (view.value?.description || "").length > 300);
const downloadStatus = computed(() => {
  const it = item.value;
  if (it?.status === "done" && it.file_missing) {
    if (it.site === "local") return t("The video file is missing, and it was uploaded from a computer — upload it again, or look for it in Maintenance → Check video files.");
    if (it.site_status === "removed") return t("The video file is missing, and the site doesn't have it any more — look for it in Maintenance → Check video files.");
    return t("The video file is missing — download it again, or look for it in Maintenance → Check video files.");
  }
  if (!it || it.status === "done") return null;
  if (it.status === "fetching") return t("Fetching details…");
  if (it.status === "pending") return t("Queued for download");
  if (it.status === "downloading") {
    const percent = Math.round(it.progress_percent || 0);
    return it.speed ? t("Downloading {percent}% · {speed}", { percent, speed: it.speed }) : t("Downloading {percent}%", { percent });
  }
  if (it.status === "failed") return it.error ? t("Download failed: {error}", { error: it.error }) : t("Download failed");
  return it.status;
});

function filterLibrary(key, value) { router.push({ name: "library", query: { [key]: value } }); }
// All the creators' videos (3.6.0; one creator = the same as clicking it).
function viewAllFrom(names) {
  router.push({ name: "library", query: { creator: names } });
}
</script>

<template>
  <div class="video-page">
    <div v-if="error" class="empty"><div class="empty-icon">🎞️</div>{{ error }}</div>
    <div v-else-if="!item" class="watch"><div class="skeleton media-skel" /><div class="skeleton side-skel" /></div>

    <div v-else class="watch" :class="{ 'no-side': !session.prefs.relatedCount && !player.queue }">
      <div class="primary">
        <!-- 3.2.0: the title and 🎲 Random above the player, in the video's own column (the related
             videos start at the top on the right, and go below everything else in a narrow window) -->
        <div class="watch-head">
          <h1 class="title">{{ title }}</h1>
          <button class="btn btn-primary random" :title="$t('Open a random video from the library (like Discover)')" @click="discover"><Icon name="dice" />{{ $t("Random") }}</button>
        </div>
        <div v-if="playable" ref="slot" class="player-slot" />
        <VideoFrames v-if="playable && session.prefs.videoFrames === true" :item-id="item.id" />
        <div v-if="!playable" class="poster">
          <img v-if="thumbUrl(item)" :src="thumbUrl(item)" alt="">
          <div class="poster-status" :class="{ failed: item.status === 'failed' || fileMissing }">
            {{ downloadStatus }}
            <div v-if="fileMissing" class="missing-actions">
              <button v-if="canRedownload" class="btn btn-primary btn-sm" :disabled="redownloading" @click="redownload">
                <Icon name="download" />{{ $t("Download again") }}
              </button>
              <RouterLink v-if="hasPermission('maintenance.run')" class="btn btn-sm" to="/admin/portal/maintenance#file-check">{{ $t("Check video files") }}</RouterLink>
            </div>
            <div v-if="item.status === 'downloading'" class="progress"><div :style="{ width: `${item.progress_percent || 0}%` }" /></div>
          </div>
        </div>

        <PlayQueue v-if="narrow" />
        <div class="byline">
          <!-- 3.6.0: who made it (the site's creators), and who uploaded it when that's someone else -->
          <span v-if="makers(item).length" class="makers"><template v-for="(c, i) in makers(item)" :key="c"><span v-if="i" class="muted">{{ ",\u00a0" }}</span><button
            class="creator text-creator" :title="$t('Show videos by {name}', { name: c })" @click="filterLibrary('creator', c)">{{ c }}</button></template></span>
          <span v-if="uploadedBy(item)" class="muted uploaded-by">{{ $t("uploaded by") }} <button class="creator uploader text-creator" :title="$t('Show videos uploaded by {name}', { name: uploadedBy(item) })"
            @click="filterLibrary('creator', uploadedBy(item))">{{ uploadedBy(item) }}</button></span>
          <span v-for="m in metaLine" :key="m" class="muted">{{ m }}</span>
          <FavoriteButton v-if="item.status === 'done'" :item-id="item.id" class="byline-fav" />
        </div>
        <ActionBar :item="item" context="page" />
        <div v-if="status" class="work" :class="status.kind">{{ status.label }}<span v-if="status.detail" class="muted"> — {{ status.detail }}</span></div>
        <div v-if="syncInfo.status === 'removed'" class="gone">{{ $t("💀 This video is no longer available on PMVHaven (not found during the last sync). Your downloaded copy is unaffected.") }}</div>

        <section class="card details">
          <div v-if="view?.description" class="group">
            <h4>{{ $t("Description") }}</h4>
            <p class="description" :class="{ clamp: longDescription && !showFullDescription }">{{ view.description }}</p>
            <button v-if="longDescription" class="btn btn-ghost btn-sm more" @click="showFullDescription = !showFullDescription">
              {{ showFullDescription ? $t("Show less") : $t("Show more") }}
            </button>
          </div>
          <div v-if="item.user_edits && Object.keys(item.user_edits).length" class="muted small">
            ✎ {{ $t("Edited by hand: {fields} — kept when syncing or repairing.", { fields: Object.keys(item.user_edits).map(fieldName).join(", ") }) }}
            <button v-if="canEdit" class="btn btn-ghost btn-sm" @click="openDialog('details', item)">{{ $t("Edit") }}</button>
          </div>
          <div v-if="performers.length" class="group">
            <h4>{{ $t("Performers") }}<span v-if="edited('performers')" class="edited" :title="$t('Edited by hand')">✎</span></h4>
            <div class="chips"><RouterLink v-for="p in performers" :key="p" :to="{ name: 'performer', params: { name: p } }" class="chip chip-performer"
                                           :class="perfClass(p)" :title="$t('{name} — profile and videos', { name: perfTitle(p) })">{{ p }}</RouterLink></div>
          </div>
          <div v-if="tags.length" class="group">
            <h4>{{ $t("Tags") }}<span v-if="edited('tags')" class="edited" :title="$t('Edited by hand')">✎</span></h4>
            <div class="chips"><button v-for="t in tags" :key="t" class="chip chip-tag" @click="filterLibrary('tag', t)">#{{ t }}</button></div>
          </div>
          <div v-if="music.length" class="group">
            <h4>{{ $t("Music") }}<span v-if="edited('music')" class="edited" :title="$t('Edited by hand')">✎</span></h4>
            <div class="chips"><span v-for="m in music" :key="m" class="chip chip-music">{{ m }}</span></div>
          </div>
          <div v-if="flags.length" class="group">
            <h4>{{ $t("Content flags") }}<span v-if="edited('flags')" class="edited" :title="$t('Edited by hand')">✎</span></h4>
            <div class="chips"><span v-for="f in flags" :key="f" class="chip">{{ f }}</span></div>
          </div>
          <details v-if="view && Object.keys(view.technical).length" class="tech">
            <summary>{{ $t("Technical Details") }}</summary>
            <div class="tech-groups">
              <div v-for="g in techGroups" :key="g.name" class="tech-group">
                <h5>{{ $t(g.label) }}</h5>
                <dl class="facts"><template v-for="[k, v] in g.rows" :key="k"><dt>{{ k }}</dt><dd :class="{ mono: k === 'pHash' }">{{ v }}</dd></template></dl>
              </div>
            </div>
            <button v-if="canMediaInfo" class="btn btn-sm mi-btn" @click="mediaInfoOpen = true"><Icon name="list" :size="14" />{{ $t("MediaInfo…") }}</button>
          </details>
          <div v-if="syncInfo.synced_at" class="muted small">{{ $t("Last synced with PMVHaven {time}", { time: formatDateTime(syncInfo.synced_at) }) }}</div>
        </section>

        <section v-if="shared.length" class="card shared">
          <h3>{{ $t("Shares scenes with") }}</h3>
          <p class="muted small">{{ $t("Videos with the same footage, found by comparing frames. A time jumps there in this video.") }}</p>
          <ul>
            <li v-for="s in shared" :key="s.id">
              <RouterLink :to="{ name: 'video', params: { id: s.id } }" class="sh-thumb"><img v-if="s.thumb" :src="s.thumb" alt="" loading="lazy"></RouterLink>
              <div class="sh-text">
                <RouterLink :to="{ name: 'video', params: { id: s.id } }" class="sh-title">{{ s.title }}</RouterLink>
                <span class="muted small">{{ [s.creator, s.length, $t("about {s} s in common", { s: s.seconds })].filter(Boolean).join(" · ") }}</span>
                <span class="times">
                  <button v-for="(sc, i) in s.scenes" :key="i" class="btn btn-ghost btn-sm" :disabled="!playable"
                          :title="$t('{from}–{to} here, {ofrom}–{oto} in the other video', { from: formatDuration(sc[0]), to: formatDuration(sc[1] + 2), ofrom: formatDuration(sc[2]), oto: formatDuration(sc[3] + 2) })"
                          @click="seekPlayer(sc[0])">{{ formatDuration(sc[0]) }}</button>
                </span>
              </div>
            </li>
          </ul>
        </section>

        <FacesOnVideo :id="props.id" />

        <section v-if="sameCreator.length" class="same">
          <div class="section-head">
            <h3>{{ $t("More from {name}", { name: creatorText(item) }) }}</h3>
            <button class="btn btn-ghost btn-sm" @click="viewAllFrom(makers(item))">{{ $t("View all") }}</button>
          </div>
          <div class="same-grid"><VideoCard v-for="v in sameCreator" :key="v.id" :item="v" small @filter="filterLibrary" /></div>
        </section>
      </div>

      <aside v-if="session.prefs.relatedCount > 0 || (player.queue && !narrow)" class="secondary">
        <PlayQueue v-if="!narrow" />
        <template v-if="session.prefs.relatedCount > 0">
          <h3 class="side-title" :title="$t('Ranked by shared performers (strongest), same creator, then shared tags')">{{ $t("Related videos") }}</h3>
          <RelatedList v-if="related.length" :items="related" />
          <p v-else class="muted small">{{ $t("No related videos yet — they're matched by shared performers, creator and tags.") }}</p>
        </template>
      </aside>
    </div>
    <MediaInfoDialog v-if="mediaInfoOpen" :id="props.id" @close="mediaInfoOpen = false" />
  </div>
</template>

<style scoped>
.video-page { padding: 0.75rem 1.5rem 2rem; }
.shared { padding: 0.9rem 1rem; margin-top: 1rem; display: flex; flex-direction: column; gap: 0.5rem; }
.shared h3 { margin: 0; font-size: 1rem; }
.shared ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.6rem; }
.shared li { display: flex; gap: 0.75rem; align-items: flex-start; }
.sh-thumb { flex: none; width: 8rem; aspect-ratio: 16 / 9; border-radius: 0.4rem; overflow: hidden; background: var(--panel-2); }
.sh-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.sh-text { min-width: 0; display: flex; flex-direction: column; gap: 0.15rem; }
.sh-title { color: var(--text); font-weight: 600; overflow-wrap: anywhere; }
.times { display: flex; flex-wrap: wrap; gap: 0.25rem; }
.times .btn { height: 1.6rem; padding: 0 0.45rem; font-variant-numeric: tabular-nums; }
.watch-head { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.75rem; }
.watch-head .title { flex: 1; min-width: 0; margin: 0; }
.watch-head .random { flex: none; }
.watch { display: grid; grid-template-columns: minmax(0, 1fr) 25rem; gap: 1.5rem; align-items: start; }
.watch.no-side { grid-template-columns: minmax(0, 1fr); }
.primary { min-width: 0; display: flex; flex-direction: column; gap: 0.75rem; }
.title { font-size: 1.3rem; letter-spacing: -0.01em; }
.byline { display: flex; flex-wrap: wrap; gap: 0.25rem 1rem; align-items: center; font-size: 0.875rem; }
.byline-fav { margin-inline-start: auto; }
.creator { border: 0; background: none; padding: 0; color: var(--accent); font: 600 0.9rem var(--font); cursor: pointer; }
.poster { position: relative; aspect-ratio: 16 / 9; border-radius: var(--radius-lg); overflow: hidden; background: #000; }
.poster img { width: 100%; height: 100%; object-fit: contain; opacity: 0.55; }
.poster-status { position: absolute; inset-inline-start: 1rem; inset-inline-end: 1rem; bottom: 1rem; padding: 0.75rem 0.875rem; border-radius: var(--radius); background: rgba(0, 0, 0, 0.75); color: #fff; font-weight: 600; }
.poster-status.failed { color: #fca5a5; }
.missing-actions { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.625rem; }
.progress { height: 0.375rem; border-radius: 3px; background: rgba(255, 255, 255, 0.2); margin-top: 0.5rem; overflow: hidden; }
.progress div { height: 100%; background: var(--accent); transition: width 0.4s; }
.work { font-weight: 600; font-size: 0.875rem; }
.work.busy { color: var(--accent); }
.work.failed { color: var(--danger); }
.gone { padding: 0.75rem 0.875rem; border-radius: var(--radius); border: 1px solid var(--gone-bg, #dc2626); background: color-mix(in srgb, var(--gone-bg, #dc2626) 14%, transparent); font-size: 0.84rem; }
.details { padding: 1rem 1.125rem; display: flex; flex-direction: column; gap: 0.875rem; }
.group { display: flex; flex-direction: column; gap: 0.5rem; }
.group h4, .side-title { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); }
.description { margin: 0; white-space: pre-line; color: var(--text-2); font-size: 0.875rem; }
.description.clamp { display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden; }
.more { align-self: flex-start; padding: 0 0.375rem; margin-top: -0.5rem; }
.edited { margin-inline-start: 0.375rem; color: var(--accent); text-transform: none; }
.chips { display: flex; flex-wrap: wrap; gap: 0.375rem; }
.tech summary { cursor: pointer; font-weight: 600; font-size: 0.84rem; }
.tech-groups { display: grid; grid-template-columns: repeat(auto-fit, minmax(10.5rem, 1fr)); gap: 0.75rem 1.5rem; margin-top: 0.625rem; }
.tech-group h5 { margin: 0 0 0.375rem; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); font-weight: 600; }
.mi-btn { margin-top: 0.75rem; }
.tech dd.mono { font-family: var(--font-mono, ui-monospace, monospace); overflow-wrap: anywhere; }
.facts { display: grid; grid-template-columns: auto 1fr; gap: 0.375rem 1rem; margin: 0; font-size: 0.8rem; }
.facts dt { color: var(--text-dim); }
.facts dd { margin: 0; overflow-wrap: anywhere; }
.small { font-size: 0.78rem; }
.section-head { display: flex; align-items: center; justify-content: space-between; margin: 0.5rem 0 0.75rem; }
.section-head h3 { font-size: 1rem; }
.same-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(13rem, 1fr)); gap: 1rem 0.875rem; }
.secondary { min-width: 0; display: flex; flex-direction: column; gap: 0.75rem; position: sticky; top: 0.75rem; }
.player-slot { border-radius: var(--radius-lg); }
.player-slot:empty { min-height: 12rem; background: #000; } /* only until the player moves in */
.media-skel { aspect-ratio: 16 / 9; border-radius: var(--radius-lg); }
.side-skel { height: 20rem; border-radius: var(--radius-lg); }
@media (max-width: 75rem) {
  .watch { grid-template-columns: 1fr; } .secondary { position: static; }
}
@media (max-width: 56rem) { .video-page { padding: 0.5rem 0.75rem 1.5rem; } }
.makers { display: inline; }
.creator.uploader { font-weight: 500; font-size: inherit; }
.uploaded-by { font-size: 0.85rem; }
</style>
