<script setup>
// How to find songs (2.34.0; config.SongsConfig.method): Shazam, Gemini (AI) or both.
// The Gemini choices need its API key (Server Settings → Credentials).
import { computed } from "vue";

const props = defineProps({
  modelValue: { type: String, default: "shazam" },
  gemini: { type: Boolean, default: false },   // is Gemini's key saved?
  label: { type: String, default: "How" },
});
const emit = defineEmits(["update:modelValue"]);
const METHODS = [
  { key: "shazam", label: "Shazam", help: "Free, no key. Best for well-known songs." },
  { key: "shazam_gemini", label: "Shazam, then Gemini (AI) for what it missed", help: "Gemini only listens where Shazam found nothing." },
  { key: "both", label: "Shazam and Gemini (AI) — a second opinion", help: "Both listen; when Shazam finds the wrong song, Gemini's answer shows up next to it." },
  { key: "gemini", label: "Gemini (AI) only", help: "For videos without well-known songs. Uses only Gemini's daily free quota." },
];
const current = computed(() => METHODS.find((m) => m.key === props.modelValue) || METHODS[0]);
</script>

<template>
  <label class="smp">
    <span class="smp-label">{{ label }}</span>
    <select class="select" :value="modelValue" @change="emit('update:modelValue', $event.target.value)">
      <option v-for="m in METHODS" :key="m.key" :value="m.key" :disabled="m.key !== 'shazam' && !gemini">
        {{ m.label }}{{ m.key !== "shazam" && !gemini ? " — needs Gemini's API key" : "" }}</option>
    </select>
    <small class="muted">{{ current.help }}</small>
  </label>
</template>

<style scoped>
.smp { display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.85rem; }
.smp-label { font-weight: 600; }
.smp small { font-size: 0.78rem; }
</style>
