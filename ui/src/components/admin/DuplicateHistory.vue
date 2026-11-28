<script setup>
// Duplicates -> Merged / Not duplicates (3.12.0, backend/library_mixins/dup_manager.py).
// Merged: the copies removed as duplicates; one can be brought back (Get it back = keep both,
// Swap = keep it instead of the other), as Server Settings -> Duplicate scan says, or asked.
// Not duplicates: the groups marked so; Show again lets the next scan find them.
import { t } from "../../lib/i18n.js";
import { ref, onMounted, watch } from "vue";
import { api } from "../../lib/api.js";
import { formatDateTime } from "../../lib/format.js";
import { toast } from "../../stores/toast.js";
import { askConfirm } from "../../stores/confirm.js";

const props = defineProps({ view: String });
const merged = ref(null);
const notDup = ref(null);
const mode = ref("ask");
const busy = ref("");

async function load() {
  try {
    if (props.view === "merged") { const r = await api("/api/library/issues/merged"); merged.value = r.items; mode.value = r.switch_mode; }
    else { notDup.value = (await api("/api/library/issues/not-duplicates")).items; }
  } catch (err) { toast(err.message, { type: "error" }); }
}
onMounted(load);
watch(() => props.view, load);

const name = (m) => m.title || m.url || m.id;
async function bringBack(m, how) {
  const kept = m.kept ? `“${m.kept.title || m.kept.id}”` : "";
  const text = how === "swap"
    ? t("Bring back “{name}” and keep it instead of {kept}? {kept} is then merged into it: its playlists, favorites and watch history move over, and its files are deleted.", { name: name(m), kept })
    : t("Bring back “{name}” as a library video of its own, next to {kept}? The pair won't be shown as duplicates again.", { name: name(m), kept: kept || t("the other one") });
  if (!(await askConfirm(text + "\n" + t("Its file is used if it's still in the download folder; otherwise it's downloaded again (only if the site still has it)."),
    { ok: how === "swap" ? t("Swap") : t("Get it back") }))) return;
  busy.value = m.id;
  try {
    const r = await api(`/api/library/issues/merged/${encodeURIComponent(m.id)}/bring-back`, { method: "POST", body: { mode: how } });
    toast(r.how === "file" ? t("Brought back from its file") : t("Downloading it again — see Tasks"), { type: "success" });
    load();
  } catch (err) { toast(err.message, { type: "error", timeout: 9000 }); }
  finally { busy.value = ""; }
}
async function showAgain(g) {
  busy.value = g.item_ids.join();
  try { await api("/api/library/issues/not-duplicates/show-again", { method: "POST", body: { item_ids: g.item_ids } }); toast(t("The next scan can find them again"), { type: "success" }); load(); }
  catch (err) { toast(err.message, { type: "error" }); }
  finally { busy.value = ""; }
}
</script>

<template>
  <section v-if="view === 'merged'" class="hist">
    <p class="muted small">{{ $t("Copies removed as duplicates, newest first, with the video each one was merged into.") }}</p>
    <div v-if="!merged" class="skeleton" style="height: 6rem" />
    <div v-else-if="!merged.length" class="empty"><div class="empty-icon">✅</div>{{ $t("No merged duplicates.") }}</div>
    <ul v-else class="card list">
      <li v-for="m in merged" :key="m.id" class="row">
        <div class="main">
          <span class="title">{{ name(m) }}</span>
          <span class="muted small">
            <template v-if="m.kept">{{ $t("merged into") }} <RouterLink :to="`/video/${m.kept.id}`">{{ m.kept.title || m.kept.id }}</RouterLink></template>
            <template v-else>{{ $t("the video it was merged into isn't in the library any more") }}</template>
            <template v-if="m.merged_at"> · {{ formatDateTime(m.merged_at) }}</template>
          </span>
          <span v-if="m.swap_pending" class="pill pill-accent">{{ m.coming_back ? $t("coming back — the swap finishes after its download") : $t("swap waiting") }}</span>
        </div>
        <div v-if="!m.coming_back" class="acts">
          <button v-if="mode !== 'swap'" class="btn btn-sm" :disabled="busy === m.id" @click="bringBack(m, 'undo')">{{ $t("Get it back") }}</button>
          <button v-if="mode !== 'undo' && m.kept" class="btn btn-sm" :disabled="busy === m.id" @click="bringBack(m, 'swap')">{{ $t("Swap") }}</button>
        </div>
      </li>
    </ul>
  </section>
  <section v-else class="hist">
    <p class="muted small">{{ $t("Groups you marked as not duplicates. Show again lets the next scan find them.") }}</p>
    <div v-if="!notDup" class="skeleton" style="height: 6rem" />
    <div v-else-if="!notDup.length" class="empty"><div class="empty-icon">✅</div>{{ $t("Nothing marked as not duplicates.") }}</div>
    <ul v-else class="card list">
      <li v-for="g in notDup" :key="g.item_ids.join()" class="row">
        <div class="main">
          <span v-for="it in g.items" :key="it.id" class="small">
            <RouterLink v-if="it.status === 'done'" :to="`/video/${it.id}`">{{ it.title || it.id }}</RouterLink>
            <span v-else class="muted">{{ it.title || it.id }} ({{ it.status || $t("removed") }})</span>
          </span>
        </div>
        <div class="acts"><button class="btn btn-sm" :disabled="busy === g.item_ids.join()" @click="showAgain(g)">{{ $t("Show again") }}</button></div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.hist { display: flex; flex-direction: column; gap: 0.625rem; }
.list { list-style: none; margin: 0; padding: 0; }
.row { display: flex; align-items: center; gap: 0.75rem; padding: 0.625rem 0.875rem; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.row:last-child { border-bottom: 0; }
.main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 0.2rem; overflow-wrap: anywhere; }
.title { font-weight: 600; }
.small { font-size: 0.8rem; }
.acts { display: flex; gap: 0.375rem; }
.pill { align-self: flex-start; }
</style>
