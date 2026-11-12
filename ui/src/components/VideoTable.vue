<script setup>
import { ref, computed, onBeforeUnmount } from "vue";
import VideoThumb from "./VideoThumb.vue";
import ActionMenu from "./ActionMenu.vue";
import Icon from "./Icon.vue";
import { itemUrlTitle, formatDate, qualityInfo, codecLabel, ratingTier } from "../lib/format.js";
import { session } from "../stores/session.js";

defineProps({ items: Array, sort: String, dir: String, picked: { type: Object, default: null } });
const emit = defineEmits(["sort", "filter"]);
const COLUMNS = [
  { key: "title", label: "Title" },
  { key: "creator", label: "Creator" },
  { key: "upload_date", label: "Uploaded" },
  { key: "download_datetime", label: "Acquired" },
  { key: "tech_duration", label: "Duration" },
  { key: "tech_resolution", label: "Quality" },
  { key: "tech_codec", label: "Codec" },
  { key: "tech_filesize", label: "Size" },
  { key: "ratings", label: "Rating" },
  { key: "views", label: "Views" },
];

// Hovering a thumbnail shows it enlarged (and playing its preview) next to the
// row; the size is the "table preview size" setting.
const THUMB_W = 6; // rem, the thumbnail column
const pop = ref(null);
let timer = null;
const popWidth = computed(() => `${(THUMB_W * session.prefs.tablePreviewScale) / 100}rem`);
function hoverIn(item, e) {
  clearTimeout(timer);
  const r = e.currentTarget.getBoundingClientRect();
  timer = setTimeout(() => {
    const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
    const w = (THUMB_W * session.prefs.tablePreviewScale / 100) * rem;
    const h = w * 9 / 16;
    const left = r.right + 12 + w > window.innerWidth ? Math.max(8, r.left - w - 12) : r.right + 12;
    const top = Math.min(window.innerHeight - h - 8, Math.max(8, r.top + r.height / 2 - h / 2));
    pop.value = { item, top, left };
  }, 200);
}
function hoverOut() { clearTimeout(timer); pop.value = null; }
onBeforeUnmount(() => clearTimeout(timer));
</script>

<template>
  <div class="table-wrap card scroll-thin" @scroll.passive="hoverOut">
    <table class="vtable">
      <thead>
        <tr>
          <th class="col-thumb" />
          <th v-for="c in COLUMNS" :key="c.key" :aria-sort="sort === c.key ? (dir === 'asc' ? 'ascending' : 'descending') : null">
            <button class="th-btn" :class="{ on: sort === c.key }" @click="emit('sort', c.key)">
              {{ c.label }}<Icon v-if="sort === c.key" :name="dir === 'asc' ? 'sortAsc' : 'sortDesc'" :size="14" />
            </button>
          </th>
          <th class="col-menu"><span class="sr-only">Actions</span></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in items" :key="item.id" :data-id="item.id" :class="{ picked: picked?.has(item.id) }">
          <td class="col-thumb" @mouseenter="hoverIn(item, $event)" @mouseleave="hoverOut">
            <router-link :to="{ name: 'video', params: { id: item.id } }"><VideoThumb :item="item" compact /></router-link>
          </td>
          <td class="col-title">
            <router-link :to="{ name: 'video', params: { id: item.id } }">{{ itemUrlTitle(item) }}</router-link>
            <span v-if="session.prefs.badges.gone && item.site_status === 'removed'" class="gone" title="Gone from the site (not found during the last sync)">💀</span>
          </td>
          <td><button v-if="item.creator" class="linkish" @click="emit('filter', 'creator', item.creator)">{{ item.creator }}</button></td>
          <td class="nowrap">{{ formatDate(item.upload_date) }}</td>
          <td class="nowrap">{{ item.download_datetime ? formatDate(item.download_datetime) : formatDate(new Date(item.created_at * 1000).toISOString()) }}</td>
          <td class="nowrap">{{ item.tech_duration }}</td>
          <td class="nowrap" :title="item.tech_resolution">{{ qualityInfo(item.tech_resolution)?.label }}</td>
          <td class="nowrap">{{ codecLabel(item.tech_codec) }}</td>
          <td class="nowrap">{{ item.tech_filesize }}</td>
          <td class="nowrap"><span v-if="item.ratings" class="badge" :class="`badge-rating-${ratingTier(item.ratings)}`">{{ item.ratings }}</span></td>
          <td class="nowrap">{{ item.site_stats?.views?.toLocaleString() ?? "" }}</td>
          <td class="col-menu"><ActionMenu :item="item" /></td>
        </tr>
      </tbody>
    </table>
  </div>
  <Teleport to="body">
    <div v-if="pop" class="table-pop card" :style="{ top: `${pop.top}px`, left: `${pop.left}px`, width: popWidth }">
      <VideoThumb :item="pop.item" playing />
    </div>
  </Teleport>
</template>

<style scoped>
.table-wrap { overflow-x: auto; }
.vtable { width: 100%; border-collapse: collapse; font-size: 0.84rem; }
th { position: sticky; top: 0; background: var(--panel); text-align: left; font-weight: 600; border-bottom: 1px solid var(--border); padding: 0; }
.th-btn { display: inline-flex; align-items: center; gap: 0.25rem; width: 100%; padding: 0.625rem; border: 0; background: none; color: var(--text-2); font: 600 0.78rem var(--font); cursor: pointer; white-space: nowrap; }
.th-btn:hover, .th-btn.on { color: var(--accent); }
td { padding: 0.375rem 0.625rem; border-bottom: 1px solid var(--border); vertical-align: middle; }
tbody tr:hover { background: var(--panel-2); }
.col-thumb { width: 6.75rem; padding: 0.375rem 0.25rem 0.375rem 0.5rem; }
.col-thumb :deep(.thumb) { width: 6rem; border-radius: 0.375rem; }
.col-title a { color: var(--text); font-weight: 550; }
.col-title { min-width: 14rem; }
.col-menu { width: 2.75rem; text-align: right; padding-right: 0.5rem; }
.gone { margin-left: 0.375rem; padding: 0 0.25rem; border-radius: 0.25rem; background: var(--gone-bg, #dc2626); cursor: help; }
.nowrap { white-space: nowrap; }
.linkish { border: 0; background: none; padding: 0; color: var(--text-2); font: inherit; cursor: pointer; text-align: left; }
.linkish:hover { color: var(--accent); }
.table-pop { position: fixed; z-index: 110; padding: 0.25rem; box-shadow: var(--shadow); pointer-events: none; animation: grow 0.15s var(--ease); }
@keyframes grow { from { opacity: 0; transform: scale(0.92); } }
tr.picked td { background: var(--accent-soft); }
</style>
