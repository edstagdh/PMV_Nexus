<script setup>
// 3.9.0 "The wall" (/wall): performers you matched or filled in, joined by the videos they share,
// as a 3D (three.js, 3d-force-graph) or 2D (force-graph) graph. Click a performer to put them in
// the middle with every video they're in (thumbnails); tick "Also who they're with" to add the
// other performers of those videos. A video opens in the side panel, then on its own page.
// Data: GET /api/performers/wall. This page is its own chunk: the libraries load only here.
import { t } from "../lib/i18n.js";
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from "vue";
import { useRoute, useRouter } from "vue-router";
import ForceGraph3D from "3d-force-graph";
import ForceGraph from "force-graph";
import * as THREE from "three";
import Icon from "../components/Icon.vue";
import PerformerPlaceholder from "../components/PerformerPlaceholder.vue";
import { api } from "../lib/api.js";

const route = useRoute();
const router = useRouter();
const stage = ref(null);
const wrap = ref(null);
const data = ref(null);
const error = ref("");
const focus = ref(null);      // person id in the middle, or null: everyone
const picked = ref(null);     // a video shown in the side panel
const showCo = ref(false);
const minShared = ref(1);
const q = ref("");
const narrow = () => window.matchMedia("(max-width: 40rem)").matches;
const panelOpen = ref(!narrow());   // a phone: the panel opens when something is picked
const MAX_FOCUS_VIDEOS = 300;
const remembered = (() => { try { return localStorage.getItem("pmv.wall.mode"); } catch { return null; } })();
const mode = ref(remembered === "2d" ? "2d" : "3d");
const no3d = ref(false);

const byId = computed(() => Object.fromEntries((data.value?.performers || []).map((p) => [p.id, p])));
const videoById = computed(() => Object.fromEntries((data.value?.videos || []).map((v) => [v.id, v])));
const videosOf = computed(() => {
  const m = {};
  for (const v of data.value?.videos || []) for (const pid of v.cast) (m[pid] ||= []).push(v);
  return m;
});
const pairs = computed(() => {
  const m = new Map();
  for (const v of data.value?.videos || []) {
    const c = v.cast;
    for (let i = 0; i < c.length; i++) for (let j = i + 1; j < c.length; j++) {
      const k = c[i] < c[j] ? `${c[i]}|${c[j]}` : `${c[j]}|${c[i]}`;
      m.set(k, (m.get(k) || 0) + 1);
    }
  }
  return m;
});
const maxShared = computed(() => Math.max(1, ...pairs.value.values()));
const me = computed(() => (focus.value ? byId.value[focus.value] : null));
const myVideos = computed(() => (focus.value ? videosOf.value[focus.value] || [] : []));
const coStars = computed(() => {
  if (!focus.value) return [];
  const m = new Map();
  for (const v of myVideos.value) for (const c of v.cast) if (c !== focus.value) m.set(c, (m.get(c) || 0) + 1);
  return [...m].sort((a, b) => b[1] - a[1]).map(([id, n]) => ({ p: byId.value[id], n }));
});
const top = computed(() => [...(data.value?.performers || [])].sort((a, b) => b.videos - a.videos).slice(0, 12));

function everyone() {
  const links = [];
  for (const [k, n] of pairs.value) if (n >= minShared.value) { const [s, tt] = k.split("|"); links.push({ source: s, target: tt, n }); }
  const used = new Set(links.flatMap((l) => [l.source, l.target]));
  const nodes = (data.value?.performers || []).filter((p) => used.has(p.id))
    .map((p) => ({ id: p.id, kind: "performer", label: p.name, gender: p.gender, val: 2 + Math.min(p.videos, 120) / 4 }));
  return { nodes, links };
}
function around(pid) {
  const nodes = new Map(), links = [];
  const add = (n) => { if (!nodes.has(n.id)) nodes.set(n.id, n); };
  const p = byId.value[pid];
  add({ id: p.id, kind: "performer", label: p.name, gender: p.gender, img: p.photo, val: 16, center: true });
  for (const v of myVideos.value.slice(0, MAX_FOCUS_VIDEOS)) {
    add({ id: "v:" + v.id, vid: v.id, kind: "video", label: v.title, img: v.thumb, val: 5 });
    links.push({ source: p.id, target: "v:" + v.id, n: 1 });
    if (!showCo.value) continue;
    for (const c of v.cast) {
      if (c === pid) continue;
      const k = c < pid ? `${c}|${pid}` : `${pid}|${c}`;
      if ((pairs.value.get(k) || 0) < minShared.value) continue;
      const o = byId.value[c];
      add({ id: o.id, kind: "performer", label: o.name, gender: o.gender, img: o.photo, val: 4 + Math.min(pairs.value.get(k), 20) });
      links.push({ source: "v:" + v.id, target: o.id, n: 1 });
    }
  }
  return { nodes: [...nodes.values()], links };
}

