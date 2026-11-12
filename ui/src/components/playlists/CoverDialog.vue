<script setup>
// Playlist -> Cover… (2.18.0): collage of the first 4 videos, one video's cover,
// or a picture from your computer (saved as WebP).
import { ref } from "vue";
import ModalDialog from "../ModalDialog.vue";
import PlaylistCover from "./PlaylistCover.vue";
import { api } from "../../lib/api.js";
import { toast } from "../../stores/toast.js";

const props = defineProps({ playlist: Object });
const emit = defineEmits(["close", "changed"]);
const busy = ref(false);
const picker = ref(null);
const thumb = (id) => `/api/library/${encodeURIComponent(id)}/thumbnail`;
const items = (props.playlist.items || []).slice(0, 60);

async function choose(mode, itemId = null) {
  busy.value = true;
  try {
    await api(`/api/playlists/${props.playlist.id}/cover`, { method: "PUT", body: { mode, item_id: itemId } });
    emit("changed");
    emit("close");
  } catch (err) { toast(err.message || "That didn't work", { type: "error" }); }
  finally { busy.value = false; }
}
async function upload(file) {
  if (!file) return;
  if (!file.type.startsWith("image/")) { toast("Pick a picture (JPEG, PNG, WebP…)", { type: "error" }); return; }
  busy.value = true;
  try {
    const r = await fetch(`/api/playlists/${props.playlist.id}/cover/image`, {
      method: "PUT", body: file, headers: { "Content-Type": file.type }, credentials: "same-origin",
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || `Upload failed (${r.status})`);
    toast("Cover saved", { type: "success" });
    emit("changed");
    emit("close");
  } catch (err) { toast(err.message || "Could not save the cover", { type: "error" }); }
  finally { busy.value = false; if (picker.value) picker.value.value = ""; }
}
</script>

<template>
  <ModalDialog title="Playlist cover" width="46rem" @close="emit('close')">
    <div class="opts">
      <button type="button" class="opt" :class="{ on: (playlist.cover?.mode || 'collage') === 'collage' }" :disabled="busy" @click="choose('collage')">
        <PlaylistCover :playlist="{ ...playlist, cover: { mode: 'collage' } }" />
        <span>Collage of the first 4 videos</span>
      </button>
      <label class="opt" :class="{ on: playlist.cover?.mode === 'image', disabled: busy }">
        <PlaylistCover v-if="playlist.cover?.mode === 'image'" :playlist="playlist" />
        <span v-else class="upload">+ Pick a picture</span>
        <span>{{ playlist.cover?.mode === "image" ? "Your picture — click to replace" : "Your own picture" }}</span>
        <input ref="picker" type="file" accept="image/*" hidden :disabled="busy" @change="upload($event.target.files[0])">
      </label>
    </div>
    <h4 class="sub">Or one video's cover</h4>
    <p v-if="!items.length" class="muted small">The playlist has no videos yet.</p>
    <div class="vids">
      <button v-for="v in items" :key="v.id" type="button" class="vid" :disabled="busy"
              :class="{ on: playlist.cover?.mode === 'video' && playlist.cover.item_id === v.id }" :title="v.title" @click="choose('video', v.id)">
        <img :src="thumb(v.id)" alt="" loading="lazy">
      </button>
    </div>
    <template #footer><button class="btn" @click="emit('close')">Close</button></template>
  </ModalDialog>
</template>

<style scoped>
.opts { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }
.opt { display: flex; flex-direction: column; gap: 0.4rem; padding: 0.4rem; border: 2px solid var(--border); border-radius: var(--radius);
  background: none; color: var(--text); cursor: pointer; text-align: left; font-size: 0.85rem; overflow: hidden; }
.opt.on, .vid.on { border-color: var(--accent); }
.opt.disabled { opacity: 0.6; pointer-events: none; }
.opt :deep(.cover) { border-radius: calc(var(--radius) - 2px); }
.upload { aspect-ratio: 16 / 9; display: grid; place-items: center; background: var(--panel-2); border-radius: calc(var(--radius) - 2px); color: var(--text-2); }
.sub { margin: 1rem 0 0.5rem; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); }
.vids { display: grid; grid-template-columns: repeat(auto-fill, minmax(8.5rem, 1fr)); gap: 0.5rem; max-height: 18rem; overflow-y: auto; }
.vid { padding: 0; border: 2px solid transparent; border-radius: var(--radius); overflow: hidden; background: var(--thumb-bg); cursor: pointer; }
.vid img { width: 100%; aspect-ratio: 16 / 9; object-fit: cover; display: block; }
.small { font-size: 0.8rem; }
@media (max-width: 32rem) { .opts { grid-template-columns: 1fr; } }
</style>
