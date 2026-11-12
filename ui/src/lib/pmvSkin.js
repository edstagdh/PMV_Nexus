// The app's player skin: Video.js v10's default video skin (frosted controls,
// touch gestures, hotkeys, tooltips, settings menu) registered as
// <pmv-video-skin>, with a few changes to its template:
//  - a Loop button, an A-B repeat button (2.18.0, off in
//    My settings by default) in the control bar, and a Next button while a
//    playlist plays (2.19.0),
//  - "720p" (converted) at the top of the ⚙ menu (converting while watching,
//    2.13.0; a bar button until 2.19.0) and "Download" under it (a bar button
//    until 2.22.0),
//  - the title at the top in fullscreen (VideoPlayer sets content-title then),
//  - a brightness / volume readout for swiping on the sides in fullscreen on
//    touch screens (2.19.0, VideoPlayer does the swiping),
//  - a big play/pause button in the middle that shows with the controls on
//    touch screens (phones and tablets have no hover, so the small button in
//    the bar is easy to miss),
//  - no Google Cast button (it needs a Cast set-up the app doesn't have),
//  - the time after the seek bar shows the video's length (click: time left).
// Packaged skins can't be changed from outside, so this subclasses the skin
// with an edited copy of its template (see the Video.js "Customize skins"
// guide); the skin's own styles are kept and a small stylesheet is added.
import "@videojs/html/video/player";
import "@videojs/html/video/skin";

const LOOP_ICON = `<svg class="media-button-icon pmv-loop-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d="M17 2l4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="M7 22l-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/></svg>`;
const DOWNLOAD_ICON = `<svg class="media-menu-trigger-item-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>`;

const NEXT_ICON = `<svg class="media-button-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
  <path d="M5.5 5.2v13.6c0 .8.9 1.3 1.6.8l9.4-6.8c.6-.4.6-1.2 0-1.6L7.1 4.4c-.7-.5-1.6 0-1.6.8z"/><rect x="17" y="4.5" width="2.5" height="15" rx="1"/></svg>`;
const HD_ICON = `<svg class="media-menu-trigger-item-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <rect x="2.5" y="5.5" width="19" height="13" rx="2.5"/><path d="M7 9.5v5M7 12h3.2M10.2 9.5v5M13.5 9.5v5h1.6a2.5 2.5 0 0 0 0-5z"/></svg>`;

const EXTRA_CSS = `
.pmv-loop[aria-pressed="true"] { color: var(--media-accent-color, #f97316); }
.pmv-loop[disabled] { opacity: 0.35; cursor: default; }
.pmv-quality[hidden], .pmv-next[hidden] { display: none; }
.pmv-quality[aria-checked="true"] .media-menu-hint-label { color: var(--media-accent-color, #f97316); font-weight: 600; }
.pmv-next[disabled] { opacity: 0.35; cursor: default; }
.pmv-swipe {
  position: absolute; left: 50%; top: 18%; translate: -50% 0; z-index: 30; pointer-events: none;
  display: flex; align-items: center; gap: 0.6rem; padding: 0.55rem 0.9rem; border-radius: 999px;
  background: rgb(0 0 0 / 0.6); color: #fff; font: 600 0.85rem/1 system-ui, sans-serif; backdrop-filter: blur(6px);
}
.pmv-swipe[hidden] { display: none; }
/* Fullscreen title ("Playlist | Title"): at most two lines, ending in "…", smaller on phones. */
.media-title { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; line-height: 1.3; bottom: auto;
  clip-path: inset(0 0 0.1em 0); } /* no sliver of a third line */
/* The skin's shading behind the title, on its own layer (the clipped title box would cut it off). */
.media-title::before { display: none; }
.pmv-title-shade {
  position: absolute; inset: 0 0 auto; z-index: 19; pointer-events: none; display: none;
  height: var(--media-controls-gradient-height); background-image: var(--media-controls-top-gradient);
  transition: opacity var(--media-duration-controls, 0.3s);
}
@container media-root (width >= 32rem) { .pmv-title-shade { display: block; } }
.media-title:not([data-visible]) + .pmv-title-shade { opacity: 0; }
.media-title[hidden] + .pmv-title-shade { display: none; }
/* Narrow players (a phone held upright) have the extra buttons along the top right: the
   title gets its own row under them, the full width. */
@container media-root (width < 32rem) {
  .media-title {
    padding-right: calc(var(--media-spacing, 0.25rem) * 6);
    padding-top: calc(var(--media-control-size, 2.5rem) + var(--media-spacing, 0.25rem) * 5);
  }
}
@container media-root (width < 28rem) { .media-title { font-size: calc(var(--media-spacing, 0.25rem) * 3.5); } }
@container media-root (width >= 48rem) { .media-title { font-size: calc(var(--media-spacing, 0.25rem) * 4.5); } }
.pmv-swipe-bar { width: 7rem; height: 0.3rem; border-radius: 999px; background: rgb(255 255 255 / 0.3); overflow: hidden; }
.pmv-swipe-bar i { display: block; height: 100%; background: var(--media-accent-color, #f97316); }
.pmv-ab { font: 700 0.72rem/1 system-ui, sans-serif; padding-inline: 0.35rem; min-width: 2.6rem; white-space: nowrap; }
.pmv-ab span { border: 1.5px dashed currentColor; border-radius: 4px; padding: 0.18rem 0.28rem; opacity: 0.8; }
.pmv-ab[data-state="a"] { color: var(--media-accent-color, #f97316); }
.pmv-ab[aria-pressed="true"] { color: var(--media-accent-color, #f97316); }
.pmv-ab[aria-pressed="true"] span { border-style: solid; opacity: 1; }
.pmv-ab[hidden] { display: none; }
.pmv-center-play {
  position: absolute; left: 50%; top: 50%; translate: -50% -50%; z-index: 25;
  width: calc(var(--media-spacing, 0.25rem) * 16); height: calc(var(--media-spacing, 0.25rem) * 16);
  border-radius: 50%; background: rgb(0 0 0 / 0.45); backdrop-filter: blur(6px);
  display: none; transition: opacity 0.2s;
}
.pmv-center-play .media-button-icon { width: 45%; height: 45%; }
/* Touch screens only, and only while the controls are showing (or before the video started). */
@media (pointer: coarse) {
  .pmv-center-play { display: grid; place-items: center; }
  media-controls:not([data-visible]) .pmv-center-play { opacity: 0; pointer-events: none; }
}
/* The small floating player: fewer buttons and a smaller centre button. */
:host([compact]) .pmv-download, :host([compact]) .pmv-loop, :host([compact]) .pmv-ab, :host([compact]) .pmv-next { display: none; }
:host([compact]) .pmv-center-play { width: calc(var(--media-spacing, 0.25rem) * 10); height: calc(var(--media-spacing, 0.25rem) * 10); }
`;

