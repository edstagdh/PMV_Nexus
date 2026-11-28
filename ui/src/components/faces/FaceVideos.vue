<script setup>
// Performer page -> "Recognised by face in" (3.12.0): videos where this performer's face was found
// and approved (Management -> Faces), which don't list them in their details.
import { ref, watch } from "vue";
import { RouterLink } from "vue-router";
import { api, qs } from "../../lib/api.js";

const props = defineProps({ name: String });
const items = ref([]);
watch(() => props.name, async (name) => {
  const d = await api(`/api/performers/faces?${qs({ name })}`).catch(() => null);
  if (name === props.name) items.value = d?.items || [];
}, { immediate: true });
</script>

<template>
  <section v-if="items.length" class="card panel fv">
    <h3 class="sub">{{ $t("Recognised by face in ({n})", { n: items.length }) }}</h3>
    <p class="muted small">{{ $t("Videos that don't list them, where their face was found and approved.") }}</p>
    <ul>
      <li v-for="v in items" :key="v.id">
        <RouterLink :to="{ name: 'video', params: { id: v.id } }" class="th"><img v-if="v.thumb" :src="v.thumb" alt="" loading="lazy"></RouterLink>
        <RouterLink :to="{ name: 'video', params: { id: v.id } }" class="title">{{ v.title }}</RouterLink>
        <span class="muted small">{{ v.creator }}</span>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.fv { display: flex; flex-direction: column; gap: 0.5rem; }
.sub { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); margin: 0; }
.fv p { margin: 0; }
.small { font-size: 0.8rem; }
ul { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr)); gap: 0.75rem; }
li { display: flex; flex-direction: column; gap: 0.2rem; min-width: 0; }
.th { aspect-ratio: 16 / 9; border-radius: 0.4rem; overflow: hidden; background: var(--panel-2); }
.th img { width: 100%; height: 100%; object-fit: cover; display: block; }
.title { color: var(--text); font-weight: 600; font-size: 0.85rem; overflow-wrap: anywhere; }
</style>
