<script setup>
// The window behind askConfirm() (stores/confirm.js), mounted once in App.vue. It sits
// above any other dialog; Escape, a click outside and the browser's back / forward
// answer "Cancel", Enter answers OK -- without reaching the dialog underneath.
import { watch, nextTick, ref, onBeforeUnmount } from "vue";
import { confirmState, settleConfirm } from "../stores/confirm.js";

const okBtn = ref(null);
const field = ref(null);
function onKey(e) {
  if (!confirmState.open) return;
  if (e.key === "Escape" || e.key === "Enter") {
    e.preventDefault();
    e.stopImmediatePropagation();
    settleConfirm(e.key === "Enter");
  }
}
const onHistory = () => settleConfirm(false);
watch(() => confirmState.open, async (open) => {
  if (open) {
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("popstate", onHistory);
    await nextTick();
    (field.value || okBtn.value)?.focus();
  } else {
    document.removeEventListener("keydown", onKey, true);
    window.removeEventListener("popstate", onHistory);
  }
});
onBeforeUnmount(() => { document.removeEventListener("keydown", onKey, true); window.removeEventListener("popstate", onHistory); });
</script>

<template>
  <Teleport to="body">
    <Transition name="cf">
      <div v-if="confirmState.open" class="cf-overlay" @mousedown.self="settleConfirm(false)">
        <div class="cf card" role="alertdialog" aria-modal="true" :aria-label="confirmState.title" aria-describedby="cf-msg">
          <h3>{{ confirmState.title }}</h3>
          <p id="cf-msg">{{ confirmState.message }}</p>
          <template v-if="confirmState.input">
            <input ref="field" v-model="confirmState.value" class="input cf-input" type="password" autocomplete="off" :aria-label="confirmState.title">
            <small v-if="confirmState.error" class="error-text">{{ confirmState.error }}</small>
          </template>
          <div class="cf-foot">
            <button class="btn" @click="settleConfirm(false)">{{ confirmState.cancel }}</button>
            <button ref="okBtn" class="btn" :class="confirmState.danger ? 'btn-danger' : 'btn-primary'" @click="settleConfirm(true)">{{ confirmState.ok }}</button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.cf-overlay { position: fixed; inset: 0; z-index: 120; background: var(--overlay); display: flex; align-items: flex-start;
  justify-content: center; padding: 18vh 1rem 1rem; backdrop-filter: blur(2px); }
.cf { width: 100%; max-width: 27rem; padding: 1.25rem 1.25rem 1rem; box-shadow: var(--shadow); }
.cf h3 { font-size: 1rem; margin: 0 0 0.5rem; }
.cf p { margin: 0; font-size: 0.9rem; color: var(--text-2); white-space: pre-line; overflow-wrap: anywhere; }
.cf-input { width: 100%; margin-top: 0.75rem; }
.cf .error-text { display: block; margin-top: 0.35rem; font-size: 0.8rem; }
.cf-foot { display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 1.25rem; }
.cf-enter-active, .cf-leave-active { transition: opacity 0.14s; }
.cf-enter-from, .cf-leave-to { opacity: 0; }
</style>
