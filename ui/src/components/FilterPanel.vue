<script>
// Same buckets as the quality badges; judged on the short side, so a
// vertical 1080x1920 video counts as 1080p.
export const QUALITIES = [
  { key: "SD", label: "480p" }, { key: "HD", label: "720p" }, { key: "FHD", label: "1080p" },
  { key: "QHD", label: "1440p" }, { key: "UHD", label: "2160p (4K)" }, { key: "8K", label: "8K" },
];
export const qualityLabel = (key) => QUALITIES.find((q) => q.key === key)?.label || key;
export const ORIENTATIONS = [
  // Getters: this list is made before a language loads, so the labels translate when read (3.7.0).
  { key: "landscape", get label() { return t("🖥️ Horizontal"); } }, { key: "portrait", get label() { return t("📱 Vertical"); } },
  { key: "square", get label() { return t("⬜ Square"); } },
];
</script>

<script setup>
import { t } from "../lib/i18n.js";
import MultiSelect from "./MultiSelect.vue";
import DurationRange from "./DurationRange.vue";
import { computed, ref, onMounted } from "vue";
import { session } from "../stores/session.js";
import { api } from "../lib/api.js";

const props = defineProps({ state: Object, facets: Object, goneCount: Number, activeCount: Number });
// My Settings -> Library -> Group codec versions (2.17.1): "AV1 (Main@L4.0)" and the
// other versions show as one "AV1"; the server matches a family to all its versions.
const codecOptions = computed(() => {
  const all = props.facets?.codecs || [];
  return session.prefs.groupCodecs ? [...new Set(all.map((c) => c.split(" (")[0].trim()).filter(Boolean))].sort() : all;
});
const emit = defineEmits(["set", "clear"]);
const RATINGS = [null, 50, 60, 70, 80, 90];

// Performer details (2.19.0), from the ThePornDB profiles of the library's performers.
const pf = ref(null);
onMounted(async () => { try { pf.value = await api("/api/library/performer-filters"); } catch { pf.value = { known: 0 }; } });
</script>

