<script setup>
// 3.9.0 "The wall" (/wall): performers you matched or filled in, joined by the videos they share,
// as a 3D (three.js, 3d-force-graph) or 2D (force-graph) graph. Click a performer to put them in
// the middle with every video they're in (thumbnails); tick "Also who they're with" to add the
// other performers of those videos. 3.9.1: hovering a video plays its preview (muted, like the
// library); a click shows it in the side panel. "Pick for me" picks one of the performer's
// videos (My Settings -> The wall: which one, the search animation, how long it takes), then
// stores/flyIn.js grows its thumbnail into the video page's player.
// Dragging always moves the view (nodes can't be dragged); 3D turns freely in every direction.
// Pictures are drawn from 256 px copies (smooth even with hundreds of thumbnails).
// Data: GET /api/performers/wall. This page is its own chunk: the libraries load only here.
import { t } from "../lib/i18n.js";
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from "vue";
import { useRoute, useRouter } from "vue-router";
import ForceGraph3D from "3d-force-graph";
import ForceGraph from "force-graph";
import * as THREE from "three";
import Icon from "../components/Icon.vue";
import PerformerPlaceholder from "../components/PerformerPlaceholder.vue";
import PreviewVideo from "../components/PreviewVideo.vue";
import { api } from "../lib/api.js";
import { session } from "../stores/session.js";
import { toast } from "../stores/toast.js";
import { pickVideo, PICK_MODES } from "../lib/wallPick.js";
import { startFlyIn } from "../stores/flyIn.js";

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
const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch { /* not kept */ } return null; };
const mode = ref(store("pmv.wall.mode") === "2d" ? "2d" : "3d");
const no3d = ref(false);
const helpOpen = ref(!store("pmv.wall.help"));   // shown once by itself, then from the ? button
const is3d = () => mode.value === "3d" && !no3d.value;

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

// ---------------------------------------------------------------- pictures: 256 px copies
const SMALL = 256;
const smalls = new Map();   // url -> {canvas, ratio, ready, failed, waiters}
function small(url, done) {
  let e = smalls.get(url);
  if (!e) {
    e = { canvas: null, ratio: 16 / 9, ready: false, failed: false, waiters: [] };
    smalls.set(url, e);
    const im = new Image();
    im.decoding = "async";
    im.onload = () => {
      const r = im.naturalWidth / im.naturalHeight || 1;
      const c = document.createElement("canvas");
      c.width = Math.max(1, Math.round(r >= 1 ? SMALL : SMALL * r));
      c.height = Math.max(1, Math.round(r >= 1 ? SMALL / r : SMALL));
      c.getContext("2d").drawImage(im, 0, 0, c.width, c.height);
      Object.assign(e, { canvas: c, ratio: r, ready: true });
      e.waiters.splice(0).forEach((f) => f(e));
      redraw2d();
    };
    im.onerror = () => { e.failed = true; e.waiters.length = 0; };
    im.src = url;
  }
  if (done) { if (e.ready) done(e); else if (!e.failed) e.waiters.push(done); }
  return e;
}
let redrawTimer = 0;
function redraw2d() {  // 2D draws only while something moves: show pictures that arrived later
  if (is3d() || !graph || redrawTimer) return;
  redrawTimer = setTimeout(() => { redrawTimer = 0; graph?.zoom?.(graph.zoom()); }, 120);
}

// ---------------------------------------------------------------- drawing
let colors = {};
function readColors() {
  const s = getComputedStyle(document.documentElement);
  const v = (name, fallback) => s.getPropertyValue(name).trim() || fallback;
  colors = { female: v("--chip-female", "#ec4899"), male: v("--chip-male", "#38bdf8"), trans: v("--chip-trans", "#c084fc"),
    unknown: "#9ca3af", video: "#14b8a6", accent: v("--accent", "#f97316"), bg: v("--bg", "#0b0b0d"), text: v("--text", "#e8eaf0") };
}
const colorOf = (n) => (n.kind === "video" ? colors.video : n.center ? colors.accent : colors[n.gender] || colors.unknown);
let graph = null;
let disposables = [];
const sprites = new Map();  // 3D: node id -> {s, h, ratio}
let hot = null;             // the node "Pick for me" is looking at: drawn bigger
let hotScale = 1;

