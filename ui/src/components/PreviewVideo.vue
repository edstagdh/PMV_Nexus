<script setup>
// Hover preview clip. Unloads itself when removed: a <video> that is just
// taken out of the page can keep its download open, and the browser only
// allows ~6 connections to the app at once, so a few hovers could leave
// later previews (and thumbnails) waiting.
import { ref, onBeforeUnmount } from "vue";

const props = defineProps({ src: String, itemId: String });
const emit = defineEmits(["failed"]);
const el = ref(null);

function onError() {
  const code = el.value?.error?.code;
  // 4 = MEDIA_ERR_SRC_NOT_SUPPORTED: missing file (404) or a format the browser can't play.
  const reason = code === 4 ? "missing or unplayable" : "failed to load";
  console.warn(`[preview] ${props.itemId}: preview ${reason} (${props.src})`);
  emit("failed", reason);
}

onBeforeUnmount(() => {
  const v = el.value;
  if (!v) return;
  v.pause();
  v.removeAttribute("src");
  v.load();
});
</script>

<template>
  <video ref="el" :src="src" :data-item-id="itemId" autoplay muted loop playsinline preload="auto" @error="onError" />
</template>
