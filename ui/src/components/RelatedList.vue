<script setup>
import VideoThumb from "./VideoThumb.vue";
import ActionMenu from "./ActionMenu.vue";
import { itemUrlTitle } from "../lib/format.js";

defineProps({ items: Array });
</script>

<template>
  <ul class="related">
    <li v-for="item in items" :key="item.id" class="rel-row">
      <router-link :to="{ name: 'video', params: { id: item.id } }" class="rel">
        <VideoThumb :item="item" compact class="rel-thumb" />
        <div class="rel-body">
          <div class="rel-title">{{ itemUrlTitle(item) }}</div>
          <div class="muted rel-meta text-creator">{{ item.creator }}</div>
          <div v-if="item.performers?.length" class="muted rel-meta">
            <template v-for="(p, i) in item.performers.slice(0, 3)" :key="p"><span v-if="i">, </span><span class="text-performer">{{ p }}</span></template>
          </div>
        </div>
      </router-link>
      <span class="rel-menu"><ActionMenu :item="item" /></span>
    </li>
  </ul>
</template>

<style scoped>
.related { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.5rem; }
.rel-row { position: relative; }
.rel-row .rel-menu { position: absolute; top: 0.125rem; right: 0; }
.rel-row .rel-body { padding-right: 1.75rem; }
.rel { display: grid; grid-template-columns: 10.5rem 1fr; gap: 0.625rem; padding: 0.25rem; border-radius: var(--radius); color: inherit; }
.rel:hover { background: var(--panel-2); text-decoration: none; }
.rel-body { min-width: 0; display: flex; flex-direction: column; gap: 0.125rem; }
.rel-title { font-weight: 600; font-size: 0.84rem; line-height: 1.3; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; color: var(--text); }
.rel:hover .rel-title { color: var(--accent); }
.rel-meta { font-size: 0.75rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