const nodeHeight3d = (n) => (n.kind === "video" ? 9 : n.center ? 22 : 12);
function sprite(n) {
  const h = nodeHeight3d(n);
  const mat = new THREE.SpriteMaterial({ color: n.kind === "video" ? 0x14b8a6 : 0x6b7280 });
  const s = new THREE.Sprite(mat);
  const entry = { s, h, ratio: n.kind === "video" ? 16 / 9 : 0.75 };
  sprites.set(n.id, entry);
  scaleSprite(n.id);
  disposables.push(mat);
  if (n.img) small(n.img, (e) => {
    if (!sprites.has(n.id) || sprites.get(n.id) !== entry) return;
    const tex = new THREE.CanvasTexture(e.canvas);
    tex.colorSpace = THREE.SRGBColorSpace;
    mat.map = tex;
    mat.color.set(0xffffff);
    mat.needsUpdate = true;
    disposables.push(tex);
    entry.ratio = e.ratio;
    scaleSprite(n.id);
  });
  return s;
}
function scaleSprite(id) {
  const e = sprites.get(id);
  if (!e) return;
  const k = id === hot ? hotScale : 1;
  e.s.scale.set(e.h * e.ratio * k, e.h * k, 1);
}
function setHot(id, k = 1.5) {
  const before = hot;
  hot = id;
  hotScale = k;
  scaleSprite(before);
  scaleSprite(id);
  redraw2d();
}

function size2d(n) {   // drawn size in graph units (2D)
  const r = Math.sqrt(n.val) * 4;
  const k = n.id === hot ? hotScale : 1;
  const e = n.img ? small(n.img) : null;
  const h = (n.kind === "video" ? r * 1.8 : r * 2.4) * k;
  const ratio = e?.ready ? e.ratio : n.kind === "video" ? 16 / 9 : 0.75;
  return { r, h, w: h * ratio, e };
}
function draw2d(n, ctx, scale) {
  const { r, h, w, e } = size2d(n);
  if (e?.ready || n.kind === "video") {
    if (e?.ready) ctx.drawImage(e.canvas, n.x - w / 2, n.y - h / 2, w, h);
    else { ctx.fillStyle = colors.video; ctx.fillRect(n.x - w / 2, n.y - h / 2, w, h); }
    if (n.center || n.id === hot) { ctx.strokeStyle = colors.accent; ctx.lineWidth = 2 / scale; ctx.strokeRect(n.x - w / 2, n.y - h / 2, w, h); }
  } else {
    ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, 2 * Math.PI); ctx.fillStyle = colorOf(n); ctx.fill();
  }
  if (n.kind === "video" && scale < 2.5) return;
  const font = 11 / scale;   // the same size on screen at any zoom
  ctx.font = `${n.center ? 600 : 400} ${font}px sans-serif`;
  ctx.fillStyle = colors.text; ctx.textAlign = "center";
  ctx.fillText(n.label.length > 40 ? n.label.slice(0, 38) + "…" : n.label, n.x, n.y + Math.max(r * 1.3, h / 2) + font);
}

function destroy() {
  if (graph) { try { graph.pauseAnimation?.(); graph._destructor?.(); } catch { /* already gone */ } graph = null; }
  for (const d of disposables) d.dispose?.();
  disposables = [];
  sprites.clear();
  for (const e of smalls.values()) e.waiters.length = 0;
  hot = null;
  if (stage.value) stage.value.innerHTML = "";
}
let fitted = false;
function size() { const el = wrap.value; return el ? [el.clientWidth, el.clientHeight] : [800, 600]; }
function build() {
  if (!data.value || !stage.value) return;
  destroy();
  readColors();
  hover.value = null;
  const g = focus.value && byId.value[focus.value] ? around(focus.value) : everyone();
  const [w, h] = size();
  // videos get the preview card instead of a tooltip
  const label = (n) => { if (n.kind === "video") return ""; const d = document.createElement("div"); d.className = "wall-tip"; d.textContent = n.label; return d.outerHTML; };
  const common = (x) => x.backgroundColor(colors.bg).width(w).height(h).graphData(g).nodeId("id").nodeVal("val")
    .nodeColor(colorOf).nodeLabel(label).linkWidth((l) => Math.min(6, 0.5 + l.n * 0.8)).enableNodeDrag(false)
    .linkColor(() => "rgba(200,210,230,0.3)").onNodeClick(onNodeClick).onNodeHover(onHover)
    .cooldownTime(g.nodes.length > 600 ? 6000 : 4000)
    .onEngineStop(() => { if (!fitted) { fitted = true; graph?.zoomToFit(600, w > 640 ? 70 : 24); } });
  fitted = false;
  if (is3d()) {
    try {
      // trackball: turns in every direction with no end (orbit stops at the poles) and glides on
      graph = common(new ForceGraph3D(stage.value, { controlType: "trackball" })).showNavInfo(false).nodeOpacity(0.95)
        .linkOpacity(0.35).nodeResolution(12).nodeThreeObject((n) => (n.img || n.kind === "video" ? sprite(n) : null));
      graph.renderer().setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      const c = graph.controls();
      if (c) { c.rotateSpeed = 2.5; c.zoomSpeed = 1.2; c.panSpeed = 0.6; c.staticMoving = false; c.dynamicDampingFactor = 0.12; }
      return;
    } catch (err) {
      console.warn("3D not available", err);  // no WebGL: the 2D wall instead
      no3d.value = true;
      destroy();
    }
  }
  graph = common(new ForceGraph(stage.value)).nodeCanvasObjectMode(() => "replace").nodeCanvasObject(draw2d)
    .nodePointerAreaPaint((n, color, ctx) => { const { w: bw, h: bh, r } = size2d(n); const ww = Math.max(bw, 2 * r), hh = Math.max(bh, 2 * r); ctx.fillStyle = color; ctx.fillRect(n.x - ww / 2, n.y - hh / 2, ww, hh); });
}

