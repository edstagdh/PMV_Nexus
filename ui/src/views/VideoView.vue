<script setup>
import { ref, computed, watch, onBeforeUnmount } from "vue";
import { useRouter } from "vue-router";
import Icon from "../components/Icon.vue";
import PlayQueue from "../components/PlayQueue.vue";
import { player, openOnPage, leavePage, parkPlayer } from "../stores/player.js";
import { setPageTitle } from "../lib/title.js";
import VideoCard from "../components/VideoCard.vue";
import RelatedList from "../components/RelatedList.vue";
import ActionBar from "../components/ActionBar.vue";
import FavoriteButton from "../components/FavoriteButton.vue";
import VideoFrames from "../components/VideoFrames.vue";
import { api, qs } from "../lib/api.js";
import { normalizeMetadata } from "../lib/metadata.js";
import { thumbUrl, compactNumber, workStatus } from "../lib/format.js";
import { onLibraryChange } from "../stores/live.js";
import { session, hasPermission } from "../stores/session.js";
import { openDialog } from "../stores/dialogs.js";
import { discover } from "../lib/discover.js";

const props = defineProps({ id: String });
const router = useRouter();
const item = ref(null);
const meta = ref(null);
const syncInfo = ref({});
const error = ref("");
const related = ref([]);
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
    // read it again whenever the video itself changed, so the page shows the new details (3.0.1).
    const changed = item.value && item.value.updated_at !== it.updated_at;
    item.value = it;
    if (md !== undefined) { meta.value = md; syncInfo.value = md?._site_sync || {}; }
    if (statusChanged && !full) load(true);
    else if (changed && !full) reloadMetadata(id);
    error.value = "";
    if (full) loadNeighbours(it);
  } catch (err) {
    if (id === props.id) error.value = err.status === 404 ? "This video is not in the library." : err.message;
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
  const [rel, same] = await Promise.all([
    api(`/api/library/${encodeURIComponent(id)}/related?limit=${session.prefs.relatedCount || 1}`).catch(() => ({ items: [] })),
    it.creator
      ? api(`/api/library?${qs({ statuses: "done", creators: it.creator, page_size: 9, sort_key: "download_datetime", sort_dir: "desc" })}`).catch(() => ({ items: [] }))
      : Promise.resolve({ items: [] }),
  ]);
  if (id !== props.id) return;
  related.value = rel.items;
  sameCreator.value = (same.items || []).filter((x) => x.id !== id).slice(0, 8);
}

