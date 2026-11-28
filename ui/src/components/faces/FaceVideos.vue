<script setup>
// Performer page (3.12.0; 3.13.0): an approved face match now adds the performer to the video, so
// those videos are in the list below like any other. Here: how many videos wait for you on the
// Faces page with this performer's face possibly in them (people who manage performers).
import { ref, watch } from "vue";
import { RouterLink } from "vue-router";
import { api, qs } from "../../lib/api.js";
import { hasPermission } from "../../stores/session.js";

const props = defineProps({ name: String });
const info = ref(null);
watch(() => props.name, async (name) => {
  if (!hasPermission("performers.manage")) return;
  const d = await api(`/api/performers/faces?${qs({ name })}`).catch(() => null);
  if (name === props.name) info.value = d;
}, { immediate: true });
</script>

<template>
  <p v-if="info?.waiting" class="fv muted small">
    {{ $tn("Their face may be in {n} more video, waiting for you:", "Their face may be in {n} more videos, waiting for you:", info.waiting) }}
    <RouterLink :to="{ name: 'portal', params: { tab: 'faces' }, query: { performer: info.performer_id } }">{{ $t("check them on the Faces page") }}</RouterLink>
  </p>
</template>

<style scoped>
.fv { margin: 0; font-size: 0.85rem; }
</style>
