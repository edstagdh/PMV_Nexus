<script setup>
// Where the panic keys lead (2.24.0; 2.24.4: an ordinary-looking article instead of a
// white page). One of lib/decoyPages.js, a different one each time; the tab title
// and icon are the article's; dark or light to match the app's theme. Nothing on
// it does anything (links are inert); the browser's Back button returns to the app.
import { computed, watch, onMounted, onBeforeUnmount } from "vue";
import { useRoute } from "vue-router";
import { DECOY_PAGES, pickDecoy } from "../lib/decoyPages.js";

const route = useRoute();
const page = computed(() => DECOY_PAGES.find((p) => p.slug === route.params.slug) || pickDecoy());

// The app's theme is dark or light? (Works for custom themes too: the page background's brightness.)
function isDark() {
  const m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(getComputedStyle(document.body).backgroundColor || "");
  if (!m) return true;
  const [r, g, b] = m.slice(1).map(Number);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b < 128;
}
const dark = isDark();

const icon = computed(() => {
  const letter = page.value.site[0];
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="${page.value.accent}"/>`
    + `<text x="16" y="22" font-family="Georgia,serif" font-size="18" font-weight="700" fill="#fff" text-anchor="middle">${letter}</text></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
});
let icons = [];
function show() {
  icons.forEach(([l]) => { l.href = icon.value; });
  document.title = `${page.value.title} | ${page.value.site}`;
}
onMounted(() => {
  icons = [...document.querySelectorAll("link[rel~='icon']")].map((l) => [l, l.href]);
  show();
});
watch(page, show); // panic keys pressed again on this page: another article
onBeforeUnmount(() => icons.forEach(([l, href]) => { l.href = href; }));
const inert = (e) => e.preventDefault();
</script>

<template>
  <div class="decoy" :class="dark ? 'dark' : 'light'" :style="{ '--accent-d': page.accent }" @click="($event.target.closest('a, button')) && inert($event)">
    <header class="d-top">
      <div class="d-wrap d-bar">
        <a href="#" class="d-brand">{{ page.site }}</a>
        <nav class="d-nav"><a v-for="n in page.nav" :key="n" href="#">{{ n }}</a></nav>
        <button type="button" class="d-sub">Subscribe</button>
      </div>
    </header>

    <div class="d-wrap d-ad-wide">
      <a href="#" class="d-ad wide" :style="{ '--ad': page.ads[0].color }">
        <span class="d-ad-tag">Ad</span><span class="d-ad-icon">{{ page.ads[0].icon }}</span>
        <span class="d-ad-text"><strong>{{ page.ads[0].brand }}</strong><span>{{ page.ads[0].line }}</span></span>
        <span class="d-ad-cta">{{ page.ads[0].cta }}</span>
      </a>
    </div>

    <main class="d-wrap d-main">
      <article class="d-article">
        <p class="d-section">{{ page.section }}</p>
        <h1>{{ page.title }}</h1>
        <p class="d-meta">By <strong>{{ page.author }}</strong> · {{ page.date }} · {{ page.read }}</p>
        <div class="d-hero" aria-hidden="true"><span>{{ page.hero }}</span></div>
        <template v-for="(b, i) in page.body" :key="i">
          <h2 v-if="b[0] === 'h2'">{{ b[1] }}</h2>
          <p v-else>{{ b[1] }}</p>
          <a v-if="i === 4" href="#" class="d-ad inline" :style="{ '--ad': page.ads[2].color }">
            <span class="d-ad-tag">Ad</span><span class="d-ad-icon">{{ page.ads[2].icon }}</span>
            <span class="d-ad-text"><strong>{{ page.ads[2].brand }}</strong><span>{{ page.ads[2].line }}</span></span>
            <span class="d-ad-cta">{{ page.ads[2].cta }}</span>
          </a>
        </template>
        <div class="d-share"><span>Share</span><button type="button">Copy link</button><button type="button">Email</button></div>
      </article>

      <aside class="d-side">
        <a v-for="ad in page.ads.slice(1)" :key="ad.brand" href="#" class="d-ad tall" :style="{ '--ad': ad.color }">
          <span class="d-ad-tag">Ad</span>
          <span class="d-ad-icon big">{{ ad.icon }}</span>
          <strong>{{ ad.brand }}</strong><span>{{ ad.line }}</span>
          <span class="d-ad-cta">{{ ad.cta }}</span>
        </a>
        <div class="d-box">
          <strong>Most read</strong>
          <a v-for="p in DECOY_PAGES.filter((x) => x.slug !== page.slug).slice(0, 3)" :key="p.slug" href="#">{{ p.title }}</a>
        </div>
      </aside>
    </main>

    <footer class="d-foot"><div class="d-wrap">© {{ new Date().getFullYear() }} {{ page.site }} · Privacy · Terms · Contact</div></footer>
  </div>
</template>

<style scoped>
.decoy { --bg-d: #fafaf7; --card-d: #ffffff; --text-d: #1f2328; --muted-d: #6b7280; --line-d: #e5e7eb;
  position: fixed; inset: 0; overflow-y: auto; background: var(--bg-d); color: var(--text-d);
  font: 400 1rem/1.7 Georgia, "Times New Roman", serif; }
