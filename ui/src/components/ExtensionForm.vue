<script setup>
// Draws the forms an extension's action or panel returns and sends the answers
// back, step by step, until it says it's done (2.30.0; backend/extensions.py has
// the form format). Used by the extension dialog and Server Settings → Extensions.
import { ref, onMounted } from "vue";
import { api } from "../lib/api.js";

const props = defineProps({
  endpoint: { type: String, required: true },   // /api/extensions/actions/<id> or /panels/<id>
  itemIds: { type: Array, default: () => [] },
  embedded: { type: Boolean, default: false },  // in a page (no own title)
  heading: { type: String, default: "" },       // embedded: the page's heading (a form title that repeats it isn't shown)
});
const emit = defineEmits(["done", "title"]);

const form = ref(null);
const values = ref({});
const message = ref(null);   // { text, level }
const busy = ref(false);
const error = ref("");

function show(result) {
  message.value = result.message ? { text: result.message, level: result.level || "info" } : null;
  if (result.form) {
    form.value = result.form;
    const v = {};
    for (const f of result.form.fields || []) {
      if (!f.name) continue;
      v[f.name] = f.type === "checkboxes" ? [...(f.value || [])] : f.type === "checkbox" ? !!f.value : (f.value ?? "");
    }
    values.value = v;
    emit("title", result.form.title || "");
  } else {
    form.value = null;
  }
  if (result.done) emit("done", message.value);
}

async function send(step, vals) {
  busy.value = true;
  error.value = "";
  try {
    show(await api(props.endpoint, { method: "POST", body: { item_ids: props.itemIds, step, values: vals } }));
  } catch (err) {
    error.value = err.message || "That didn't work";
  } finally {
    busy.value = false;
  }
}

const submit = () => send(form.value?.step ?? null, values.value);
function toggle(name, value, on) {
  const list = new Set(values.value[name] || []);
  on ? list.add(value) : list.delete(value);
  values.value[name] = [...list];
}
onMounted(() => send(null, {}));
defineExpose({ submit, busy, form });
</script>

<template>
  <div class="xf">
    <p v-if="message" class="xf-msg" :class="message.level">{{ message.text }}</p>
    <div v-if="!form && busy" class="muted small">Loading…</div>
    <form v-if="form" :id="'xf-' + endpoint" class="xf-form" @submit.prevent="submit">
      <h3 v-if="embedded && form.title && form.title !== heading" class="xf-title">{{ form.title }}</h3>
      <p v-if="form.description" class="help-text xf-desc">{{ form.description }}</p>
      <template v-for="(f, i) in form.fields || []" :key="f.name || i">
        <p v-if="f.type === 'info'" class="xf-info">{{ f.label }}<template v-if="f.value"> {{ f.value }}</template></p>
        <p v-else-if="f.type === 'link'" class="xf-info">{{ f.label }} <a :href="f.value" target="_blank" rel="noopener noreferrer">{{ f.text || f.value }}</a></p>
        <label v-else-if="f.type === 'checkbox'" class="switch"><input v-model="values[f.name]" type="checkbox">{{ f.label }}</label>
        <fieldset v-else-if="f.type === 'checkboxes' || f.type === 'radio'" class="xf-set">
          <legend>{{ f.label }}</legend>
          <label v-for="o in f.options || []" :key="o.value" class="switch">
            <input v-if="f.type === 'radio'" v-model="values[f.name]" type="radio" :value="o.value" :disabled="o.disabled">
            <input v-else type="checkbox" :checked="(values[f.name] || []).includes(o.value)" :disabled="o.disabled"
                   @change="toggle(f.name, o.value, $event.target.checked)">
            {{ o.label }}
          </label>
          <small v-if="f.help" class="muted">{{ f.help }}</small>
        </fieldset>
        <label v-else class="field">
          <span>{{ f.label }}</span>
          <select v-if="f.type === 'select'" v-model="values[f.name]" class="select" :required="f.required">
            <option v-for="o in f.options || []" :key="o.value" :value="o.value" :disabled="o.disabled">{{ o.label }}</option>
          </select>
          <textarea v-else-if="f.type === 'textarea'" v-model="values[f.name]" class="input" rows="4" :placeholder="f.placeholder" :required="f.required" />
          <input v-else v-model="values[f.name]" class="input" :type="f.type === 'password' ? 'password' : f.type === 'number' ? 'number' : 'text'"
                 :placeholder="f.placeholder" :required="f.required" :autocomplete="f.type === 'password' ? 'off' : undefined">
          <small v-if="f.help" class="muted">{{ f.help }}</small>
        </label>
      </template>
      <p v-if="error" class="xf-msg error">{{ error }}</p>
      <div v-if="embedded" class="form-actions">
        <button class="btn btn-primary btn-sm" :disabled="busy">{{ busy ? "Please wait…" : form.submit || "Continue" }}</button>
      </div>
    </form>
    <p v-else-if="error" class="xf-msg error">{{ error }}</p>
  </div>
</template>

<style scoped>
.xf-form { display: flex; flex-direction: column; gap: 0.75rem; }
.xf-title { margin: 0; font-size: 1rem; }
.xf-desc { margin: 0; white-space: pre-line; }
.xf-info { margin: 0; font-size: 0.875rem; white-space: pre-line; overflow-wrap: anywhere; }
.xf-set { border: 0; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.4rem; }
.xf-set legend { font-size: 0.85rem; font-weight: 600; margin-bottom: 0.3rem; }
.xf-msg { margin: 0 0 0.75rem; font-size: 0.85rem; padding: 0.5rem 0.7rem; border-radius: var(--radius); border: 1px solid var(--border); white-space: pre-line; }
.xf-msg.success { color: var(--success); border-color: color-mix(in srgb, var(--success) 45%, transparent); }
.xf-msg.error { color: var(--danger); border-color: color-mix(in srgb, var(--danger) 45%, transparent); }
.field small { display: block; margin-top: 0.25rem; }
</style>
