<script setup>
// My settings -> Video page -> Panic keys (2.24.0; 2.24.2: recorded, then confirmed).
//   1. "Recording keys": the keys show as the app detects them (at most 3).
//   2. Once they're all let go: "The app detected A + D" -> Use these keys / Try again / Cancel.
//   3. Saved only after "Use these keys".
import { ref, computed, onBeforeUnmount } from "vue";
import { session, savePref } from "../stores/session.js";
import { keyLabel, pausePanic, playerKeyClashes } from "../lib/panic.js";

const MAX_KEYS = 3;
const stage = ref("idle");     // idle | recording | confirm
const held = ref([]);          // [{code, label}] detected in this recording
const tooMany = ref(false);
let down = new Set();
const keys = computed(() => session.prefs.panicKeys || []);
const plain = (list) => list.length === 1 && !/^(Control|Alt|Meta|OS|F\d+)/.test(list[0].code);
const joined = (list) => list.map((k) => k.label).join(" + ");
const clashes = computed(() => playerKeyClashes(held.value));

function onDown(e) {
  e.preventDefault();          // while recording only: the keys mustn't do anything else
  e.stopImmediatePropagation();
  if (e.code === "Escape" && !held.value.length) { cancel(); return; }
  if (down.has(e.code)) return;
  down.add(e.code);
  if (held.value.some((k) => k.code === e.code)) return;
  if (held.value.length >= MAX_KEYS) { tooMany.value = true; return; }
  held.value = [...held.value, { code: e.code, label: keyLabel(e) }];
}
function onUp(e) {
  e.preventDefault();
  e.stopImmediatePropagation();
  down.delete(e.code);
  if (!down.size && held.value.length) { listen(false); stage.value = "confirm"; }
}
function listen(on) {
  pausePanic(on);
  const fn = on ? window.addEventListener : window.removeEventListener;
  fn("keydown", onDown, true);
  fn("keyup", onUp, true);
}
function record() {
  held.value = [];
  tooMany.value = false;
  down = new Set();
  stage.value = "recording";
  listen(true);
}
function confirmKeys() { savePref("panicKeys", held.value); stage.value = "idle"; }
function cancel() { listen(false); stage.value = "idle"; held.value = []; }
onBeforeUnmount(() => listen(false));
</script>

<template>
  <div class="panic">
    <!-- 1. Recording -->
    <template v-if="stage === 'recording'">
      <div class="state rec" aria-live="polite">
        <span class="dot" aria-hidden="true" /><strong>Recording keys…</strong>
        <span class="muted">Press the keys together (up to {{ MAX_KEYS }}), then let go. Esc to cancel.</span>
      </div>
      <div class="keys">
        <kbd v-for="k in held" :key="k.code">{{ k.label }}</kbd>
        <span v-if="!held.length" class="muted">Waiting for keys…</span>
      </div>
      <button class="btn btn-sm" @click="cancel">Cancel</button>
    </template>

    <!-- 2. Confirm what was detected -->
    <template v-else-if="stage === 'confirm'">
      <div class="state" aria-live="polite">
        <span>The app detected <strong>{{ held.length === 1 ? "this key" : `these ${held.length} keys` }}</strong>:</span>
      </div>
      <div class="keys"><kbd v-for="k in held" :key="k.code">{{ k.label }}</kbd></div>
      <small v-if="tooMany" class="warn">More than {{ MAX_KEYS }} keys were pressed — only the first {{ MAX_KEYS }} were kept.</small>
      <small v-if="plain(held)" class="warn">One key on its own is easy to press by accident — two or three is safer (e.g. A + D).</small>
      <span class="muted small">Not what you pressed? Some keyboards don't report certain keys pressed together — try again, or pick other keys.</span>
      <div class="notice" role="note">
        <strong>{{ held.length === 1 ? "This key belongs" : "These keys belong" }} to this site from now on.</strong>
        Here, outside text fields, the browser won't act on {{ held.length === 1 ? "it" : "them" }} — e.g. Firefox's
        “search for text when you start typing” — so the panic is never missed. With another Ctrl / Alt / Shift / ⌘ held
        (Ctrl + F, Ctrl + A…) the browser still gets {{ held.length === 1 ? "it" : "them" }}. Other sites aren't affected.
        <template v-if="clashes.length">
          <br><strong>Also the player's shortcut:</strong>
          <span v-for="(c, i) in clashes" :key="c.label">{{ c.label }} ({{ c.does }}){{ i < clashes.length - 1 ? ", " : "" }}</span>
          — that shortcut stops working.
        </template>
      </div>
      <div class="btns">
        <button class="btn btn-sm btn-primary" @click="confirmKeys">Use these keys</button>
        <button class="btn btn-sm" @click="record">Try again</button>
        <button class="btn btn-sm btn-ghost" @click="cancel">Cancel</button>
      </div>
    </template>

    <!-- 3. Saved / off -->
    <template v-else>
      <div class="keys">
        <template v-if="keys.length"><kbd v-for="k in keys" :key="k.code">{{ k.label }}</kbd></template>
        <span v-else class="muted">Off</span>
      </div>
      <small v-if="keys.length" class="muted small">Only pressing {{ keys.length === 1 ? keys[0].label : `${joined(keys)} all at once` }} sets it off.
        On this site the browser doesn't act on {{ keys.length === 1 ? "this key" : "these keys" }} (outside text fields).</small>
      <div class="btns">
        <button class="btn btn-sm" @click="record">{{ keys.length ? "Change" : "Set keys" }}</button>
        <button v-if="keys.length" class="btn btn-sm btn-ghost" @click="savePref('panicKeys', [])">Turn off</button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.panic { display: flex; flex-direction: column; gap: 0.45rem; align-items: flex-start; max-width: 24rem; }
.state { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem; font-size: 0.875rem; }
.state .muted { font-size: 0.8rem; width: 100%; }
.dot { width: 0.6rem; height: 0.6rem; border-radius: 50%; background: #e11d2e; animation: blink 1s ease-in-out infinite; }
@keyframes blink { 50% { opacity: 0.25; } }
.keys { display: flex; flex-wrap: wrap; gap: 0.3rem; align-items: center; min-height: 1.9rem; }
kbd { font: 600 0.8rem/1 ui-monospace, monospace; padding: 0.35rem 0.55rem; border-radius: 6px; border: 1px solid var(--border-strong);
  background: var(--panel-2); box-shadow: 0 1px 0 var(--border-strong); }
.btns { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.small { font-size: 0.78rem; }
.warn { color: var(--warning, #d97706); font-size: 0.78rem; }
.notice { font-size: 0.8rem; line-height: 1.45; padding: 0.55rem 0.7rem; border-radius: var(--radius);
  border: 1px solid color-mix(in srgb, var(--warning, #d97706) 45%, transparent);
  background: color-mix(in srgb, var(--warning, #d97706) 10%, transparent); color: var(--text-2); }
</style>
