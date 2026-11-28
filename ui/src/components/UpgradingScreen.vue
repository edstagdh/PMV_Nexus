<script setup>
// 3.12.0: shown instead of the app while long upgrade steps run (stores/upgrade.js).
import { computed } from "vue";
import { upgrade } from "../stores/upgrade.js";

const percent = computed(() => {
  if (!upgrade.total) return null;
  const part = upgrade.step_total ? (upgrade.step_done || 0) / upgrade.step_total : 0;
  return Math.min(100, Math.round(((upgrade.done + part) / upgrade.total) * 100));
});
</script>

<template>
  <div class="upgrading" role="status" aria-live="polite">
    <div class="card box">
      <div class="spinner" />
      <h1>{{ $t("Upgrading PMV Nexus") }}</h1>
      <p class="muted">{{ $t("Your library is being moved to the new version's format. It can be used again as soon as this is done — leave the app running. A copy of the database was made first.") }}</p>
      <p v-if="upgrade.current" class="step">{{ upgrade.current }}</p>
      <div v-if="percent !== null" class="bar"><div :style="{ width: `${percent}%` }" /></div>
      <p v-if="upgrade.total" class="muted small">{{ $t("Step {n} of {total}", { n: Math.min(upgrade.done + 1, upgrade.total), total: upgrade.total }) }}</p>
      <p v-if="upgrade.error" class="error">{{ upgrade.error }}</p>
    </div>
  </div>
</template>

<style scoped>
.upgrading { min-height: 100vh; display: grid; place-items: center; padding: 1rem; }
.box { max-width: 34rem; padding: 1.75rem; display: flex; flex-direction: column; gap: 0.75rem; align-items: center; text-align: center; }
h1 { font-size: 1.35rem; margin: 0; }
p { margin: 0; }
.step { font-weight: 600; }
.small { font-size: 0.8rem; }
.bar { width: 100%; height: 0.5rem; border-radius: 999px; background: var(--panel-2); overflow: hidden; }
.bar div { height: 100%; background: var(--accent); transition: width 0.4s; }
.error { color: var(--danger); }
.spinner { width: 1.75rem; height: 1.75rem; border-radius: 50%; border: 3px solid var(--border-strong); border-top-color: var(--accent); animation: spin 0.8s linear infinite; }
</style>