<template>
  <section class="fpanel card">
    <div class="field exclude">
      <label class="field-title" for="flt-exclude">{{ $t("Exclude") }}</label>
      <input id="flt-exclude" class="input" type="search" :value="state.x" maxlength="500" autocomplete="off"
             :placeholder="$t('Leave out… e.g. tag:avc title:downbad music:remix pov')"
             :title="$t('Videos with any of these are left out. A word alone: anywhere. field:word: only there (tag:, performer:, title:, creator:, music:, description:, codec:). “Two words” together; * as in the search.')"
             @change="emit('set', { x: $event.target.value.trim() })" @keydown.enter="emit('set', { x: $event.target.value.trim() })">
    </div>
    <div class="field">
      <label class="field-title" for="flt-performers">{{ $t("Performers") }}</label>
      <MultiSelect input-id="flt-performers" :options="facets?.performers || []" :model-value="state.performers" :placeholder="$t('Search performers…')"
                   @update:model-value="emit('set', { performer: $event })" />
    </div>
    <div class="field">
      <label class="field-title" for="flt-tags">{{ $t("Tags") }}</label>
      <MultiSelect input-id="flt-tags" :options="facets?.tags || []" :model-value="state.tags" :placeholder="$t('Search tags…')"
                   @update:model-value="emit('set', { tag: $event })" />
    </div>
    <div class="field">
      <label class="field-title" for="flt-creators">{{ $t("Creators") }}</label>
      <MultiSelect input-id="flt-creators" :options="facets?.creators || []" :model-value="state.creators" :placeholder="$t('Search creators…')"
                   @update:model-value="emit('set', { creator: $event })" />
    </div>
    <div class="field">
      <label class="field-title" for="flt-codecs">{{ $t("Codecs") }}</label>
      <MultiSelect input-id="flt-codecs" :options="codecOptions" :model-value="state.codecs" :placeholder="$t('Search codecs…')"
                   @update:model-value="emit('set', { codec: $event })" />
    </div>
    <div class="field duration">
      <span>{{ $t("Duration") }}</span>
      <DurationRange :min="state.dsmin" :max="state.dsmax"
                     @change="emit('set', { dsmin: $event.min ?? '', dsmax: $event.max ?? '', dmin: '', dmax: '' })" />
    </div>
    <div class="field quality">
      <span>{{ $t("Quality") }}</span>
      <div class="qchips" role="group" :aria-label="$t('Exact qualities')">
        <button v-for="q in QUALITIES" :key="q.key" type="button" class="qchip" :class="{ on: state.quality.includes(q.key) }"
                :aria-pressed="state.quality.includes(q.key)"
                @click="emit('set', { quality: state.quality.includes(q.key) ? state.quality.filter((x) => x !== q.key) : [...state.quality, q.key] })">
          {{ q.label }}
        </button>
      </div>
      <select class="select select-sm" :value="state.qmin ?? ''" :aria-label="$t('Minimum quality')" @change="emit('set', { qmin: $event.target.value })">
        <option value="">{{ $t("Any quality or better") }}</option>
        <option v-for="q in QUALITIES.slice(0, -1)" :key="q.key" :value="q.key">{{ $t("{quality} or better", { quality: q.label }) }}</option>
      </select>
      <div class="qchips orient" role="group" :aria-label="$t('Orientation')">
        <button v-for="o in ORIENTATIONS" :key="o.key" type="button" class="qchip" :class="{ on: state.orient === o.key }"
                :aria-pressed="state.orient === o.key" @click="emit('set', { orient: state.orient === o.key ? '' : o.key })">
          {{ o.label }}
        </button>
      </div>
    </div>
    <label class="field">
      <span>{{ $t("Minimum rating") }}</span>
      <select class="select" :value="state.rating ?? ''" @change="emit('set', { rating: $event.target.value })">
        <option v-for="r in RATINGS" :key="String(r)" :value="r ?? ''">{{ r ? $t("{percent}% or higher", { percent: r }) : $t("Any rating") }}</option>
      </select>
    </label>
    <div class="toggles">
      <label class="switch" :title="$t('Off: videos with ANY selected tag/performer. On: videos with ALL of them.')">
        <input type="checkbox" :checked="state.all" @change="emit('set', { match: $event.target.checked ? 'all' : '' })">
        {{ $t("Must match all tags / performers") }}
      </label>
      <label class="switch" :title="$t('Only videos with no music listed (e.g. to run Find songs on them)')">
        <input type="checkbox" :checked="state.nomusic" @change="emit('set', { nomusic: $event.target.checked ? '1' : '' })">
        {{ $t("Only without music") }}
      </label>
      <label class="switch" :title="$t('Only videos with no performers listed')">
        <input type="checkbox" :checked="state.noperf" @change="emit('set', { noperf: $event.target.checked ? '1' : '' })">
        {{ $t("Only without performers") }}
      </label>
      <label class="site-status">
        <span>{{ $t("On PMVHaven") }}</span>
        <select class="select select-sm" :value="state.gone === true ? '1' : state.gone === false ? '0' : ''"
                @change="emit('set', { gone: $event.target.value })">
          <option value="">{{ $t("Any") }}</option>
          <option value="0">{{ $t("Still on PMVHaven") }}</option>
          <option value="1">💀 {{ $t("Gone from PMVHaven") }}{{ goneCount ? ` (${goneCount})` : "" }}</option>
        </select>
      </label>
    </div>
    <div class="field pdetails">
      <span class="field-title" :title="$t('A video matches when one of its performers has all of these (matched to ThePornDB)')">{{ $t("Performer details") }}</span>
      <p v-if="pf && !pf.known" class="muted small">{{ $t("Needs performers matched to ThePornDB (Management → Maintenance → Performers).") }}</p>
      <div v-else-if="pf" class="pgrid">
        <div class="field">
          <label class="field-title sub" for="flt-pcup">{{ $t("Cup size") }}</label>
          <MultiSelect input-id="flt-pcup" :options="pf.cups" :model-value="state.pcup" :placeholder="$t('Any cup…')"
                       @update:model-value="emit('set', { pcup: $event })" />
        </div>
        <div class="field">
          <span class="field-title sub">{{ $t("Age") }}</span>
          <div class="range">
            <input class="input" type="number" min="18" max="99" :placeholder="pf.age_min ? `${pf.age_min}` : $t('Min')" :aria-label="$t('Youngest age')"
                   :value="state.pamin ?? ''" @change="emit('set', { pamin: $event.target.value })">
            <span class="muted">{{ $t("to") }}</span>
            <input class="input" type="number" min="18" max="99" :placeholder="pf.age_max ? `${pf.age_max}` : $t('Max')" :aria-label="$t('Oldest age')"
                   :value="state.pamax ?? ''" @change="emit('set', { pamax: $event.target.value })">
          </div>
        </div>
        <div class="field">
          <label class="field-title sub" for="flt-phair">{{ $t("Hair") }}</label>
          <MultiSelect input-id="flt-phair" :options="pf.hair" :model-value="state.phair" :placeholder="$t('Any hair colour…')"
                       @update:model-value="emit('set', { phair: $event })" />
        </div>
        <div class="field">
          <label class="field-title sub" for="flt-peyes">{{ $t("Eyes") }}</label>
          <MultiSelect input-id="flt-peyes" :options="pf.eyes" :model-value="state.peyes" :placeholder="$t('Any eye colour…')"
                       @update:model-value="emit('set', { peyes: $event })" />
        </div>
        <div class="field">
          <label class="field-title sub" for="flt-penh">{{ $t("Breasts") }}</label>
          <select id="flt-penh" class="select" :value="state.penh === true ? '1' : state.penh === false ? '0' : ''"
                  @change="emit('set', { penh: $event.target.value })">
            <option value="">{{ $t("Any") }}</option>
            <option value="1">{{ $t("Enhanced") }}</option>
            <option value="0">{{ $t("Natural") }}</option>
          </select>
        </div>
      </div>
    </div>
    <div class="foot">
      <span class="muted">{{ activeCount ? $tn("{n} filter on — ✕ next to the search box clears them", "{n} filters on — ✕ next to the search box clears them", activeCount) : $t("No filters on") }}</span>
    </div>
  </section>
