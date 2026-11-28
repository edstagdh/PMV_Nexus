<script setup>
// Help: the Guide and Changelog (the project's own docs/GUIDE.md and
// CHANGELOG.md, served by /api/system/docs/<key>) and About (version,
// updates, links, server details, license). Open to every signed-in user.
import { t, locale } from "../lib/i18n.js";
import { ref, computed, watch, nextTick } from "vue";
import { useRoute, useRouter } from "vue-router";
import Icon from "../components/Icon.vue";
import { api } from "../lib/api.js";
import { renderMarkdown } from "../lib/markdown.js";
import { session } from "../stores/session.js";
import { toast } from "../stores/toast.js";

const route = useRoute();
const router = useRouter();
const TABS = [{ key: "guide", label: t("Guide") }, { key: "changelog", label: t("Changelog") }, { key: "terms", label: t("Terms of use") }, { key: "about", label: t("About") }];
const tab = computed(() => (TABS.some((tb) => tb.key === route.params.tab) ? route.params.tab : "guide"));

const docs = {};
const html = ref("");
const toc = ref([]);
const error = ref("");
const loading = ref(false);
const article = ref(null);

async function loadDoc(key) {
  if (!(key in docs)) {
    const resp = await fetch(`/api/system/docs/${key}`, { credentials: "same-origin" });
    if (!resp.ok) throw new Error(`Could not load the ${key} (${resp.status})`);
    docs[key] = await resp.text();
  }
  return docs[key];
}

// ---- About ----
const info = ref(null);
const license = ref("");
const checking = ref(false);
async function loadAbout() {
  info.value = await api("/api/system/info");
  try { license.value = await loadDoc("license"); } catch { license.value = ""; }
}
async function checkUpdate() {
  checking.value = true;
  try {
    const update = await api("/api/system/check-update", { method: "POST" });
    info.value = { ...info.value, update };
    toast(update.error ? t("Check failed: {error}", { error: update.error }) : update.update_available ? t("Version {version} is available", { version: update.latest }) : t("You're up to date"),
      { type: update.error ? "error" : "success" });
  } catch (err) {
    toast(err.message || t("Could not check for updates"), { type: "error" });
  } finally {
    checking.value = false;
  }
}
const updateLine = computed(() => {
  const u = info.value?.update;
  if (!u) return t("Not checked yet.");
  if (u.error) return t("Last check failed: {error}", { error: u.error });
  if (u.update_available) return u.newer > 1 ? t("Version {version} is available ({n} releases newer than yours).", { version: u.latest, n: u.newer })
    : t("Version {version} is available.", { version: u.latest });
  if (u.checked_at) return t("Up to date (checked {time}).", { time: new Date(u.checked_at * 1000).toLocaleString(locale()) });
  return t("Not checked yet.");
});
const issueUrl = computed(() => {
  const i = info.value || {};
  // The issue goes to the project on GitHub, so it's written in English whatever the app's language.
  const body = [
    "### What happened?", "", "### Steps to reproduce", "1. ", "2. ", "",
    "### What did you expect to happen?", "",
    "### Logs / screenshots", "<!-- Relevant lines from Admin → Logs or data/logs, or screenshots. Remove anything private. -->", "",
    "### Environment",
    `- App version: ${i.version || "unknown"}`, `- OS: ${i.platform || "unknown"}`, `- Python: ${i.python || "unknown"}`,  // i18n-ignore
    `- FFmpeg: ${i.ffmpeg || "not found"}`, `- Browser: ${navigator.userAgent}`, `- Role: ${session.user?.role || "unknown"}`,  // i18n-ignore
  ].join("\n");
  const base = i.new_issue_url || "https://github.com/edstagdh/PMV_Nexus/issues/new";
  return `${base}?${new URLSearchParams({ labels: "bug", title: "[Bug] ", body })}`;
});

async function load() {
  error.value = "";
  loading.value = true;
  try {
    if (tab.value === "about") {
      await loadAbout();
    } else {
      html.value = renderMarkdown(await loadDoc(tab.value));
      await nextTick();
      // Contents list from the article's own section headings.
      const sel = tab.value === "changelog" ? "h2" : "h2, h3";
      toc.value = [...(article.value?.querySelectorAll(sel) || [])].map((h) => ({ id: h.id, text: h.textContent, level: h.tagName }));
      if (route.hash) document.getElementById(route.hash.slice(1))?.scrollIntoView();
    }
  } catch (err) {
    error.value = err.message || t("Could not load this page");
  } finally {
    loading.value = false;
  }
}
watch(tab, load, { immediate: true });

function jump(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  history.replaceState(history.state, "", `#${id}`);
}
// In-article links to other help pages ("#…" anchors and /help/… links) stay in the app.
function onArticleClick(e) {
  const a = e.target.closest("a[href]");
  if (!a) return;
  const href = a.getAttribute("href");
  if (href.startsWith("#")) { e.preventDefault(); jump(href.slice(1)); }
  else if (/^\/help\/(guide|changelog|about)/.test(href)) { e.preventDefault(); router.push({ name: "help", params: { tab: href.split("/")[2] } }); }
}
</script>

