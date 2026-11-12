<script setup>
import { onMounted, onBeforeUnmount } from "vue";
import Icon from "./Icon.vue";

defineProps({ title: String, width: { type: String, default: "32.5rem" } });
const emit = defineEmits(["close"]);
const onKey = (e) => { if (e.key === "Escape") emit("close"); };
// The browser's back / forward buttons leave the page the dialog belongs to: close it (2.18.0).
const onHistory = () => emit("close");
onMounted(() => { document.addEventListener("keydown", onKey); window.addEventListener("popstate", onHistory); });
onBeforeUnmount(() => { document.removeEventListener("keydown", onKey); window.removeEventListener("popstate", onHistory); });
</script>

<template>
  <Teleport to="body">
    <div class="modal-overlay" @mousedown.self="emit('close')">
      <div class="modal card" role="dialog" aria-modal="true" :aria-label="title" :style="{ maxWidth: width }">
        <header class="modal-head">
          <h3>{{ title }}</h3>
          <button class="btn btn-ghost btn-icon btn-sm" aria-label="Close" @click="emit('close')"><Icon name="x" /></button>
        </header>
        <div class="modal-body"><slot /></div>
        <footer v-if="$slots.footer" class="modal-foot"><slot name="footer" /></footer>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.modal-overlay { position: fixed; inset: 0; z-index: 100; background: var(--overlay); display: flex; align-items: flex-start; justify-content: center; padding: 10vh 1rem 1rem; backdrop-filter: blur(2px); }
.modal { width: 100%; box-shadow: var(--shadow); display: flex; flex-direction: column; max-height: 80vh; animation: pop 0.16s var(--ease); }
.modal-head { display: flex; align-items: center; justify-content: space-between; padding: 0.875rem 1rem 0.625rem; }
.modal-head h3 { font-size: 1rem; }
.modal-body { padding: 0.25rem 1rem 1rem; overflow-y: auto; }
.modal-foot { display: flex; justify-content: flex-end; gap: 0.5rem; padding: 0.75rem 1rem; border-top: 1px solid var(--border); }
@keyframes pop { from { transform: translateY(0.5rem) scale(0.98); opacity: 0; } }
</style>
