<script setup>
// Grey silhouette for a performer without a photo (2.17.0): female or male
// (ThePornDB's gender, or what was set on the profile of someone not on it).
defineProps({ gender: { type: String, default: "female" } });
</script>

<template>
  <svg class="ph" viewBox="0 0 120 180" role="img" :aria-label="gender === 'male' ? 'No photo (male)' : 'No photo (female)'"
       preserveAspectRatio="xMidYMax meet">
    <template v-if="gender === 'male'">
      <circle cx="60" cy="62" r="25" />
      <path d="M60 96c-28 0-46 14-50 38l-2 46h104l-2-46c-4-24-22-38-50-38z" />
    </template>
    <template v-else>
      <!-- hair behind the head, falling to the shoulders -->
      <path d="M60 30c-22 0-34 16-33 38 1 18 -4 34 -10 44 10 4 22 4 30 0h26c8 4 20 4 30 0-6-10-11-26-10-44 1-22-11-38-33-38z" class="hair" />
      <ellipse cx="60" cy="63" rx="21" ry="24" />
      <path d="M60 100c-24 0-40 12-45 32-3 12-5 30-5 48h100c0-18-2-36-5-48-5-20-21-32-45-32z" />
    </template>
  </svg>
</template>

<style scoped>
.ph { width: 100%; height: 100%; display: block; background: var(--panel-2); }
.ph * { fill: color-mix(in srgb, var(--text-dim) 45%, var(--panel-2)); }
.ph .hair { fill: color-mix(in srgb, var(--text-dim) 30%, var(--panel-2)); }
</style>