// ---------------------------------------------------------------- drawing
const css = () => getComputedStyle(document.documentElement);
let colors = {};
function readColors() {
  const s = css();
  const v = (name, fallback) => s.getPropertyValue(name).trim() || fallback;
  colors = { female: v("--chip-female", "#ec4899"), male: v("--chip-male", "#38bdf8"), trans: v("--chip-trans", "#c084fc"),
    unknown: "#9ca3af", video: "#14b8a6", accent: v("--accent", "#f97316"), bg: v("--bg", "#0b0b0d"), text: v("--text", "#e8eaf0") };
}
const colorOf = (n) => (n.kind === "video" ? colors.video : n.center ? colors.accent : colors[n.gender] || colors.unknown);
let graph = null;
let disposables = [];
const images = new Map();   // 2D: url -> Image
const loader = new THREE.TextureLoader();

function sprite(n) {
  const h = n.kind === "video" ? 9 : n.center ? 22 : 12;
  const mat = new THREE.SpriteMaterial({ color: 0xffffff });
  const s = new THREE.Sprite(mat);
  s.scale.set(n.kind === "video" ? 16 : h * 0.75, h, 1);
  const tex = loader.load(n.img, (tx) => {
    const r = tx.image.width / tx.image.height || 1;
    s.scale.set(h * r, h, 1);
  });
  tex.colorSpace = THREE.SRGBColorSpace;
  mat.map = tex;
  mat.needsUpdate = true;
  disposables.push(tex, mat);
  return s;
}
function img2d(url) {
  let im = images.get(url);
  if (!im) { im = new Image(); im.src = url; images.set(url, im); }
  return im.complete && im.naturalWidth ? im : null;
}
function draw2d(n, ctx, scale) {
  const r = Math.sqrt(n.val) * 4;
  const im = n.img && img2d(n.img);
  if (im) {
    const h = n.kind === "video" ? r * 1.8 : r * 2.4;
    const w = h * (im.naturalWidth / im.naturalHeight);
    ctx.drawImage(im, n.x - w / 2, n.y - h / 2, w, h);
    if (n.center) { ctx.strokeStyle = colors.accent; ctx.lineWidth = 2 / scale; ctx.strokeRect(n.x - w / 2, n.y - h / 2, w, h); }
  } else {
    ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, 2 * Math.PI); ctx.fillStyle = colorOf(n); ctx.fill();
  }
  if (n.kind === "video" && scale < 2.5) return;
  const size = Math.max(11 / scale, 2.5);
  ctx.font = `${n.center ? 600 : 400} ${size}px sans-serif`;
  ctx.fillStyle = colors.text; ctx.textAlign = "center";
  ctx.fillText(n.label.length > 40 ? n.label.slice(0, 38) + "…" : n.label, n.x, n.y + r * 1.3 + size);
}

