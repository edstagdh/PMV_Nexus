<script setup>
// A small SVG chart for the Stats page: bars (stacked when there are several
// series) or lines, a few gridlines, sparse x labels, a legend, and the
// values under the pointer. No chart library -- nothing loaded from outside.
import { ref, computed } from "vue";

const props = defineProps({
  labels: { type: Array, default: () => [] },                 // x values (strings)
  series: { type: Array, default: () => [] },                 // [{ name, values, color }]
  type: { type: String, default: "bar" },                     // bar | line
  format: { type: Function, default: (v) => Math.round(v).toLocaleString() },
  xFormat: { type: Function, default: (s) => s },
  height: { type: Number, default: 180 },
});
const W = 640, PAD_L = 52, PAD_R = 8, PAD_T = 10, PAD_B = 24;
const H = computed(() => props.height);
const n = computed(() => props.labels.length);
const stacked = computed(() => props.type === "bar" && props.series.length > 1);
const max = computed(() => {
  let m = 0;
  for (let i = 0; i < n.value; i++) {
    if (stacked.value) m = Math.max(m, props.series.reduce((a, s) => a + (s.values[i] || 0), 0));
    else for (const s of props.series) m = Math.max(m, s.values[i] || 0);
  }
  if (m < 4) return 4;  // small counts: whole-number gridlines
  const p = 10 ** Math.floor(Math.log10(m));
  return Math.ceil(m / p * 1.05) * p;
});
const plotW = W - PAD_L - PAD_R;
const plotH = computed(() => H.value - PAD_T - PAD_B);
const x = (i) => PAD_L + (n.value <= 1 ? plotW / 2 : (props.type === "bar" ? (i + 0.5) * plotW / n.value : i * plotW / (n.value - 1)));
const y = (v) => PAD_T + plotH.value - (v / max.value) * plotH.value;
const ticks = computed(() => [0, 0.25, 0.5, 0.75, 1].map((f) => f * max.value));
const barW = computed(() => Math.max(1.5, (plotW / Math.max(1, n.value)) * 0.72));
const xTicks = computed(() => {
  const step = Math.max(1, Math.ceil(n.value / 6));
  return props.labels.map((l, i) => ({ l, i })).filter(({ i }) => i % step === 0 || i === n.value - 1);
});
const bars = computed(() => {
  if (props.type !== "bar") return [];
  const out = [];
  for (let i = 0; i < n.value; i++) {
    let base = 0;
    props.series.forEach((s, si) => {
      const v = s.values[i] || 0;
      if (!v) return;
      const w = stacked.value ? barW.value : barW.value / props.series.length;
      const left = stacked.value ? x(i) - barW.value / 2 : x(i) - barW.value / 2 + si * w;
      const top = y(stacked.value ? base + v : v);
      const bottom = y(stacked.value ? base : 0);
      out.push({ key: `${i}-${si}`, x: left, y: top, w: Math.max(1, w - (stacked.value ? 0 : 1)), h: Math.max(0.5, bottom - top), color: s.color });
      if (stacked.value) base += v;
    });
  }
  return out;
});
const lines = computed(() => (props.type === "line" ? props.series.map((s) => ({
  ...s, d: s.values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v || 0).toFixed(1)}`).join(" "),
})) : []));

const hover = ref(null);
const svg = ref(null);
function onMove(e) {
  const r = svg.value.getBoundingClientRect();
  const px = ((e.clientX - r.left) / r.width) * W;
  if (!n.value) return;
  let i = props.type === "bar" ? Math.floor((px - PAD_L) / (plotW / n.value)) : Math.round((px - PAD_L) / (plotW / Math.max(1, n.value - 1)));
  i = Math.max(0, Math.min(n.value - 1, i));
  hover.value = i;
}
const empty = computed(() => !props.series.some((s) => s.values.some((v) => v)));
</script>

<template>
  <div class="chart">
    <div class="legend" v-if="series.length > 1 || type === 'line'">
      <span v-for="s in series" :key="s.name"><i :style="{ background: s.color }" />{{ s.name }}</span>
    </div>
    <div class="wrap">
      <svg ref="svg" :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none" role="img" :aria-label="series.map((s) => s.name).join(', ')"
           @mousemove="onMove" @mouseleave="hover = null">
        <g class="grid">
          <line v-for="t in ticks" :key="t" :x1="PAD_L" :x2="W - PAD_R" :y1="y(t)" :y2="y(t)" />
        </g>
        <g class="ylab"><text v-for="t in ticks" :key="t" :x="PAD_L - 6" :y="y(t) + 3">{{ format(t) }}</text></g>
        <g class="xlab"><text v-for="t in xTicks" :key="t.i" :x="x(t.i)" :y="H - 6">{{ xFormat(t.l) }}</text></g>
        <rect v-for="b in bars" :key="b.key" :x="b.x" :y="b.y" :width="b.w" :height="b.h" :fill="b.color" rx="1.5" />
        <path v-for="l in lines" :key="l.name" :d="l.d" :stroke="l.color" fill="none" stroke-width="2" vector-effect="non-scaling-stroke" />
        <line v-if="hover !== null" class="cursor" :x1="x(hover)" :x2="x(hover)" :y1="PAD_T" :y2="PAD_T + plotH" />
      </svg>
      <div v-if="empty" class="none muted">Nothing in this range yet</div>
      <div v-if="hover !== null && !empty" class="tip" :style="{ left: `${(x(hover) / W) * 100}%` }">
        <strong>{{ xFormat(labels[hover], true) }}</strong>
        <div v-for="s in series" :key="s.name"><i :style="{ background: s.color }" />{{ s.name }}: {{ format(s.values[hover] || 0) }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chart { display: flex; flex-direction: column; gap: 0.375rem; min-width: 0; }
.legend { display: flex; flex-wrap: wrap; gap: 0.25rem 0.875rem; font-size: 0.75rem; color: var(--text-2); }
.legend i, .tip i { display: inline-block; width: 0.625rem; height: 0.625rem; border-radius: 2px; margin-right: 0.3rem; vertical-align: -1px; }
.wrap { position: relative; }
svg { width: 100%; height: auto; display: block; overflow: visible; }
.grid line { stroke: var(--border); stroke-width: 1; vector-effect: non-scaling-stroke; }
.ylab text { font-size: 10px; fill: var(--text-dim); text-anchor: end; }
.xlab text { font-size: 10px; fill: var(--text-dim); text-anchor: middle; }
.cursor { stroke: var(--text-dim); stroke-dasharray: 3 3; vector-effect: non-scaling-stroke; }
.tip { position: absolute; top: 0; transform: translateX(-50%); pointer-events: none; background: var(--panel); border: 1px solid var(--border-strong);
  border-radius: var(--radius); padding: 0.375rem 0.5rem; font-size: 0.72rem; white-space: nowrap; box-shadow: var(--shadow); z-index: 2; }
.none { position: absolute; inset: 0; display: grid; place-items: center; font-size: 0.8rem; }
</style>
