<script setup>
// ♥ on the video page: adds the video to / removes it from your built-in
// Favorites playlist (GET/POST /api/playlists/favorites).
import { ref, watch } from "vue";
import Icon from "./Icon.vue";
import { api, qs } from "../lib/api.js";
import { toast } from "../stores/toast.js";
import { setFavoriteLocal } from "../stores/favorites.js";

const props = defineProps({ itemId: String });
const on = ref(false);
const busy = ref(false);

watch(() => props.itemId, async (id) => {
  on.value = false;
  if (!id) return;
  try { on.value = (await api(`/api/playlists/favorites?${qs({ item_id: id })}`)).favorite; } catch { /* leave it off */ }
}, { immediate: true });

async function toggle() {
  busy.value = true;
  try {
    on.value = (await api("/api/playlists/favorites", { method: "POST", body: { item_id: props.itemId, on: !on.value } })).favorite;
    setFavoriteLocal(props.itemId, on.value);   // the ♥ badge on thumbnails
    toast(on.value ? "Added to Favorites" : "Removed from Favorites");
  } catch (err) { toast(err.message || "That didn't work", { type: "error" }); }
  finally { busy.value = false; }
}
</script>

<template>
  <button class="btn btn-sm fav" :class="{ on }" :disabled="busy" :aria-pressed="on"
          :title="on ? 'In your Favorites — click to remove' : 'Add to Favorites'" @click="toggle">
    <Icon name="heart" :size="15" />{{ on ? "Favorite" : "Add to Favorites" }}
  </button>
</template>

<style scoped>
.fav { gap: 0.375rem; }
.fav.on { color: var(--accent); border-color: var(--accent); }
.fav.on :deep(svg) { fill: currentColor; }
</style>