function destroy() {
  if (graph) { try { graph.pauseAnimation?.(); graph._destructor?.(); } catch { /* already gone */ } graph = null; }
  for (const d of disposables) d.dispose?.();
  disposables = [];
  if (stage.value) stage.value.innerHTML = "";
}
let fitted = false;
function size() { const el = wrap.value; return el ? [el.clientWidth, el.clientHeight] : [800, 600]; }
function build() {
  if (!data.value || !stage.value) return;
  destroy();
  readColors();
  const g = focus.value && byId.value[focus.value] ? around(focus.value) : everyone();
  const [w, h] = size();
  const label = (n) => { const d = document.createElement("div"); d.className = "wall-tip"; d.textContent = n.label; return d.outerHTML; };
  const common = (x) => x.backgroundColor(colors.bg).width(w).height(h).graphData(g).nodeId("id").nodeVal("val")
    .nodeColor(colorOf).nodeLabel(label).linkWidth((l) => Math.min(6, 0.5 + l.n * 0.8))
    .linkColor(() => "rgba(200,210,230,0.3)").onNodeClick(onNodeClick).cooldownTime(g.nodes.length > 600 ? 6000 : 4000)
    .onEngineStop(() => { if (!fitted) { fitted = true; graph?.zoomToFit(600, w > 640 ? 70 : 24); } });
  fitted = false;
  if (mode.value === "3d" && !no3d.value) {
    try {
      graph = common(new ForceGraph3D(stage.value, { controlType: "orbit" })).showNavInfo(false).nodeOpacity(0.95)
        .linkOpacity(0.35).nodeResolution(12).nodeThreeObject((n) => (n.img ? sprite(n) : null));
      return;
    } catch (err) {
      console.warn("3D not available", err);  // no WebGL: the 2D wall instead
      no3d.value = true;
      destroy();
    }
  }
  graph = common(new ForceGraph(stage.value)).nodeCanvasObjectMode(() => "replace").nodeCanvasObject(draw2d)
    .nodePointerAreaPaint((n, color, ctx) => { ctx.fillStyle = color; const r = Math.sqrt(n.val) * 4 * 1.3; ctx.fillRect(n.x - r, n.y - r, 2 * r, 2 * r); });
}
function onNodeClick(n) {
  if (n.kind === "video") { picked.value = videoById.value[n.vid] || null; panelOpen.value = true; return; }
  setFocus(n.id);
}
function setFocus(id) {
  picked.value = null;
  focus.value = id;
  const p = id ? byId.value[id] : null;
  router.replace({ query: p ? { p: p.name } : {} });
  panelOpen.value = true;
}
function setMode(m) { mode.value = m; try { localStorage.setItem("pmv.wall.mode", m); } catch { /* not kept */ } }
function find() {
  const want = q.value.trim().toLowerCase();
  const p = (data.value?.performers || []).find((x) => x.name.toLowerCase() === want || x.link.toLowerCase() === want);
  if (p) { setFocus(p.id); q.value = ""; }
}

watch([focus, showCo, minShared, mode], () => nextTick(build));
let ro = null;
onMounted(async () => {
  try {
    data.value = await api("/api/performers/wall");
    if (pairs.value.size > 6000) minShared.value = 2;   // a large library: the strongest ties first
    const want = String(route.query.p || "").toLowerCase();
    const p = want && data.value.performers.find((x) => x.name.toLowerCase() === want || x.link.toLowerCase() === want);
    if (p) { focus.value = p.id; panelOpen.value = true; }
    await nextTick();
    build();
  } catch (err) { error.value = err.message || t("Could not load the wall"); }
  ro = new ResizeObserver(() => { const [w, h] = size(); graph?.width(w).height(h); });
  if (wrap.value) ro.observe(wrap.value);
});
onBeforeUnmount(() => { ro?.disconnect(); destroy(); images.clear(); });
</script>

