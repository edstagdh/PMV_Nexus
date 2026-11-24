<script setup>
// Text from a task that names a video (3.5.0): the video's title in it becomes a link to its
// page ("Finding songs: “After Party”" -> After Party links). `item`: {id, title} or null.
import { computed } from "vue";

const props = defineProps({ text: { type: String, default: "" }, item: { type: Object, default: null } });
const parts = computed(() => {
  const t = props.text || "", name = props.item?.title;
  const at = name ? t.indexOf(name) : -1;
  return at < 0 ? null : { before: t.slice(0, at), name, after: t.slice(at + name.length) };
});
</script>

<template>
  <template v-if="parts">{{ parts.before }}<router-link :to="{ name: 'video', params: { id: item.id } }" class="item-link"
    :title="$t('Open “{title}”', { title: item.title })" @click.stop>{{ parts.name }}</router-link>{{ parts.after }}</template>
  <template v-else>{{ text }}</template>
</template>

<style scoped>
.item-link { color: inherit; text-decoration: underline; text-decoration-color: color-mix(in srgb, currentColor 40%, transparent);
  text-underline-offset: 2px; }
.item-link:hover { color: var(--accent); text-decoration-color: currentColor; }
</style>