// ---------------------------------------------------------------- hover preview
const hover = ref(null);       // {v}
const pointer = ref({ x: 0, y: 0 });
let hoverTimer = 0;
let dragging = false;   // no preview while turning / moving the view
function onDown() { dragging = true; clearTimeout(hoverTimer); hover.value = null; }
function onUp() { dragging = false; }
function onHover(n) {
  clearTimeout(hoverTimer);
  if (!n || n.kind !== "video" || picking.value || dragging) { hover.value = null; return; }
  const v = videoById.value[n.vid];
  hoverTimer = setTimeout(() => { hover.value = v ? { v } : null; }, 200);
}
function onMove(e) {
  const r = wrap.value?.getBoundingClientRect();
  if (r) pointer.value = { x: e.clientX - r.left, y: e.clientY - r.top };
}
const hoverStyle = computed(() => {
  const [W, H] = size();
  const cw = Math.min(288, W - 24), ch = cw * 9 / 16 + 48;
  let x = pointer.value.x + 18, y = pointer.value.y + 18;
  if (x + cw > W - 8) x = pointer.value.x - cw - 18;
  if (y + ch > H - 8) y = pointer.value.y - ch - 18;
  return { left: `${Math.max(8, x)}px`, top: `${Math.max(8, y)}px`, width: `${cw}px` };
});

// ---------------------------------------------------------------- clicks
function onNodeClick(n) {
  if (picking.value) return;
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
function setMode(m) { mode.value = m; store("pmv.wall.mode", m); }
function find() {
  const want = q.value.trim().toLowerCase();
  const p = (data.value?.performers || []).find((x) => x.name.toLowerCase() === want || x.link.toLowerCase() === want);
  if (p) { setFocus(p.id); q.value = ""; }
}
function toggleHelp(on = !helpOpen.value) { helpOpen.value = on; if (!on) store("pmv.wall.help", "1"); }

// ---------------------------------------------------------------- Pick for me
const picking = ref(false);
let run = null;
const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const openVideo = (v) => router.push({ name: "video", params: { id: v.id } });
const nodes = () => graph?.graphData().nodes || [];

function aim(n, dist, ms) {   // 3D: dist = camera distance; 2D: dist = zoom level
  if (is3d()) {
    const cam = graph.camera();
    const dir = new THREE.Vector3(cam.position.x - n.x, cam.position.y - n.y, cam.position.z - n.z);
    if (dir.lengthSq() < 1e-6) dir.set(0, 0, 1);
    dir.normalize().multiplyScalar(dist);
    graph.cameraPosition({ x: n.x + dir.x, y: n.y + dir.y, z: n.z + dir.z }, { x: n.x, y: n.y, z: n.z }, ms);
  } else {
    graph.centerAt(n.x, n.y, ms);
    graph.zoom(dist, ms);
  }
}
function fill(n, share) {    // the distance / zoom at which node n takes `share` of the view
  const [W, H] = size();
  if (is3d()) {
    const e = sprites.get(n.id) || { h: nodeHeight3d(n), ratio: 16 / 9 };
    const h = e.h * (n.id === hot ? hotScale : 1);
    const tan = Math.tan(THREE.MathUtils.degToRad(graph.camera().fov / 2));
    const seen = Math.max(h / share, (h * e.ratio) / (share * (W / H)));
    return seen / (2 * tan);
  }
  const { w, h } = size2d(n);
  return Math.min((share * H) / h, (share * W) / w);
}
function screenRect(n) {      // where node n's picture is on the screen now
  const box = wrap.value.getBoundingClientRect();
  const [, H] = size();
  let cx, cy, w, h;
  if (is3d()) {
    const e = sprites.get(n.id);
    const c = graph.graph2ScreenCoords(n.x, n.y, n.z);
    const cam = graph.camera();
    const dist = cam.position.distanceTo(new THREE.Vector3(n.x, n.y, n.z));
    const perUnit = H / (2 * dist * Math.tan(THREE.MathUtils.degToRad(cam.fov / 2)));
    h = e.h * (n.id === hot ? hotScale : 1) * perUnit;
    w = h * e.ratio;
    ({ x: cx, y: cy } = c);
  } else {
    const s = size2d(n);
    const c = graph.graph2ScreenCoords(n.x, n.y);
    h = s.h * graph.zoom();
    w = s.w * graph.zoom();
    ({ x: cx, y: cy } = c);
  }
  return { left: box.left + cx - w / 2, top: box.top + cy - h / 2, width: w, height: h };
}
function shuffle(list) {
  const a = [...list];
  for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; }
  return a;
}