</template>

<style scoped>
.qchips { display: flex; flex-wrap: wrap; gap: 0.25rem; }
.qchip { border: 1px solid var(--border-strong); background: var(--panel-2); color: var(--text-2); border-radius: 999px; padding: 0.2rem 0.6rem; font: 600 0.75rem var(--font); cursor: pointer; }
.qchip:hover { color: var(--text); }
.qchip.on { background: var(--accent-soft); border-color: var(--accent); color: var(--accent); }
.quality .select-sm { height: 2rem; margin-top: 0.25rem; }
.qchips.orient { margin-top: 0.375rem; }
.site-status { display: inline-flex; align-items: center; gap: 0.5rem; font-size: 0.8125rem; color: var(--text-2); }
.site-status .select-sm { height: 2rem; }
.fpanel { display: grid; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr)); gap: 0.875rem 1.125rem; padding: 1rem; }
.range { display: flex; align-items: center; gap: 0.5rem; }
.range .input { width: 100%; }
.foot { grid-column: 1 / -1; display: flex; align-items: center; justify-content: flex-end; gap: 0.75rem;
  padding-top: 0.75rem; border-top: 1px solid var(--border); font-size: 0.8125rem; }
.exclude { grid-column: 1 / -1; }
.duration { grid-column: span 2; min-width: 0; }
@media (max-width: 40rem) { .duration { grid-column: 1 / -1; } }
.pdetails { grid-column: 1 / -1; border-top: 1px solid var(--border); padding-top: 0.75rem; }
.pgrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); gap: 0.625rem 1rem; }
.field-title.sub { font-size: 0.75rem; }
.small { font-size: 0.8125rem; margin: 0; }
.toggles { display: flex; flex-direction: column; gap: 0.625rem; justify-content: center; }
</style>
