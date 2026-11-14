<script setup>
// Re-encode dialog body (2.22.0): pick the codec, see the server's settings for it
// and change them for this re-encode only. Used for one video and for picked ones.
import { computed } from "vue";
import { AV1_PRESETS, CODEC_INFO, isChanged, reencodeState } from "../lib/reencodeOptions.js";

const props = defineProps({
  defaults: Object,     // GET /api/reencode/defaults
  state: Object,        // reencodeState(); edited in place
  exclude: String,      // the codec the video already is in (one video)
});
const c = computed(() => props.state.codec);
const info = computed(() => CODEC_INFO[c.value]);
const d = computed(() => props.defaults);
const changed = computed(() => isChanged(props.state, d.value));
function reset() { Object.assign(props.state, reencodeState(d.value, props.exclude), { codec: props.state.codec }); }
const presetLabel = (p) => (c.value === "av1" ? AV1_PRESETS.find((x) => x.value === String(p))?.label || p : p);
</script>

<template>
  <div class="reopts">
    <div class="row">
      <span class="lbl">Codec</span>
      <div class="codecs" role="radiogroup" aria-label="Codec">
        <label v-for="(ci, key) in CODEC_INFO" :key="key" class="codec" :class="{ on: c === key, off: key === exclude || defaults.encoders?.[key] === false }">
          <input v-model="state.codec" type="radio" :value="key" :disabled="key === exclude || defaults.encoders?.[key] === false">
          <strong>{{ ci.label }}</strong>
          <small v-if="key === exclude">The video already is {{ ci.label.split(" ")[0] }}</small>
          <small v-else-if="defaults.encoders?.[key] === false">Your FFmpeg has no encoder for it</small>
          <small v-else>{{ ci.help }}<template v-if="key === defaults.codec"> · server default</template></small>
        </label>
      </div>
    </div>

    <div class="row">
      <label class="lbl" :for="`re-crf-${c}`">Quality (CRF)<small>{{ info.crf[0] }}–{{ info.crf[1] }}, lower = better and bigger; {{ info.crfHelp }}. Server: {{ d[c].crf }}</small></label>
      <div class="range"><input :id="`re-crf-${c}`" v-model.number="state[c].crf" type="range" :min="info.crf[0]" :max="info.crf[1]" step="1"><output>{{ state[c].crf }}</output></div>
    </div>
    <div class="row">
      <label class="lbl" :for="`re-preset-${c}`">{{ c === "av1" ? "Speed preset" : "Encoding preset" }}<small>Slower = smaller file at the same quality. Server: {{ presetLabel(d[c].preset) }}</small></label>
      <select :id="`re-preset-${c}`" v-model="state[c].preset" class="select">
        <template v-if="c === 'av1'"><option v-for="p in AV1_PRESETS" :key="p.value" :value="p.value">{{ p.label }}</option></template>
        <template v-else><option v-for="p in defaults.hevc_presets" :key="p" :value="p">{{ p }}</option></template>
      </select>
    </div>
    <div class="row">
      <span class="lbl">Options</span>
      <div class="toggles">
        <label class="switch"><input v-model="state.copy_audio" type="checkbox">Copy the audio when it's AAC</label>
        <label class="switch"><input v-model="state.downscale" type="checkbox">Downscale to 1080p if larger</label>
        <label class="switch"><input v-model="state.remove_chapters" type="checkbox">Remove chapters</label>
        <label class="switch"><input v-model="state.keep_original_file" type="checkbox">Keep the original file as "_old"</label>
      </div>
    </div>
    <p class="foot muted">
      <template v-if="changed">Changed for this re-encode only. <button type="button" class="linkish" @click="reset">Back to the server's settings</button></template>
      <template v-else>Server Settings (Server Settings → Re-encode). Changes here are for this re-encode only.</template>
    </p>
  </div>
</template>

<style scoped>
.reopts { display: flex; flex-direction: column; gap: 0.85rem; }
.row { display: grid; grid-template-columns: 10rem 1fr; gap: 0.75rem; align-items: start; }
@media (max-width: 36rem) { .row { grid-template-columns: 1fr; gap: 0.35rem; } }
.lbl { font-weight: 600; font-size: 0.875rem; display: flex; flex-direction: column; gap: 0.15rem; }
.lbl small { font-weight: 400; color: var(--text-3); font-size: 0.75rem; }
.codecs { display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); gap: 0.5rem; }
.codec { display: grid; grid-template-columns: auto 1fr; column-gap: 0.5rem; align-items: center; padding: 0.55rem 0.7rem;
  border: 1px solid var(--border); border-radius: var(--radius); cursor: pointer; }
.codec small { grid-column: 2; color: var(--text-3); font-size: 0.75rem; }
.codec.on { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, transparent); }
.codec.off { opacity: 0.55; cursor: not-allowed; }
.range { display: flex; align-items: center; gap: 0.75rem; }
.range input { flex: 1; }
.range output { min-width: 2ch; font-variant-numeric: tabular-nums; font-weight: 600; }
.select { max-width: 14rem; }
.toggles { display: flex; flex-direction: column; gap: 0.4rem; }
.foot { margin: 0; font-size: 0.8125rem; }
.linkish { background: none; border: 0; padding: 0; color: var(--accent); cursor: pointer; font: inherit; text-decoration: underline; }
</style>
