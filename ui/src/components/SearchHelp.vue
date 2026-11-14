<script setup>
// The "?" in the library's search box: a small card of search examples.
// Clicking an example puts it in the search box.
import { ref, onBeforeUnmount } from "vue";
import Icon from "./Icon.vue";

const emit = defineEmits(["pick"]);
const open = ref(false);
const root = ref(null);

const EXAMPLES = [
  { q: "sunset", what: "Anywhere: title, creator, description, tags, performers, music" },
  { q: "alyx star natasha", what: "Every word, in any order" },
  { q: '"golden hour"', what: "Quotes: these words together, as written" },
  { q: "title:sunset", what: "Only in titles" },
  { q: "creator:rhythmix", what: "Only the creator's name" },
  { q: "performer:ava", what: "Performers (also model:)" },
  { q: "tag:pmv", what: "Tags" },
  { q: "tag:avc", what: "Tags containing \"avc\" anywhere" },
  { q: "tag:4k*", what: "Tags starting with 4k (* = any letters)" },
  { q: "music:remix", what: "Music credits" },
  { q: "codec:hevc", what: "Codec, any version (codec:av1*, codec:*l5.1*)" },
  { q: "description:tracklist", what: "Descriptions (also desc:)" },
];

function onDoc(e) { if (!root.value?.contains(e.target)) close(); }
function onKey(e) { if (e.key === "Escape") close(); }
function toggle() {
  open.value = !open.value;
  if (open.value) { document.addEventListener("pointerdown", onDoc); document.addEventListener("keydown", onKey); }
  else close();
}
function close() {
  open.value = false;
  document.removeEventListener("pointerdown", onDoc);
  document.removeEventListener("keydown", onKey);
}
function pick(q) { emit("pick", q); close(); }
onBeforeUnmount(close);
</script>

<template>
  <span ref="root" class="search-help">
    <button type="button" class="help-btn" :class="{ on: open }" title="Search tips" aria-label="Search tips"
            :aria-expanded="open" @click="toggle"><Icon name="help" :size="16" /></button>
    <div v-if="open" class="help-pop card" role="dialog" aria-label="Search tips">
      <div class="help-title">Search tips</div>
      <p class="muted">Finds videos with every word you type, in any order; matches parts of words and ignores case
        (<code class="inl">avc</code> finds "AVC", "H264-AVC"…). <code class="inl">*</code> stands for any letters within one tag or name.
        Start with a field name and a colon to search just that field.</p>
      <ul>
        <li v-for="ex in EXAMPLES" :key="ex.q">
          <button type="button" class="ex" :title="`Search for ${ex.q}`" @click="pick(ex.q)"><code>{{ ex.q }}</code></button>
          <span class="muted">{{ ex.what }}</span>
        </li>
      </ul>
      <p class="muted small">Filters (tags, performers, length, quality…) are under <strong>Filters</strong>; press <kbd>/</kbd> to jump to the search box.</p>
    </div>
  </span>
</template>

<style scoped>
.search-help { position: relative; display: inline-flex; }
.help-btn { display: grid; place-items: center; width: 1.625rem; height: 1.625rem; border: 0; border-radius: 50%;
  background: none; color: var(--text-dim); cursor: pointer; }
.help-btn:hover, .help-btn.on { color: var(--accent); background: var(--accent-soft); }
.help-pop { position: absolute; top: calc(100% + 0.5rem); right: -0.5rem; z-index: 30; width: min(26rem, calc(100vw - 2rem));
  padding: 0.875rem 1rem; box-shadow: var(--shadow); border-color: var(--border-strong); font-size: 0.8125rem; }
.help-title { font-weight: 650; margin-bottom: 0.25rem; }
.help-pop p { margin: 0 0 0.625rem; }
.help-pop .small { margin: 0.625rem 0 0; font-size: 0.75rem; }
ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.375rem; }
li { display: grid; grid-template-columns: minmax(9.5rem, auto) 1fr; gap: 0.625rem; align-items: baseline; }
.ex { border: 0; background: none; padding: 0; text-align: left; cursor: pointer; }
.ex code { font: 0.75rem var(--mono); color: var(--accent); background: var(--panel-2); border-radius: 0.25rem; padding: 0.125rem 0.375rem; }
.ex:hover code { background: var(--accent-soft); }
.inl { font: 0.72rem var(--mono); color: var(--accent); background: var(--panel-2); border-radius: 0.25rem; padding: 0 0.25rem; }
kbd { font: 0.6875rem var(--mono); border: 1px solid var(--border-strong); border-radius: 0.25rem; padding: 0 0.25rem; }
@media (max-width: 32.5rem) { li { grid-template-columns: 1fr; gap: 0.125rem; } }
</style>
