<script setup>
// Performer profile -> Your photos (2.16.0): pictures added by hand next to
// ThePornDB's. Each is converted to WebP on the server; one can be shown first
// (her main photo, also on similar-performer cards). They survive refreshes from
// ThePornDB and are in every backup.
import { askConfirm } from "../stores/confirm.js";
import { ref } from "vue";
import Icon from "./Icon.vue";
import { api } from "../lib/api.js";
import { toast } from "../stores/toast.js";

const props = defineProps({ performer: Object });
const emit = defineEmits(["changed"]);
const busy = ref("");
const over = ref(false);
const picker = ref(null);
const kb = (n) => (n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.round(n / 1024)} KB`);

async function upload(files) {
  // One photo of your own per performer (2.18.0): a new one replaces it.
  const list = [...files].filter((f) => f.type.startsWith("image/")).slice(0, 1);
  if (!list.length) { toast("Pick a picture (JPEG, PNG, WebP…)", { type: "error" }); return; }
  if (props.performer.custom_photos.length && !(await askConfirm("Replace your photo of her with this one?", { ok: "Replace" }))) {
    if (picker.value) picker.value.value = "";
    return;
  }
  for (const [n, file] of list.entries()) {
    busy.value = list.length > 1 ? `Adding ${n + 1} of ${list.length}…` : "Adding…";
    try {
      const r = await fetch(`/api/performers/${props.performer.id}/photos/upload`, {
        method: "PUT", body: file, headers: { "Content-Type": file.type }, credentials: "same-origin",
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || `Upload failed (${r.status})`);
      toast(`Photo saved (${kb(data.original_size)} → ${kb(data.size)} as WebP)`, { type: "success" });
    } catch (err) { toast(`${file.name}: ${err.message || "could not add it"}`, { type: "error" }); }
  }
  busy.value = "";
  if (picker.value) picker.value.value = "";
  emit("changed");
}
async function setMain(photo) {
  busy.value = photo.id;
  try {
    await api(`/api/performers/${props.performer.id}/photos/main`, { method: "POST", body: { photo_id: photo.main ? null : photo.id } });
    emit("changed");
  } catch (err) { toast(err.message || "That didn't work", { type: "error" }); }
  finally { busy.value = ""; }
}
async function remove(photo) {
  if (!(await askConfirm("Remove this photo? It's deleted from the server.", { ok: "Remove", danger: true }))) return;
  busy.value = photo.id;
  try { await api(`/api/performers/${props.performer.id}/photos/${photo.id}`, { method: "DELETE" }); emit("changed"); }
  catch (err) { toast(err.message || "Could not remove it", { type: "error" }); }
  finally { busy.value = ""; }
}
function drop(e) { over.value = false; upload(e.dataTransfer?.files || []); }
</script>

<template>
  <section class="card panel photos" :class="{ over }" @dragover.prevent="over = true" @dragleave="over = false" @drop.prevent="drop">
    <div class="head">
      <div>
        <h2>Your photo</h2>
        <p class="muted small">Add your own picture of her next to ThePornDB's (up to 10) — drop it here or pick it. It's saved as WebP,
          kept when her profile refreshes, and included in every backup. One per performer: a new one replaces it.</p>
      </div>
      <label class="btn btn-sm btn-primary" :class="{ disabled: busy }">
        <Icon name="plus" :size="14" />{{ busy && !performer.custom_photos.some((c) => c.id === busy) ? busy
          : performer.custom_photos.length ? "Replace photo" : "Add photo" }}
        <input ref="picker" type="file" accept="image/*" hidden :disabled="!!busy" @change="upload($event.target.files)">
      </label>
    </div>
    <ul v-if="performer.custom_photos.length" class="grid">
      <li v-for="c in performer.custom_photos" :key="c.id" :class="{ main: c.main }">
        <img :src="c.url" alt="" loading="lazy">
        <span v-if="c.main" class="badge">Shown first</span>
        <div class="acts">
          <button class="btn btn-sm" :disabled="!!busy" :title="c.main ? 'Show ThePornDB\'s main photo first again' : 'Make this her main photo'"
                  @click="setMain(c)">{{ c.main ? "Unset first" : "Show first" }}</button>
          <button class="btn btn-sm btn-ghost" :disabled="!!busy" aria-label="Remove photo" title="Remove" @click="remove(c)"><Icon name="trash" :size="14" /></button>
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.photos { padding: 1rem 1.125rem; display: flex; flex-direction: column; gap: 0.75rem; transition: outline-color 0.15s; outline: 2px dashed transparent; outline-offset: -6px; }
.photos.over { outline-color: var(--accent); }
.head { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; flex-wrap: wrap; }
.head h2 { font-size: 1.05rem; margin: 0; }
.head p { margin: 0.25rem 0 0; max-width: 44rem; }
.small { font-size: 0.8rem; }
.disabled { opacity: 0.6; pointer-events: none; }
.grid { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(8.5rem, 1fr)); gap: 0.75rem; }
.grid li { position: relative; display: flex; flex-direction: column; gap: 0.35rem; }
.grid img { width: 100%; aspect-ratio: 2 / 3; object-fit: cover; border-radius: var(--radius); background: var(--panel-2); display: block; }
.grid li.main img { outline: 2px solid var(--accent); outline-offset: 2px; }
.badge { position: absolute; top: 0.35rem; left: 0.35rem; font-size: 0.7rem; font-weight: 700; background: var(--accent); color: #fff; padding: 0.1rem 0.4rem; border-radius: 999px; }
.acts { display: flex; gap: 0.3rem; }
.acts .btn:first-child { flex: 1; }
</style>
