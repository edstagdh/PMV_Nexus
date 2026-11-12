<script setup>
import VideoThumb from "./VideoThumb.vue";
import ActionMenu from "./ActionMenu.vue";
import { itemUrlTitle } from "../lib/format.js";

defineProps({ item: Object, small: Boolean });
const emit = defineEmits(["filter"]);
</script>

<template>
  <article class="vcard" :class="{ small }">
    <router-link :to="{ name: 'video', params: { id: item.id } }" class="vcard-link" :title="itemUrlTitle(item)">
      <VideoThumb :item="item" />
    </router-link>
    <div class="vcard-body">
      <div class="vcard-head">
        <router-link :to="{ name: 'video', params: { id: item.id } }" class="vcard-title">{{ itemUrlTitle(item) }}</router-link>
        <ActionMenu v-if="!small" :item="item" />
      </div>
      <div class="vcard-meta">
        <button v-if="item.creator" class="linkish text-creator" :title="`Show videos by ${item.creator}`" @click="emit('filter', 'creator', item.creator)">{{ item.creator }}</button>
      </div>
      <div v-if="!small && item.performers?.length" class="vcard-people">
        <button v-for="p in item.performers.slice(0, 3)" :key="p" class="chip chip-performer" @click="emit('filter', 'performer', p)">{{ p }}</button>
        <span v-if="item.performers.length > 3" class="muted more" :title="item.performers.slice(3).join(', ')">+{{ item.performers.length - 3 }}</span>
      </div>
    </div>
  </article>
</template>

<style scoped>
.vcard { display: flex; flex-direction: column; gap: 0.5rem; min-width: 0; }
.vcard-link { display: block; border-radius: var(--radius); transition: transform 0.15s var(--ease); }
.vcard-link:hover { transform: translateY(-2px); }
.vcard-body { display: flex; flex-direction: column; gap: 0.25rem; padding: 0 0.125rem; min-width: 0; }
.vcard-head { display: flex; align-items: flex-start; gap: 0.25rem; }
.vcard-head :deep(.kebab) { margin: -0.25rem -0.25rem 0 auto; }
.vcard-title { flex: 1; min-width: 0; color: var(--text); font-weight: 600; font-size: 0.875rem; line-height: 1.35; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.vcard-title:hover { color: var(--accent); text-decoration: none; }
.vcard-meta { display: flex; align-items: center; gap: 0.5rem; font-size: 0.8rem; min-width: 0; }
.vcard-meta > * { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.vcard-people { display: flex; gap: 0.25rem; flex-wrap: wrap; align-items: center; }
.vcard-people .chip { height: 1.375rem; font-size: 0.72rem; padding: 0 0.5rem; max-width: 9rem; }
.more { font-size: 0.75rem; cursor: default; }
.small .vcard-title { font-size: 0.8rem; }
.linkish { border: 0; background: none; padding: 0; color: var(--text-2); font: inherit; cursor: pointer; min-width: 0; }
.linkish:hover { color: var(--accent); }
</style>