<template>
  <div class="help">
    <header class="head">
      <h1>{{ $t("Help") }}</h1>
      <nav class="tabs" role="tablist">
        <router-link v-for="t in TABS" :key="t.key" :to="{ name: 'help', params: { tab: t.key } }" role="tab" class="tab"
                     :class="{ active: tab === t.key }" :aria-selected="tab === t.key">{{ t.label }}</router-link>
      </nav>
    </header>

    <div v-if="error" class="empty"><div class="empty-icon">⚠️</div>{{ error }}</div>

    <section v-else-if="tab === 'about'" class="about">
      <div v-if="!info" class="skeleton about-skel" />
      <template v-else>
        <div class="card hero">
          <div class="logo">▶</div>
          <div>
            <h2>PMV Nexus</h2>
            <p class="version">{{ $t("Version {version}", { version: info.version || "?" }) }}</p>
            <p class="muted">{{ $t("A self-hosted library manager for PMVs: download, organize, re-encode and de-duplicate videos from one web app. Works with PMVHaven and its sister sites. An independent project, not affiliated with any of them — see Terms of use.") }}</p>
          </div>
        </div>
        <dl class="card facts">
          <dt>{{ $t("Updates") }}</dt>
          <dd>
            {{ updateLine }}
            <a v-if="info.update?.update_available && (info.update.release_url || info.releases_url)" :href="info.update.release_url || info.releases_url" target="_blank" rel="noopener noreferrer">{{ $t("See what's new and download") }}</a>
            <button class="btn btn-sm" :disabled="checking" @click="checkUpdate">{{ checking ? $t("Checking…") : $t("Check now") }}</button>
          </dd>
          <dt>{{ $t("License") }}</dt><dd>{{ info.license || "MIT" }}</dd>
          <dt>{{ $t("Source code") }}</dt><dd><a :href="info.repo_url" target="_blank" rel="noopener noreferrer">{{ info.repo_url }}</a></dd>
          <dt>{{ $t("Report a bug") }}</dt><dd><a :href="issueUrl" target="_blank" rel="noopener noreferrer">{{ $t("Open a prefilled GitHub issue") }}</a></dd>
          <dt>{{ $t("Server") }}</dt><dd>{{ `Python ${info.python || "?"} · ${info.platform || "?"}` }}</dd>  <!-- i18n-ignore -->
          <dt>FFmpeg</dt><dd>{{ info.ffmpeg || $t("not found") }}</dd>
        </dl>
        <div class="card credits">
          <h3>{{ $t("Credits") }}</h3>
          <ul>
            <li>{{ $t("Duplicate detection follows the approach of") }} <a href="https://github.com/0x90d/videoduplicatefinder" target="_blank" rel="noopener noreferrer">{{ $t("VideoDuplicateFinder") }}</a> {{ $t("(re-implemented, no code copied).") }}</li>
            <li><Tx msg="Video playback by {link1}; media processing by {link2} and {link3}."><template #link1><a href="https://videojs.org" target="_blank" rel="noopener noreferrer">{{ $t("Video.js") }}</a></template><template #link2><a href="https://ffmpeg.org" target="_blank" rel="noopener noreferrer">FFmpeg</a></template><template #link3><a href="https://mediaarea.net/en/MediaInfo" target="_blank" rel="noopener noreferrer">{{ $t("MediaInfo") }}</a></template></Tx></li>
            <li>{{ $t("Built with FastAPI, SQLite, OpenCV/NumPy, Vue and Vite.") }}</li>
          </ul>
          <details>
            <summary>{{ $t("License text") }}</summary>
            <pre>{{ license || $t("LICENSE file not found.") }}</pre>
          </details>
        </div>
      </template>
    </section>

    <div v-else class="doc-layout">
      <nav v-if="toc.length > 2" class="toc" :aria-label="$t('Contents')">
        <div class="toc-title">{{ $t("Contents") }}</div>
        <a v-for="h in toc" :key="h.id" :href="`#${h.id}`" :class="{ sub: h.level === 'H3' }" @click.prevent="jump(h.id)">{{ h.text }}</a>
      </nav>
      <div v-if="loading && !html" class="skeleton doc-skel" />
      <!-- renderMarkdown escapes all text and only allows safe link targets. -->
      <article v-else ref="article" class="card markdown" @click="onArticleClick" v-html="html" />
    </div>
  </div>
</template>