.decoy.dark { --bg-d: #121417; --card-d: #1a1d21; --text-d: #e6e6e3; --muted-d: #9aa0a6; --line-d: #2a2e33; }
.decoy a { color: inherit; text-decoration: none; }
.d-wrap { max-width: 68rem; margin: 0 auto; padding: 0 1.25rem; }
.d-top { border-bottom: 1px solid var(--line-d); background: var(--card-d); position: sticky; top: 0; z-index: 2; }
.d-bar { display: flex; align-items: center; gap: 1.5rem; height: 3.75rem; font-family: system-ui, sans-serif; }
.d-brand { font: 700 1.35rem/1 Georgia, serif; color: var(--accent-d) !important; white-space: nowrap; }
.d-nav { display: flex; gap: 1.1rem; font-size: 0.9rem; color: var(--muted-d); flex: 1; overflow: hidden; }
.d-sub { margin-left: auto; border: 0; border-radius: 999px; padding: 0.45rem 1rem; background: var(--accent-d); color: #fff; font: 600 0.85rem system-ui, sans-serif; cursor: pointer; }
.d-ad-wide { margin-top: 1.25rem; }
.d-main { display: grid; grid-template-columns: minmax(0, 1fr) 18rem; gap: 3rem; padding-top: 1.75rem; padding-bottom: 3rem; }
.d-article h1 { font-size: clamp(1.7rem, 3.2vw, 2.4rem); line-height: 1.2; margin: 0.25rem 0 0.75rem; }
.d-article h2 { font-size: 1.3rem; margin: 2rem 0 0.4rem; }
.d-article p { margin: 0 0 1.1rem; }
.d-section { font: 700 0.75rem system-ui, sans-serif; letter-spacing: 0.08em; text-transform: uppercase; color: var(--accent-d); margin: 0 !important; }
.d-meta { font: 0.85rem system-ui, sans-serif; color: var(--muted-d); }
.d-hero { aspect-ratio: 16 / 7; border-radius: 0.75rem; margin: 1.25rem 0 1.75rem; display: grid; place-items: center; font-size: 4rem;
  background: linear-gradient(135deg, color-mix(in srgb, var(--accent-d) 35%, var(--card-d)), color-mix(in srgb, var(--accent-d) 10%, var(--card-d))); }
.d-share { display: flex; gap: 0.5rem; align-items: center; margin-top: 2rem; font: 0.85rem system-ui, sans-serif; color: var(--muted-d); }
.d-share button { border: 1px solid var(--line-d); background: var(--card-d); color: var(--text-d); border-radius: 999px; padding: 0.3rem 0.8rem; font: inherit; cursor: pointer; }
.d-side { display: flex; flex-direction: column; gap: 1.25rem; }
.d-box { display: flex; flex-direction: column; gap: 0.6rem; padding: 1rem; border: 1px solid var(--line-d); border-radius: 0.75rem; background: var(--card-d); font-size: 0.92rem; }
.d-box a:hover { color: var(--accent-d); }
/* Ads */
.d-ad { position: relative; display: flex; align-items: center; gap: 0.9rem; border-radius: 0.75rem; font-family: system-ui, sans-serif;
  background: linear-gradient(120deg, color-mix(in srgb, var(--ad) 22%, var(--card-d)), var(--card-d)); border: 1px solid var(--line-d); }
.d-ad.wide { padding: 0.9rem 1.2rem; }
.d-ad.inline { padding: 0.9rem 1.1rem; margin: 1.5rem 0; }
.d-ad.tall { flex-direction: column; align-items: flex-start; gap: 0.35rem; padding: 1.4rem 1.1rem 1.1rem; }
.d-ad strong { font-size: 0.95rem; }
.d-ad-text { display: flex; flex-direction: column; flex: 1; font-size: 0.85rem; color: var(--muted-d); }
.d-ad-text strong { color: var(--text-d); }
.d-ad.tall > span:not(.d-ad-tag):not(.d-ad-icon):not(.d-ad-cta) { font-size: 0.85rem; color: var(--muted-d); }
.d-ad-icon { font-size: 1.7rem; }
.d-ad-icon.big { font-size: 2.4rem; }
.d-ad-cta { white-space: nowrap; border-radius: 999px; padding: 0.35rem 0.85rem; background: var(--ad); color: #fff; font-size: 0.8rem; font-weight: 600; margin-top: 0.3rem; }
.d-ad-tag { position: absolute; top: 0.35rem; right: 0.5rem; font-size: 0.62rem; letter-spacing: 0.05em; color: var(--muted-d); border: 1px solid var(--line-d); border-radius: 3px; padding: 0 0.25rem; }
.d-foot { border-top: 1px solid var(--line-d); padding: 1.5rem 0 2rem; font: 0.8rem system-ui, sans-serif; color: var(--muted-d); }
@media (max-width: 52rem) {
  .d-main { grid-template-columns: 1fr; gap: 1.5rem; }
  .d-nav { display: none; }
  .d-ad.wide .d-ad-cta { display: none; }
}
</style>
