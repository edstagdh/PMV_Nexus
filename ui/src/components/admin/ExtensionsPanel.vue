<script setup>
// Server Settings -> Extensions (2.30.0): the add-ons found in the app's
// extensions/ folder, whether they loaded, and the settings panels they bring
// (each drawn by components/ExtensionForm.vue). Only shown when there are any.
import ExtensionForm from "../ExtensionForm.vue";
import { ext } from "../../stores/extensions.js";
</script>

<template>
  <div class="xp">
    <section class="card panel">
      <h2>{{ $t("Installed extensions") }}</h2>
      <p class="help-text"><Tx msg="Add-ons from the {code1} folder in the app folder. Removing one's folder (with the app stopped) removes it."><template #code1><code>extensions</code></template></Tx></p>
      <ul class="list">
        <li v-for="e in ext.extensions" :key="e.name">
          <strong>{{ e.name }}</strong><span v-if="e.version" class="muted ver">{{ e.version }}</span>
          <span class="pill" :class="e.ok ? 'pill-ok' : 'pill-bad'">{{ e.ok ? $t("Loaded") : $t("Failed") }}</span>
          <div v-if="e.description" class="muted small">{{ e.description }}</div>
          <div v-if="e.error" class="error-text small">{{ $t("{error} — details in the log.", { error: e.error }) }}</div>
        </li>
      </ul>
    </section>
    <section v-for="p in ext.panels" :key="p.id" class="card panel">
      <h2>{{ p.label }}</h2>
      <ExtensionForm :endpoint="`/api/extensions/panels/${encodeURIComponent(p.id)}`" :heading="p.label" embedded />
    </section>
  </div>
</template>

<style scoped>
.xp { display: flex; flex-direction: column; gap: 1rem; }
.list { list-style: none; margin: 0.5rem 0 0; padding: 0; display: flex; flex-direction: column; gap: 0.6rem; }
.ver { margin-inline-start: 0.4rem; }
.pill { margin-inline-start: 0.5rem; }
.small { font-size: 0.8rem; }
</style>