async function pickForMe() {
  if (!focus.value || picking.value) return;
  const how = session.prefs.wallPick;
  const { video, fallback } = pickVideo(myVideos.value, how, focus.value);
  if (!video) return;
  if (fallback) toast(t("None fits “{how}” — this one is picked at random", { how: t(PICK_MODES[how]) }));
  const target = nodes().find((n) => n.vid === video.id);
  if (!session.prefs.wallPickAnimate || reduceMotion() || !target || !graph) { openVideo(video); return; }

  const me_ = run = { skipped: false, wake: null };
  picking.value = true;
  hover.value = null;
  picked.value = null;
  const T = session.prefs.wallPickSeconds * 1000;
  const wait = (ms) => new Promise((done) => { const id = setTimeout(done, ms); me_.wake = () => { clearTimeout(id); done(); }; });
  try {
    for (const n of nodes()) { n.fx = n.x; n.fy = n.y; if (is3d()) n.fz = n.z; }   // hold still while it looks
    if (is3d()) graph.enableNavigationControls(false);
    // 1. look through a few of the videos, slowing down -- 45 %
    const hops = [...shuffle(nodes().filter((n) => n.kind === "video" && n !== target)).slice(0, 7), target];
    const weights = hops.map((_, i) => 1 + i * 0.6);
    const sum = weights.reduce((a, b) => a + b, 0);
    for (let i = 0; i < hops.length && !me_.skipped; i++) {
      const ms = (T * 0.45 * weights[i]) / sum;
      setHot(hops[i].id, 1.5);
      aim(hops[i], fill(hops[i], 0.28), ms);
      await wait(ms);
    }
    // 2. lock on -- 15 %
    if (!me_.skipped) { setHot(target.id, 1.9); await wait(T * 0.07); }
    if (!me_.skipped) { setHot(target.id, 1.5); await wait(T * 0.08); }
    // 3. zoom in until it fills the view -- 25 %
    if (!me_.skipped) { aim(target, fill(target, 0.9), T * 0.25); await wait(T * 0.25); }
    if (me_.skipped) { openVideo(video); return; }
    // 4. the picture grows into the video page's player -- 15 %
    if (video.thumb) startFlyIn(video.thumb, screenRect(target), T * 0.15 + 250);
    openVideo(video);
  } finally {
    if (run === me_) run = null;
    picking.value = false;
  }
}
function skipPick() { if (run) { run.skipped = true; run.wake?.(); } }
function onKey(e) {
  if (e.key === "Escape") { if (picking.value) skipPick(); else if (helpOpen.value) toggleHelp(false); }
}

