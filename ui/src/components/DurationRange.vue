<script setup>
// Library -> Filters -> Duration (3.5.0): one slider with two handles (shortest and longest)
// that snap to POINTS, and exact m:ss boxes for any length in between. Emits "change"
// {min, max} in seconds (null = no limit) when a handle is let go or a box is changed.
import { ref, computed, watch } from "vue";

// Every minute to 10 (where most PMVs are), then wider steps; the last point is "no limit".
const POINTS = [0, 60, 120, 180, 240, 300, 360, 420, 480, 540, 600, 720, 900, 1200, 1500, 1800, 2700, 3600, null];
const LAST = POINTS.length - 1;

const props = defineProps({ min: { type: Number, default: null }, max: { type: Number, default: null } });
const emit = defineEmits(["change"]);

/** The point index nearest to `secs` (null = the end). */
function nearest(secs, fallback) {
  if (secs == null) return fallback;
  let best = 0;
  POINTS.forEach((p, i) => { if (p != null && Math.abs(p - secs) <= Math.abs((POINTS[best] ?? 1e9) - secs)) best = i; });
  return best;
}
const lo = ref(0);
const hi = ref(LAST);
watch(() => [props.min, props.max], () => { lo.value = nearest(props.min, 0); hi.value = nearest(props.max, LAST); }, { immediate: true });

function fmt(secs) {
  if (secs == null) return "";
  const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), s = Math.round(secs % 60);
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}
/** "5", "5:30", "1:02:00", "90s" -> seconds; "" -> null; NaN when it can't be read. */
function parse(text) {
  const t = String(text || "").trim().toLowerCase();
  if (!t) return null;
  if (/^\d+(\.\d+)?s$/.test(t)) return Math.round(parseFloat(t));
  if (/^\d+(\.\d+)?$/.test(t)) return Math.round(parseFloat(t) * 60);  // a plain number = minutes
  const parts = t.split(":");
  if (parts.length > 3 || parts.some((p) => !/^\d+$/.test(p))) return NaN;
  return parts.reduce((acc, p) => acc * 60 + Number(p), 0);
}

// While a handle moves, the boxes show where it is; the filter changes when it's let go.
const shownMin = computed(() => (lo.value === nearest(props.min, 0) && props.min != null ? props.min : POINTS[lo.value] || null));
const shownMax = computed(() => (hi.value === nearest(props.max, LAST) && props.max != null ? props.max : POINTS[hi.value]));
const label = (i) => (POINTS[i] == null ? "no limit" : POINTS[i] < 60 ? "0" : fmt(POINTS[i]).replace(/:00$/, "") + (POINTS[i] < 3600 ? " min" : " h"));

function moveLo(v) { lo.value = Math.min(Number(v), hi.value); }
function moveHi(v) { hi.value = Math.max(Number(v), lo.value); }
// The handle that wasn't moved keeps its exact value (e.g. 2:30 typed in the box).
function commit() {
  emit("change", {
    min: lo.value === nearest(props.min, 0) ? props.min : POINTS[lo.value] || null,
    max: hi.value === nearest(props.max, LAST) ? props.max : POINTS[hi.value],
  });
}

const minText = ref(""), maxText = ref(""), bad = ref("");
watch([shownMin, shownMax], () => { minText.value = fmt(shownMin.value); maxText.value = fmt(shownMax.value); bad.value = ""; }, { immediate: true });
function typed() {
  const a = parse(minText.value), b = parse(maxText.value);
  if (Number.isNaN(a) || Number.isNaN(b)) { bad.value = "Write a length like 3, 3:30 or 1:05:00"; return; }
  if (a != null && b != null && a > b) { bad.value = "The shortest is longer than the longest"; return; }
  bad.value = "";
  emit("change", { min: a || null, max: b });
}
const fill = computed(() => ({ left: `${(lo.value / LAST) * 100}%`, right: `${100 - (hi.value / LAST) * 100}%` }));
</script>

<template>
  <div class="drange">
    <div class="track" aria-hidden="true"><span class="fill" :style="fill" /></div>
    <input class="thumb" type="range" min="0" :max="LAST" step="1" :value="lo" aria-label="Shortest"
           :aria-valuetext="label(lo)" @input="moveLo($event.target.value)" @change="commit">
    <input class="thumb" type="range" min="0" :max="LAST" step="1" :value="hi" aria-label="Longest"
           :aria-valuetext="label(hi)" @input="moveHi($event.target.value)" @change="commit">
    <div class="ticks" aria-hidden="true">
      <span v-for="(p, i) in POINTS" :key="i" :style="{ left: `${(i / LAST) * 100}%` }"
            :class="{ major: [0, 5, 10, 13, 15, 17, LAST].includes(i) }">{{ [0, 5, 10, 13, 15, 17, LAST].includes(i) ? (p == null ? "∞" : p >= 3600 ? "1h" : p / 60) : "" }}</span>
    </div>
    <div class="exact">
      <input v-model="minText" class="input" inputmode="numeric" placeholder="0:00" aria-label="Shortest (minutes:seconds)"
             title="Shortest: minutes or minutes:seconds (e.g. 3 or 3:30)" @change="typed" @keydown.enter="typed">
      <span class="muted">to</span>
      <input v-model="maxText" class="input" inputmode="numeric" placeholder="no limit" aria-label="Longest (minutes:seconds)"
             title="Longest: minutes or minutes:seconds; empty = no limit" @change="typed" @keydown.enter="typed">
    </div>
    <p v-if="bad" class="err small">{{ bad }}</p>
  </div>
</template>

<style scoped>
.drange { position: relative; padding-top: 0.35rem; }
.track { position: relative; height: 4px; margin: 0.5rem 0.5rem 0; border-radius: 2px; background: var(--border-strong); }
.fill { position: absolute; top: 0; bottom: 0; background: var(--accent); border-radius: 2px; }
.thumb { position: absolute; left: 0; right: 0; top: 0.35rem; width: 100%; height: 1.1rem; margin: 0; padding: 0 0;
  background: none; pointer-events: none; -webkit-appearance: none; appearance: none; }
.thumb::-webkit-slider-thumb { -webkit-appearance: none; pointer-events: auto; width: 1rem; height: 1rem; border-radius: 50%;
  background: var(--accent); border: 2px solid var(--panel); box-shadow: 0 0 0 1px var(--accent); cursor: grab; margin-top: -0.1rem; }
.thumb::-moz-range-thumb { pointer-events: auto; width: 0.85rem; height: 0.85rem; border-radius: 50%; background: var(--accent);
  border: 2px solid var(--panel); cursor: grab; }
.thumb::-webkit-slider-runnable-track { background: none; height: 1rem; }
.thumb::-moz-range-track { background: none; }
.thumb:focus-visible::-webkit-slider-thumb { box-shadow: 0 0 0 3px var(--accent-soft); }
.ticks { position: relative; height: 1rem; margin: 0.35rem 0.5rem 0; font-size: 0.68rem; color: var(--text-dim); }
.ticks span { position: absolute; transform: translateX(-50%); top: 0; }
.ticks span::before { content: ""; position: absolute; left: 50%; top: -0.45rem; width: 1px; height: 0.25rem; background: var(--border-strong); }
.ticks span.major::before { height: 0.35rem; background: var(--text-dim); }
.exact { display: flex; align-items: center; gap: 0.4rem; margin-top: 0.4rem; }
.exact .input { width: 6rem; text-align: center; font-variant-numeric: tabular-nums; }
.err { color: var(--danger); margin: 0.25rem 0 0; }
</style>
