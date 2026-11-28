<script setup>
// Management -> Maintenance -> Check video files (3.11.0, backend/library_mixins/file_check.py):
// every downloaded video's file -- there, not empty, readable (Thorough: every packet read and a
// few seconds decoded). A missing one is looked for in the download folder; found ones can be
// linked back, the rest downloaded again. Nothing is deleted or moved.
import { t, tn } from "../../lib/i18n.js";
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import Icon from "../Icon.vue";
import { api } from "../../lib/api.js";
import { formatDateTime } from "../../lib/format.js";
import { toast } from "../../stores/toast.js";
import { hasPermission } from "../../stores/session.js";

const info = ref(null);
const thorough = ref(false);
const busy = ref(false);
const working = ref(new Set());   // video ids with a request on its way

async function load() { try { info.value = await api("/api/maintenance/file-check"); } catch { /* shown as "…" */ } }
async function run() {
  busy.value = true;
  try {
    await api("/api/maintenance/file-check", { method: "POST", body: { thorough: thorough.value } });
    toast(t("Checking the video files — follow it on Tasks"), { type: "success" });
    load();
  } catch (err) { toast(err.message || t("Couldn't start it"), { type: "error" }); }
  finally { busy.value = false; }
}
onMounted(load);
const timer = setInterval(() => { if (info.value?.running || info.value?.problems?.some((p) => p.downloading)) load(); }, 4000);
onBeforeUnmount(() => clearInterval(timer));

const LABEL = {
  missing: () => t("missing"),
  empty: () => t("empty (0 bytes)"),
  unreadable: () => t("unreadable"),
  damaged: () => t("damaged"),
};
const HOW = {
  reencode_backup: () => t("the copy a re-encode kept (…_old)"),
  same_name: () => t("same file name, another folder"),
  same_id: () => t("its video id is in the name"),
};
const problems = computed(() => info.value?.problems || []);
const linkable = computed(() => problems.value.filter((p) => p.found && !p.downloading));
const downloadable = computed(() => problems.value.filter((p) => canDownload(p) && !p.found));

function canDownload(p) {
  return !p.downloading && p.site !== "local" && p.site_status !== "removed" && hasPermission("videos.add");
}

async function each(list, fn, okText) {
  let ok = 0;
  const errors = [];
  for (const p of list) {
    working.value = new Set([...working.value, p.id]);
    try { await fn(p); ok++; } catch (err) { errors.push(`${p.title}: ${err.message}`); }
    const w = new Set(working.value); w.delete(p.id); working.value = w;
  }
  if (ok) toast(okText(ok), { type: "success" });
  if (errors.length) toast(errors.slice(0, 3).join(" · "), { type: "error", timeout: 9000 });
  load();
}
const link = (list) => each(list, (p) => api(`/api/library/${encodeURIComponent(p.id)}/link-file`, { method: "POST", body: { path: p.found } }),
  (n) => tn("Linked {n} video to its file", "Linked {n} videos to their files", n));
const download = (list, replace = false) => each(list, (p) => api(`/api/library/${encodeURIComponent(p.id)}/redownload`, { method: "POST", body: { replace_damaged: replace } }),
  (n) => tn("Downloading {n} video again — see Tasks", "Downloading {n} videos again — see Tasks", n));
</script>

