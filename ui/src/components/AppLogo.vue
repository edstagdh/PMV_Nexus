<script setup>
// The app's logo (src/assets/logo.svg): an arch around a play button.
import logoUrl from "../assets/logo.svg";

defineProps({ size: { type: String, default: "2rem" } });
</script>

<template>
  <img :src="logoUrl" alt="" aria-hidden="true" class="app-logo" :style="{ width: size, height: size }">
</template>

<style scoped>
.app-logo { display: block; flex-shrink: 0; }
</style>
