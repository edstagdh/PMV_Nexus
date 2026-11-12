<script setup>
// Thumbnail with badges; plays the short preview clip while hovered.
// Which optional badges show is a user setting; duration and quality always show.
import { ref, computed, onBeforeUnmount } from "vue";
import PreviewVideo from "./PreviewVideo.vue";
import { thumbUrl, previewUrl, ratingTier, codecLabel, qualityInfo, ORIENTATION_GLYPH, workStatus } from "../lib/format.js";
import { session } from "../stores/session.js";
import { watchedShare } from "../stores/watchProgress.js";

const props = defineProps({ item: Object, compact: Boolean, playing: Boolean });
const hovering = ref(false);
let timer = null;
const thumb = computed(() => thumbUrl(props.item));
const preview = computed(() => previewUrl(props.item));
const tier = computed(() => ratingTier(props.item.ratings));
const quality = computed(() => qualityInfo(props.item.tech_resolution));
const codec = computed(() => codecLabel(props.item.tech_codec));
const status = computed(() => workStatus(props.item));
const show = computed(() => session.prefs.badges);
// Small thumbnails (related videos) show only the badges picked in Settings.
const small = computed(() => new Set(session.prefs.relatedBadges));
const previewFailed = ref("");
// How far I got in it (2.24.0): a thin line along the bottom, like YouTube.
const watched = computed(() => (session.prefs.watchedBar === false ? null : watchedShare(props.item)));
// Vertical (and other narrow) videos are shown whole, with black bars at the
// sides, instead of being cropped to fill the 16:9 frame.
const narrowImage = ref(false);
const contain = computed(() => narrowImage.value || quality.value?.orientation === "portrait");
function onThumbLoad(e) { narrowImage.value = e.target.naturalWidth / e.target.naturalHeight < 1.3; }

function enter() { clearTimeout(timer); if (preview.value && !previewFailed.value) timer = setTimeout(() => (hovering.value = true), 250); }
function leave() { clearTimeout(timer); hovering.value = false; }
onBeforeUnmount(() => clearTimeout(timer));
</script>

<template>
  <div class="thumb" :class="{ compact, contain }" @mouseenter="enter" @mouseleave="leave">
    <img v-if="thumb" :src="thumb" alt="" loading="lazy" decoding="async" @load="onThumbLoad">
    <div v-else class="thumb-empty">No thumbnail</div>
    <PreviewVideo v-if="(hovering || playing) && preview && !previewFailed" :src="preview" :item-id="item.id" class="thumb-preview"
                  @failed="previewFailed = $event; hovering = false" />
    <div v-if="previewFailed && (hovering || playing)" class="preview-note">Preview {{ previewFailed }}</div>
    <div v-if="watched" class="watched" :title="watched >= 0.95 ? 'Watched' : `Watched ${Math.round(watched * 100)}%`">
      <i :style="{ width: `${Math.max(2, watched * 100)}%` }" />
    </div>
    <template v-if="!compact">
      <div class="tl">
        <span v-if="show.gone && item.site_status === 'removed'" class="badge badge-gone" title="Gone from the site (not found during the last sync)">💀</span>
        <span v-if="show.status && status" class="badge" :class="`badge-status-${status.kind}`" :title="status.detail || status.label">{{ status.label }}</span>
      </div>
      <div class="tr">
        <span v-if="show.rating && item.ratings" class="badge" :class="tier && `badge-rating-${tier}`">★ {{ item.ratings }}</span>
      </div>
      <div class="bottom">
        <span v-if="show.codec && codec" class="badge" :class="{ 'badge-hevc': codec === 'HEVC' }">{{ codec }}</span>
        <span v-if="show.filesize && item.tech_filesize" class="badge">{{ item.tech_filesize }}</span>
        <span v-if="quality" class="badge" :title="item.tech_resolution">
          <template v-if="show.orientation && quality.orientation">{{ ORIENTATION_GLYPH[quality.orientation] }} </template>{{ quality.label }}
        </span>
        <span v-if="item.tech_duration" class="badge">{{ item.tech_duration }}</span>
      </div>
    </template>
    <template v-else>
      <div class="tl"><span v-if="small.has('gone') && item.site_status === 'removed'" class="badge badge-gone" title="Gone from the site">💀</span></div>
      <div class="tr"><span v-if="small.has('rating') && item.ratings" class="badge" :class="tier && `badge-rating-${tier}`">★ {{ item.ratings }}</span></div>
      <div class="bottom">
        <span v-if="small.has('codec') && codec" class="badge" :class="{ 'badge-hevc': codec === 'HEVC' }">{{ codec }}</span>
        <span v-if="small.has('filesize') && item.tech_filesize" class="badge">{{ item.tech_filesize }}</span>
        <span v-if="small.has('quality') && quality" class="badge" :title="item.tech_resolution">{{ quality.label }}</span>
        <span v-if="small.has('duration') && item.tech_duration" class="badge">{{ item.tech_duration }}</span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.thumb { position: relative; aspect-ratio: 16 / 9; border-radius: var(--radius); overflow: hidden; background: var(--thumb-bg, #000); }
.thumb img, .thumb-preview { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.thumb-preview { animation: fade 0.2s ease; }
.thumb.contain img, .thumb.contain .thumb-preview { object-fit: contain; background: #000; }
.watched { position: absolute; left: 0; right: 0; bottom: 0; height: 4px; background: rgb(255 255 255 / 0.35); z-index: 1; }
.watched i { display: block; height: 100%; background: var(--watched-color, #e11d2e); }
.compact .watched { height: 3px; }
.thumb-empty { position: absolute; inset: 0; display: grid; place-items: center; color: var(--text-dim); font-size: 0.75rem; }
.tl, .tr, .bottom { position: absolute; display: flex; gap: 0.25rem; }
.tl { top: 0.375rem; left: 0.375rem; right: 40%; flex-wrap: wrap; }
.tr { top: 0.375rem; right: 0.375rem; }
.bottom { bottom: 0.375rem; right: 0.375rem; left: 0.375rem; justify-content: flex-end; flex-wrap: wrap-reverse; }
.compact .bottom { bottom: 0.25rem; right: 0.25rem; left: 0.25rem; flex-wrap: nowrap; overflow: hidden; }
.compact .tl, .compact .tr { top: 0.25rem; }
.compact .badge { height: 1.125rem; padding: 0 0.3rem; font-size: 0.625rem; }
.preview-note { position: absolute; left: 0.375rem; right: 0.375rem; top: 50%; transform: translateY(-50%); text-align: center; font-size: 0.7rem; color: #fff; background: rgba(0, 0, 0, 0.6); border-radius: var(--radius-sm); padding: 0.25rem; pointer-events: none; }
.badge-gone { background: var(--gone-bg, #dc2626); color: #fff; font-size: 0.8rem; height: 1.375rem; padding: 0 0.375rem; box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.35); }
.badge-status-busy { background: var(--accent); color: #fff; }
.badge-status-queued { background: rgba(0, 0, 0, 0.72); color: #fff; }
.badge-status-failed { background: var(--danger); color: #fff; }
@keyframes fade { from { opacity: 0; } }
</style>