function buildTemplate(base) {
  const template = base.cloneNode(true);
  const root = template.content;
  // No Cast button (and its tooltip).
  root.querySelectorAll("media-cast-button").forEach((btn) => {
    const tip = btn.id && root.querySelector(`media-tooltip[trigger="${btn.id}"]`);
    tip?.remove();
    btn.remove();
  });
  // Show the video's total length after the seek bar (2.18.0), not the time left; clicking still toggles.
  root.querySelectorAll('media-time[type="remaining"]').forEach((t) => t.setAttribute("type", "duration"));
  const secondary = root.querySelector(".video-controls-secondary");
  secondary?.insertAdjacentHTML("afterbegin",
    `<button type="button" class="media-button pmv-ab" aria-pressed="false" hidden><span>A-B</span></button>`
    + `<button type="button" class="media-button pmv-loop" aria-pressed="false" title="Loop" aria-label="Loop">${LOOP_ICON}</button>`);
  // Next video (playlists): right after the play button.
  root.querySelector(".video-controls-primary media-play-button")?.insertAdjacentHTML("afterend",
    `<button type="button" class="media-button pmv-next" title="Next video" aria-label="Next video" hidden>${NEXT_ICON}</button>`);
  // 720p (converted while watching) as the first entry of the ⚙ menu, above Speed.
  root.querySelector("media-menu > media-menu-content")?.insertAdjacentHTML("afterbegin",
    `<media-menu-item class="media-menu-trigger-item pmv-quality" role="menuitemcheckbox" aria-checked="false" hidden>${HD_ICON}`
    + `<span>720p</span><span class="media-menu-hint"><span class="media-menu-hint-label">Off</span></span></media-menu-item>`
    // Download the video (2.22.0: moved here from the control bar), under 720p.
    + `<media-menu-item class="media-menu-trigger-item pmv-download" role="menuitem" title="Download the video">${DOWNLOAD_ICON}<span>Download</span></media-menu-item>`);
  root.querySelector("media-title")?.insertAdjacentHTML("afterend", `<div class="pmv-title-shade" aria-hidden="true"></div>`);
  root.querySelector(".video-status-indicators")?.insertAdjacentHTML("beforeend",
    `<div class="pmv-swipe" hidden aria-live="polite"><span class="pmv-swipe-label"></span><span class="pmv-swipe-bar"><i></i></span></div>`);
  root.querySelector("media-controls")?.insertAdjacentHTML("beforeend",
    `<media-play-button class="media-button media-play-button pmv-center-play">
       <media-icon name="restart" class="media-button-icon media-play-button-restart-icon"></media-icon>
       <media-icon name="play" class="media-button-icon media-play-button-play-icon"></media-icon>
       <media-icon name="pause" class="media-button-icon media-play-button-pause-icon"></media-icon>
     </media-play-button>`);
  return template;
}

let sheet = null;
if (!customElements.get("pmv-video-skin")) {
  const Base = customElements.get("video-skin");
  const template = buildTemplate(Base.template);
  class PmvVideoSkin extends Base {
    static template = template;

    constructor() {
      super();
      if (!sheet) { sheet = new CSSStyleSheet(); sheet.replaceSync(EXTRA_CSS); }
      this.shadowRoot.adoptedStyleSheets = [...this.shadowRoot.adoptedStyleSheets, sheet];
    }

    part(selector) { return this.shadowRoot.querySelector(selector); }

    /** Call after the player was moved to another place in the page (video
     * page <-> mini-player). Video.js 10.3's popups (volume slider, menus,
     * tooltips) lose their trigger when their element is disconnected and
     * connected again -- the volume slider then never opens. Fresh copies of
     * the popup elements link up again. */
    refreshPopups() {
      const popups = [...this.shadowRoot.querySelectorAll("[data-popup]")];
      for (const el of popups) {
        if (el.parentElement?.closest("[data-popup]")) continue; // copied along with its outer popup
        el.replaceWith(el.cloneNode(true));
      }
    }

    /** Page mode: hotkeys work anywhere on the page; mini-player: only when the player has focus. */
    setGlobalHotkeys(on) {
      this.shadowRoot.querySelectorAll("media-hotkey").forEach((k) => {
        if (on) k.setAttribute("target", "document");
        else k.removeAttribute("target");
      });
    }
  }
  customElements.define("pmv-video-skin", PmvVideoSkin);
}
