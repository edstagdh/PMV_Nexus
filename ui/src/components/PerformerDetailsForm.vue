<script setup>
// 3.9.0: details typed in by hand for a performer not on ThePornDB -- often amateurs: the
// usual facts, other names, a bio and links to their pages (OnlyFans, Instagram, a site...).
// Saved with POST /api/performers/local; the server checks every field (clean_local_details).
import { t } from "../lib/i18n.js";
import { ref } from "vue";
import Icon from "./Icon.vue";
import { api } from "../lib/api.js";
import { toast } from "../stores/toast.js";

const props = defineProps({ name: String, details: Object });
const emit = defineEmits(["saved", "cancel"]);
const d = props.details || {};
const num = (v) => (v ? parseInt(String(v), 10) || "" : "");
const form = ref({
  aliases: (d.aliases || []).join(", "), birthday: d.birthday || "", birthplace: d.birthplace || "",
  nationality: d.nationality || "", ethnicity: d.ethnicity || "", hair_colour: d.hair_colour || "",
  eye_colour: d.eye_colour || "", height: num(d.height), weight: num(d.weight), measurements: d.measurements || "",
  cupsize: d.cupsize || "", fake_boobs: d.fake_boobs === true ? "yes" : d.fake_boobs === false ? "no" : "",
  tattoos: d.tattoos || "", piercings: d.piercings || "", career_start_year: d.career_start_year || "",
  career_end_year: d.career_end_year || "", bio: d.bio || "",
  links: (d.links || []).map((l) => ({ ...l })),
});
const busy = ref(false);
const error = ref("");
const TEXT = [
  ["birthplace", t("Birthplace")], ["nationality", t("Nationality")], ["ethnicity", t("Ethnicity")],
  ["hair_colour", t("Hair")], ["eye_colour", t("Eyes")], ["measurements", t("Measurements")], ["cupsize", t("Cup size")],
  ["tattoos", t("Tattoos")], ["piercings", t("Piercings")],
];
function addLink() { form.value.links.push({ label: "", url: "" }); }
async function save() {
  busy.value = true; error.value = "";
  const f = form.value;
  const details = { ...f, aliases: f.aliases.split(",").map((s) => s.trim()).filter(Boolean),
    links: f.links.filter((l) => l.url.trim()) };
  try {
    await api("/api/performers/local", { method: "POST", body: { name: props.name, details } });
    toast(t("Details saved"), { type: "success" });
    emit("saved");
  } catch (err) { error.value = err.message || t("That didn't work"); }
  finally { busy.value = false; }
}
</script>

<template>
  <section class="card panel details-form">
    <h3 class="sub">{{ $t("Details") }}</h3>
    <p class="muted small">{{ $t("For a performer who isn't on ThePornDB. Fill in what you know; empty fields aren't shown.") }}</p>
    <label class="field wide">{{ $t("Other names") }}<input v-model="form.aliases" class="input" :placeholder="$t('Separated by commas')"></label>
    <div class="grid">
      <label class="field">{{ $t("Born") }}<input v-model="form.birthday" class="input" type="date"></label>
      <label v-for="[k, label] in TEXT" :key="k" class="field">{{ label }}<input v-model="form[k]" class="input" maxlength="100"></label>
      <label class="field">{{ $t("Height (cm)") }}<input v-model="form.height" class="input" type="number" min="100" max="230"></label>
      <label class="field">{{ $t("Weight (kg)") }}<input v-model="form.weight" class="input" type="number" min="30" max="200"></label>
      <label class="field">{{ $t("Enhanced") }}
        <select v-model="form.fake_boobs" class="select"><option value="">{{ $t("Not known") }}</option><option value="yes">{{ $t("Yes") }}</option><option value="no">{{ $t("No") }}</option></select></label>
      <label class="field">{{ $t("Active from (year)") }}<input v-model="form.career_start_year" class="input" type="number" min="1950" max="2100"></label>
      <label class="field">{{ $t("Active until (year)") }}<input v-model="form.career_end_year" class="input" type="number" min="1950" max="2100"></label>
    </div>
    <label class="field wide">{{ $t("Bio") }}<textarea v-model="form.bio" class="input" rows="4" maxlength="5000" /></label>
    <div class="field wide">{{ $t("Links") }}
      <small class="muted">{{ $t("Their pages: OnlyFans, Fansly, Instagram, X, Reddit, a website… The name is optional.") }}</small>
      <div v-for="(l, i) in form.links" :key="i" class="link-row">
        <input v-model="l.label" class="input label" maxlength="40" :placeholder="$t('Name, e.g. Instagram')" :aria-label="$t('Link name')">
        <input v-model="l.url" class="input" type="url" placeholder="https://…" :aria-label="$t('Web address')">
        <button type="button" class="btn btn-ghost btn-sm" :aria-label="$t('Remove')" @click="form.links.splice(i, 1)"><Icon name="x" :size="14" /></button>
      </div>
      <button v-if="form.links.length < 20" type="button" class="btn btn-sm add" @click="addLink"><Icon name="plus" :size="14" />{{ $t("Add a link") }}</button>
    </div>
    <p v-if="error" class="error-text">{{ error }}</p>
    <div class="form-actions">
      <button class="btn btn-primary btn-sm" :disabled="busy" @click="save">{{ busy ? $t("Saving…") : $t("Save") }}</button>
      <button class="btn btn-ghost btn-sm" :disabled="busy" @click="emit('cancel')">{{ $t("Cancel") }}</button>
    </div>
  </section>
</template>

<style scoped>
.details-form { display: flex; flex-direction: column; gap: 0.75rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr)); gap: 0.6rem 0.9rem; }
.field { display: flex; flex-direction: column; gap: 0.3rem; font-size: 0.85rem; font-weight: 600; min-width: 0; }
.field .input, .field .select { font-weight: 400; }
.link-row { display: flex; gap: 0.4rem; align-items: center; font-weight: 400; }
.link-row .label { flex: 0 0 11rem; }
.link-row .input:not(.label) { flex: 1; min-width: 0; }
.add { align-self: flex-start; }
.small { font-size: 0.8rem; margin: 0; }
@media (max-width: 40rem) { .link-row { flex-wrap: wrap; } .link-row .label { flex-basis: 100%; } }
</style>