watch([focus, showCo, minShared, mode], () => nextTick(build));
let ro = null;
onMounted(async () => {
  window.addEventListener("keydown", onKey);
  window.addEventListener("pointerup", onUp);
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
onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKey);
  window.removeEventListener("pointerup", onUp);
  skipPick();
  clearTimeout(hoverTimer);
  clearTimeout(redrawTimer);
  ro?.disconnect();
  destroy();
  smalls.clear();
});
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
      <button v-if="focus" class="btn btn-sm btn-primary" :disabled="picking || !myVideos.length" @click="pickForMe"><Icon name="dice" :size="14" />{{ $t("Pick for me") }}</button>
      <button v-if="focus" class="btn btn-sm" :disabled="picking" @click="setFocus(null)"><Icon name="back" :size="14" />{{ $t("Everyone") }}</button>
      <span class="bar-end">
        <button class="btn btn-ghost btn-icon btn-sm" :class="{ active: helpOpen }" :aria-expanded="helpOpen" :aria-label="$t('How the wall works')"
                :title="$t('How the wall works')" @click="toggleHelp()"><Icon name="help" :size="16" /></button>
        <button class="btn btn-ghost btn-sm" :aria-expanded="panelOpen" @click="panelOpen = !panelOpen">{{ panelOpen ? $t("Hide panel") : $t("Show panel") }}</button>
      </span>
    </div>

    <div class="stage-wrap">
      <div ref="wrap" class="stage-box" @pointermove="onMove" @pointerdown.capture="onDown" @pointerleave="hover = null">
        <p v-if="error" class="empty">{{ error }}</p>
        <p v-else-if="data && !data.performers.length" class="empty">{{ $t("Nobody on the wall yet: it shows performers linked to ThePornDB, FansDB or StashDB, or with details, photos or a gender set on their page.") }}</p>
        <div v-else-if="!data" class="skeleton fill" />
        <div ref="stage" class="stage" />

        <div v-if="hover" class="hover-card card" :style="hoverStyle">
          <div class="hc-media">
            <PreviewVideo v-if="hover.v.preview" :key="hover.v.id" :src="hover.v.preview" :item-id="hover.v.id" />
            <img v-else-if="hover.v.thumb" :src="hover.v.thumb" alt="">
          </div>
          <div class="hc-text"><strong>{{ hover.v.title }}</strong><span class="muted">{{ [hover.v.creator, hover.v.length].filter(Boolean).join(" · ") }}</span></div>
        </div>

        <div v-if="helpOpen" class="help card" role="dialog" :aria-label="$t('How the wall works')">
          <button class="btn btn-ghost btn-icon btn-sm close" :aria-label="$t('Close')" @click="toggleHelp(false)"><Icon name="x" :size="14" /></button>
          <h2>{{ $t("How the wall works") }}</h2>
          <ul>
            <li v-if="is3d()">{{ $t("Drag to turn it — any direction, all the way round. Right-drag (two fingers on a phone) moves it; scroll or pinch zooms.") }}</li>
            <li v-else>{{ $t("Drag to move it; scroll or pinch zooms.") }}</li>
            <li>{{ $t("Each dot is a performer; a line means they share videos — the thicker, the more. “Shared videos at least” hides the weaker lines.") }}</li>
            <li>{{ $t("Click a performer to put them in the middle with all their videos. “Also who they’re with” adds the other performers in those videos; “Everyone” goes back.") }}</li>
            <li>{{ $t("With a mouse, point at a video to play its preview. Click (tap) it to see it in the panel and open it.") }}</li>
            <li>{{ $t("Pick for me opens one of their videos — which one, and the search before it, are set in My Settings → The wall. Click, Esc or Skip ends the search.") }}</li>
          </ul>
        </div>

        <div v-if="picking" class="picking" @click="skipPick">
          <button class="btn btn-sm skip" @click.stop="skipPick">{{ $t("Skip") }}</button>
        </div>
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
          <div class="acts">
            <button class="btn btn-primary btn-sm" :disabled="picking || !myVideos.length" @click="pickForMe"><Icon name="dice" :size="14" />{{ $t("Pick for me") }}</button>
            <RouterLink :to="{ name: 'performer', params: { name: me.link } }" class="btn btn-sm">{{ $t("Profile") }}</RouterLink>
          </div>
          <p class="muted small">{{ $t("Picks: {how}", { how: $t(PICK_MODES[session.prefs.wallPick]) }) }} · <RouterLink :to="{ name: 'settings', query: { tab: 'wall' } }">{{ $t("change") }}</RouterLink></p>
          <p v-if="myVideos.length > MAX_FOCUS_VIDEOS" class="muted small">{{ $t("The wall shows {n} of their {total} videos.", { n: MAX_FOCUS_VIDEOS, total: myVideos.length }) }}</p>
          <template v-if="coStars.length">
            <h3>{{ $t("Most often with") }}</h3>
            <ul class="list">
              <li v-for="c in coStars.slice(0, 10)" :key="c.p.id"><button @click="setFocus(c.p.id)"><span>{{ c.p.name }}</span><span class="n">{{ $tn("{n} shared", "{n} shared", c.n) }}</span></button></li>
            </ul>
          </template>
        </template>
        <template v-else>
          <h2>{{ $t("Everyone") }}</h2>
          <p class="muted small">{{ $t("{p} performers · {v} videos", { p: data.performers.length, v: data.videos.length }) }}</p>
          <h3>{{ $t("Most videos") }}</h3>
          <ul class="list">
            <li v-for="p in top" :key="p.id"><button @click="setFocus(p.id)"><span>{{ p.name }}</span><span class="n">{{ p.videos }}</span></button></li>
          </ul>
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
.bar-end { margin-inline-start: auto; display: inline-flex; gap: 0.25rem; align-items: center; }
.bar-end .active { color: var(--accent); }
.stage-wrap { position: relative; flex: 1; min-height: 0; overflow: hidden; display: flex; }
.stage-box { position: relative; flex: 1; min-width: 0; }
.stage, .fill { position: absolute; inset: 0; }
.empty { position: absolute; inset: 0; display: grid; place-items: center; padding: 2rem; text-align: center; color: var(--text-dim); z-index: 1; }
.hover-card { position: absolute; z-index: 4; padding: 0; overflow: hidden; pointer-events: none; box-shadow: 0 0.75rem 2rem rgba(0, 0, 0, 0.45); }
.hc-media { aspect-ratio: 16 / 9; background: #000; }
.hc-media video, .hc-media img { width: 100%; height: 100%; object-fit: contain; display: block; }
.hc-text { padding: 0.45rem 0.6rem; display: flex; flex-direction: column; gap: 0.1rem; font-size: 0.8rem; }
.hc-text strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.help { position: absolute; top: 0.75rem; inset-inline-start: 0.75rem; z-index: 5; width: min(26rem, calc(100% - 1.5rem)); padding: 0.9rem 1rem;
  box-sizing: border-box; box-shadow: 0 0.75rem 2rem rgba(0, 0, 0, 0.45); max-height: calc(100% - 1.5rem); overflow: auto; }
.help h2 { font-size: 1rem; margin: 0 2rem 0.5rem 0; }
.help ul { margin: 0; padding-inline-start: 1.1rem; display: flex; flex-direction: column; gap: 0.4rem; font-size: 0.85rem; line-height: 1.4; }
.picking { position: absolute; inset: 0; z-index: 6; cursor: pointer; }
.skip { position: absolute; bottom: 1rem; left: 50%; transform: translateX(-50%); }
.drawer { position: relative; flex: none; width: 20rem; margin: 0 0.75rem 0.75rem; align-self: flex-start; max-height: calc(100% - 0.75rem); overflow: auto;
  padding: 0.9rem; display: flex; flex-direction: column; gap: 0.5rem; z-index: 2; box-sizing: border-box; }
.drawer > * { flex-shrink: 0; }
.drawer h2 { font-size: 1.05rem; margin: 0; overflow-wrap: anywhere; padding-inline-end: 2rem; }
.drawer h3 { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); margin: 0.4rem 0 0; }
.close { position: absolute; top: 0.5rem; inset-inline-end: 0.5rem; }
.small { font-size: 0.8rem; margin: 0; }
.acts { display: flex; gap: 0.4rem; flex-wrap: wrap; }
.list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.list button { all: unset; box-sizing: border-box; display: flex; justify-content: space-between; gap: 0.5rem; width: 100%;
  padding: 0.3rem 0.4rem; border-radius: 0.35rem; cursor: pointer; font-size: 0.85rem; color: var(--text); }
.list button:hover, .list button:focus-visible { background: var(--panel-hover); }
.list span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.n { color: var(--text-dim); flex: none; }
.who { display: flex; gap: 0.7rem; align-items: center; }
.ph { width: 3.5rem; height: 3.5rem; border-radius: 50%; object-fit: cover; flex: none; overflow: hidden; display: block; }
.vthumb { width: 100%; border-radius: 0.4rem; }
.drawer > .btn { align-self: flex-start; }
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
