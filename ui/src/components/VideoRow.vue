<script setup>
import { computed } from "vue";
import VideoThumb from "./VideoThumb.vue";
import ActionBar from "./ActionBar.vue";
import { itemUrlTitle, formatDate, workStatus } from "../lib/format.js";

const props = defineProps({ item: Object });
const emit = defineEmits(["filter"]);
const status = computed(() => workStatus(props.item));
</script>

<template>
  <article class="vrow card">
    <router-link :to="{ name: 'video', params: { id: item.id } }" class="vrow-thumb"><VideoThumb :item="item" /></router-link>
    <div class="vrow-body">
      <router-link :to="{ name: 'video', params: { id: item.id } }" class="vrow-title">{{ itemUrlTitle(item) }}</router-link>
      <div class="vrow-meta">
        <button v-if="item.creator" class="linkish text-creator" @click="emit('filter', 'creator', item.creator)">{{ item.creator }}</button>
        <span v-if="item.download_datetime" class="muted">Downloaded {{ formatDate(item.download_datetime) }}</span>
        <span v-if="item.site_stats?.views != null" class="muted">{{ item.site_stats.views.toLocaleString() }} views</span>
        <span v-if="status" class="status" :class="status.kind" :title="status.detail">{{ status.label }}</span>
      </div>
      <div v-if="item.performers?.length" class="vrow-chips">
        <button v-for="p in item.performers" :key="p" class="chip chip-performer" @click="emit('filter', 'performer', p)">{{ p }}</button>
      </div>
      <div v-if="item.tags?.length" class="vrow-chips">
        <button v-for="t in item.tags.slice(0, 12)" :key="t" class="chip chip-tag" @click="emit('filter', 'tag', t)">#{{ t }}</button>
        <span v-if="item.tags.length > 12" class="muted" :title="item.tags.slice(12).join(', ')">+{{ item.tags.length - 12 }}</span>
      </div>
      <ActionBar :item="item" class="vrow-actions" />
    </div>
  </article>
</template>

<style scoped>
.vrow { display: grid; grid-template-columns: 17rem 1fr; gap: 1rem; padding: 0.625rem; }
.vrow-body { display: flex; flex-direction: column; gap: 0.375rem; min-width: 0; }
.vrow-title { color: var(--text); font-weight: 650; font-size: 0.95rem; }
.vrow-title:hover { color: var(--accent); text-decoration: none; }
.vrow-meta { display: flex; flex-wrap: wrap; gap: 0.25rem 0.875rem; font-size: 0.8rem; align-items: center; }
.vrow-chips { display: flex; flex-wrap: wrap; gap: 0.25rem; align-items: center; }
.vrow-chips .chip { height: 1.375rem; font-size: 0.72rem; padding: 0 0.5rem; }
.vrow-actions { margin-top: auto; padding-top: 0.25rem; }
.status { font-weight: 600; }
.status.busy { color: var(--accent); }
.status.failed { color: var(--danger); }
.linkish { border: 0; background: none; padding: 0; color: var(--text-2); font: inherit; cursor: pointer; }
.linkish:hover { color: var(--accent); }
@media (max-width: 40rem) { .vrow { grid-template-columns: 1fr; } }
</style>
