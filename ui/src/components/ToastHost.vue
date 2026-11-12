<script setup>
import { useRouter } from "vue-router";
import { toasts, dismissToast } from "../stores/toast.js";

const router = useRouter();
function open(t) {
  dismissToast(t.id);
  if (t.link) router.push(t.link);
}
</script>

<template>
  <div class="toast-host" aria-live="polite">
    <TransitionGroup name="toast">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="[`toast-${t.type}`, { linked: t.link }]" @click="open(t)">
        <div class="msg">{{ t.message }}</div>
        <div v-if="t.body" class="body">{{ t.body }}</div>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-host { position: fixed; right: 1rem; bottom: 1rem; z-index: 200; display: flex; flex-direction: column; gap: 0.5rem; align-items: flex-end; pointer-events: none; }
.toast { pointer-events: auto; cursor: pointer; max-width: min(26.25rem, calc(100vw - 2rem)); padding: 0.625rem 0.875rem; border-radius: var(--radius); background: var(--panel); border: 1px solid var(--border-strong); border-left-width: 4px; box-shadow: var(--shadow); font-size: 0.8125rem; }
.toast .body { margin-top: 0.15rem; color: var(--text-2); font-size: 0.76rem; overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.toast.linked .msg { text-decoration: underline; text-decoration-color: var(--border-strong); text-underline-offset: 2px; }
.toast-error { border-color: var(--danger); }
.toast-success { border-color: var(--success); }
.toast-warning { border-color: var(--warning, #f59e0b); }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(0.5rem); }
.toast-enter-active, .toast-leave-active { transition: all 0.2s var(--ease); }
</style>
