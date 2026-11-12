<script setup>
import PlaylistCover from "../components/playlists/PlaylistCover.vue";
// Playlists: your own (private or public) and the public ones of other users.
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { useRouter } from "vue-router";
import Icon from "../components/Icon.vue";
import PlaylistEditDialog from "../components/dialogs/PlaylistEditDialog.vue";
import { api } from "../lib/api.js";
import { onLibraryChange } from "../stores/live.js";

const router = useRouter();
const playlists = ref(null);
const error = ref("");
const creating = ref(false);

async function load() {
  try { playlists.value = (await api("/api/playlists")).playlists; error.value = ""; }
  catch (err) { error.value = err.message || "Could not load the playlists"; }
}
onMounted(load);
const stop = onLibraryChange((changed) => { if (!changed || changed.some((id) => id.startsWith("playlist:"))) load(); });
onBeforeUnmount(stop);

const mine = computed(() => (playlists.value || []).filter((p) => p.mine));
const others = computed(() => (playlists.value || []).filter((p) => !p.mine));
function created(p) { creating.value = false; router.push({ name: "playlist", params: { id: p.id } }); }
</script>

<template>
  <div class="page">
    <header class="page-head">
      <div>
        <h1>Playlists</h1>
        <p>Collect videos to play in a row. Add a video from its ⋮ menu, or everything in a library view with the <strong>Playlist</strong> button
          next to the filters — which can also keep the view as a <em>smart</em> playlist that fills itself.</p>
      </div>
      <button class="btn btn-primary btn-sm" @click="creating = true"><Icon name="plus" />New playlist</button>
    </header>

    <p v-if="error" class="error-text">{{ error }}</p>
    <div v-else-if="!playlists" class="grid"><div v-for="n in 4" :key="n" class="skeleton" style="aspect-ratio: 16 / 11; border-radius: var(--radius-lg)" /></div>
    <template v-else>
      <section>
        <h2 class="sec">Your playlists</h2>
        <div v-if="mine.length" class="grid">
          <RouterLink v-for="p in mine" :key="p.id" :to="{ name: 'playlist', params: { id: p.id } }" class="pl card">
            <PlaylistCover :playlist="p">
              <span class="count">{{ p.item_count }} {{ p.item_count === 1 ? "video" : "videos" }}</span>
            </PlaylistCover>
            <div class="info">
              <strong class="name"><Icon v-if="p.system === 'favorites'" name="heart" :size="14" class="fav-icon" />{{ p.name }}</strong>
              <span class="pills">
                <span v-if="p.kind === 'smart'" class="pill pill-accent" title="Filled by a filter">Smart</span>
                <span class="pill" :class="{ 'pill-ok': p.visibility === 'public' }">{{ p.visibility === "public" ? "Public" : "Private" }}</span>
              </span>
            </div>
          </RouterLink>
        </div>
        <div v-else class="card empty small-empty"><div class="empty-icon">🎶</div><p>No playlists yet. Make one with <strong>New playlist</strong>.</p></div>
      </section>

      <section v-if="others.length">
        <h2 class="sec">Public playlists from other users</h2>
        <div class="grid">
          <RouterLink v-for="p in others" :key="p.id" :to="{ name: 'playlist', params: { id: p.id } }" class="pl card">
            <PlaylistCover :playlist="p">
              <span class="count">{{ p.item_count }} {{ p.item_count === 1 ? "video" : "videos" }}</span>
            </PlaylistCover>
            <div class="info">
              <strong class="name">{{ p.name }}</strong>
              <span class="pills">
                <span v-if="p.kind === 'smart'" class="pill pill-accent">Smart</span>
                <span class="muted small">by {{ p.owner }}</span>
              </span>
            </div>
          </RouterLink>
        </div>
      </section>
    </template>

    <PlaylistEditDialog v-if="creating" @close="creating = false" @saved="created" />
  </div>
</template>

<style scoped>
.sec { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-dim); margin: 0.25rem 0 0.75rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr)); gap: 1rem; }
.pl { overflow: hidden; color: var(--text); text-decoration: none; transition: border-color 0.15s; }
.pl:hover { border-color: var(--accent); text-decoration: none; }
.cover { position: relative; aspect-ratio: 16 / 9; background: var(--thumb-bg); display: grid; overflow: hidden; }
.cover.n2, .cover.n3, .cover.n4 { grid-template-columns: 1fr 1fr; }
.cover.n3 img:first-child { grid-row: span 2; }
.cover.n4 { grid-template-rows: 1fr 1fr; }
.cover img { width: 100%; height: 100%; object-fit: cover; min-height: 0; }
.cover.n2 { grid-template-rows: 1fr; }
.empty-cover { display: grid; place-items: center; color: var(--text-dim); }
.count { position: absolute; right: 0.375rem; bottom: 0.375rem; padding: 0.1rem 0.45rem; border-radius: 0.25rem; background: rgba(0, 0, 0, 0.75); color: #fff; font-size: 0.72rem; font-weight: 700; }
.info { padding: 0.625rem 0.75rem; display: flex; flex-direction: column; gap: 0.3rem; }
.name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fav-icon { color: var(--accent); fill: currentColor; margin-right: 0.3rem; vertical-align: -2px; }
.pills { display: flex; gap: 0.375rem; align-items: center; }
.small { font-size: 0.76rem; }
.small-empty { padding: 1.5rem; }
</style>