<template>
  <div class="wall-page">
    <div class="bar">
      <div class="seg" role="group" :aria-label="$t('View')">
        <button class="btn btn-sm" :class="{ 'btn-primary': mode === '3d' && !no3d }" :aria-pressed="mode === '3d' && !no3d" :disabled="no3d"
                :title="no3d ? $t('This browser can’t draw 3D (WebGL is off)') : ''" @click="setMode('3d')">3D</button>
        <button class="btn btn-sm" :class="{ 'btn-primary': mode === '2d' || no3d }" :aria-pressed="mode === '2d' || no3d" @click="setMode('2d')">2D</button>
      </div>
      <form class="find" @submit.prevent="find">
        <input v-model="q" class="input" list="wall-names" :placeholder="$t('Find a performer')" :aria-label="$t('Find a performer')" @change="find">
        <datalist id="wall-names"><option v-for="p in data?.performers || []" :key="p.id" :value="p.name" /></datalist>
      </form>
      <label class="min" :title="$t('Only lines between performers who share at least this many videos')">
        {{ $t("Shared videos at least") }}
        <input v-model.number="minShared" type="range" min="1" :max="Math.min(10, maxShared)" step="1">
        <output>{{ minShared }}</output>
      </label>
      <label v-if="focus" class="co"><input v-model="showCo" type="checkbox"> {{ $t("Also who they’re with") }}</label>
      <button v-if="focus" class="btn btn-sm" @click="setFocus(null)"><Icon name="back" :size="14" />{{ $t("Everyone") }}</button>
      <button class="btn btn-ghost btn-sm panel-btn" :aria-expanded="panelOpen" @click="panelOpen = !panelOpen">{{ panelOpen ? $t("Hide panel") : $t("Show panel") }}</button>
    </div>

    <div class="stage-wrap">
      <div ref="wrap" class="stage-box">
        <p v-if="error" class="empty">{{ error }}</p>
        <p v-else-if="data && !data.performers.length" class="empty">{{ $t("Nobody on the wall yet: it shows performers linked to ThePornDB, FansDB or StashDB, or with details, photos or a gender set on their page.") }}</p>
        <div v-else-if="!data" class="skeleton fill" />
        <div ref="stage" class="stage" />
      </div>

      <aside v-if="data && panelOpen" class="drawer card">
        <template v-if="picked">
          <button class="btn btn-ghost btn-icon btn-sm close" :aria-label="$t('Back')" @click="picked = null"><Icon name="x" :size="14" /></button>
          <img v-if="picked.thumb" :src="picked.thumb" alt="" class="vthumb">
          <h2>{{ picked.title }}</h2>
          <p class="muted small">{{ [picked.creator, picked.length].filter(Boolean).join(" · ") }}</p>
          <RouterLink :to="{ name: 'video', params: { id: picked.id } }" class="btn btn-primary btn-sm">{{ $t("Open the video") }}</RouterLink>
          <h3>{{ $t("Performers") }}</h3>
          <ul class="list">
            <li v-for="c in picked.cast" :key="c"><button @click="setFocus(c)"><span>{{ byId[c]?.name }}</span><span class="n">{{ $tn("{n} video", "{n} videos", byId[c]?.videos || 0) }}</span></button></li>
          </ul>
        </template>
        <template v-else-if="me">
          <button class="btn btn-ghost btn-icon btn-sm close" :aria-label="$t('Everyone')" @click="setFocus(null)"><Icon name="x" :size="14" /></button>
          <div class="who">
            <img v-if="me.photo" :src="me.photo" alt="" class="ph"><span v-else class="ph"><PerformerPlaceholder :gender="me.gender" /></span>
            <div><h2>{{ me.name }}</h2><p class="muted small">{{ $tn("{n} video", "{n} videos", me.videos) }}</p></div>
          </div>
          <RouterLink :to="{ name: 'performer', params: { name: me.link } }" class="btn btn-sm">{{ $t("Profile") }}</RouterLink>
          <p v-if="myVideos.length > MAX_FOCUS_VIDEOS" class="muted small">{{ $t("The wall shows {n} of them; all are listed below.", { n: MAX_FOCUS_VIDEOS }) }}</p>
          <template v-if="coStars.length">
            <h3>{{ $t("Most often with") }}</h3>
            <ul class="list">
              <li v-for="c in coStars.slice(0, 10)" :key="c.p.id"><button @click="setFocus(c.p.id)"><span>{{ c.p.name }}</span><span class="n">{{ $tn("{n} shared", "{n} shared", c.n) }}</span></button></li>
            </ul>
          </template>
          <h3>{{ $t("Videos") }}</h3>
          <ul class="list">
            <li v-for="v in myVideos" :key="v.id"><RouterLink :to="{ name: 'video', params: { id: v.id } }"><span>{{ v.title }}</span><span class="n">{{ v.length }}</span></RouterLink></li>
          </ul>
        </template>
        <template v-else>
          <h2>{{ $t("Everyone") }}</h2>
          <p class="muted small">{{ $t("{p} performers · {v} videos", { p: data.performers.length, v: data.videos.length }) }}</p>
          <h3>{{ $t("Most videos") }}</h3>
          <ul class="list">
            <li v-for="p in top" :key="p.id"><button @click="setFocus(p.id)"><span>{{ p.name }}</span><span class="n">{{ p.videos }}</span></button></li>
          </ul>
          <p class="muted small">{{ $t("Each dot is a performer; a line means they share videos (thicker: more). Click one to put them in the middle with their videos. Drag to turn, scroll to zoom.") }}</p>
        </template>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.wall-page { display: flex; flex-direction: column; height: 100dvh; min-height: 24rem; }
