<script setup>
// Management -> Names -> Spellings (upper/lower case): tags, performers and music written in
// more than one way ("Natural Tits" / "Natural tits"). The app already shows
// one spelling for each (the most used; on a tie the newest); here you can
// pick which one instead. Videos keep what their source wrote -- only what's
// shown, filtered and suggested changes (see backend/spellings.py).
import { t } from "../../lib/i18n.js";
import { ref, watch } from "vue";
import { api, qs } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";

const FIELDS = [{ key: "tags", label: t("Tags") }, { key: "performers", label: t("Performers") }, { key: "music", label: t("Music") }];
const field = ref("tags");
const groups = ref(null);
const busy = ref("");
const error = ref("");

async function load() {
  groups.value = null;
  try { groups.value = (await api(`/api/library/spellings?${qs({ field: field.value })}`)).groups; error.value = ""; }
  catch (err) { error.value = err.message || t("Could not load the spellings"); }
}
watch(field, load, { immediate: true });

async function choose(g, spelling) {
  busy.value = g.key;
  try {
    await api("/api/library/spellings", { method: "POST", body: { field: field.value, value: g.key, spelling } });
    toast(spelling ? t("“{spelling}” it is", { spelling }) : t("Back to automatic"), { type: "success" });
    await load();
  } catch (err) { toast(err.message || t("Could not save"), { type: "error" }); }
  finally { busy.value = ""; }
}
</script>

<template>
  <section class="card block">
    <div class="block-head">
      <div>
        <h2>{{ $t("Spellings") }}</h2>
        <p class="muted small">{{ $t("Values written in more than one way count as one: everywhere the app shows the spelling most videos use (on a tie, the newest), so it follows your library as new videos arrive. Pick a spelling to always use that one instead. Your videos keep what PMVHaven wrote; only what's shown, filtered and suggested changes.") }}</p>
      </div>
      <div class="seg">
        <button v-for="f in FIELDS" :key="f.key" type="button" class="btn btn-sm" :class="{ 'btn-primary': field === f.key }" @click="field = f.key">{{ f.label }}</button>
      </div>
    </div>
    <p v-if="error" class="error-text">{{ error }}</p>
    <div v-else-if="!groups" class="skeleton" style="height: 4rem; border-radius: var(--radius)" />
    <p v-else-if="!groups.length" class="muted small">{{ { tags: $t("Every tag is written one way only."), performers: $t("Every performer is written one way only."), music: $t("Every song is written one way only.") }[field] }}</p>
    <ul v-else class="groups">
      <li v-for="g in groups" :key="g.key">
        <span class="total muted">{{ $tn("{n} video", "{n} videos", g.total) }}</span>
        <div class="variants" role="radiogroup" :aria-label="$t('Spelling of {chosen}', { chosen: g.chosen })">
          <label class="choice" :class="{ on: !g.pinned }">
            <input type="radio" :name="g.key" :checked="!g.pinned" :disabled="busy === g.key" @change="choose(g, null)">
            {{ $t("Automatic") }}<span class="muted"> ({{ g.pinned ? $t("most used") : g.chosen }})</span>
          </label>
          <label v-for="v in g.variants" :key="v.value" class="choice" :class="{ on: g.pinned && g.chosen === v.value }">
            <input type="radio" :name="g.key" :checked="g.pinned && g.chosen === v.value" :disabled="busy === g.key" @change="choose(g, v.value)">
            {{ v.value }}<span class="muted"> · {{ v.count }}</span>
          </label>
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.block { padding: 1rem 1.125rem; display: flex; flex-direction: column; gap: 0.75rem; }
.block-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; }
.block-head h2 { font-size: 1.05rem; }
.block-head p { margin: 0.25rem 0 0; max-width: 50rem; }
.seg { display: flex; gap: 0.375rem; flex-wrap: wrap; }
.small { font-size: 0.8rem; }
.groups { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.375rem; max-height: 28rem; overflow-y: auto; }
.groups li { display: flex; gap: 0.75rem; align-items: center; padding: 0.375rem 0.5rem; border-radius: var(--radius); border: 1px solid var(--border); flex-wrap: wrap; }
.total { width: 5.5rem; font-size: 0.75rem; flex-shrink: 0; }
.variants { display: flex; gap: 0.375rem; flex-wrap: wrap; }
.choice { display: inline-flex; align-items: center; gap: 0.375rem; padding: 0.2rem 0.6rem; border-radius: 999px; border: 1px solid var(--border-strong); font-size: 0.8rem; cursor: pointer; }
.choice.on { border-color: var(--accent); background: var(--accent-soft); }
.choice input { accent-color: var(--accent); margin: 0; }
@media (max-width: 56rem) { .block-head { flex-direction: column; } }
</style>
