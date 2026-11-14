<script setup>
// Upload page: the file has to be re-encoded before browsers can play it.
// Pick the codec and its quality/speed; starts from Server Settings -> Uploads.
import { ref, computed, watch } from "vue";

const props = defineProps({ status: Object, busy: Boolean });
const emit = defineEmits(["convert", "cancel"]);

const CODECS = [
  { key: "h264", label: "AVC (H.264)", help: "Plays on everything. Bigger files. The safe choice.", crf: [0, 51], tip: "18–23 is typical" },
  { key: "hevc", label: "HEVC (H.265)", help: "About half the size of AVC; plays on most devices and browsers.", crf: [0, 51], tip: "22–26 is typical" },
  { key: "av1", label: "AV1", help: "Smallest files, slowest to encode; some older devices can't play it.", crf: [1, 63], tip: "28–35 is typical" },
];
const X_PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"];
const AV1_PRESETS = Array.from({ length: 14 }, (_, n) => String(n));

const pick = ref({ codec: "h264", crf: 20, preset: "medium" });
function useDefaults(codec) {
  const d = props.status?.codec_defaults?.[codec] || props.status?.defaults || {};
  pick.value = { codec, crf: d.crf ?? 20, preset: String(d.preset ?? "medium") };
}
watch(() => props.status?.defaults?.codec, (c) => { if (c) useDefaults(c); }, { immediate: true });

const codec = computed(() => CODECS.find((c) => c.key === pick.value.codec));
const presets = computed(() => (pick.value.codec === "av1" ? AV1_PRESETS : X_PRESETS));
const missing = (key) => props.status?.encoders?.[key] === false;
const probe = computed(() => props.status?.probe || {});
</script>

<template>
  <section class="card panel">
    <h2>This video needs converting</h2>
    <p class="help-text">{{ status.reason || status.message }}
      <template v-if="probe.video"> (video: {{ probe.video }}{{ probe.pix_fmt ? `, ${probe.pix_fmt}` : "" }}, audio: {{ probe.audio || "none" }}{{ probe.width ? `, ${probe.width}×${probe.height}` : "" }})</template>
      It's added to the library once the conversion is done.</p>
    <p v-if="status.message && status.message !== status.reason" class="error-text">{{ status.message }}</p>
    <div class="form-row">
      <span class="label">Codec</span>
      <div class="field-col">
        <div class="seg">
          <button v-for="c in CODECS" :key="c.key" type="button" class="btn btn-sm" :class="{ 'btn-primary': pick.codec === c.key }"
                  :disabled="missing(c.key)" :title="missing(c.key) ? 'Your FFmpeg has no encoder for this' : c.help" @click="useDefaults(c.key)">{{ c.label }}</button>
        </div>
        <small class="help-text">{{ codec.help }}</small>
      </div>
    </div>
    <div class="form-row">
      <label class="label" for="cv-crf">Quality (CRF)<small>{{ codec.crf[0] }}–{{ codec.crf[1] }}. Lower = better and bigger; {{ codec.tip }}.</small></label>
      <div class="range"><input id="cv-crf" v-model.number="pick.crf" type="range" :min="codec.crf[0]" :max="codec.crf[1]" step="1"><output>{{ pick.crf }}</output></div>
    </div>
    <div class="form-row">
      <label class="label" for="cv-preset">{{ pick.codec === "av1" ? "Speed preset" : "Encoding preset" }}<small>{{ pick.codec === "av1" ? "0 = slowest/smallest … 13 = fastest." : "Slower = smaller file at the same quality." }}</small></label>
      <select id="cv-preset" v-model="pick.preset" class="select" style="max-width: 14rem">
        <option v-for="p in presets" :key="p" :value="p">{{ p }}</option>
      </select>
    </div>
    <div class="form-actions">
      <button class="btn btn-primary" :disabled="busy || missing(pick.codec)" @click="emit('convert', { ...pick, crf: Number(pick.crf) })">Convert and add</button>
      <button class="btn" :disabled="busy" @click="emit('cancel')">Cancel the upload</button>
    </div>
  </section>
</template>

<style scoped>
h2 { font-size: 1.05rem; margin: 0; }
.field-col { display: flex; flex-direction: column; gap: 0.375rem; }
.seg { display: flex; flex-wrap: wrap; gap: 0.375rem; }
.range { display: flex; align-items: center; gap: 0.75rem; }
.range input { flex: 1; max-width: 22rem; }
.range output { min-width: 2rem; font-variant-numeric: tabular-nums; }
</style>