@media (max-width: 56.25rem) { .wall-page { height: calc(100dvh - 3.25rem); } }
.bar { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; padding: 0.5rem 1rem; }
.seg { display: inline-flex; gap: 0.25rem; }
.find .input { width: 14rem; max-width: 60vw; }
.min, .co { display: inline-flex; align-items: center; gap: 0.4rem; font-size: 0.82rem; }
.min input { width: 7rem; }
.panel-btn { margin-inline-start: auto; }
.stage-wrap { position: relative; flex: 1; min-height: 0; overflow: hidden; display: flex; }
.stage-box { position: relative; flex: 1; min-width: 0; }
.stage, .fill { position: absolute; inset: 0; }
.empty { position: absolute; inset: 0; display: grid; place-items: center; padding: 2rem; text-align: center; color: var(--text-dim); z-index: 1; }
.drawer { position: relative; flex: none; width: 20rem; margin: 0 0.75rem 0.75rem; align-self: flex-start; max-height: calc(100% - 0.75rem); overflow: auto;
  padding: 0.9rem; display: flex; flex-direction: column; gap: 0.5rem; z-index: 2; box-sizing: border-box; }
.drawer > * { flex-shrink: 0; }
.drawer h2 { font-size: 1.05rem; margin: 0; overflow-wrap: anywhere; padding-inline-end: 2rem; }
.drawer h3 { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); margin: 0.4rem 0 0; }
.close { position: absolute; top: 0.5rem; inset-inline-end: 0.5rem; }
.small { font-size: 0.8rem; margin: 0; }
.list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.list button, .list a { all: unset; box-sizing: border-box; display: flex; justify-content: space-between; gap: 0.5rem; width: 100%;
  padding: 0.3rem 0.4rem; border-radius: 0.35rem; cursor: pointer; font-size: 0.85rem; color: var(--text); }
.list button:hover, .list a:hover, .list button:focus-visible, .list a:focus-visible { background: var(--panel-hover); }
.list span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.n { color: var(--text-dim); flex: none; }
.who { display: flex; gap: 0.7rem; align-items: center; }
.ph { width: 3.5rem; height: 3.5rem; border-radius: 50%; object-fit: cover; flex: none; overflow: hidden; display: block; }
.vthumb { width: 100%; border-radius: 0.4rem; }
.drawer .btn { align-self: flex-start; }
@media (max-width: 40rem) {
  .stage-wrap { flex-direction: column; }
  .drawer { margin: 0; width: auto; align-self: stretch; max-height: 45%; border-radius: var(--radius-lg) var(--radius-lg) 0 0; }
  .bar { gap: 0.4rem; padding: 0.4rem 0.6rem; }
  .min input { width: 5rem; }
  .find .input { width: 9rem; }
}
</style>
<style>
.wall-tip { background: rgba(10, 12, 18, 0.85); color: #fff; padding: 2px 6px; border-radius: 4px; font: 12px sans-serif; }
</style>
