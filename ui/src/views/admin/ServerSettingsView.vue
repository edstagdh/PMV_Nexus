<script setup>
// Admin -> Server Settings: app-wide configuration (not per-user preferences,
// which are under Settings). Tabs: Download, Re-encode, Uploads, Duplicate scan, Logging, Proxy, Performers,
// Credentials, Opening the app (+ Extensions). Saved to config overrides by /api/settings/*.
import { t } from "../../lib/i18n.js";
import { askConfirm } from "../../stores/confirm.js";
import { ref, computed, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import FolderPicker from "../../components/admin/FolderPicker.vue";
import CredentialsPanel from "../../components/admin/CredentialsPanel.vue";
import ProxyPanel from "../../components/admin/ProxyPanel.vue";
import ExtensionsPanel from "../../components/admin/ExtensionsPanel.vue";
import { ext } from "../../stores/extensions.js";
import { orderTabs } from "../../lib/tabOrder.js";
import TabBar from "../../components/TabBar.vue";
import { api } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";

const route = useRoute();
const router = useRouter();
const BASE_TABS = [
  { key: "download", label: t("Download") },
  { key: "reencode", label: t("Re-encode") },
  { key: "uploads", label: t("Uploads") },
  { key: "duplicates", label: t("Duplicate scan") },
  { key: "logging", label: t("Logging") },
  { key: "proxy", label: t("Proxy") },
  { key: "performers", label: t("Performers") },
  { key: "credentials", label: t("Credentials") },
  { key: "opening", label: t("Opening the app") },
];
// Extensions (2.30.0): a tab only when there are any.
// In your order (3.4.0, lib/tabOrder.js): the first one opens when no tab is asked for.
const TABS = computed(() => orderTabs(ext.extensions.length || ext.panels.length
  ? [...BASE_TABS, { key: "extensions", label: t("Extensions") }] : BASE_TABS, "server"));
const tab = computed(() => (TABS.value.some((tb) => tb.key === route.query.tab) ? route.query.tab : TABS.value[0].key));
const openTab = (key) => router.replace({ query: { ...route.query, tab: key } });

const PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"];
const CODECS = [
  { key: "hevc", label: "HEVC (H.265)", encoder: "x265", help: t("Plays almost everywhere (TVs, phones, browsers). The usual choice.") },
  { key: "av1", label: "AV1", encoder: t("SVT-AV1"), help: t("Smaller files at the same quality, but slower to encode and some older devices can't play it.") },
  { key: "h264", label: "AVC (H.264)", encoder: "x264", help: t("Plays on every device, but files get bigger: for videos an older TV or phone can't play, not to save space.") },
];
// SVT-AV1 presets: 0 = slowest / smallest ... 13 = fastest.
const AV1_PRESETS = Array.from({ length: 14 }, (_, n) => ({
  value: n,
  label: n === 8 ? t("{n} (default)", { n }) : n <= 3 ? t("{n} — very slow", { n }) : n <= 6 ? t("{n} — slow", { n })
    : n >= 11 ? t("{n} — very fast", { n }) : String(n),
}));
const ALGORITHMS = [
  { key: "grayscale", label: t("Grayscale (default)"), help: t("Compares small grayscale frames pixel by pixel. Fast and reliable for re-uploads and re-encodes.") },
  { key: "phash", label: t("Perceptual hash"), help: t("Compares frame fingerprints; copes better with colour changes, crops and overlays.") },
  { key: "combined", label: t("Combined"), help: t("A match by either method counts. Finds the most, with a slightly higher risk of false matches.") },
];

const settings = ref(null);
const error = ref("");
const dl = ref({});
const re = ref({});
const pb = ref({}); // converting while watching (2.13.0)
const dup = ref({});
const upl = ref({});
const lg = ref({});
const perf = ref({});
const tray = ref({});   // opening the app (2.31.0)
function saveTray() {
  const v = tray.value;
  return run("tray", () => api("/api/settings/tray", {
    method: "POST", body: { open_browser: !!v.open_browser, ...(v.browser_editable ? { browser: v.browser || "" } : {}) },
  }));
}
function savePerformers() {
  const v = perf.value;
  return run("performers", () => api("/api/settings/performers", {
    method: "POST", body: { merge_aliases: !!v.merge_aliases, refresh_days: int(v.refresh_days, 7, 365),
      tpdb_timeout: int(v.tpdb_timeout, 10, 600), tpdb_retries: int(v.tpdb_retries, 0, 5) },
  }));
}
const LOG_LEVELS = [
  { key: "DEBUG", label: t("Debug"), help: t("Everything, including routine details — for tracking down a problem.") },
  { key: "INFO", label: t("Info"), help: t("What the app does and every change made by a user (default).") },
  { key: "WARNING", label: t("Warning"), help: t("Only problems and things that need attention.") },
  { key: "ERROR", label: t("Error"), help: t("Only failures.") },
];
const ROTATIONS = ["1 day", "1 week", "1 month", "10 MB", "50 MB", "100 MB"];
const RETENTIONS = ["1 week", "1 month", "3 months", "6 months", "12 months", "forever"];
const IP_MODES = [
  { key: "hashed", label: t("Pseudonym"), help: t("A stable code per address, like ip-3f9a1c0b7d2e: you can follow one address without seeing it.") },
  { key: "masked", label: t("Last part only"), help: "e.g. ***.***.***.23" },
  { key: "full", label: t("Full address"), help: t("The real address.") },
  { key: "off", label: t("Not recorded"), help: t("No address at all.") },
];
const withCurrent = (list, value) => (value && !list.includes(value) ? [value, ...list] : list);
const UPLOAD_CODECS = [
  { key: "h264", label: "AVC (H.264)", help: t("Plays on everything; bigger files.") },
  { key: "hevc", label: "HEVC (H.265)", help: t("About half the size; uses the Re-encode tab's HEVC quality and preset.") },
  { key: "av1", label: "AV1", help: t("Smallest, slowest to encode; uses the Re-encode tab's AV1 quality and preset.") },
];
const folders = ref({ output: "", outputMove: true, cache: "", cacheMove: true });
const picking = ref(""); // "output" | "cache"
const busy = ref("");

async function load() {
  try {
    const s = await api("/api/settings");
    settings.value = s;
    dl.value = { ...s.download };
    re.value = { ...s.reencode };
    pb.value = { ...s.playback };
    dup.value = { ...s.duplicate_scan };
    upl.value = { ...s.upload };
    lg.value = { ...s.logging };
    perf.value = { ...s.performers };
    tray.value = { ...s.tray };
    folders.value = { output: s.download.output_dir, outputMove: true, cache: s.download.cache_dir, cacheMove: true };
    error.value = "";
  } catch (err) {
    error.value = err.message || t("Could not load the settings");
  }
}
onMounted(load);

// Re-encoding / sharing on or off everywhere (2.22.0); saved straight away.
function setFeature(name, on) {
  return run(`feature-${name}`, () => api("/api/settings/features", { method: "POST", body: { [name]: on } }),
    (on ? t("{feature} switched on", { feature: name === "reencode" ? t("Re-encoding") : name }) : t("{feature} switched off", { feature: name === "reencode" ? t("Re-encoding") : name })));
}

async function run(key, fn, okText = t("Saved")) {
  busy.value = key;
  try { await fn(); toast(okText, { type: "success" }); await load(); }
  catch (err) { toast(err.message || t("Could not save"), { type: "error", timeout: 8000 }); }
  finally { busy.value = ""; }
}

async function saveFolder(kind) {
  const isOut = kind === "output";
  const dir = (isOut ? folders.value.output : folders.value.cache).trim();
  const move = isOut ? folders.value.outputMove : folders.value.cacheMove;
  if (!dir) return;
  if (move && !(await askConfirm((isOut ? t("Move the existing videos to {folder}?", { folder: `\n${dir}` }) : t("Move the existing cache files to {folder}?", { folder: `\n${dir}` }))
    + "\n" + t("This can take a while for a big library."), { ok: t("Move") }))) return;
  busy.value = kind;
  api(isOut ? "/api/settings/download-dir" : "/api/settings/cache-dir", { method: "POST", body: { new_dir: dir, move_existing: move } })
    .then((r) => { toast(move ? t("Folder changed · {n} files moved", { n: r?.moved_files ?? 0 })
      : r?.relinked_files ? t("Folder changed · {n} files found there and linked", { n: r.relinked_files }) : t("Folder changed"), { type: "success" }); return load(); })
    .catch((err) => toast(err.message || t("Could not change the folder"), { type: "error", timeout: 8000 }))
    .finally(() => { busy.value = ""; });
}
function picked(path) {
  folders.value[picking.value] = path;
  picking.value = "";
}

const int = (v, min, max) => Math.min(max, Math.max(min, Math.round(Number(v) || min)));
function saveDownload() {
  const d = dl.value;
  return run("download", () => api("/api/settings/download", {
    method: "POST",
    body: {
      max_concurrent_downloads: int(d.max_concurrent_downloads, 1, 10),
      max_retries: int(d.max_retries, 0, 20),
      chunk_size_kb: int(d.chunk_size_kb, 16, 8192),
      download_funscripts: !!d.download_funscripts,
      save_metadata_json: !!d.save_metadata_json,
      min_free_percent: int(d.min_free_percent, 0, 50),
    },
  }));
}
function savePlayback() {
  const p = pb.value;
  return run("playback", () => api("/api/settings/playback", {
    method: "POST",
    body: { live_convert: !!p.live_convert, max_height: p.max_height === 480 ? 480 : 720,
            cpu_percent: int(p.cpu_percent, 10, 100), max_sessions: int(p.max_sessions, 1, 8) },
  }));
}
function saveReencode() {
  const r = re.value;
  return run("reencode", () => api("/api/settings/reencode", {
    method: "POST",
    body: {
      codec: r.codec, target_crf: int(r.target_crf, 0, 51), preset: r.preset,
      av1_crf: int(r.av1_crf, 1, 63), av1_preset: int(r.av1_preset, 0, 13),
      h264_crf: int(r.h264_crf ?? 20, 0, 51), h264_preset: r.h264_preset || "medium",
      warn_crf: !!r.warn_crf, copy_audio: !!r.copy_audio, downscale: !!r.downscale,
      cpu_limit_mode: r.cpu_limit_mode, cpu_limit_cores: int(r.cpu_limit_cores, 1, cpuCount.value),
      cpu_limit_percent: int(r.cpu_limit_percent, 5, 100),
      remove_chapters: !!r.remove_chapters, keep_original_file: !!r.keep_original_file, ask_each_time: r.ask_each_time !== false, check_first: r.check_first !== false,
    },
  }));
}
const cpuCount = computed(() => settings.value?.system?.cpu_count || 1);
const encoderMissing = computed(() => settings.value?.system?.encoders?.[re.value.codec] === false);
// Cores an encode will use with the current choice.
const coresUsed = computed(() => {
  const r = re.value, n = cpuCount.value;
  if (r.cpu_limit_mode === "cores") return Math.min(n, Math.max(1, Number(r.cpu_limit_cores) || 1));
  if (r.cpu_limit_mode === "percent") return Math.min(n, Math.max(1, Math.round((n * (Number(r.cpu_limit_percent) || 0)) / 100)));
  return n;
});

const DUP_DEFAULTS = { algorithm: "grayscale", percent: 96, samples: 10, phash_quorum: 0.6, duration_tolerance_percent: 5, use_audio: true, audio_min_similarity: 70, workers: 2 };
function saveDuplicates() {
  const d = dup.value;
  const clamp = (v, min, max) => Math.min(max, Math.max(min, Number(v)));
  return run("duplicates", () => api("/api/settings/duplicate-scan", {
    method: "POST",
    body: {
      scan_after_download: !!d.scan_after_download, scan_after_reencode: !!d.scan_after_reencode,
      scan_after_repair: !!d.scan_after_repair, algorithm: d.algorithm, percent: clamp(d.percent, 80, 100), samples: int(d.samples, 4, 30),
      phash_quorum: clamp(d.phash_quorum, 0.1, 1), duration_tolerance_percent: clamp(d.duration_tolerance_percent, 0, 50),
      use_audio: !!d.use_audio, audio_min_similarity: clamp(d.audio_min_similarity, 55, 100), workers: int(d.workers, 1, 16),
    },
  }));
}
const scanTest = ref(null);
async function testScanner() {
  scanTest.value = "busy";
  try { scanTest.value = await api("/api/settings/upload/scanner-test", { method: "POST" }); }
  catch (err) { scanTest.value = { ok: false, detail: err.message || t("The test didn't run") }; }
}

function saveUploads() {
  const u = upl.value;
  return run("uploads", () => api("/api/settings/upload", {
    method: "POST",
    body: { convert_codec: u.convert_codec, h264_crf: int(u.h264_crf, 0, 51), h264_preset: u.h264_preset, auto_convert: !!u.auto_convert,
      virus_scan: u.virus_scan || "auto", hash_check: u.hash_check || "off", max_size_gb: int(u.max_size_gb, 1, 1000), max_waiting: int(u.max_waiting, 1, 100) },
  }));
}
async function saveLogging() {
  const l = lg.value;
  if (!l.enabled && !(await askConfirm(t("Switch logging off? Nothing will be recorded — not in the console, the log file or Admin → Logs — until you switch it back on."), { ok: t("Switch off"), danger: true }))) return;
  return run("logging", () => api("/api/settings/logging", {
    method: "POST",
    body: {
      enabled: !!l.enabled, console: !!l.console, file: !!l.file, app_log: !!l.app_log, level: l.level,
      rotation: l.rotation, retention: l.retention, ip_mode: l.ip_mode,
      app_log_retention_days: int(l.app_log_retention_days, 1, 3650), app_log_max_rows: int(l.app_log_max_rows, 1000, 50000000),
    },
  }));
}
function resetDuplicates() { dup.value = { ...dup.value, ...DUP_DEFAULTS }; }
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>{{ $t("Server Settings") }}</h1>
        <p>{{ $t("App-wide settings for everyone. Your own interface preferences are under") }} <RouterLink to="/settings">{{ $t("My Settings") }}</RouterLink>.</p>
      </div>
    </header>

    <TabBar :tabs="TABS" :active="tab" page="server" :label="$t('Server settings')" @select="openTab" />

    <CredentialsPanel v-if="tab === 'credentials'" />
    <ExtensionsPanel v-else-if="tab === 'extensions'" />
    <ProxyPanel v-else-if="tab === 'proxy' && settings" :key="settings.proxy?.url + settings.proxy?.site_mode" :settings="settings" @saved="load" />
    <p v-else-if="error" class="error-text">{{ error }}</p>
    <div v-else-if="!settings" class="skeleton" style="height: 18rem; border-radius: var(--radius-lg)" />

    <!-- ================= Download ================= -->
    <template v-else-if="tab === 'download'">
      <section class="card panel">
        <h2>{{ $t("Folders") }}</h2>
        <div class="form-row">
          <label class="label" for="out-dir">{{ $t("Download folder") }}<small>{{ $t("Where the videos are saved.") }}</small></label>
          <div class="field-col">
            <div class="inline-input">
              <input id="out-dir" v-model="folders.output" class="input mono">
              <button class="btn btn-sm" @click="picking = 'output'">{{ $t("Browse…") }}</button>
            </div>
            <div class="form-actions">
              <label class="switch"><input v-model="folders.outputMove" type="checkbox">{{ $t("Move the videos already downloaded") }}</label>
              <span style="flex: 1" />
              <button class="btn btn-primary btn-sm" :disabled="busy === 'output' || folders.output === settings.download.output_dir" @click="saveFolder('output')">
                {{ busy === "output" ? $t("Changing…") : $t("Change folder") }}</button>
            </div>
          </div>
        </div>
        <div class="form-row">
          <label class="label" for="cache-dir">{{ $t("Cache folder") }}<small>{{ $t("Thumbnails, preview clips and metadata JSON.") }}</small></label>
          <div class="field-col">
            <div class="inline-input">
              <input id="cache-dir" v-model="folders.cache" class="input mono">
              <button class="btn btn-sm" @click="picking = 'cache'">{{ $t("Browse…") }}</button>
            </div>
            <div class="form-actions">
              <label class="switch"><input v-model="folders.cacheMove" type="checkbox">{{ $t("Move the files already cached") }}</label>
              <span style="flex: 1" />
              <button class="btn btn-primary btn-sm" :disabled="busy === 'cache' || folders.cache === settings.download.cache_dir" @click="saveFolder('cache')">
                {{ busy === "cache" ? $t("Changing…") : $t("Change folder") }}</button>
            </div>
          </div>
        </div>
      </section>

      <section class="card panel">
        <h2>{{ $t("Downloading") }}</h2>
        <div class="form-row">
          <label class="label" for="dl-conc">{{ $t("Downloads at the same time") }}<small>{{ $t("Over the direct connection. 1 is safest; more than 2 most likely gets downloads refused or slowed by the site. To download more at once, split them with a proxy (Proxy tab).") }}</small></label>
          <div class="field-col">
            <input id="dl-conc" v-model.number="dl.max_concurrent_downloads" class="input input-num" type="number" min="1" max="10">
            <small v-if="dl.max_concurrent_downloads > 2" class="error-text">{{ $t("More than 2 will most likely cause problems.") }}</small>
          </div>
        </div>
        <div class="form-row">
          <label class="label" for="dl-free">{{ $t("Stop downloads below (% free)") }}<small>{{ $t("0–50; 0 = off. When the drive with the videos or the app's data gets this full, downloads stop (and start again by themselves above it) so files aren't damaged. Re-encodes carry on.") }}</small></label>
          <div class="field-col">
            <input id="dl-free" v-model.number="dl.min_free_percent" class="input input-num" type="number" min="0" max="50">
            <small v-if="settings?.disk?.path" :class="settings.disk.low ? 'error-text' : 'muted'">{{ $t("Now: {percent}% free ({gb} GB) on {path}", { percent: settings.disk.free_percent, gb: (settings.disk.free_bytes / 1024 ** 3).toFixed(1), path: settings.disk.path }) }}{{ settings.disk.low ? " — " + $t("downloads are stopped") : "" }}</small>
          </div>
        </div>
        <div class="form-row">
          <label class="label" for="dl-retries">{{ $t("Retries per video") }}<small>{{ $t("0–20, before it's marked failed") }}</small></label>
          <input id="dl-retries" v-model.number="dl.max_retries" class="input input-num" type="number" min="0" max="20">
        </div>
        <div class="form-row">
          <label class="label" for="dl-chunk">{{ $t("Chunk size (KB)") }}<small>{{ $t("16–8192; leave as is unless downloads are unstable") }}</small></label>
          <input id="dl-chunk" v-model.number="dl.chunk_size_kb" class="input input-num" type="number" min="16" max="8192">
        </div>
        <div class="form-row">
          <span class="label">{{ $t("Extras") }}</span>
          <div class="toggles">
            <label class="switch"><input v-model="dl.download_funscripts" type="checkbox">{{ $t("Download funscripts when available") }}</label>
            <label class="switch"><input v-model="dl.save_metadata_json" type="checkbox">{{ $t("Save a metadata JSON next to each video") }}</label>
          </div>
        </div>
        <div class="form-actions"><button class="btn btn-primary btn-sm" :disabled="busy === 'download'" @click="saveDownload">{{ $t("Save") }}</button></div>
      </section>
    </template>

    <!-- ================= Re-encode ================= -->
    <section v-else-if="tab === 'reencode'" class="card panel">
      <div class="feature">
        <label class="switch"><input type="checkbox" :checked="settings.features?.reencode !== false" :disabled="busy === 'feature-reencode'"
               @change="setFeature('reencode', $event.target.checked)"><strong>{{ $t("Re-encoding") }}</strong></label>
        <p class="help-text">{{ $t("Off: “Re-encode” disappears from every menu and page for everyone, and the server refuses new re-encodes (ones already queued still finish). Converting uploads (Uploads tab) isn't affected.") }}</p>
      </div>
      <p class="help-text">{{ $t("Re-encoding makes videos smaller in a more efficient codec (HEVC, AV1), or playable on older devices (AVC). Start it from a video's ⋮ menu (“Re-encode…”), which asks for the codec and starts from the settings below — they can be changed there for that one re-encode.") }}</p>
      <div class="form-row">
        <span class="label">{{ $t("Codec") }}</span>
        <div class="field-col">
          <div class="seg">
            <button v-for="c in CODECS" :key="c.key" type="button" class="btn btn-sm" :class="{ 'btn-primary': re.codec === c.key }" @click="re.codec = c.key">{{ c.label }}</button>
          </div>
          <small class="help-text">{{ CODECS.find((c) => c.key === re.codec)?.help }}</small>
          <p v-if="encoderMissing" class="error-text">{{ $t("Your FFmpeg has no {encoder} encoder — re-encodes to this codec will fail. Install a full FFmpeg build.", { encoder: CODECS.find((c) => c.key === re.codec)?.encoder }) }}</p>
        </div>
      </div>
      <template v-if="re.codec === 'av1'">
        <div class="form-row">
          <label class="label" for="re-av1-crf">{{ $t("Quality (CRF)") }}<small>{{ $t("1–63. Lower = better and bigger. 28–35 is typical; default 32.") }}</small></label>
          <div class="range"><input id="re-av1-crf" v-model.number="re.av1_crf" type="range" min="1" max="63" step="1"><output>{{ re.av1_crf }}</output></div>
        </div>
        <div class="form-row">
          <label class="label" for="re-av1-preset">{{ $t("Speed preset") }}<small>{{ $t("Lower = slower, smaller file at the same quality.") }}</small></label>
          <select id="re-av1-preset" v-model.number="re.av1_preset" class="select" style="max-width: 14rem">
            <option v-for="p in AV1_PRESETS" :key="p.value" :value="p.value">{{ p.label }}</option>
          </select>
        </div>
      </template>
      <template v-else-if="re.codec === 'h264'">
        <div class="form-row">
          <label class="label" for="re-h264-crf">{{ $t("Quality (CRF)") }}<small>{{ $t("0–51. Lower = better and bigger. The video was already compressed, so keep it low (18–22) to lose nothing more you can see; default 20.") }}</small></label>
          <div class="range"><input id="re-h264-crf" v-model.number="re.h264_crf" type="range" min="0" max="51" step="1"><output>{{ re.h264_crf }}</output></div>
        </div>
        <div class="form-row">
          <label class="label" for="re-h264-preset">{{ $t("Encoding preset") }}<small>{{ $t("Slower = smaller file at the same quality.") }}</small></label>
          <select id="re-h264-preset" v-model="re.h264_preset" class="select" style="max-width: 14rem">
            <option v-for="p in PRESETS" :key="p" :value="p">{{ p }}</option>
          </select>
        </div>
      </template>
      <template v-else>
        <div class="form-row">
          <label class="label" for="re-crf">{{ $t("Quality (CRF)") }}<small>{{ $t("0–51. Lower = better and bigger. 22–26 is typical; default 24.") }}</small></label>
          <div class="range"><input id="re-crf" v-model.number="re.target_crf" type="range" min="0" max="51" step="1"><output>{{ re.target_crf }}</output></div>
        </div>
        <div class="form-row">
          <label class="label" for="re-preset">{{ $t("Encoding preset") }}<small>{{ $t("Slower = smaller file at the same quality.") }}</small></label>
          <select id="re-preset" v-model="re.preset" class="select" style="max-width: 14rem">
            <option v-for="p in PRESETS" :key="p" :value="p">{{ p }}</option>
          </select>
        </div>
      </template>
      <div class="form-row">
        <span class="label">{{ $t("CPU use") }}<small>{{ $tn("This server has {n} core.", "This server has {n} cores.", cpuCount) }}</small></span>
        <div class="field-col">
          <div class="seg">
            <button type="button" class="btn btn-sm" :class="{ 'btn-primary': re.cpu_limit_mode === 'off' }" @click="re.cpu_limit_mode = 'off'">{{ $t("All cores") }}</button>
            <button type="button" class="btn btn-sm" :class="{ 'btn-primary': re.cpu_limit_mode === 'cores' }" @click="re.cpu_limit_mode = 'cores'">{{ $t("Number of cores") }}</button>
            <button type="button" class="btn btn-sm" :class="{ 'btn-primary': re.cpu_limit_mode === 'percent' }" @click="re.cpu_limit_mode = 'percent'">{{ $t("% of cores") }}</button>
          </div>
          <div v-if="re.cpu_limit_mode === 'cores'" class="range">
            <input v-model.number="re.cpu_limit_cores" type="range" min="1" :max="cpuCount" step="1" :aria-label="$t('Cores')">
            <output>{{ coresUsed }} / {{ cpuCount }}</output>
          </div>
          <div v-else-if="re.cpu_limit_mode === 'percent'" class="range">
            <input v-model.number="re.cpu_limit_percent" type="range" min="5" max="100" step="5" :aria-label="$t('Percent of cores')">
            <output>{{ re.cpu_limit_percent }}%</output>
          </div>
          <small class="help-text">{{ re.cpu_limit_mode === "off" ? $t("An encode uses {used} of {total} cores.", { used: coresUsed, total: cpuCount }) : $t("An encode uses {used} of {total} cores — the rest stay free for playback and other work.", { used: coresUsed, total: cpuCount }) }}</small>
        </div>
      </div>
      <div class="form-row">
        <span class="label">{{ $t("Options") }}</span>
        <div class="toggles">
          <label class="switch"><input v-model="re.copy_audio" type="checkbox">{{ $t("Copy the audio instead of re-encoding it (when it's AAC)") }}</label>
          <label class="switch"><input v-model="re.downscale" type="checkbox">{{ $t("Downscale to 1080p if larger") }}</label>
          <label class="switch"><input v-model="re.remove_chapters" type="checkbox">{{ $t("Remove chapters") }}</label>
          <label class="switch"><input v-model="re.keep_original_file" type="checkbox">{{ $t("Keep the original file as \"_old\"") }}</label>
        </div>
      </div>
      <div class="form-row">
        <span class="label">{{ $t("Check the file first") }}<small>{{ $t("Before each re-encode.") }}</small></span>
        <div class="field-col">
          <label class="switch"><input v-model="re.check_first" type="checkbox">{{ $t("Make sure the video file isn't damaged before re-encoding it") }}</label>
          <small class="help-text">{{ $t("Reads the file through and decodes three short parts (a few seconds to a minute). A damaged file isn't re-encoded: it's reported, and the original stays. Re-encoding it a third time anyway uses the repair mode.") }}</small>
        </div>
      </div>
      <div class="form-row">
        <span class="label">{{ $t("Re-encode…") }}<small>{{ $t("What a video's ⋮ menu and Library → Select do.") }}</small></span>
        <div class="field-col">
          <label class="switch"><input v-model="re.ask_each_time" type="checkbox">{{ $t("Ask for the codec and settings each time") }}</label>
          <small class="help-text">{{ re.ask_each_time !== false
            ? $t("A window opens to pick the codec, starting from the settings above, which can be changed for that re-encode.")
            : $t("No window: videos are queued straight away with the codec and settings above. A video that already is in that codec still gets the window, to pick the other one.") }}</small>
        </div>
      </div>
      <div class="form-actions"><button class="btn btn-primary btn-sm" :disabled="busy === 'reencode'" @click="saveReencode">{{ $t("Save") }}</button></div>

      <h3 class="sub-h">{{ $t("Converting while watching") }}</h3>
      <Tx tag="p" class="help-text" msg="The player's {menu} — and any video the browser can't play — streams a copy the server converts on the fly (H.264, like Plex / Jellyfin). Nothing is stored: the pieces are deleted when playback stops. FFmpeg pauses itself once it's about a minute ahead of the viewer, so a paused video costs nothing."><template #menu><strong>⚙ → 720p</strong></template></Tx>
      <div class="form-row">
        <span class="label">{{ $t("Converting while watching") }}</span>
        <label class="switch"><input v-model="pb.live_convert" type="checkbox">{{ $t("On") }}</label>
      </div>
      <template v-if="pb.live_convert">
        <div class="form-row">
          <span class="label">{{ $t("Largest size") }}<small>{{ $t("Smaller videos keep their size.") }}</small></span>
          <div class="seg">
            <button type="button" class="btn btn-sm" :class="{ 'btn-primary': pb.max_height !== 480 }" @click="pb.max_height = 720">720p</button>
            <button type="button" class="btn btn-sm" :class="{ 'btn-primary': pb.max_height === 480 }" @click="pb.max_height = 480">480p</button>
          </div>
        </div>
        <div class="form-row">
          <span class="label">{{ $t("CPU use") }}<small>{{ $t("Per video being watched, at low priority.") }}</small></span>
          <div class="field-col">
            <div class="range">
              <input v-model.number="pb.cpu_percent" type="range" min="10" max="100" step="5" :aria-label="$t('Percent of cores')">
              <output>{{ pb.cpu_percent }}%</output>
            </div>
            <small class="help-text">{{ $t("{used} of {total} cores. Too few for a big video and the player waits (it needs about one core for 720p on a modern CPU).", { used: Math.max(1, Math.round(cpuCount * pb.cpu_percent / 100)), total: cpuCount }) }}</small>
          </div>
        </div>
        <div class="form-row">
          <span class="label">{{ $t("At the same time") }}<small>{{ $t("Videos converted at once (one per person).") }}</small></span>
          <input v-model.number="pb.max_sessions" class="input input-num" type="number" min="1" max="8">
        </div>
      </template>
      <div class="form-actions"><button class="btn btn-primary btn-sm" :disabled="busy === 'playback'" @click="savePlayback">{{ $t("Save") }}</button></div>
    </section>

    <!-- ================= Uploads ================= -->
    <section v-else-if="tab === 'uploads'" class="card panel">
      <p class="help-text"><Tx msg="What the {link1} page does with a file browsers can't play. MP4 files with a codec browsers play are kept as they are. Others are first re-packed into MP4 without touching the picture (fast, no quality loss). Only when that isn't possible is the video re-encoded — the uploader picks the codec, starting from the choice here."><template #link1><RouterLink to="/upload">{{ $t("Upload") }}</RouterLink></template></Tx></p>
      <div class="form-row">
        <span class="label">{{ $t("Convert to") }}<small>{{ $t("The suggested codec (and the one used automatically).") }}</small></span>
        <div class="field-col">
          <div class="seg">
            <button v-for="c in UPLOAD_CODECS" :key="c.key" type="button" class="btn btn-sm" :class="{ 'btn-primary': upl.convert_codec === c.key }"
                    @click="upl.convert_codec = c.key">{{ c.label }}</button>
          </div>
          <small class="help-text">{{ UPLOAD_CODECS.find((c) => c.key === upl.convert_codec)?.help }}</small>
          <p v-if="settings?.system?.encoders?.[upl.convert_codec] === false" class="error-text">{{ $t("Your FFmpeg has no encoder for this codec.") }}</p>
        </div>
      </div>
      <template v-if="upl.convert_codec === 'h264'">
        <div class="form-row">
          <label class="label" for="up-crf">{{ $t("AVC quality (CRF)") }}<small>{{ $t("0–51. Lower = better and bigger. 18–23 is typical; default 20.") }}</small></label>
          <div class="range"><input id="up-crf" v-model.number="upl.h264_crf" type="range" min="0" max="51" step="1"><output>{{ upl.h264_crf }}</output></div>
        </div>
        <div class="form-row">
          <label class="label" for="up-preset">{{ $t("AVC preset") }}<small>{{ $t("Slower = smaller file at the same quality.") }}</small></label>
          <select id="up-preset" v-model="upl.h264_preset" class="select" style="max-width: 14rem">
            <option v-for="p in PRESETS" :key="p" :value="p">{{ p }}</option>
          </select>
        </div>
      </template>
      <div class="form-row">
        <span class="label">{{ $t("Without asking") }}</span>
        <label class="switch"><input v-model="upl.auto_convert" type="checkbox">{{ $t("Convert automatically with these settings — the uploader isn't asked") }}</label>
      </div>
      <h3 class="sub-h">{{ $t("Safety") }}</h3>
      <p class="help-text">{{ $t("Only {formats} files can be picked. The server then checks that the file's contents really are that kind of video, that FFmpeg finds a video track in it, and re-writes every upload so only its picture and sound are kept. Uploads are handled one at a time, in the order they arrive.", { formats: ".mp4 .m4v .mkv .webm .mov .avi .wmv .ts" }) }}</p>
      <div class="form-row">
        <label class="label" for="up-scan">{{ $t("Virus scan") }}<small>{{ upl.scanner ? $t("Found on the server: {scanner}.", { scanner: upl.scanner }) : $t("No antivirus found on the server (Microsoft Defender on Windows, or ClamAV).") }}</small></label>
        <select id="up-scan" v-model="upl.virus_scan" class="select" style="max-width: 22rem">
          <option value="auto">{{ $t("Scan when an antivirus is available") }}</option>
          <option value="required">{{ $t("Required — refuse uploads without one") }}</option>
          <option value="off">{{ $t("Off") }}</option>
        </select>
      </div>
      <div class="form-row">
        <span class="label">{{ $t("Test the scanner") }}<small>{{ $t("Scans a small harmless file and shows exactly what the antivirus answered — to see why scanning doesn't work.") }}</small></span>
        <div class="field-col">
          <button class="btn btn-sm" style="align-self: flex-start" :disabled="scanTest === 'busy'" @click="testScanner">{{ scanTest === "busy" ? $t("Testing…") : $t("Test the scanner") }}</button>
          <div v-if="scanTest && scanTest !== 'busy'" class="scan-result" :class="scanTest.ok ? 'ok' : 'bad'">
            <strong>{{ scanTest.ok ? $t("✓ The scanner works") : $t("✗ The scanner doesn't work") }}</strong>
            <span v-if="scanTest.scanner">{{ $t("Scanner: {name}", { name: scanTest.scanner }) }}<template v-if="scanTest.exit_code != null"> · {{ $t("exit code {code} · answer: {answer}", { code: scanTest.exit_code, answer: scanTest.verdict }) }}</template></span>
            <span v-if="scanTest.detail">{{ scanTest.detail }}</span>
            <pre v-if="scanTest.output">{{ scanTest.output }}</pre>
          </div>
        </div>
      </div>
      <div class="form-row">
        <label class="label" for="up-hash">{{ $t("Fingerprint check (VirusTotal)") }}<small>{{ $t("Looks up the file's SHA-256 — only the fingerprint is sent, never the file. It catches files already known to be malicious; a file nobody has seen before (most videos) is “unknown” and passes.") }}
          {{ upl.virustotal_configured ? $t("API key saved.") : $t("Needs a free VirusTotal API key in Credentials.") }}</small></label>
        <select id="up-hash" v-model="upl.hash_check" class="select" style="max-width: 22rem">
          <option value="off">{{ $t("Off") }}</option>
          <option value="on">{{ $t("On — refuse known-bad files") }}</option>
          <option value="required">{{ $t("Required — also refuse when it can't be checked") }}</option>
        </select>
      </div>
      <div class="form-row">
        <label class="label" for="up-max">{{ $t("Largest file (GB)") }}<small>1–1000</small></label>
        <input id="up-max" v-model.number="upl.max_size_gb" class="input input-num" type="number" min="1" max="1000">
      </div>
      <div class="form-row">
        <label class="label" for="up-wait">{{ $t("Unfinished uploads per person") }}<small>{{ $t("1–100; more are refused until some finish") }}</small></label>
        <input id="up-wait" v-model.number="upl.max_waiting" class="input input-num" type="number" min="1" max="100">
      </div>
      <div class="form-actions"><button class="btn btn-primary btn-sm" :disabled="busy === 'uploads'" @click="saveUploads">{{ $t("Save") }}</button></div>
    </section>

    <!-- ================= Performers ================= -->
    <section v-else-if="tab === 'performers'" class="card panel">
      <p class="help-text"><Tx msg="Performer profiles come from ThePornDB (API key in the {link1} tab). Matching status and names waiting for a pick: {link2}."><template #link1><RouterLink :to="{ query: { tab: 'credentials' } }">{{ $t("Credentials") }}</RouterLink></template><template #link2><RouterLink :to="{ name: 'portal', params: { tab: 'maintenance' } }">{{ $t("Management → Maintenance → Performers") }}</RouterLink></template></Tx></p>
      <p v-if="!perf.tpdb_configured" class="error-text">{{ $t("No ThePornDB API key saved yet.") }}</p>
      <div class="form-row">
        <span class="label">{{ $t("Aliases") }}<small>{{ $t("e.g. “Luna P” and “Luna Prism”, when ThePornDB says they're the same person.") }}</small></span>
        <label class="switch"><input v-model="perf.merge_aliases" type="checkbox">{{ $t("Show a performer's aliases as their main name everywhere (filters, chips, suggestions, related videos). A wrong merge can be split on the profile page.") }}</label>
      </div>
      <div class="form-row">
        <span class="label">{{ $t("Matching") }}</span>
        <Tx tag="p" class="muted small" style="margin: 0" msg="New names are matched, and old profiles refreshed, by the job {job} in {link} (daily at first; change when it runs, or turn it off, there).">
          <template #job><strong>{{ $t("Match & refresh performers") }}</strong></template>
          <template #link><RouterLink :to="{ name: 'portal', params: { tab: 'scheduler' } }">{{ $t("Management → Scheduler") }}</RouterLink></template></Tx>
      </div>
      <div class="form-row">
        <label class="label" for="pf-days">{{ $t("Refresh profiles every") }}<small>{{ $t("Performer data rarely changes; 30 days is plenty.") }}</small></label>
        <div class="inline-input"><input id="pf-days" v-model.number="perf.refresh_days" class="input input-num" style="max-width: 7rem" type="number" min="7" max="365"><span class="muted">{{ $t("days") }}</span></div>
      </div>
      <div class="form-row">
        <label class="label" for="pf-timeout">{{ $t("Wait for ThePornDB") }}<small>{{ $t("How long to wait for an answer before trying again. Its search can take a while when it's busy.") }}</small></label>
        <div class="inline-input"><input id="pf-timeout" v-model.number="perf.tpdb_timeout" class="input input-num" style="max-width: 7rem" type="number" min="10" max="600"><span class="muted">{{ $t("seconds") }}</span></div>
      </div>
      <div class="form-row">
        <label class="label" for="pf-retries">{{ $t("Tries after no answer") }}<small>{{ $t("Each one waits a little longer first. 0: give up after the first.") }}</small></label>
        <input id="pf-retries" v-model.number="perf.tpdb_retries" class="input input-num" style="max-width: 7rem" type="number" min="0" max="5">
      </div>
      <div class="form-actions"><button class="btn btn-primary btn-sm" :disabled="busy === 'performers'" @click="savePerformers">{{ $t("Save") }}</button></div>
    </section>

    <!-- ================= Opening the app ================= -->
    <section v-else-if="tab === 'opening'" class="card panel">
      <p class="help-text">{{ $t("How the app opens on the computer it runs on: the Start-menu and desktop shortcuts, the tray icon, and Setup's “Open PMV Nexus now”.") }}</p>
      <div class="form-row">
        <span class="label">{{ $t("When it starts") }}<small>{{ $t("Started from a shortcut. Clicking a shortcut while it's already running always opens it.") }}</small></span>
        <label class="switch"><input v-model="tray.open_browser" type="checkbox">{{ $t("Open the app in the browser. Off: it only starts — its icon appears by the clock (click it to open the app).") }}</label>
      </div>
      <div class="form-row">
        <label class="label" for="tr-browser">{{ $t("Browser") }}<small><Tx msg="Empty: the default browser. {code1} is the app's address (added at the end when missing)."><template #code1><code>{url}</code></template></Tx></small></label>
        <div>
          <input id="tr-browser" v-model="tray.browser" class="input" style="width: 100%" :readonly="!tray.browser_editable" spellcheck="false"
                 :placeholder="$t('Default browser')">
          <p class="muted small" style="margin: 0.4rem 0 0"><Tx msg="e.g. {code1} (a Firefox profile) or {code2}."><template #code1><code>"C:\Program Files\Mozilla Firefox\firefox.exe" -P Videos --new-window {url}</code></template><template #code2><code>"C:\Program Files\Google\Chrome\Application\chrome.exe" --profile-directory="Profile 1" {url}</code></template></Tx></p>
          <p v-if="!tray.browser_editable" class="muted small" style="margin: 0.4rem 0 0"><Tx msg="It's a program the app starts, so it can only be changed on the computer the app runs on — open the app there at {code1}."><template #code1><code>http://localhost</code></template></Tx></p>
        </div>
      </div>
      <div class="form-actions"><button class="btn btn-primary btn-sm" :disabled="busy === 'tray'" @click="saveTray">{{ $t("Save") }}</button></div>
    </section>

    <!-- ================= Logging ================= -->
    <section v-else-if="tab === 'logging'" class="card panel">
      <Tx tag="p" class="help-text" msg="What the app records and where. Changes apply right away, no restart. Log files are in {folder}."><template #folder><code>{{ lg.log_dir }}</code></template></Tx>
      <div class="form-row">
        <span class="label">{{ $t("Logging") }}<small>{{ $t("Off = nothing is recorded anywhere.") }}</small></span>
        <label class="switch"><input v-model="lg.enabled" type="checkbox">{{ lg.enabled ? $t("On") : $t("Off — nothing is recorded") }}</label>
      </div>
      <fieldset class="lg-group" :disabled="!lg.enabled">
        <div class="form-row">
          <span class="label">{{ $t("Record to") }}</span>
          <div class="toggles">
            <label class="switch"><input v-model="lg.app_log" type="checkbox">{{ $t("Admin → Logs (searchable)") }}</label>
            <label class="switch"><input v-model="lg.file" type="checkbox">{{ $t("Log file (backend.log)") }}</label>
            <label class="switch"><input v-model="lg.console" type="checkbox">{{ $t("Console (the server window)") }}</label>
          </div>
        </div>
        <div class="form-row">
          <span class="label">{{ $t("Level") }}</span>
          <div class="field-col">
            <div class="seg">
              <button v-for="l in LOG_LEVELS" :key="l.key" type="button" class="btn btn-sm" :class="{ 'btn-primary': lg.level === l.key }" @click="lg.level = l.key">{{ l.label }}</button>
            </div>
            <small class="help-text">{{ LOG_LEVELS.find((l) => l.key === lg.level)?.help || lg.level }}</small>
          </div>
        </div>
        <div class="form-row">
          <label class="label" for="lg-rot">{{ $t("Log file: new file every") }}<small>{{ $t("By time, or when it reaches a size. Old files are zipped.") }}</small></label>
          <select id="lg-rot" v-model="lg.rotation" class="select" style="max-width: 14rem" :disabled="!lg.file">
            <option v-for="r in withCurrent(ROTATIONS, lg.rotation)" :key="r" :value="r">{{ r }}</option>
          </select>
        </div>
        <div class="form-row">
          <label class="label" for="lg-ret">{{ $t("Log file: keep old files for") }}</label>
          <select id="lg-ret" v-model="lg.retention" class="select" style="max-width: 14rem" :disabled="!lg.file">
            <option v-for="r in withCurrent(RETENTIONS, lg.retention)" :key="r" :value="r">{{ r }}</option>
          </select>
        </div>
        <div class="form-row">
          <label class="label" for="lg-days">{{ $t("Admin → Logs: keep entries for") }}<small>{{ $t("Days. Whichever limit is reached first wins.") }}</small></label>
          <div class="inline-input"><input id="lg-days" v-model.number="lg.app_log_retention_days" class="input input-num" style="max-width: 7rem" type="number" min="1" max="3650" :disabled="!lg.app_log"><span class="muted">{{ $t("days, at most") }}</span>
            <input v-model.number="lg.app_log_max_rows" class="input input-num" style="max-width: 9rem" type="number" min="1000" step="1000" :aria-label="$t('Maximum entries')" :disabled="!lg.app_log"><span class="muted">{{ $t("entries") }}</span></div>
        </div>
        <div class="form-row">
          <span class="label">{{ $t("Client addresses") }}<small>{{ $t("How sign-ins and actions show who connected.") }}</small></span>
          <div class="field-col">
            <div class="seg">
              <button v-for="m in IP_MODES" :key="m.key" type="button" class="btn btn-sm" :class="{ 'btn-primary': lg.ip_mode === m.key }" @click="lg.ip_mode = m.key">{{ m.label }}</button>
            </div>
            <small class="help-text">{{ IP_MODES.find((m) => m.key === lg.ip_mode)?.help }}</small>
          </div>
        </div>
      </fieldset>
      <div class="form-actions"><button class="btn btn-primary btn-sm" :disabled="busy === 'logging'" @click="saveLogging">{{ $t("Save") }}</button></div>
    </section>

    <!-- ================= Duplicate scan ================= -->
    <section v-else-if="tab === 'duplicates'" class="card panel">
      <p class="help-text"><Tx msg="How the duplicate scan on the {link1} page compares videos. Changing the method or thresholds makes the next scan compare everything again; changing the sample count or audio check also re-reads every file."><template #link1><RouterLink to="/admin/duplicates">{{ $t("Duplicates") }}</RouterLink></template></Tx></p>
      <div class="form-row">
        <span class="label">{{ $t("Scan automatically") }}<small>{{ $t("About a minute after the last of these, so a batch starts one scan. Only new or changed files are read.") }}</small></span>
        <div class="toggles">
          <label class="switch"><input v-model="dup.scan_after_download" type="checkbox">{{ $t("After a download") }}</label>
          <label class="switch"><input v-model="dup.scan_after_reencode" type="checkbox">{{ $t("After a re-encode") }}</label>
          <label class="switch"><input v-model="dup.scan_after_repair" type="checkbox">{{ $t("After a repair / update") }}</label>
          <small class="help-text"><Tx msg="The regular scan is the “Daily duplicate scan” job on the {link1} page."><template #link1><RouterLink to="/admin/scheduler">{{ $t("Scheduler") }}</RouterLink></template></Tx></small>
        </div>
      </div>
      <div class="form-row">
        <label class="label" for="dup-alg">{{ $t("Method") }}</label>
        <div class="field-col">
          <select id="dup-alg" v-model="dup.algorithm" class="select" style="max-width: 16rem">
            <option v-for="a in ALGORITHMS" :key="a.key" :value="a.key">{{ a.label }}</option>
          </select>
          <small class="help-text">{{ ALGORITHMS.find((a) => a.key === dup.algorithm)?.help }}</small>
        </div>
      </div>
      <div class="form-row">
        <label class="label" for="dup-pct">{{ $t("Required similarity") }}<small>{{ $t("80–100%. Higher = fewer, surer matches. Default 96.") }}</small></label>
        <div class="range"><input id="dup-pct" v-model.number="dup.percent" type="range" min="80" max="100" step="0.5"><output>{{ dup.percent }}%</output></div>
      </div>
      <div class="form-row">
        <label class="label" for="dup-samples">{{ $t("Frames compared per video") }}<small>{{ $t("4–30. More = slower first scan, fewer misses. Default 10.") }}</small></label>
        <input id="dup-samples" v-model.number="dup.samples" class="input input-num" type="number" min="4" max="30">
      </div>
      <div v-if="dup.algorithm !== 'grayscale'" class="form-row">
        <label class="label" for="dup-q">{{ $t("Perceptual hash quorum") }}<small>{{ $t("Share of frames that must match on their own. Default 0.6.") }}</small></label>
        <div class="range"><input id="dup-q" v-model.number="dup.phash_quorum" type="range" min="0.1" max="1" step="0.05"><output>{{ Number(dup.phash_quorum).toFixed(2) }}</output></div>
      </div>
      <div class="form-row">
        <label class="label" for="dup-dur">{{ $t("Duration tolerance") }}<small>{{ $t("0–50%. Videos whose lengths differ more are never compared. Default 5.") }}</small></label>
        <div class="range"><input id="dup-dur" v-model.number="dup.duration_tolerance_percent" type="range" min="0" max="50" step="1"><output>{{ dup.duration_tolerance_percent }}%</output></div>
      </div>
      <div class="form-row">
        <span class="label">{{ $t("Audio check") }}<small>{{ $t("Rejects visual matches whose audio is different (e.g. same clips, other song).") }}</small></span>
        <label class="switch"><input v-model="dup.use_audio" type="checkbox">{{ dup.use_audio ? $t("On") : $t("Off") }}</label>
      </div>
      <div v-if="dup.use_audio" class="form-row">
        <label class="label" for="dup-audio">{{ $t("Audio similarity needed") }}<small>{{ $t("55–100%. Default 70.") }}</small></label>
        <div class="range"><input id="dup-audio" v-model.number="dup.audio_min_similarity" type="range" min="55" max="100" step="1"><output>{{ dup.audio_min_similarity }}%</output></div>
      </div>
      <div class="form-row">
        <label class="label" for="dup-workers">{{ $t("Files read at the same time") }}<small>{{ $t("1–16. More is faster on SSDs, slower on hard drives. Default 2.") }}</small></label>
        <input id="dup-workers" v-model.number="dup.workers" class="input input-num" type="number" min="1" max="16">
      </div>
      <div class="form-actions">
        <button class="btn btn-primary btn-sm" :disabled="busy === 'duplicates'" @click="saveDuplicates">{{ $t("Save") }}</button>
        <button class="btn btn-ghost btn-sm" @click="resetDuplicates">{{ $t("Restore defaults") }}</button>
      </div>
    </section>

    <FolderPicker v-if="picking" :start="folders[picking]" :title="picking === 'output' ? $t('Download folder') : $t('Cache folder')"
                  @close="picking = ''" @pick="picked" />
  </div>
</template>

<style scoped>
.field-col { display: flex; flex-direction: column; gap: 0.5rem; min-width: 0; }
.toggles { display: flex; flex-direction: column; gap: 0.625rem; }
.seg { display: flex; gap: 0.25rem; flex-wrap: wrap; }
.range { display: flex; align-items: center; gap: 0.75rem; }
.range input { flex: 1; max-width: 20rem; accent-color: var(--accent); }
.range output { min-width: 3.5rem; font-variant-numeric: tabular-nums; font-weight: 600; }
.lg-group { border: 0; margin: 0; padding: 0; min-width: 0; display: flex; flex-direction: column; gap: inherit; }
.lg-group:disabled { opacity: 0.5; }
.sub-h { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); margin: 1rem 0 0.25rem; }
.scan-result { display: flex; flex-direction: column; gap: 0.2rem; font-size: 0.8rem; padding: 0.5rem 0.625rem; border-radius: var(--radius); border: 1px solid; }
.scan-result.ok { border-color: var(--success); }
.scan-result.bad { border-color: var(--danger); }
.scan-result pre { margin: 0.25rem 0 0; white-space: pre-wrap; font-size: 0.72rem; max-height: 10rem; overflow: auto; color: var(--text-2); }
.feature { display: flex; flex-direction: column; gap: 0.35rem; }
section.feature { margin-bottom: 1rem; }
.panel > .feature { padding-bottom: 1rem; margin-bottom: 1rem; border-bottom: 1px solid var(--border); }
.feature .help-text { margin: 0; }
</style>
