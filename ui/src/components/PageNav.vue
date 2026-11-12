<script setup>
import { computed } from "vue";
import Icon from "./Icon.vue";

const props = defineProps({ page: Number, totalPages: Number });
const emit = defineEmits(["go"]);

// 1 … 4 5 [6] 7 8 … 40
const pages = computed(() => {
  const { page, totalPages: n } = props;
  const set = new Set([1, n, page - 2, page - 1, page, page + 1, page + 2].filter((p) => p >= 1 && p <= n));
  const sorted = [...set].sort((a, b) => a - b);
  const out = [];
  sorted.forEach((p, i) => { if (i && p - sorted[i - 1] > 1) out.push("…" + p); out.push(p); });
  return out;
});
</script>

<template>
  <nav v-if="totalPages > 1" class="pager" aria-label="Pages">
    <button class="btn btn-sm btn-icon" :disabled="page <= 1" aria-label="Previous page" @click="emit('go', page - 1)"><Icon name="left" /></button>
    <template v-for="p in pages" :key="p">
      <span v-if="typeof p === 'string'" class="gap">…</span>
      <button v-else class="btn btn-sm" :class="{ active: p === page }" :aria-current="p === page ? 'page' : null" @click="emit('go', p)">{{ p }}</button>
    </template>
    <button class="btn btn-sm btn-icon" :disabled="page >= totalPages" aria-label="Next page" @click="emit('go', page + 1)"><Icon name="right" /></button>
  </nav>
</template>

<style scoped>
.pager { display: flex; align-items: center; justify-content: center; gap: 0.25rem; flex-wrap: wrap; }
.pager .btn { min-width: 2rem; }
.gap { color: var(--text-dim); padding: 0 0.25rem; }
</style>