<template>
  <section id="file-check" class="card block">
    <div class="block-head">
      <div>
        <h2>{{ $t("Check video files") }}</h2>
        <p class="muted small">{{ $t("Goes through every downloaded video and checks that its file is there, isn't empty and can be read. Thorough also reads the whole file and decodes a few seconds of it, which finds cut-off and damaged files but takes about as long as reading the whole library. For a missing file it looks in the download folder for it (moved, renamed, or the copy a re-encode keeps), so you can link it back; the rest can be downloaded again. Nothing is deleted or moved.") }}</p>
        <p v-if="info?.checked_at" class="small">
          {{ $t("Last checked {time}{how}: {n} videos", { time: formatDateTime(info.checked_at), how: info.thorough ? $t(" (thorough)") : "", n: info.checked }) }}
          <span v-if="info.stopped" class="muted"> · {{ $t("stopped before the end") }}</span>
          · <strong>{{ problems.length ? $tn("{n} with a problem", "{n} with a problem", problems.length) : $t("✅ all fine") }}</strong>
        </p>
        <p v-else-if="info" class="muted small">{{ $t("Not checked yet.") }}</p>
      </div>
      <div class="bar">
        <label class="small"><input v-model="thorough" type="checkbox"> {{ $t("Thorough") }}</label>
        <button class="btn btn-primary btn-sm" :disabled="busy || !info || info.running" @click="run">
          <Icon name="search" />{{ info?.running ? $t("Checking…") : $t("Check now") }}
        </button>
      </div>
    </div>

    <template v-if="problems.length">
      <div class="bar">
        <button v-if="linkable.length" class="btn btn-sm" :disabled="working.size > 0" @click="link(linkable)">
          <Icon name="check" />{{ $t("Link the {n} found", { n: linkable.length }) }}</button>
        <button v-if="downloadable.length" class="btn btn-sm" :disabled="working.size > 0"
                @click="download(downloadable.filter((p) => p.problem === 'missing' || p.problem === 'empty'))">
          <Icon name="download" />{{ $t("Download the missing ones again ({n})", { n: downloadable.filter((p) => p.problem === 'missing' || p.problem === 'empty').length }) }}</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>{{ $t("Video") }}</th><th>{{ $t("Problem") }}</th><th>{{ $t("File") }}</th><th /></tr></thead>
          <tbody>
            <tr v-for="p in problems" :key="p.id">
              <td><RouterLink :to="`/video/${p.id}`">{{ p.title || p.id }}</RouterLink>
                <div v-if="p.site_status === 'removed'" class="muted tiny">{{ $t("not on the site any more") }}</div></td>
              <td><span class="kind" :class="p.problem">{{ LABEL[p.problem]?.() || p.problem }}</span>
                <div v-if="p.detail" class="muted tiny">{{ p.detail }}</div></td>
              <td class="path">
                <div :title="p.path">{{ p.path }}</div>
                <div v-if="p.found" class="found" :title="p.found">→ {{ p.found }}
                  <span class="muted">({{ HOW[p.found_how]?.() }}<template v-if="p.found_more">{{ $t(", {n} more", { n: p.found_more }) }}</template>)</span></div>
              </td>
              <td class="acts">
                <span v-if="p.downloading" class="pill pill-accent">{{ $t("downloading") }}</span>
                <template v-else>
                  <button v-if="p.found" class="btn btn-sm" :disabled="working.has(p.id)" @click="link([p])">{{ $t("Link this file") }}</button>
                  <button v-if="canDownload(p)" class="btn btn-ghost btn-sm" :disabled="working.has(p.id)"
                          @click="download([p], p.problem === 'unreadable' || p.problem === 'damaged')">{{ $t("Download again") }}</button>
                </template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>

<style scoped>
.block { padding: 1rem 1.125rem; display: flex; flex-direction: column; gap: 0.75rem; }
.block-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; }
.block-head h2 { font-size: 1.05rem; }
.block-head p { margin: 0.25rem 0 0; max-width: 50rem; }
.bar { display: flex; align-items: center; gap: 0.625rem; flex-wrap: wrap; }
.small { font-size: 0.8rem; }
.tiny { font-size: 0.72rem; }
.table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: var(--radius); }
table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
th { text-align: start; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-dim); padding: 0.5rem 0.625rem; border-bottom: 1px solid var(--border); background: var(--panel-2); }
td { padding: 0.45rem 0.625rem; border-bottom: 1px solid var(--border); vertical-align: top; }
.path { font-family: var(--mono); font-size: 0.72rem; overflow-wrap: anywhere; }
.found { color: var(--success); margin-top: 0.2rem; }
.acts { white-space: nowrap; text-align: end; }
.acts .btn + .btn { margin-inline-start: 0.375rem; }
.kind { font-size: 0.7rem; font-weight: 700; padding: 0.1rem 0.4rem; border-radius: 0.25rem; white-space: nowrap;
        background: color-mix(in srgb, var(--danger) 15%, transparent); color: var(--danger); }
.kind.unreadable, .kind.damaged { background: color-mix(in srgb, var(--warning) 15%, transparent); color: var(--warning); }
@media (max-width: 56rem) { .block-head { flex-direction: column; } .acts { white-space: normal; } }
</style>
