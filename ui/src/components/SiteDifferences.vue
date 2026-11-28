<script setup>
// Performer page (3.12.0): a performer linked to more than one site (ThePornDB, FansDB, StashDB) --
// what each site says, side by side; the details they disagree on first and marked.
import { ref, computed } from "vue";

const props = defineProps({ diff: Object });
const all = ref(false);
const rows = computed(() => (all.value ? props.diff.fields : props.diff.fields.filter((f) => f.differs)));
</script>

<template>
  <section class="card sd">
    <div class="head">
      <h3 class="sub">{{ $t("The sites compared") }}</h3>
      <button class="btn btn-ghost btn-sm" @click="all = !all">{{ all ? $t("Only the differences") : $t("Every detail") }}</button>
    </div>
    <p class="muted small">{{ diff.differing ? $tn("{n} detail differs between the sites.", "{n} details differ between the sites.", diff.differing)
      : $t("The sites agree on every detail they share.") }}</p>
    <div v-if="rows.length" class="wrap">
      <table>
        <thead><tr><th />
          <th v-for="s in diff.sites" :key="s.key">{{ s.label }}</th></tr></thead>
        <tbody>
          <tr v-for="f in rows" :key="f.field" :class="{ differs: f.differs }">
            <th scope="row">{{ f.label }}<span v-if="f.differs" class="mark" :title="$t('The sites differ')">≠</span>
              <span v-else-if="f.differs === null" class="muted small" :title="$t('Written differently by the sites (a country code and a name): not compared')">?</span></th>
            <td v-for="s in diff.sites" :key="s.key">{{ f.values[s.key] ?? "—" }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.sd { padding: 1rem 1.125rem; display: flex; flex-direction: column; gap: 0.5rem; }
.sd p { margin: 0; }
.head { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; }
.sub { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); margin: 0; }
.small { font-size: 0.8rem; }
.wrap { overflow-x: auto; }
table { border-collapse: collapse; font-size: 0.84rem; min-width: 100%; }
th, td { text-align: left; padding: 0.3rem 0.6rem; border-bottom: 1px solid var(--border); vertical-align: top; }
td { min-width: 5.5rem; }   /* words aren't split: a narrow screen scrolls the table sideways */
thead th { font-size: 0.75rem; color: var(--text-dim); font-weight: 600; white-space: nowrap; }
tbody th { font-weight: 600; white-space: nowrap; }
tr.differs td, tr.differs th { background: color-mix(in srgb, var(--warning, #c90) 12%, transparent); }
.mark { margin-left: 0.35rem; color: var(--warning, #c90); font-weight: 700; }
</style>