// The app's one player (stores/player.js) is placed into `slot` when the
// video can play; leaving the page hands it to the mini-player.
const slot = ref(null);
watch(() => props.id, (id, old) => {
  if (old && player.mode === "page") parkPlayer(); // keep it out of the old page's DOM while the new one loads
  item.value = null; meta.value = null; related.value = []; sameCreator.value = []; showFullDescription.value = false;
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
const playable = computed(() => item.value?.status === "done" && item.value?.video_path);
// Hand the player this page's slot once the video can play; if it can't (still
// downloading, removed), a video playing elsewhere carries on in the mini-player.
watch([slot, () => item.value?.id, playable], () => {
  if (playable.value && slot.value) openOnPage(item.value, slot.value);
  else if (item.value && !playable.value && player.mode === "page") leavePage();
}, { flush: "post" });
watch(error, (e) => { if (e && player.mode === "page") leavePage(); });
const status = computed(() => (item.value ? workStatus(item.value) : null));
const metaLine = computed(() => {
  const v = view.value || {};
  return [
    v.views != null ? `${Number(v.views).toLocaleString()} views` : null,
    v.uploadDate ? `Uploaded ${v.uploadDate}` : null,
    v.rating || item.value?.ratings ? `★ ${v.rating || item.value.ratings}` : null,
    v.likes != null ? `${compactNumber(v.likes)} likes` : null,
    v.favorites != null ? `${compactNumber(v.favorites)} favorites` : null,
    item.value?.tech_duration || v.duration,
  ].filter(Boolean);
});
watch([title, error], ([t, e]) => setPageTitle(`/video/${props.id}`, e ? "Video not found" : t), { immediate: true });
// Technical Details in sections (3.0.1): side by side when there's room, else one under the other.
const TECH_GENERAL = ["Duration", "File Size", "Overall Bitrate", "Container", "Format"];
const techGroups = computed(() => {
  const groups = { General: [], Video: [], Audio: [], Other: [] };
  for (const [k, v] of Object.entries(view.value?.technical || {})) {
    if (v === null || v === undefined || v === "") continue;
    const name = TECH_GENERAL.includes(k) ? "General"
      : /^video\b|^resolution$|^fps$|^frame rate|^hdr|^bit depth|^pixel|^colou?r/i.test(k) ? "Video"
      : /^audio\b|^sample rate|^channels?$/i.test(k) ? "Audio" : "Other";
    groups[name].push([k, typeof v === "boolean" ? (v ? "Yes" : "No") : v]);
  }
  return Object.entries(groups).filter(([, rows]) => rows.length).map(([name, rows]) => ({ name, rows }));
});
const longDescription = computed(() => (view.value?.description || "").length > 300);
const downloadStatus = computed(() => {
  const it = item.value;
  if (!it || it.status === "done") return null;
  if (it.status === "fetching") return "Fetching details…";
  if (it.status === "pending") return "Queued for download";
  if (it.status === "downloading") return `Downloading ${Math.round(it.progress_percent || 0)}%${it.speed ? ` · ${it.speed}` : ""}`;
  if (it.status === "failed") return `Download failed${it.error ? `: ${it.error}` : ""}`;
  return it.status;
});

function filterLibrary(key, value) { router.push({ name: "library", query: { [key]: value } }); }
</script>

<template>
  <div class="video-page">
    <div v-if="error" class="empty"><div class="empty-icon">🎞️</div>{{ error }}</div>
    <div v-else-if="!item" class="watch"><div class="skeleton media-skel" /><div class="skeleton side-skel" /></div>

    <div v-else class="watch" :class="{ 'no-side': !session.prefs.relatedCount && !player.queue }">
      <div class="primary">
        <div v-if="playable" ref="slot" class="player-slot" />
        <VideoFrames v-if="playable && session.prefs.videoFrames !== false" :item-id="item.id" :can-redo="canEdit" />
        <div v-else class="poster">
          <img v-if="thumbUrl(item)" :src="thumbUrl(item)" alt="">
          <div class="poster-status" :class="{ failed: item.status === 'failed' }">
            {{ downloadStatus }}
            <div v-if="item.status === 'downloading'" class="progress"><div :style="{ width: `${item.progress_percent || 0}%` }" /></div>
          </div>
        </div>

        <PlayQueue v-if="narrow" />
        <div class="title-row">
          <h1 class="title">{{ title }}</h1>
          <button class="btn btn-primary btn-sm random" title="Open a random video from the library (like Discover)" @click="discover"><Icon name="dice" />Random</button>
        </div>
        <div class="byline">
          <button v-if="item.creator" class="creator text-creator" :title="`Show videos by ${item.creator}`" @click="filterLibrary('creator', item.creator)">{{ item.creator }}</button>
          <span v-for="m in metaLine" :key="m" class="muted">{{ m }}</span>
          <FavoriteButton v-if="item.status === 'done'" :item-id="item.id" class="byline-fav" />
        </div>
        <ActionBar :item="item" context="page" />
        <div v-if="status" class="work" :class="status.kind">{{ status.label }}<span v-if="status.detail" class="muted"> — {{ status.detail }}</span></div>
        <div v-if="syncInfo.status === 'removed'" class="gone">💀 This video is no longer available on PMVHaven (not found during the last sync). Your downloaded copy is unaffected.</div>

        <section class="card details">
          <div v-if="view?.description" class="group">
            <h4>Description</h4>
            <p class="description" :class="{ clamp: longDescription && !showFullDescription }">{{ view.description }}</p>
            <button v-if="longDescription" class="btn btn-ghost btn-sm more" @click="showFullDescription = !showFullDescription">
              {{ showFullDescription ? "Show less" : "Show more" }}
            </button>
          </div>
          <div v-if="item.user_edits && Object.keys(item.user_edits).length" class="muted small">
            ✎ Edited by hand: {{ Object.keys(item.user_edits).join(", ") }} — kept when syncing or repairing.
            <button v-if="canEdit" class="btn btn-ghost btn-sm" @click="openDialog('details', item)">Edit</button>
          </div>
          <div v-if="performers.length" class="group">
            <h4>Performers<span v-if="edited('performers')" class="edited" title="Edited by hand">✎</span></h4>
            <div class="chips"><RouterLink v-for="p in performers" :key="p" :to="{ name: 'performer', params: { name: p } }" class="chip chip-performer"
                                           :title="`${p}: profile and videos`">{{ p }}</RouterLink></div>
          </div>
          <div v-if="tags.length" class="group">
            <h4>Tags<span v-if="edited('tags')" class="edited" title="Edited by hand">✎</span></h4>
            <div class="chips"><button v-for="t in tags" :key="t" class="chip chip-tag" @click="filterLibrary('tag', t)">#{{ t }}</button></div>
          </div>
          <div v-if="music.length" class="group">
            <h4>Music<span v-if="edited('music')" class="edited" title="Edited by hand">✎</span></h4>
            <div class="chips"><span v-for="m in music" :key="m" class="chip chip-music">{{ m }}</span></div>
          </div>
          <div v-if="flags.length" class="group">
            <h4>Content flags<span v-if="edited('flags')" class="edited" title="Edited by hand">✎</span></h4>
            <div class="chips"><span v-for="f in flags" :key="f" class="chip">{{ f }}</span></div>
          </div>
          <details v-if="view && Object.keys(view.technical).length" class="tech">
            <summary>Technical Details</summary>
            <div class="tech-groups">
              <div v-for="g in techGroups" :key="g.name" class="tech-group">
                <h5>{{ g.name }}</h5>
                <dl class="facts"><template v-for="[k, v] in g.rows" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></template></dl>
              </div>
            </div>
          </details>
          <div v-if="syncInfo.synced_at" class="muted small">Last synced with PMVHaven {{ new Date(syncInfo.synced_at * 1000).toLocaleString() }}</div>
        </section>

        <section v-if="sameCreator.length" class="same">
          <div class="section-head">
            <h3>More from {{ item.creator }}</h3>
            <button class="btn btn-ghost btn-sm" @click="filterLibrary('creator', item.creator)">View all</button>
          </div>
          <div class="same-grid"><VideoCard v-for="v in sameCreator" :key="v.id" :item="v" small @filter="filterLibrary" /></div>
        </section>
      </div>

      <aside v-if="session.prefs.relatedCount > 0 || (player.queue && !narrow)" class="secondary">
        <PlayQueue v-if="!narrow" />
        <template v-if="session.prefs.relatedCount > 0">
          <h3 class="side-title" title="Ranked by shared performers (strongest), same creator, then shared tags">Related videos</h3>
          <RelatedList v-if="related.length" :items="related" />
          <p v-else class="muted small">No related videos yet — they're matched by shared performers, creator and tags.</p>
        </template>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.video-page { padding: 0.75rem 1.5rem 2rem; }
.title-row { display: flex; align-items: flex-start; gap: 0.75rem; }
.title-row .title { flex: 1; min-width: 0; }
.title-row .random { flex: none; margin-top: 0.125rem; }
.watch { display: grid; grid-template-columns: minmax(0, 1fr) 25rem; gap: 1.5rem; align-items: start; }
.watch.no-side { grid-template-columns: minmax(0, 1fr); }
.primary { min-width: 0; display: flex; flex-direction: column; gap: 0.75rem; }
.title { font-size: 1.3rem; letter-spacing: -0.01em; }
.byline { display: flex; flex-wrap: wrap; gap: 0.25rem 1rem; align-items: center; font-size: 0.875rem; }
.byline-fav { margin-left: auto; }
.creator { border: 0; background: none; padding: 0; color: var(--accent); font: 600 0.9rem var(--font); cursor: pointer; }
.poster { position: relative; aspect-ratio: 16 / 9; border-radius: var(--radius-lg); overflow: hidden; background: #000; }
.poster img { width: 100%; height: 100%; object-fit: contain; opacity: 0.55; }
.poster-status { position: absolute; left: 1rem; right: 1rem; bottom: 1rem; padding: 0.75rem 0.875rem; border-radius: var(--radius); background: rgba(0, 0, 0, 0.75); color: #fff; font-weight: 600; }
.poster-status.failed { color: #fca5a5; }
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
.edited { margin-left: 0.375rem; color: var(--accent); text-transform: none; }
.chips { display: flex; flex-wrap: wrap; gap: 0.375rem; }
.tech summary { cursor: pointer; font-weight: 600; font-size: 0.84rem; }
.tech-groups { display: grid; grid-template-columns: repeat(auto-fit, minmax(10.5rem, 1fr)); gap: 0.75rem 1.5rem; margin-top: 0.625rem; }
.tech-group h5 { margin: 0 0 0.375rem; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); font-weight: 600; }
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
@media (max-width: 75rem) { .watch { grid-template-columns: 1fr; } .secondary { position: static; } }
@media (max-width: 56rem) { .video-page { padding: 0.5rem 0.75rem 1.5rem; } }
</style>