<style scoped>
.help { padding: 1rem 1.5rem 2rem; display: flex; flex-direction: column; gap: 1rem; }
.head { display: flex; flex-direction: column; gap: 0.5rem; }
.head h1 { font-size: 1.4rem; }
.tabs { display: flex; gap: 0.25rem; }
.tab { padding: 0.625rem 0.875rem; color: var(--text-2); font-weight: 600; font-size: 0.875rem; border-bottom: 2px solid transparent; }
.tab:hover { color: var(--text); text-decoration: none; }
.tab.active { color: var(--accent); border-bottom-color: var(--accent); }
.doc-layout { display: grid; grid-template-columns: minmax(0, 1fr); gap: 1.5rem; align-items: start; }
.doc-layout:has(> .toc) { grid-template-columns: 15rem minmax(0, 1fr); }
.toc { position: sticky; top: 0.75rem; display: flex; flex-direction: column; gap: 0.125rem; max-height: calc(100vh - 2rem); overflow-y: auto; font-size: 0.8rem; }
.toc-title { font-size: 0.7rem; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-dim); margin-bottom: 0.375rem; }
.toc a { color: var(--text-2); padding: 0.2rem 0.5rem; border-radius: var(--radius-sm); }
.toc a:hover { background: var(--panel-2); color: var(--text); text-decoration: none; }
.toc a.sub { padding-inline-start: 1.25rem; font-size: 0.76rem; color: var(--text-dim); }
.markdown { padding: 1.25rem 1.75rem; line-height: 1.6; font-size: 0.9rem; overflow-wrap: anywhere; }
.markdown :deep(h1) { font-size: 1.5rem; margin: 0 0 0.75rem; }
.markdown :deep(h2) { font-size: 1.15rem; margin: 1.75rem 0 0.5rem; padding-top: 0.75rem; border-top: 1px solid var(--border); scroll-margin-top: 1rem; }
.markdown :deep(h3) { font-size: 1rem; margin: 1.25rem 0 0.375rem; scroll-margin-top: 1rem; }
.markdown :deep(p) { margin: 0.5rem 0; color: var(--text-2); }
.markdown :deep(img.doc-shot) { display: block; max-width: 100%; height: auto; margin: 0.75rem 0; border-radius: var(--radius); border: 1px solid var(--border); }
.markdown :deep(li) { color: var(--text-2); margin: 0.2rem 0; }
.markdown :deep(ul), .markdown :deep(ol) { padding-inline-start: 1.4rem; margin: 0.4rem 0; }
.markdown :deep(strong) { color: var(--text); }
.markdown :deep(code) { font-family: var(--mono); font-size: 0.82em; background: var(--panel-2); border: 1px solid var(--border); border-radius: 0.25rem; padding: 0.05rem 0.3rem; }
.markdown :deep(pre) { background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius); padding: 0.75rem; overflow-x: auto; }
.markdown :deep(pre code) { border: 0; background: none; padding: 0; }
.markdown :deep(blockquote) { margin: 0.75rem 0; padding: 0.25rem 1rem; border-inline-start: 3px solid var(--accent); background: var(--accent-soft); border-radius: 0 var(--radius-sm) var(--radius-sm) 0; }
.markdown :deep(hr) { border: 0; border-top: 1px solid var(--border); margin: 1.5rem 0; }
.markdown :deep(.md-table-wrap) { overflow-x: auto; margin: 0.75rem 0; }
.markdown :deep(table) { border-collapse: collapse; font-size: 0.84rem; }
.markdown :deep(th), .markdown :deep(td) { border: 1px solid var(--border); padding: 0.375rem 0.625rem; text-align: start; vertical-align: top; }
.markdown :deep(th) { background: var(--panel-2); }
.about { display: flex; flex-direction: column; gap: 1rem; }
.hero { display: flex; gap: 1.25rem; align-items: center; padding: 1.25rem; }
.logo { width: 3.5rem; height: 3.5rem; flex: none; border-radius: 0.875rem; background: var(--accent); color: #fff; display: grid; place-items: center; font-size: 1.4rem; }
.hero h2 { font-size: 1.25rem; }
.hero p { margin: 0.25rem 0 0; }
.version { font-weight: 700; color: var(--accent); }
.facts { display: grid; grid-template-columns: 9rem 1fr; gap: 0.625rem 1rem; padding: 1rem 1.25rem; margin: 0; font-size: 0.875rem; }
.facts dt { color: var(--text-dim); }
.facts dd { margin: 0; display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; overflow-wrap: anywhere; }
.credits { padding: 1rem 1.25rem; font-size: 0.875rem; }
.credits h3 { font-size: 0.95rem; }
.credits ul { padding-inline-start: 1.2rem; color: var(--text-2); }
.credits pre { white-space: pre-wrap; font: 0.78rem/1.5 var(--mono); background: var(--bg); padding: 0.75rem; border-radius: var(--radius); border: 1px solid var(--border); }
.credits summary { cursor: pointer; font-weight: 600; }
.about-skel { height: 20rem; border-radius: var(--radius-lg); }
.doc-skel { height: 30rem; border-radius: var(--radius-lg); }
@media (max-width: 64rem) {
  .doc-layout, .doc-layout:has(> .toc) { grid-template-columns: minmax(0, 1fr); }
  .toc { display: none; }
}
@media (max-width: 56rem) { .help { padding: 0.75rem 1rem 1.5rem; } .facts { grid-template-columns: 1fr; gap: 0.125rem 0; } .facts dd { margin-bottom: 0.5rem; } }
</style>
