<script setup>
// The playlist being played (stores/player.js), shown beside the video:
// what's next, and Previous / Next / Shuffle / Repeat / Autoplay.
import { computed, ref, watch, nextTick } from "vue";
import { RouterLink } from "vue-router";
import Icon from "./Icon.vue";
import VideoThumb from "./VideoThumb.vue";
import {
  player, queueNext, queuePrev, playQueueItem, toggleShuffle, toggleRepeat, toggleQueueAutoplay, clearQueue,
} from "../stores/player.js";

const q = computed(() => player.queue);
const list = ref(null);
const rows = computed(() => (q.value ? q.value.order.map((id, n) => ({ n, item: q.value.items[id] })).filter((r) => r.item) : []));

// Keep the current video in view inside the list.
watch(() => q.value?.index, async () => {
  await nextTick();
  list.value?.querySelector(".row.current")?.scrollIntoView({ block: "nearest" });
}, { immediate: true });
</script>

<template>
  <section v-if="q" class="queue card">
    <header class="head">
      <div class="name">
        <RouterLink :to="{ name: 'playlist', params: { id: q.playlistId } }" class="title">{{ q.name }}</RouterLink>
        <span class="muted small">{{ q.index + 1 }} / {{ q.order.length }}<template v-if="player.queueEnded"> · finished</template></span>
      </div>
      <button class="btn btn-ghost btn-icon btn-sm" title="Stop playing this playlist" @click="clearQueue"><Icon name="x" :size="15" /></button>
    </header>
    <div class="controls">
      <button class="btn btn-ghost btn-icon btn-sm" title="Previous" @click="queuePrev"><Icon name="skipPrev" :size="15" /></button>
      <button class="btn btn-ghost btn-icon btn-sm" title="Next" :disabled="q.index >= q.order.length - 1 && !q.repeat" @click="queueNext()"><Icon name="skipNext" :size="15" /></button>
      <span class="sep" />
      <button class="btn btn-ghost btn-sm toggle" :class="{ on: q.shuffle }" :aria-pressed="q.shuffle" title="Shuffle" @click="toggleShuffle"><Icon name="shuffle" :size="15" />Shuffle</button>
      <button class="btn btn-ghost btn-sm toggle" :class="{ on: q.repeat }" :aria-pressed="q.repeat" title="Start over after the last video" @click="toggleRepeat"><Icon name="repeat" :size="15" />Repeat</button>
      <button class="btn btn-ghost btn-sm toggle" :class="{ on: q.autoplay }" :aria-pressed="q.autoplay" title="Play the next video when one ends" @click="toggleQueueAutoplay"><Icon name="play" :size="13" />Autoplay</button>
    </div>
    <ol ref="list" class="list scroll-thin">
      <li v-for="r in rows" :key="r.item.id" class="row" :class="{ current: r.n === q.index }">
        <button class="row-btn" :title="r.item.title" @click="playQueueItem(r.item.id)">
          <span class="num muted">{{ r.n === q.index ? "▶" : r.n + 1 }}</span>
          <span class="thumb"><VideoThumb :item="r.item" compact /></span>
          <span class="text">
            <span class="t">{{ r.item.title || r.item.url }}</span>
            <span class="muted small">{{ r.item.creator }}</span>
          </span>
        </button>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.queue { display: flex; flex-direction: column; overflow: hidden; }
.head { display: flex; align-items: flex-start; gap: 0.5rem; padding: 0.75rem 0.75rem 0.25rem 0.875rem; }
.name { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.title { font-weight: 700; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.small { font-size: 0.75rem; }
.controls { display: flex; flex-wrap: wrap; align-items: center; gap: 0.125rem; padding: 0 0.5rem 0.5rem; border-bottom: 1px solid var(--border); }
.sep { flex: 1; }
.toggle { gap: 0.3rem; font-size: 0.75rem; padding: 0 0.45rem; color: var(--text-dim); }
.toggle.on { color: var(--accent); background: var(--accent-soft); }
.list { list-style: none; margin: 0; padding: 0.25rem; max-height: 22rem; overflow-y: auto; }
.row-btn { width: 100%; display: flex; align-items: center; gap: 0.5rem; padding: 0.3rem; border: 0; border-radius: var(--radius-sm);
  background: none; color: var(--text); font: inherit; text-align: left; cursor: pointer; }
.row-btn:hover { background: var(--panel-2); }
.row.current .row-btn { background: var(--accent-soft); }
.num { width: 1.25rem; text-align: center; font-size: 0.72rem; flex-shrink: 0; }
.row.current .num { color: var(--accent); }
.thumb { width: 6.5rem; flex-shrink: 0; }
.text { min-width: 0; display: flex; flex-direction: column; }
.t { font-size: 0.8rem; font-weight: 600; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }
</style>
