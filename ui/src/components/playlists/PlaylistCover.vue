<script setup>
// A playlist's cover (2.18.0): the collage of its first 4 videos (default), one
// video's cover, or an image of the owner's own -- `playlist.cover` says which.
import { computed } from "vue";
import Icon from "../Icon.vue";

const props = defineProps({ playlist: Object });
const thumb = (id) => `/api/library/${encodeURIComponent(id)}/thumbnail`;
const cover = computed(() => props.playlist.cover || { mode: "collage" });
const ids = computed(() => (props.playlist.cover_ids || []).slice(0, 4));
</script>

<template>
  <div v-if="cover.mode === 'image'" class="cover"><img :src="cover.url" alt="" loading="lazy"><slot /></div>
  <div v-else-if="cover.mode === 'video'" class="cover"><img :src="thumb(cover.item_id)" alt="" loading="lazy"><slot /></div>
  <div v-else class="cover" :class="`n${ids.length}`">
    <img v-for="id in ids" :key="id" :src="thumb(id)" alt="" loading="lazy">
    <span v-if="!ids.length" class="empty-cover"><Icon :name="playlist.system === 'favorites' ? 'heart' : 'playlist'" :size="28" /></span>
    <slot />
  </div>
</template>

<style scoped>
.cover { position: relative; aspect-ratio: 16 / 9; background: var(--thumb-bg); display: grid; overflow: hidden; }
.cover.n2, .cover.n3, .cover.n4 { grid-template-columns: 1fr 1fr; }
.cover.n3 img:first-child { grid-row: span 2; }
.cover.n4 { grid-template-rows: 1fr 1fr; }
.cover.n2 { grid-template-rows: 1fr; }
.cover img { width: 100%; height: 100%; object-fit: cover; min-height: 0; display: block; }
.empty-cover { display: grid; place-items: center; color: var(--text-dim); }
</style>
