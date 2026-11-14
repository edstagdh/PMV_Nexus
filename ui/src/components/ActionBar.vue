<script setup>
// The item's actions as a row of buttons (list view, video page).
import { computed } from "vue";
import Icon from "./Icon.vue";
import { itemActions } from "../lib/actions.js";

const props = defineProps({ item: Object, context: { type: String, default: "library" }, size: { type: String, default: "sm" } });
const actions = computed(() => itemActions(props.item, { context: props.context }));
</script>

<template>
  <div class="action-bar">
    <template v-for="a in actions" :key="a.key">
      <a v-if="a.href" class="btn" :class="size === 'sm' && 'btn-sm'" :href="a.href" target="_blank" rel="noopener noreferrer">
        <Icon :name="a.icon" :size="15" />{{ a.label }}
      </a>
      <button v-else class="btn" :class="[size === 'sm' && 'btn-sm', a.danger && 'btn-danger']" :title="a.title" @click.stop="a.run()">
        <Icon :name="a.icon" :size="15" />{{ a.label }}
      </button>
    </template>
  </div>
</template>

<style scoped>
.action-bar { display: flex; flex-wrap: wrap; gap: 0.375rem; }
</style>
