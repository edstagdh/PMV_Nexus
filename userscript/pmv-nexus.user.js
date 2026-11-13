// ==UserScript==
// @name         PMV Nexus - PMVHaven companion
// @namespace    edstagdh
// @version      2.20
// @description  Companion for PMV Nexus: a download button on PMVHaven / PornTubeAI / HMVHaven video pages, and badges marking the videos you already have.
// @homepageURL  https://github.com/edstagdh/PMV_Nexus
// @supportURL   https://github.com/edstagdh/PMV_Nexus/issues
// @updateURL    https://raw.githubusercontent.com/edstagdh/PMV_Nexus/main/userscript/pmv-nexus.user.js
// @downloadURL  https://raw.githubusercontent.com/edstagdh/PMV_Nexus/main/userscript/pmv-nexus.user.js
// @match        https://pmvhaven.com/*
// @match        https://porntubeai.com/*
// @match        https://hmvhaven.com/*
// @grant        GM_xmlhttpRequest
// @grant        unsafeWindow
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_addValueChangeListener
// @grant        GM_registerMenuCommand
// @connect      localhost
// @run-at       document-start
// ==/UserScript==

// Changes:
// v2.20: a short description, and it updates itself from GitHub (PMV Nexus 3.0.0). v2.19: renamed for PMV Nexus
// (install it fresh: a renamed script is a new script to Tampermonkey; enter the address and API key again). v2.18:
// menu "Change PMV Nexus Address / API Key" (e.g. after the app's port changed); the Windows installer writes the
// chosen port into this file's default address. Send the current PMVHaven video URL to your local PMV Nexus, with
// SPA-aware URL cleaning, server-side "already exists" detection, grid/listing badges showing download status on
// every video card, a manual cache-clear control, a backend health/status indicator that's coordinated across tabs,
// the video's rating pulled straight from PMVHaven's own API, a default-volume fix for the player, and a full tag
// list rendered as compact split minus/tag/plus vote pills that highlight your actual votes. v2.17: also runs on
// hmvhaven.com (PMVHaven's sister site, same platform as porntubeai.com) — the download button, badges and tag votes
// work there the same way, with the same download-manager address and API key. v2.16: keeps a local copy of PMV
// Nexus's list of downloaded videos (and removed duplicates) in GM storage, synced once a day (any tab, coordinated
// across tabs) and on demand from the menu ("Sync Downloaded List Now") — grid/listing badges now come straight from
// that list with no request per card; only the current video's own button still asks the server live (to catch
// queued/downloading). Videos removed on the Duplicates page show as "Duplicate" (purple) with the title of the copy
// that was kept. The old per-URL 30-day cache is replaced by this list. v2.15: fixed the user-id sniffer actually
// never capturing anything — it was patching plain window.fetch/XMLHttpRequest, which in Tampermonkey's isolated JS
// world are NOT the same objects the page's own SPA code calls into (only this userscript's own requests were ever
// visible to it). Now patches unsafeWindow.fetch / unsafeWindow.XMLHttpRequest.prototype.open instead, added @grant
// unsafeWindow, and added a "Toggle Verbose User-ID Sniffer Logging" menu command to confirm from the console that
// it's actually seeing page requests. v2.14: the site calls GET /api/xp/<id> as part of its own client-side hydration
// on nearly every page, not just Profile — so removed the manual "open Profile to detect id" menu command and toast
// nudge entirely. Also switched to document-start so the sniffer installs before the site's own bundle runs. v2.13:
// dropped the per-video/per-tag local vote memory entirely — the ONLY thing persisted now is your PMVHaven user id
// itself. Vote-pill highlighting is derived purely by checking that id against each tag's own live
// tagVotes[].upvotes/downvotes arrays on every render. v2.12: detects and caches your real PMVHaven user id by
// passively watching every fetch/XHR request the page makes for GET /api/xp/<id>. v2.11: after a successful vote, the
// direction was remembered locally (GM storage, keyed per video + tag) and the matching button stayed highlighted
// (green for upvoted, red for downvoted). v2.10: matched the site's own dark/orange palette — the tag label keeps the
// site's own orange-gradient tag-pill look, while the vote buttons use the same neutral #2A2A2A/#3A3A3A the site uses
// for its own icon buttons (colored only on hover); shows the net vote count as "(n)" next to the tag name; and
// shrunk the pill's padding/button size for a lower-profile row. v2.9: redesigned each tag pill as a three-segment
// control (red "−" | tag name | green "+") instead of small inline arrows. v2.8: both tag vote buttons now call the
// site's confirmed voting endpoint, POST /api/videos/<id>/tags/vote with {tag, voteType: "upvote"|"downvote",
// tagType}, covering both directions on already-attached tags; since that endpoint's own response shape wasn't
// confirmed, a successful vote triggers a re-fetch of the video's metadata so the pill is redrawn from the server's
// real tagVotes counts rather than a parsed/guessed response. v2.7: (superseded) wired the upvote button to
// /api/videos/<id>/tags/suggest, the site's "Add a New Tag" endpoint, as a stand-in before the dedicated /tags/vote
// endpoint was found; downvote was disabled since no downvote path was known yet. v2.6: replaced the site's own
// truncated "top 5 tags" row on a video's own page with the FULL tag list (net up/down vote counts computed from the
// video API's tagVotes array) and added inline vote buttons per tag. v2.5: on video pages, dynamically detects the
// site's <video> element (even across SPA navigation / re-sourced players) and sets its volume to 4% once metadata
// for the current source has loaded, since the player otherwise always starts at 100%. v2.4: on a video's own page,
// fetches the rating from https://pmvhaven.com/api/videos/<id> and displays it in the site's own "XX% out of 100%"
// format right after the duration in the views/date/duration row. v2.3: added a backend status icon
// (online/offline/checking) that refreshes every 30s; multiple open tabs share one cached result via GM storage so
// only one tab pings the backend at a time, with GM_addValueChangeListener pushing the result to sibling tabs
// instantly. v2.2: download button icon now turns green (downloaded), glowing yellow (queued), or glowing pink
// (downloading). v2.1: buttons live in the site's top bar, anchored to the search icon, and status checks no longer
// re-fire on every SPA polling tick.


(function () {
  "use strict";

  const DEFAULT_BASE_URL = "http://localhost:8676";
  const BUTTON_ID = "pmv-manager-add-btn";
  const CLEAR_CACHE_BUTTON_ID = "pmv-manager-clear-cache-btn";
  const TOOLBAR_WRAP_ID = "pmv-manager-toolbar-buttons";
  const STATUS_DOT_CLASS = "pmv-status-dot";
  const DEBUG = true;

  // Retry limit for live status checks (see the DOWNLOADED-LIST MODULE for
  // the locally stored list that replaced the old per-URL cache in v2.16).
  const MAX_RETRIES = 5;
  const LEGACY_CACHE_KEY = "pmv_exists_cache_v2"; // pre-2.16 cache, emptied on startup

  // Status polling loop config (for queued/active downloads)
  const QUEUED_POLL_INTERVAL_MS = 10 * 1000; // 10 seconds
  const QUEUED_POLL_TIMEOUT_MS = 10 * 60 * 1000; // 10 minutes total timeout
  const ACTIVE_STATUSES = new Set(["pending", "fetching", "downloading"]);
  const DONE_STATUS = "done";
  // Removed as a duplicate on PMV Nexus's Duplicates page; the
  // video it duplicates is downloaded.
  const MERGED_STATUS = "merged";

  // Backend health/status indicator config. The stored value is shared (via
  // GM storage) across every open PMVHaven tab, so at most one tab pings the
  // backend per BACKEND_STATUS_INTERVAL_MS window; the rest read the cached
  // result instead. GM_addValueChangeListener pushes fresh results to
  // sibling tabs the instant one tab finishes a check.
  const BACKEND_STATUS_ID = "pmv-manager-status-btn";
  const BACKEND_STATUS_STORAGE_KEY = "pmv_backend_status_v1";
  const BACKEND_STATUS_INTERVAL_MS = 30 * 1000; // 30 seconds — treat a cached result older than this as stale
  const BACKEND_STATUS_LOCAL_TICK_MS = 5 * 1000; // how often each tab re-checks the shared value locally
  const BACKEND_STATUS_CLAIM_GRACE_MS = 10 * 1000; // how long a "checking" claim from another tab is honored before we assume it stalled
  const BACKEND_HEALTH_ENDPOINT = "/api/health"; // adjust to match your backend's actual health route

  // Track active 30s polling loops: url -> { timerId, startTime }
  const activePollers = new Map();

  // =========================================================================
  // --- LOGGING HELPER ---
  // =========================================================================

  function log(...args) {
    if (DEBUG) {
      console.log("%c[PMV Manager]", "color: #f97316; font-weight: bold;", ...args);
    }
  }

  function logWarn(...args) {
    console.warn("%c[PMV Manager]", "color: #eab308; font-weight: bold;", ...args);
  }

  function logError(...args) {
    console.error("%c[PMV Manager]", "color: #ef4444; font-weight: bold;", ...args);
  }

  // =========================================================================
  // --- CURRENT USER ID DETECTION MODULE ---
  // The site never exposes the logged-in user's own id directly in any page
  // we've inspected, but the SPA itself calls GET /api/xp/<yourUserId> to
  // render your XP bar on nearly every page load, not just Profile. Rather
  // than guess or reverse-engineer auth state, we passively watch every
  // fetch/XHR request the page makes — ours and the site's own — for that
  // URL pattern and cache the id (GM storage) the first time we see it.
  //
  // Two things had to be right for this to actually work:
  //   1. TIMING: that XP call fires very early, as part of the SPA's own
  //      client-side hydration, so the script runs at "document-start" (see
  //      the @run-at header) so our fetch/XHR wrapping is installed before
  //      the site's own bundle executes and makes that call.
  //   2. WORLD: Tampermonkey runs userscripts in an isolated JS world — the
  //      DOM is shared with the page, but built-in globals like
  //      window.fetch/XMLHttpRequest are NOT. Patching plain window.fetch
  //      only intercepts calls made by this userscript itself. We patch
  //      unsafeWindow.fetch / unsafeWindow.XMLHttpRequest.prototype.open
  //      instead, since unsafeWindow is the real page-world window the
  //      site's own code actually calls into.
  // =========================================================================

  const USER_ID_STORAGE_KEY = "pmv_current_user_id_v1";
  const USER_ID_XP_REGEX = /\/api\/xp\/([a-fA-F0-9]{24})(?:[/?#]|$)/;

  // Toggle via the "Toggle Verbose User-ID Sniffer Logging" menu command if
  // you ever need to confirm the sniffer is actually seeing page requests —
  // logs every URL it inspects, not just matches. Off by default since it's
  // noisy (fires on every fetch/XHR call the page makes).
  let userIdSnifferVerbose = false;

  function getCachedUserId() {
    return GM_getValue(USER_ID_STORAGE_KEY, "") || null;
  }

  function maybeCaptureUserIdFromUrl(url) {
    // Once an id is cached there's nothing left to detect, so bail before
    // even running the regex — this is what makes the sniffer effectively
    // "run in the background only until we have a key," at negligible cost
    // per request rather than a cheap-but-nonzero cost forever.
    if (getCachedUserId()) return;
    if (!url || typeof url !== "string") return;
    if (userIdSnifferVerbose) log(`[user-id sniffer] saw request: ${url}`);
    const match = url.match(USER_ID_XP_REGEX);
    if (!match) return;
    const id = match[1];
    GM_setValue(USER_ID_STORAGE_KEY, id);
    log(`Detected and cached your PMVHaven user id: ${id}`);
  }

  // CRITICAL: Tampermonkey runs userscripts in an isolated JS world. The DOM
  // is shared with the real page, but built-in globals like window.fetch and
  // XMLHttpRequest are NOT — each world gets its own copies. Patching plain
  // `window.fetch` here would only ever intercept fetch calls made by this
  // userscript itself, never the site's own SPA code, which runs in the
  // actual page world. `unsafeWindow` is Tampermonkey's handle to that real
  // page window, so we patch fetch/XHR there instead — this is the only way
  // to actually see requests the site itself makes.
  const pageWindow = typeof unsafeWindow !== "undefined" ? unsafeWindow : window;

  // Wraps fetch/XHR just enough to peek at the URL before forwarding the
  // call unmodified — never blocks, delays, or alters the real request, and
  // any error while inspecting is swallowed so a sniffing bug can never
  // break the site itself.
  (function installUserIdSniffer() {
    const _fetch = pageWindow.fetch;
    pageWindow.fetch = function (input, init) {
      try {
        const url = typeof input === "string" ? input : input && input.url;
        maybeCaptureUserIdFromUrl(url);
      } catch (e) {
        /* never let sniffing break the real request */
      }
      return _fetch.apply(this, arguments);
    };

    const _open = pageWindow.XMLHttpRequest.prototype.open;
    pageWindow.XMLHttpRequest.prototype.open = function (method, url, ...rest) {
      try {
        maybeCaptureUserIdFromUrl(url);
      } catch (e) {
        /* ignore */
      }
      return _open.call(this, method, url, ...rest);
    };
  })();

  // =========================================================================
  // --- CONFIG MODULE ---
  // =========================================================================

  function getConfig() {
    let baseUrl = GM_getValue("pmv_manager_base_url", "");
    let apiKey = GM_getValue("pmv_manager_api_key", "");
    if (!baseUrl) {
      baseUrl = prompt("PMV Nexus URL:", DEFAULT_BASE_URL) || DEFAULT_BASE_URL;
      GM_setValue("pmv_manager_base_url", baseUrl);
      log("Base URL initialized:", baseUrl);
    }
    if (!apiKey) {
      apiKey = prompt("PMV Nexus API key:", "") || "";
      GM_setValue("pmv_manager_api_key", apiKey);
      log("API Key initialized");
    }
    return { baseUrl, apiKey };
  }

  // =========================================================================
  // --- DOWNLOADED-LIST MODULE (local copy of the server's list) ---
  //
  // The PMV Nexus's GET /api/external/index returns the id of every
  // downloaded video, and of every duplicate removed on its Duplicates page
  // together with the video that was kept. A copy lives in GM storage
  // (shared by all tabs) and is refreshed once a day -- by whichever tab
  // notices first, with a claim so tabs don't all sync at once -- or on
  // demand from the menu. Lookups are instant and need no server.
  //
  //   - Grid / listing cards are marked from this list only (no request
  //     per card) once it has been synced at least once.
  //   - The current video's own button still asks the server live, to catch
  //     videos that are queued or downloading right now.
  //   - When a live check or a status poll reports "done", the id is added
  //     to the local list straight away.
  //
  // Failed live checks are never stored -- they only live in the in-memory
  // `failedAttempts` retry tracker for this page session.
  // =========================================================================

  const INDEX_STORAGE_KEY = "pmv_download_index_v1";
  const INDEX_CLAIM_KEY = "pmv_download_index_sync_claim_v1";
  const INDEX_MAX_AGE_MS = 24 * 60 * 60 * 1000; // sync once a day
  const INDEX_CLAIM_GRACE_MS = 60 * 1000; // another tab's sync claim is honoured this long

  const inFlightChecks = new Set();
  const failedAttempts = new Map(); // url -> { count, nextTryAt } (in-memory only, never persisted)

  // In-memory view of the stored list: { syncedAt, done: Set<id>, merged: Map<id, {of, title}> }
  let downloadIndex = null;

  function loadIndex() {
    try {
      const raw = JSON.parse(GM_getValue(INDEX_STORAGE_KEY, "") || "null");
      if (raw && Array.isArray(raw.done)) {
        return {
          syncedAt: raw.syncedAt || 0,
          done: new Set(raw.done),
          merged: new Map(Object.entries(raw.merged || {})),
        };
      }
    } catch (e) {
      logError("Failed to read the stored downloaded list:", e);
    }
    return { syncedAt: 0, done: new Set(), merged: new Map() };
  }

  function getIndex() {
    if (!downloadIndex) downloadIndex = loadIndex();
    return downloadIndex;
  }

  function saveIndex(idx) {
    downloadIndex = idx;
    GM_setValue(INDEX_STORAGE_KEY, JSON.stringify({
      syncedAt: idx.syncedAt,
      done: Array.from(idx.done),
      merged: Object.fromEntries(idx.merged),
    }));
  }

  // True once the list has been synced at least once: from then on, a video
  // missing from it is simply "not downloaded" for grid cards.
  function indexIsUsable() {
    return getIndex().syncedAt > 0;
  }

  // Returns { exists, status, label } from the local list, or null if the
  // video isn't in it.
  function getCachedResult(url) {
    const id = getVideoIdFromUrl(url);
    if (!id) return null;
    const idx = getIndex();
    if (idx.done.has(id)) return { exists: true, status: DONE_STATUS };
    const dup = idx.merged.get(id);
    if (dup) {
      return {
        exists: true,
        status: MERGED_STATUS,
        label: dup.title ? `Duplicate of “${dup.title}” (already downloaded)` : "Duplicate (already downloaded)",
      };
    }
    return null;
  }

  // Records a confirmed "done" in the local list right away (between syncs).
  // Anything else (queued, downloading, failed checks) is never stored.
  function setCachedResult(url, data) {
    if (!data || data.exists !== true || data.status !== DONE_STATUS) return;
    const id = getVideoIdFromUrl(url);
    if (!id) return;
    const idx = loadIndex(); // re-read: another tab may have synced meanwhile
    if (idx.done.has(id)) return;
    idx.done.add(id);
    idx.merged.delete(id);
    saveIndex(idx);
    log(`Added ${id} to the local downloaded list.`);
  }

  // Fetches the full list from the server. `force` skips the once-a-day
  // check and the other-tab claim (menu command / cache clear).
  function syncIndex(force) {
    const idx = getIndex();
    if (!force && idx.syncedAt && Date.now() - idx.syncedAt < INDEX_MAX_AGE_MS) return;
    if (!force) {
      const claim = Number(GM_getValue(INDEX_CLAIM_KEY, 0)) || 0;
      if (Date.now() - claim < INDEX_CLAIM_GRACE_MS) return; // another tab is syncing
    }
    GM_setValue(INDEX_CLAIM_KEY, Date.now());

    const { baseUrl, apiKey } = getConfig();
    log("Syncing the downloaded list from PMV Nexus...");
    GM_xmlhttpRequest({
      method: "GET",
      url: `${baseUrl}/api/external/index`,
      headers: { "X-API-Key": apiKey },
      onload: (res) => {
        GM_setValue(INDEX_CLAIM_KEY, 0);
        if (res.status < 200 || res.status >= 300) {
          logWarn(`Downloaded-list sync failed: HTTP ${res.status}`);
          if (force) showToast(`Sync failed (HTTP ${res.status})`, true);
          return;
        }
        try {
          const data = JSON.parse(res.responseText);
          saveIndex({
            syncedAt: Date.now(),
            done: new Set(data.done || []),
            merged: new Map(Object.entries(data.merged || {})),
          });
          log(`Downloaded list synced: ${downloadIndex.done.size} downloaded, ${downloadIndex.merged.size} duplicates.`);
          if (force) showToast(`Synced: ${downloadIndex.done.size} downloaded, ${downloadIndex.merged.size} duplicates`);
          refreshAllStatuses();
        } catch (e) {
          logError("Could not read the downloaded list:", e);
          if (force) showToast("Sync failed (unreadable response)", true);
        }
      },
      onerror: () => {
        GM_setValue(INDEX_CLAIM_KEY, 0);
        logWarn("Downloaded-list sync failed: PMV Nexus unreachable");
        if (force) showToast("Sync failed: PMV Nexus unreachable", true);
      },
    });
  }

  // Another tab synced or added an id: pick up the new list and repaint.
  function setupIndexSync() {
    GM_addValueChangeListener(INDEX_STORAGE_KEY, (name, oldValue, newValue, remote) => {
      if (!remote) return;
      downloadIndex = null;
      refreshAllStatuses();
    });
  }

  // Re-marks every visible card and the current video's button.
  function refreshAllStatuses() {
    document.querySelectorAll(`.${GRID_BADGE_CLASS}`).forEach((b) => b.remove());
    processedCards = new WeakSet();
    if (isVideoPage()) checkExists(window.location.href);
    processGridCards();
  }

  // Forgets the local list and live-check cooldowns, then syncs afresh.
  function clearCache() {
    log("Clearing the local downloaded list.");
    failedAttempts.clear();
    saveIndex({ syncedAt: 0, done: new Set(), merged: new Map() });
    setButtonState("idle");
    showToast("Download status cleared — syncing the downloaded list again");
    ensureToolbarButtons();
    syncButtonVisibility();
    refreshAllStatuses();
    syncIndex(true);
  }

  // =========================================================================
  // --- ACTIVE STATUS POLLING MODULE (10s interval, 10m timeout) ---
  // =========================================================================

  function startStatusPolling(url) {
    if (activePollers.has(url)) {
      return; // Silently exit if already polling this url.
    }

    log(`Starting 10-second status polling loop for ${url} (10 min timeout)...`);
    const startTime = Date.now();

    const timerId = setInterval(() => {
      const elapsedTime = Date.now() - startTime;
      if (elapsedTime >= QUEUED_POLL_TIMEOUT_MS) {
        logWarn(`Status polling for ${url} reached the 10-minute timeout limit. Halting loop.`);
        stopStatusPolling(url);
        return;
      }

      log(`Polling status update for active item (${Math.round(elapsedTime / 1000)}s elapsed)...`);
      pollStatusUpdate(url);
    }, QUEUED_POLL_INTERVAL_MS);

    activePollers.set(url, { timerId, startTime });
  }

  function stopStatusPolling(url) {
    if (activePollers.has(url)) {
      const poller = activePollers.get(url);
      clearInterval(poller.timerId);
      activePollers.delete(url);
      log(`Stopped status polling loop for ${url}`);
    }
  }

  function pollStatusUpdate(url) {
    const { baseUrl, apiKey } = getConfig();

    GM_xmlhttpRequest({
      method: "GET",
      url: `${baseUrl}/api/external/exists?url=${encodeURIComponent(url)}`,
      headers: { "X-API-Key": apiKey },
      onload: (res) => {
        if (res.status >= 200 && res.status < 300) {
          let result = false;
          try {
            const data = JSON.parse(res.responseText);
            if (data.exists) result = data;
          } catch (e) {
            logError("Failed to parse JSON response during status poll:", e);
          }

          if (result) {
            const status = result.status;
            // Only ever cached once it reaches "done" — setCachedResult's
            // own guard would drop anything else anyway, but we skip the
            // call entirely for in-progress statuses for clarity.
            if (status === DONE_STATUS) {
              setCachedResult(url, { exists: true, status });
            }
            applyStatusToUI(url, "exists", status, result.label);

            if (!ACTIVE_STATUSES.has(status)) {
              log(`Final status reached for ${url}: '${status}'. Stopping 10s polling loop.`);
              stopStatusPolling(url);
            }
          } else {
            // No longer reported as existing — nothing to cache, just
            // reflect it in the UI and stop polling.
            applyStatusToUI(url, "idle");
            log(`Item is no longer reported as existing on server for ${url}. Stopping polling loop.`);
            stopStatusPolling(url);
          }
        } else {
          logWarn(`Status poll request returned HTTP ${res.status} for ${url}`);
        }
      },
      onerror: (err) => {
        logWarn(`Status poll network error for ${url}:`, err);
      },
    });
  }

  // =========================================================================
  // --- BACKEND HEALTH/STATUS MODULE ---
  //
  // Coordination rules:
  //   - The last known result is stored under BACKEND_STATUS_STORAGE_KEY as
  //     { status, timestamp }, shared by every open PMVHaven tab via GM
  //     storage.
  //   - A tab only pings the backend if the stored value is missing or older
  //     than BACKEND_STATUS_INTERVAL_MS (30s). Otherwise it just renders the
  //     cached value.
  //   - Before issuing the actual request, a tab writes a "checking" claim
  //     with a fresh timestamp. This is a *soft* lock, not a true mutex: two
  //     tabs polling in the same instant can still both slip through and
  //     both fire a request. That's an acceptable tradeoff for a cheap
  //     health ping — the point is "usually one request," not a hard
  //     guarantee — but worth knowing if you ever put something expensive
  //     behind this same pattern.
  //   - GM_addValueChangeListener pushes the result to sibling tabs the
  //     instant one tab finishes a check, so they don't have to wait for
  //     their own next local tick to update their icon.
  // =========================================================================

  const BACKEND_STATUS_STYLES = {
    online: { color: "text-green-500", title: "Backend online — click to re-check", dot: "bg-green-500" },
    offline: { color: "text-red-500", title: "Backend unreachable — click to retry", dot: "bg-red-500" },
    checking: { color: "text-gray-400", title: "Checking backend status…", dot: "bg-gray-400" },
    unknown: { color: "text-gray-400", title: "Backend status unknown — click to check", dot: "bg-gray-400" },
  };

  const BACKEND_STATUS_ALL_COLOR_CLASSES = Array.from(
    new Set(Object.values(BACKEND_STATUS_STYLES).map((s) => s.color))
  );

  let backendPingInFlight = false;

  function readBackendStatus() {
    try {
      const raw = GM_getValue(BACKEND_STATUS_STORAGE_KEY, "");
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (e) {
      logWarn("Failed to parse stored backend status, treating as unknown:", e);
      return null;
    }
  }

  function writeBackendStatus(status) {
    const payload = { status, timestamp: Date.now() };
    GM_setValue(BACKEND_STATUS_STORAGE_KEY, JSON.stringify(payload));
    return payload;
  }

  function isBackendStatusStale(entry) {
    return !entry || Date.now() - entry.timestamp >= BACKEND_STATUS_INTERVAL_MS;
  }

  function applyBackendStatusToIcon(entry) {
    const btn = document.getElementById(BACKEND_STATUS_ID);
    if (!btn) return;

    const state = (entry && entry.status) || "unknown";
    const style = BACKEND_STATUS_STYLES[state] || BACKEND_STATUS_STYLES.unknown;

    btn.classList.remove(...BACKEND_STATUS_ALL_COLOR_CLASSES);
    btn.classList.add(style.color);
    btn.title = style.title;

    const dot = btn.querySelector(`.${STATUS_DOT_CLASS}`);
    if (dot) {
      dot.style.display = "block";
      dot.className = `${STATUS_DOT_CLASS} absolute -top-0.5 -right-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-black/40 ${style.dot}`;
    }
  }

  function pingBackendHealth() {
    if (backendPingInFlight) return; // this tab already has a request in flight
    backendPingInFlight = true;

    // Soft claim: stamp "checking" immediately so sibling tabs that run
    // their local tick in the next few seconds see a fresh timestamp and
    // back off, instead of every open tab firing a request at once.
    const claim = writeBackendStatus("checking");
    applyBackendStatusToIcon(claim);

    const { baseUrl, apiKey } = getConfig();

    log("Pinging backend health endpoint...");

    GM_xmlhttpRequest({
      method: "GET",
      url: `${baseUrl}${BACKEND_HEALTH_ENDPOINT}`,
      headers: { "X-API-Key": apiKey },
      timeout: 8000,
      onload: (res) => {
        backendPingInFlight = false;
        const status = res.status >= 200 && res.status < 300 ? "online" : "offline";
        const payload = writeBackendStatus(status);
        applyBackendStatusToIcon(payload);
        log(`Backend health check result: ${status} (HTTP ${res.status})`);
      },
      onerror: () => {
        backendPingInFlight = false;
        const payload = writeBackendStatus("offline");
        applyBackendStatusToIcon(payload);
        logWarn("Backend health check failed: network error.");
      },
      ontimeout: () => {
        backendPingInFlight = false;
        const payload = writeBackendStatus("offline");
        applyBackendStatusToIcon(payload);
        logWarn("Backend health check timed out.");
      },
    });
  }

  // Called on every local tick (and once at startup). Reads the shared
  // value first; only pings the backend if it's missing, stale, or a
  // lingering "checking" claim has clearly stalled. Pass force=true to
  // bypass staleness (used by the manual click-to-refresh handler).
  function refreshBackendStatus(force) {
    const entry = readBackendStatus();

    if (!force) {
      if (entry && entry.status === "checking") {
        // Another tab claimed a check recently — trust it a little longer
        // rather than piling on, then fall through to a real check if that
        // claim looks stale (e.g. the tab that made it was closed mid-request).
        if (Date.now() - entry.timestamp < BACKEND_STATUS_CLAIM_GRACE_MS) {
          applyBackendStatusToIcon(entry);
          return;
        }
      } else if (!isBackendStatusStale(entry)) {
        applyBackendStatusToIcon(entry);
        return;
      }
    }

    pingBackendHealth();
  }

  function onBackendStatusClick() {
    refreshBackendStatus(true);
  }

  // Cross-tab live sync: as soon as any tab writes a new result, every other
  // tab's icon updates immediately instead of waiting for its own next tick.
  function setupBackendStatusSync() {
    if (typeof GM_addValueChangeListener !== "function") {
      logWarn("GM_addValueChangeListener unavailable — backend status will still sync via polling, just less instantly.");
      return;
    }
    GM_addValueChangeListener(BACKEND_STATUS_STORAGE_KEY, (name, oldValue, newValue, remote) => {
      if (!remote) return; // this tab's own writes are already applied directly
      try {
        const entry = JSON.parse(newValue);
        applyBackendStatusToIcon(entry);
      } catch (e) {
        logWarn("Failed to parse incoming backend status update from another tab:", e);
      }
    });
  }

  // =========================================================================
  // --- UI BUTTON MODULE (header "Add to PMV Nexus" button) ---
  // =========================================================================

  const STATUS_TITLES = {
    idle: "Add to PMV Nexus",
    checking: "Checking status…",
    done: "Already downloaded — click to queue again",
    pending: "Queued — click to queue again",
    fetching: "Queued — click to queue again",
    downloading: "Downloading — click to queue again",
    merged: "Duplicate — already downloaded as another video; click to queue anyway",
    failed: "Could not verify status after 5 attempts — click to try anyway",
  };

  const STATUS_DOT_COLORS = {
    done: "bg-green-500",
    pending: "bg-yellow-400",
    fetching: "bg-yellow-400",
    downloading: "bg-pink-400",
    merged: "bg-purple-500",
    failed: "bg-red-500",
  };

  // Icon color (and, for active states, a pulsing glow) per status. "idle"
  // and "checking" are neutral; "done" turns solid green; queued states glow
  // yellow; an active download glows pink.
  const STATUS_BUTTON_CLASSES = {
    idle: ["text-orange-400"],
    checking: ["text-gray-400"],
    done: ["text-green-500"],
    pending: ["text-yellow-400", "pmv-glow-yellow"],
    fetching: ["text-yellow-400", "pmv-glow-yellow"],
    downloading: ["text-pink-400", "pmv-glow-pink"],
    merged: ["text-purple-400"],
    failed: ["text-red-500"],
  };

  // Every class STATUS_BUTTON_CLASSES might apply, so setButtonState can
  // always strip the full set before applying the classes for the new state.
  const ALL_STATUS_BUTTON_CLASSES = Array.from(
    new Set(Object.values(STATUS_BUTTON_CLASSES).flat())
  );

  function applyButtonColorState(btn, styleKey) {
    btn.classList.remove(...ALL_STATUS_BUTTON_CLASSES);
    btn.classList.add(...(STATUS_BUTTON_CLASSES[styleKey] || STATUS_BUTTON_CLASSES.idle));
  }

  const CONFIRM_MESSAGES = {
    done: "This video is already downloaded. Send it to PMV Nexus again anyway?",
    downloading: "This video is currently downloading. Queue it again anyway?",
    pending: "This video is already queued. Queue it again anyway?",
    fetching: "This video is already queued. Queue it again anyway?",
    merged: "This video is a duplicate of one you already downloaded. Download it anyway?",
    failed: "Could not verify video status. Try sending it anyway?",
  };

  function setButtonState(state, extra) {
    const btn = document.getElementById(BUTTON_ID);
    if (!btn) return;

    const currentStatus = btn.dataset.pmvStatus || "";
    const currentState = btn.dataset.pmvState || "";
    const newStatus = extra || "";

    // DOM update guard: prevents rewriting properties repeatedly if state hasn't changed
    if (currentState === state && currentStatus === newStatus) {
      return;
    }

    const dot = btn.querySelector(`.${STATUS_DOT_CLASS}`);

    if (state === "checking") {
      btn.dataset.pmvState = "checking";
      delete btn.dataset.pmvStatus;
      btn.classList.add("opacity-50", "cursor-wait");
      btn.title = STATUS_TITLES.checking;
      applyButtonColorState(btn, "checking");
      if (dot) dot.style.display = "none";
    } else if (state === "exists") {
      btn.dataset.pmvState = "exists";
      btn.dataset.pmvStatus = extra || "";
      btn.classList.remove("opacity-50", "cursor-wait");
      btn.title = (btn.dataset.pmvLabel && extra === MERGED_STATUS
        ? `${btn.dataset.pmvLabel} — click to queue anyway`
        : STATUS_TITLES[extra]) || "Already on server — click to queue again";
      applyButtonColorState(btn, extra);
      if (dot) {
        dot.style.display = "block";
        dot.className = `${STATUS_DOT_CLASS} absolute -top-0.5 -right-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-black/40 ${
          STATUS_DOT_COLORS[extra] || "bg-orange-400"
        }`;
      }
    } else if (state === "failed") {
      btn.dataset.pmvState = "failed";
      btn.dataset.pmvStatus = "failed";
      btn.classList.remove("opacity-50", "cursor-wait");
      btn.title = STATUS_TITLES.failed;
      applyButtonColorState(btn, "failed");
      if (dot) {
        dot.style.display = "block";
        dot.className = `${STATUS_DOT_CLASS} absolute -top-0.5 -right-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-black/40 bg-red-500`;
      }
    } else {
      btn.dataset.pmvState = "idle";
      delete btn.dataset.pmvStatus;
      btn.classList.remove("opacity-50", "cursor-wait");
      btn.title = STATUS_TITLES.idle;
      applyButtonColorState(btn, "idle");
      if (dot) dot.style.display = "none";
    }
  }

  // =========================================================================
  // --- STATUS CHECK CORE (shared by the header button AND grid badges) ---
  // =========================================================================

  // Applies a resolved status to every place it's relevant: the header
  // button (if we're currently viewing that video) and any matching grid /
  // listing cards visible on the page right now.
  function applyStatusToUI(url, state, extra, label) {
    if (window.location.href === url) {
      const btn = document.getElementById(BUTTON_ID);
      if (btn) {
        if (label) btn.dataset.pmvLabel = label;
        else delete btn.dataset.pmvLabel;
      }
      if (state === "checking") setButtonState("checking");
      else if (state === "exists") setButtonState("exists", extra);
      else if (state === "failed") setButtonState("failed");
      else setButtonState("idle");
    }

    // Skip the transient "checking" state for grid badges to avoid flicker
    // across dozens of cards; only render settled states.
    if (state === "checking") return;

    const anchors = findGridAnchorsForUrl(url);
    for (const anchor of anchors) {
      if (state === "exists") setGridBadge(anchor, extra || "done", label);
      else if (state === "failed") setGridBadge(anchor, "failed");
      else setGridBadge(anchor, "idle");
    }
  }

  // Looks up (from cache or network), then reports the result via
  // applyStatusToUI. Used both by the header button and by grid cards.
  //
  // Note: since only "done" entries are ever cached, a cache hit always
  // means "confirmed downloaded" — anything else (idle, in-progress, or a
  // failed check) always requires a fresh network round-trip.
  // `live` (the current video's own button) asks the server even when the
  // video isn't in the local list, to catch queued / downloading videos.
  // Grid cards (`live` false) rely on the local list once it has synced.
  function checkVideoStatus(url, live) {
    const cached = getCachedResult(url);
    if (cached) {
      applyStatusToUI(url, "exists", cached.status, cached.label);
      if (!live || cached.status === DONE_STATUS) return;
    } else if (!live && indexIsUsable() && !activePollers.has(url)) {
      applyStatusToUI(url, "idle");
      return;
    }

    const attemptInfo = failedAttempts.get(url) || { count: 0, nextTryAt: 0 };
    if (attemptInfo.count >= MAX_RETRIES) {
      logWarn(`Max retries (${MAX_RETRIES}) reached for ${url}. Halting checks and reporting failed state.`);
      applyStatusToUI(url, "failed");
      return;
    }

    // Cooldown guard: prevent rapid-fire retries if network/server is down
    if (Date.now() < attemptInfo.nextTryAt) {
      return;
    }

    if (inFlightChecks.has(url)) {
      return; // Request already active for this URL
    }

    inFlightChecks.add(url);
    applyStatusToUI(url, "checking");
    const { baseUrl, apiKey } = getConfig();

    log(`Checking status for ${url} (Attempt ${attemptInfo.count + 1}/${MAX_RETRIES})...`);

    GM_xmlhttpRequest({
      method: "GET",
      url: `${baseUrl}/api/external/exists?url=${encodeURIComponent(url)}`,
      headers: { "X-API-Key": apiKey },
      onload: (res) => {
        inFlightChecks.delete(url);

        if (res.status >= 200 && res.status < 300) {
          let result = false;
          try {
            const data = JSON.parse(res.responseText);
            if (data.exists) result = data;
          } catch (e) {
            logError("Failed to parse JSON response from backend:", e);
          }

          failedAttempts.delete(url); // Reset attempts on successful response

          if (result && result.status === DONE_STATUS) {
            setCachedResult(url, { exists: true, status: DONE_STATUS });
          }
          // Any other outcome (idle / pending / fetching / downloading) is
          // intentionally NOT written to the persistent cache.

          applyStatusToUI(url, result ? "exists" : "idle", result ? result.status : null, result ? result.label : null);

          if (result && ACTIVE_STATUSES.has(result.status)) {
            startStatusPolling(url);
          }
        } else {
          handleCheckError(url, `HTTP ${res.status}: ${res.statusText}`);
        }
      },
      onerror: () => {
        inFlightChecks.delete(url);
        handleCheckError(url, "Network failure or server unreachable");
      },
    });
  }

  function handleCheckError(url, reason) {
    const attemptInfo = failedAttempts.get(url) || { count: 0, nextTryAt: 0 };
    const newCount = attemptInfo.count + 1;

    logWarn(`Status check failed for ${url} (Attempt ${newCount}/${MAX_RETRIES}). Reason: ${reason}`);

    if (newCount >= MAX_RETRIES) {
      logError(`Reached limit of ${MAX_RETRIES} attempts for ${url}. Reporting failed state (not cached).`);
      // Failed checks are never persisted to the cache — only reflected in
      // the UI for this page view. `failedAttempts` (in-memory only) keeps
      // us from hammering the server again until a manual retry/cache clear.
      failedAttempts.set(url, { count: newCount, nextTryAt: attemptInfo.nextTryAt });
      applyStatusToUI(url, "failed");
    } else {
      // Add a 5 second cooldown before allowing retries again
      failedAttempts.set(url, { count: newCount, nextTryAt: Date.now() + 5000 });
      applyStatusToUI(url, "idle");
    }
  }

  // Thin wrapper kept for clarity at call sites that only care about the
  // current video page's button.
  function checkExists(url) {
    checkVideoStatus(url, true);
  }

  // =========================================================================
  // --- GRID / LISTING BADGE MODULE ---
  // Scans every video card shown on listing pages (home, tags, search,
  // filters, etc.) and stamps a small status badge on each one, using the
  // exact same cache/retry/poll infrastructure as the header button.
  // =========================================================================

  const GRID_BADGE_CLASS = "pmv-grid-badge";
  const CARD_CHECK_INTERVAL_MS = 150; // throttle: one check dispatched per tick
  // Anchors we've already enqueued this "session". Reassigned (not just
  // cleared) by clearCache() so stale WeakSet entries for removed DOM nodes
  // don't matter and every currently-visible card gets re-enqueued.
  let processedCards = new WeakSet();
  const cardCheckQueue = [];
  let cardCheckTimer = null;

  const GRID_STATE_STYLES = {
    done: { text: "✓ Downloaded", bg: "rgba(22,163,74,0.9)", color: "#fff" },
    downloading: { text: "⏳ Downloading", bg: "rgba(249,115,22,0.9)", color: "#fff" },
    pending: { text: "⏳ Queued", bg: "rgba(249,115,22,0.9)", color: "#fff" },
    fetching: { text: "⏳ Queued", bg: "rgba(249,115,22,0.9)", color: "#fff" },
    merged: { text: "⧉ Duplicate", bg: "rgba(147,51,234,0.9)", color: "#fff" },
    failed: { text: "⚠ Check failed", bg: "rgba(220,38,38,0.9)", color: "#fff" },
  };

  // Strips tracking query params (?from=home, etc.) so grid URLs match the
  // canonical URL used when a video is queued from its own page.
  function canonicalizeHref(href) {
    try {
      const u = new URL(href, window.location.origin);
      return `${u.origin}${u.pathname}`;
    } catch (e) {
      return href;
    }
  }

  // Every real video card is an <a href="/video/..."> link. On the main
  // listing grid these carry a data-video-id attribute, but the compact
  // "Recommended" / sidebar cards on a video's own page use the same href
  // pattern without that attribute — so we match on href alone. Sponsored /
  // native-ad cards are still excluded naturally: they link off-site and
  // never start with "/video/".
  function getVideoCardAnchors() {
    return Array.from(document.querySelectorAll('a[href^="/video/"]'));
  }

  function findGridAnchorsForUrl(url) {
    return getVideoCardAnchors().filter((a) => canonicalizeHref(a.getAttribute("href")) === url);
  }

  // Finds where to mount the badge inside a card: prefer the visible tag
  // pill row (so it matches the site's existing pill styling), falling back
  // to the padded content wrapper if the layout doesn't match expectations.
  function findBadgeMountPoint(cardEl) {
    const candidateRows = cardEl.querySelectorAll(".flex.items-center.gap-1\\.5.flex-wrap");
    for (const row of candidateRows) {
      if (row.style.display !== "none") {
        return row;
      }
    }
    return cardEl.querySelector(".p-4.space-y-3");
  }

  function makeGridBadgeEl() {
    const badge = document.createElement("span");
    badge.className = GRID_BADGE_CLASS;
    Object.assign(badge.style, {
      display: "inline-flex",
      alignItems: "center",
      gap: "4px",
      padding: "2px 8px",
      borderRadius: "9999px",
      fontSize: "11px",
      fontWeight: "700",
      lineHeight: "1.4",
      marginRight: "4px",
      marginBottom: "4px",
      whiteSpace: "nowrap",
      boxShadow: "0 1px 3px rgba(0,0,0,0.4)",
    });
    return badge;
  }

  function setGridBadge(cardEl, state, label) {
    let badge = cardEl.querySelector(`.${GRID_BADGE_CLASS}`);

    // idle / unknown / not-downloaded -> no badge, keeps the grid clean
    const style = GRID_STATE_STYLES[state];
    if (!style) {
      if (badge) badge.remove();
      return;
    }

    if (!badge) {
      const mount = findBadgeMountPoint(cardEl);
      if (!mount) return;
      badge = makeGridBadgeEl();
      mount.insertBefore(badge, mount.firstChild);
    }

    badge.textContent = style.text;
    badge.style.background = style.bg;
    badge.style.color = style.color;
    badge.title =
      state === "failed"
        ? "Could not verify this video's status with PMV Nexus."
        : label || "Status from PMV Nexus.";
  }

  function enqueueCardCheck(anchor) {
    const url = canonicalizeHref(anchor.getAttribute("href"));
    cardCheckQueue.push(url);
    runCardCheckQueue();
  }

  function runCardCheckQueue() {
    if (cardCheckTimer) return;
    cardCheckTimer = setInterval(() => {
      const url = cardCheckQueue.shift();
      if (!url) {
        clearInterval(cardCheckTimer);
        cardCheckTimer = null;
        return;
      }
      checkVideoStatus(url);
    }, CARD_CHECK_INTERVAL_MS);
  }

  function processGridCards() {
    const anchors = getVideoCardAnchors();
    for (const anchor of anchors) {
      if (processedCards.has(anchor)) continue;
      processedCards.add(anchor);
      enqueueCardCheck(anchor);
    }
  }

  // Debounced re-scan: DOM mutations from pagination/filtering/infinite
  // scroll often happen in bursts, so wait for things to settle first.
  let gridScanTimer = null;
  function scheduleGridScan() {
    clearTimeout(gridScanTimer);
    gridScanTimer = setTimeout(processGridCards, 300);
  }

  function setupGridObserver() {
    const observer = new MutationObserver(() => {
      scheduleGridScan();
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  // =========================================================================
  // --- VIDEO RATING + TAGS DISPLAY MODULE ---
  // Fetches the current video's full metadata straight from PMVHaven's own
  // API (same-origin, so a plain fetch() is enough — no GM_xmlhttpRequest /
  // API key needed) and uses it for two things:
  //
  //   1. The star rating, rendered as a fourth item in the views/date/
  //      duration meta row on a video's own page, right after the duration
  //      (same as before).
  //   2. The FULL tag list with vote counts (see the TAG VOTES DISPLAY
  //      MODULE below), replacing the site's own server-rendered top5Tags
  //      row.
  //
  // The video id embedded in the page URL (the 24-char hex Mongo id after
  // the last underscore in the slug, e.g.
  // ".../34-35-pmv_65d8fd1e7a5c2388f6fcd696") is used to hit
  // https://pmvhaven.com/api/videos/<id>.
  // =========================================================================

  const RATING_ROW_SELECTOR =
    ".flex.flex-wrap.items-center.gap-3.sm\\:gap-4.text-xs.sm\\:text-sm.text-gray-400";
  const RATING_ITEM_ID = "pmv-manager-rating-item";
  const VIDEO_ID_REGEX = /_([a-fA-F0-9]{24})(?:[/?#]|$)/;

  let currentRatingVideoId = null; // video id the in-flight/most recent metadata fetch is for
  let pendingRatingText = null; // resolved rating text waiting to be mounted (row may not exist yet)
  let pendingTagVotesData = null; // { videoId, tagVotes } waiting to be mounted (see TAG VOTES module)

  function getVideoIdFromUrl(url) {
    const match = url.match(VIDEO_ID_REGEX);
    return match ? match[1] : null;
  }

  function findRatingRow() {
    return document.querySelector(RATING_ROW_SELECTOR);
  }

  // Built to visually match the existing views/date/duration items exactly
  // (same wrapper classes, same icon sizing) — a filled star stands in for
  // the site's own lucide icon set.
  function buildRatingItemEl(ratingText) {
    const div = document.createElement("div");
    div.id = RATING_ITEM_ID;
    div.setAttribute("class", "flex items-center gap-1.5 whitespace-nowrap");
    div.innerHTML =
      `<svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true" role="img" class="iconify iconify--lucide icon" style="font-size: 16px;" width="1em" height="1em" viewBox="0 0 24 24">` +
      `<path fill="currentColor" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z"></path>` +
      `</svg>` +
      `<span data-v-71e78e86="">${ratingText}</span>`;
    return div;
  }

  function removeRatingItem() {
    const el = document.getElementById(RATING_ITEM_ID);
    if (el) el.remove();
  }

  // Safe to call repeatedly (e.g. from the SPA fallback poll): no-ops once
  // the item is already mounted, and does nothing if the fetch hasn't
  // resolved yet or the meta row hasn't rendered yet.
  function tryMountPendingRating() {
    if (!pendingRatingText) return;
    if (document.getElementById(RATING_ITEM_ID)) return;
    const row = findRatingRow();
    if (!row) return; // row not rendered yet — a later poll tick will retry
    row.appendChild(buildRatingItemEl(pendingRatingText));
  }

  function resetRatingDisplay() {
    currentRatingVideoId = null;
    pendingRatingText = null;
    removeRatingItem();
    pendingTagVotesData = null;
    removeRenderedTagVotes();
  }

  // Fetches (or reuses) the metadata for the video identified by pageUrl and
  // mounts the rating + full tag list once resolved.
  function loadAndDisplayRating(pageUrl) {
    const videoId = getVideoIdFromUrl(pageUrl);
    if (!videoId) {
      resetRatingDisplay();
      return;
    }

    if (videoId === currentRatingVideoId) {
      tryMountPendingRating(); // same video — just make sure it's still mounted
      tryMountPendingTags();
      return;
    }

    currentRatingVideoId = videoId;
    pendingRatingText = null;
    pendingTagVotesData = null;
    removeRatingItem(); // clear the previous video's stale rating while we fetch
    removeRenderedTagVotes();

    log(`Fetching metadata (rating + tags) for video ${videoId}...`);

    fetch(`/api/videos/${videoId}`, { credentials: "include" })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((json) => {
        if (videoId !== currentRatingVideoId) return; // navigated away before this resolved
        const data = json && json.data;
        if (!data) {
          logWarn(`No data in API response for video ${videoId}.`);
          return;
        }

        // --- Rating ---
        // Match PMVHaven's own displayed rating format (a percentage out of
        // 100), which is the Bayesian-adjusted score rather than the raw
        // /5 average. Falls back to deriving a percentage from the raw
        // /5 rating if bayesianRating isn't present in the response.
        let percent = null;
        if (typeof data.bayesianRating === "number") {
          percent = data.bayesianRating;
        } else if (typeof data.rating === "number") {
          percent = (data.rating / 5) * 100;
        }
        if (percent === null) {
          logWarn(`No rating field in API response for video ${videoId}.`);
        } else {
          pendingRatingText = `${Math.round(percent)}%`;
          tryMountPendingRating();
        }

        // --- Full tag list with vote counts ---
        if (Array.isArray(data.tagVotes) && data.tagVotes.length) {
          pendingTagVotesData = { videoId, tagVotes: data.tagVotes };
          tryMountPendingTags();
        } else {
          logWarn(`No tagVotes array in API response for video ${videoId}.`);
        }
      })
      .catch((e) => {
        logWarn(`Failed to fetch metadata for video ${videoId}:`, e);
      });
  }

  // =========================================================================
  // --- TAG VOTES DISPLAY MODULE ---
  // Replaces the site's own truncated "top 5 tags" row (rendered server-side
  // in the page HTML, driven by top5Tags) with the FULL tag list pulled from
  // the same /api/videos/<id> call above, computing each tag's net score
  // from tagVotes[].upvotes/downvotes, and adds inline up/down vote buttons.
  //
  // NOTE ON VOTING (confirmed by inspecting the site's own requests):
  // POST /api/videos/<id>/tags/vote with a JSON body of
  // { tag, voteType: "upvote" | "downvote", tagType } is the real endpoint
  // the site uses to vote on an already-attached tag — this covers BOTH
  // directions. (There's a separate endpoint, /api/videos/<id>/tags/suggest,
  // used by the "Add a New Tag" popup for brand-new tags; that one isn't
  // used here since this script only needs to vote on tags already shown.)
  //
  // The response shape for /tags/vote wasn't confirmed, so rather than
  // parse it and risk showing a wrong number, a successful vote forces a
  // re-fetch of the video's own metadata — the pill then re-renders from
  // the server's real, authoritative tagVotes counts.
  //
  // HIGHLIGHTING: whether *you* voted a given tag/direction is derived
  // purely by checking your cached PMVHaven user id (see the USER ID
  // DETECTION MODULE above) against that tag's own tagVotes[].upvotes /
  // .downvotes arrays from the API response — nothing about individual
  // votes is stored locally. Until the user id is known, no vote gets
  // highlighted (there is no per-tag/per-video local fallback).
  // =========================================================================

  const TAG_VOTE_ENDPOINT_TEMPLATE = "/api/videos/{id}/tags/vote"; // confirmed — see note above
  const DEFAULT_TAG_TYPE = "general"; // seen in the site's own request; adjust if a tag needs a different type
  const TAG_PILL_CLASS = "pmv-tag-vote-pill";
  const TAGS_RENDERED_ATTR = "pmvTagsFor";

  const tagVoteInFlight = new Set(); // `${videoId}:${tag}:${direction}` currently submitting

  // Determines which button (if any) should show as "voted" for this tag,
  // purely by checking whether our cached user id appears in this tag's own
  // upvotes/downvotes arrays from the server. Definitive and correct even
  // for votes cast before this script existed — nothing is inferred from a
  // locally remembered click. If the user id isn't known yet, we simply
  // don't highlight anything for this tag until it is (the background
  // sniffer in the USER ID DETECTION MODULE will pick it up passively).
  function getVoteHighlight(tv) {
    const userId = getCachedUserId();
    if (!userId) return null;
    if (Array.isArray(tv.upvotes) && tv.upvotes.includes(userId)) return "upvote";
    if (Array.isArray(tv.downvotes) && tv.downvotes.includes(userId)) return "downvote";
    return null;
  }

  // Finds the row that currently holds the site's own truncated tag list.
  // The "Tags" / "Music" / "Models" rows all share the same wrapper class,
  // so we match on the row whose own label starts with "Tags" rather than
  // relying on position.
  function findTagsRow() {
    const rows = document.querySelectorAll(".flex.items-center.gap-2.flex-wrap");
    for (const row of rows) {
      const label = row.querySelector("span");
      if (label && label.textContent.trim().startsWith("Tags")) {
        return row;
      }
    }
    return null;
  }

  function removeRenderedTagVotes() {
    document.querySelectorAll(`.${TAG_PILL_CLASS}`).forEach((el) => el.remove());
    const row = findTagsRow();
    if (row) delete row.dataset[TAGS_RENDERED_ATTR];
  }

  function buildTagVotePill(videoId, tv) {
    const upCount = Array.isArray(tv.upvotes) ? tv.upvotes.length : 0;
    const downCount = Array.isArray(tv.downvotes) ? tv.downvotes.length : 0;
    const highlight = getVoteHighlight(tv); // "upvote" | "downvote" | null — always derived live

    const pill = document.createElement("span");
    pill.className = TAG_PILL_CLASS;
    pill.dataset.tag = tv.tag;
    pill.title = `${upCount} up / ${downCount} down`;

    const downBtn = document.createElement("button");
    downBtn.type = "button";
    downBtn.className = "pmv-tag-downvote";
    if (highlight === "downvote") downBtn.classList.add("voted");
    downBtn.textContent = "−";
    downBtn.title = `Downvote '${tv.tag}' (${downCount})`;
    downBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      castTagVote(videoId, tv.tag, "downvote", downBtn);
    });

    const link = document.createElement("a");
    link.className = "pmv-tag-label";
    link.href = `/search?tags=${encodeURIComponent(tv.tag)}`;
    link.title = `Search for '${tv.tag}'`;
    link.textContent = `#${tv.tag} `;

    const countSpan = document.createElement("span");
    countSpan.className = "pmv-tag-count";
    countSpan.textContent = `(${upCount - downCount})`;
    link.appendChild(countSpan);

    const upBtn = document.createElement("button");
    upBtn.type = "button";
    upBtn.className = "pmv-tag-upvote";
    if (highlight === "upvote") upBtn.classList.add("voted");
    upBtn.textContent = "+";
    upBtn.title = `Upvote '${tv.tag}' (${upCount})`;
    upBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      castTagVote(videoId, tv.tag, "upvote", upBtn);
    });

    pill.appendChild(downBtn);
    pill.appendChild(link);
    pill.appendChild(upBtn);
    return pill;
  }

  // Renders the full tag list into the site's own Tags row, replacing
  // whatever tag pills the site rendered server-side (its own <a> tags,
  // limited to top5Tags). Leaves the row's own "Tags" label span in place.
  // Returns true once mounted for this videoId, false if the row isn't in
  // the DOM yet (caller should retry later).
  function renderTagVotesRow(videoId, tagVotes) {
    const row = findTagsRow();
    if (!row) return false;

    if (row.dataset[TAGS_RENDERED_ATTR] === videoId) return true; // already current

    const label = row.querySelector("span");
    Array.from(row.children).forEach((child) => {
      if (child !== label) child.remove();
    });

    tagVotes
      .slice()
      .sort((a, b) => {
        const netA = (a.upvotes ? a.upvotes.length : 0) - (a.downvotes ? a.downvotes.length : 0);
        const netB = (b.upvotes ? b.upvotes.length : 0) - (b.downvotes ? b.downvotes.length : 0);
        return netB - netA;
      })
      .forEach((tv) => row.appendChild(buildTagVotePill(videoId, tv)));

    row.dataset[TAGS_RENDERED_ATTR] = videoId;
    return true;
  }

  // Safe to call repeatedly from the SPA fallback poll: no-ops once the
  // pending data has already been mounted, and does nothing if the fetch
  // hasn't resolved yet or the tags row hasn't rendered yet.
  function tryMountPendingTags() {
    if (!pendingTagVotesData) return;
    renderTagVotesRow(pendingTagVotesData.videoId, pendingTagVotesData.tagVotes);
  }

  // Calls the confirmed /tags/vote endpoint for either direction, then
  // forces a re-fetch of the video's own metadata so the pill's score AND
  // highlight are redrawn from the server's real, authoritative tagVotes
  // arrays rather than anything guessed or remembered locally.
  function castTagVote(videoId, tag, voteType, btnEl) {
    const key = `${videoId}:${tag}:${voteType}`;
    if (tagVoteInFlight.has(key)) return;
    tagVoteInFlight.add(key);
    if (btnEl) btnEl.disabled = true;

    const url = TAG_VOTE_ENDPOINT_TEMPLATE.replace("{id}", videoId);

    fetch(url, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tag, voteType, tagType: DEFAULT_TAG_TYPE }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        log(`Tag vote submitted: ${voteType} on '${tag}' for ${videoId}. Refreshing tag counts...`);
        // Force loadAndDisplayRating to treat this as a fresh video so it
        // re-fetches and re-renders the tags row with real server counts
        // and highlight state — nothing is written to local storage here.
        currentRatingVideoId = null;
        pendingTagVotesData = null;
        loadAndDisplayRating(window.location.href);
      })
      .catch((e) => {
        logWarn(`Tag vote failed (${voteType}) for '${tag}' on ${videoId}:`, e);
        showToast(`Could not submit ${voteType} for #${tag} — see console for details`, true);
      })
      .finally(() => {
        tagVoteInFlight.delete(key);
        if (btnEl) btnEl.disabled = false;
      });
  }

  // =========================================================================
  // --- VIDEO VOLUME MODULE ---
  // The site's player always starts a fresh video at 100% volume. Since the
  // whole page is client-rendered, the <video> element may not exist yet
  // when we first look for it, and on SPA navigation the same wrapper often
  // gets reused with a brand new src (a fresh blob: URL per video) rather
  // than the element being recreated. To handle both cases without a
  // separate observer, we lean on the same fallback-poll pattern already
  // used for the rating widget: every tick, if we're on a video page, check
  // whether the current <video>'s source is one we've already applied our
  // default volume to. If it's a new source and metadata isn't loaded yet
  // (readyState 0), wait for the video's own 'loadedmetadata' event — which
  // fires once per source and is the earliest safe point to set volume
  // without it being clobbered by the player's own setup logic — then set
  // it. Once applied for a given source we leave the volume alone, so a
  // manual adjustment by the user during playback of that same video is
  // never overridden.
  // =========================================================================

  const DEFAULT_VOLUME = 0.04;
  const VIDEO_SELECTOR = "video[data-vc-page-audio-managed]";

  let volumeAppliedForSrc = null;

  function getCurrentVideoSrcKey(video) {
    // Prefer currentSrc (the resolved URL actually playing, e.g. the
    // blob: URL); fall back to the src attribute/property if currentSrc
    // isn't populated yet.
    return video.currentSrc || video.getAttribute("src") || video.src || null;
  }

  function applyDefaultVolume(video, srcKey) {
    video.volume = DEFAULT_VOLUME;
    volumeAppliedForSrc = srcKey;
    log(`Set video volume to ${Math.round(DEFAULT_VOLUME * 100)}% for ${srcKey}`);
  }

  function applyDefaultVolumeIfNeeded() {
    const video = document.querySelector(VIDEO_SELECTOR);
    if (!video) return; // player not mounted yet — a later poll tick will retry

    const srcKey = getCurrentVideoSrcKey(video);
    if (!srcKey || srcKey === volumeAppliedForSrc) return; // no source yet, or already handled

    if (video.readyState >= 1) {
      // HAVE_METADATA or later — metadata for this source has already
      // loaded, so it's safe to set the volume right now.
      applyDefaultVolume(video, srcKey);
    } else {
      // Wait for metadata to load for THIS source, then double-check the
      // source hasn't already changed again by the time the event fires
      // (e.g. rapid back-to-back navigation).
      video.addEventListener(
        "loadedmetadata",
        () => {
          const latestKey = getCurrentVideoSrcKey(video);
          if (latestKey === srcKey) {
            applyDefaultVolume(video, srcKey);
          }
        },
        { once: true }
      );
    }
  }

  function resetVolumeTracking() {
    volumeAppliedForSrc = null;
  }

  // =========================================================================
  // --- LIBRARY ACTION MODULE (header button click) ---
  // =========================================================================

  function onButtonClick() {
    const btn = document.getElementById(BUTTON_ID);
    const state = btn && btn.dataset.pmvState;

    if (state === "exists" || state === "failed") {
      const status = btn.dataset.pmvStatus;
      const message = CONFIRM_MESSAGES[status] || "Send this video to PMV Nexus anyway?";
      if (!confirm(message)) return;
    }

    sendToLibrary();
  }

  function sendToLibrary() {
    const { baseUrl, apiKey } = getConfig();
    const url = window.location.href;

    log(`Sending video URL to library: ${url}`);

    GM_xmlhttpRequest({
      method: "POST",
      url: `${baseUrl}/api/external/library`,
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": apiKey,
      },
      data: JSON.stringify({ url }),
      onload: (res) => {
        if (res.status >= 200 && res.status < 300) {
          log(`Successfully added video to library: ${url}`);
          showToast("Added to download library");
          // Freshly queued, so it's "pending" — not cached (only "done" is).
          applyStatusToUI(url, "exists", "pending");

          startStatusPolling(url);
        } else {
          logError(`Library submission failed (Status ${res.status}): ${res.responseText}`);
          showToast(`Failed: ${res.status} ${res.responseText.slice(0, 120)}`, true);
        }
      },
      onerror: (err) => {
        logError("Network error sending to library:", err);
        showToast("Could not reach PMV Nexus", true);
      },
    });
  }

  function showToast(message, isError) {
    const el = document.createElement("div");
    el.textContent = message;
    Object.assign(el.style, {
      position: "fixed",
      bottom: "24px",
      left: "50%",
      transform: "translateX(-50%)",
      background: isError ? "#dc2626" : "#f97316",
      color: "#fff",
      padding: "10px 18px",
      borderRadius: "8px",
      fontSize: "14px",
      zIndex: 999999,
      boxShadow: "0 10px 25px rgba(0,0,0,0.4)",
    });
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3000);
  }

  // =========================================================================
  // --- HEADER MOUNTING MODULE ---
  // Injects both controls directly into the site's own top bar so they sit
  // alongside the search / upload / settings / notification icons, using the
  // site's own Tailwind utility classes so they inherit the theme for free.
  // The top bar persists across the whole SPA, so we mount once and just
  // toggle visibility of the download button per-route from then on. If the
  // framework ever tears down and rebuilds the header, ensureToolbarButtons()
  // transparently re-mounts on the next fallback tick (see onUrlChange).
  // =========================================================================

  // The site renders a separate desktop/mobile header pair (only one of
  // which is visible at a time via responsive CSS), so document.querySelector
  // can hand back a hidden copy. Walk all matches and use the first one that
  // is actually on screen.
  function getVisibleSearchTrigger() {
    const candidates = document.querySelectorAll(".search-trigger");
    for (const el of candidates) {
      if (el.offsetParent !== null) return el;
    }
    return null;
  }

  // =========================================================================
  // --- GLOBAL STYLES (pulsing glow for queued/downloading icon states) ---
  // =========================================================================

  const STYLE_TAG_ID = "pmv-manager-styles";

  function injectGlobalStyles() {
    if (document.getElementById(STYLE_TAG_ID)) return;

    const style = document.createElement("style");
    style.id = STYLE_TAG_ID;
    style.textContent = `
      @keyframes pmv-pulse-yellow {
        0%, 100% { filter: drop-shadow(0 0 2px rgba(250, 204, 21, 0.5)); }
        50% { filter: drop-shadow(0 0 7px rgba(250, 204, 21, 1)); }
      }
      @keyframes pmv-pulse-pink {
        0%, 100% { filter: drop-shadow(0 0 2px rgba(244, 114, 182, 0.5)); }
        50% { filter: drop-shadow(0 0 7px rgba(244, 114, 182, 1)); }
      }
      .pmv-glow-yellow { animation: pmv-pulse-yellow 1.4s ease-in-out infinite; }
      /* "Duplicate" colours (v2.16): the site's own CSS may not ship these utility classes. */
      #pmv-manager-add-btn.text-purple-400 { color: #c084fc; }
      .pmv-status-dot.bg-purple-500 { background-color: #a855f7; }
      .pmv-glow-pink { animation: pmv-pulse-pink 1.4s ease-in-out infinite; }

      /* Split minus / tag / plus vote pill — orange tag label matches the
         site's own tag-pill treatment; the vote buttons use the same
         neutral #2A2A2A / #3A3A3A the site uses for its own icon buttons,
         with a colored hover hint instead of a solid red/green fill. */
      .pmv-tag-vote-pill {
        display: inline-flex;
        align-items: stretch;
        border-radius: 9999px;
        overflow: hidden;
        border: 1px solid rgba(249, 115, 22, 0.3);
        margin: 0 5px 5px 0;
        vertical-align: middle;
      }
      .pmv-tag-vote-pill .pmv-tag-label {
        display: flex;
        align-items: center;
        padding: 3px 10px;
        background: linear-gradient(135deg, rgba(249, 115, 22, 0.15), rgba(239, 68, 68, 0.15));
        color: #fdba74;
        font-weight: 600;
        font-size: 11px;
        text-decoration: none;
        white-space: nowrap;
      }
      .pmv-tag-vote-pill .pmv-tag-label:hover {
        background: linear-gradient(135deg, rgba(249, 115, 22, 0.25), rgba(239, 68, 68, 0.25));
      }
      .pmv-tag-vote-pill .pmv-tag-count { opacity: 0.7; margin-left: 3px; }
      .pmv-tag-vote-pill .pmv-tag-downvote,
      .pmv-tag-vote-pill .pmv-tag-upvote {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 20px;
        border: none;
        cursor: pointer;
        font-weight: 700;
        font-size: 12px;
        line-height: 1;
        padding: 0;
        background: #2a2a2a;
        color: #9ca3af;
      }
      .pmv-tag-vote-pill .pmv-tag-downvote { border-right: 1px solid rgba(249, 115, 22, 0.25); }
      .pmv-tag-vote-pill .pmv-tag-downvote:hover { background: #3a3a3a; color: #f87171; }
      .pmv-tag-vote-pill .pmv-tag-downvote.voted { background: rgba(239, 68, 68, 0.35); color: #f87171; }
      .pmv-tag-vote-pill .pmv-tag-downvote.voted:hover { background: rgba(239, 68, 68, 0.5); }
      .pmv-tag-vote-pill .pmv-tag-upvote { border-left: 1px solid rgba(249, 115, 22, 0.25); }
      .pmv-tag-vote-pill .pmv-tag-upvote:hover { background: #3a3a3a; color: #4ade80; }
      .pmv-tag-vote-pill .pmv-tag-upvote.voted { background: rgba(34, 197, 94, 0.35); color: #4ade80; }
      .pmv-tag-vote-pill .pmv-tag-upvote.voted:hover { background: rgba(34, 197, 94, 0.5); }
      .pmv-tag-vote-pill button:disabled { opacity: 0.4; cursor: wait; }
    `;
    document.head.appendChild(style);
  }

  function findToolbarContainer() {
    // Anchor on the search icon: it's the leftmost control in the row, has a
    // stable dedicated class, and (unlike the notification bell) is always
    // present regardless of unread state.
    const search = getVisibleSearchTrigger();
    if (search && search.parentElement) return { toolbar: search.parentElement, anchor: search };
    return null;
  }

  function buildIconButton({ id, title, colorClass, svgInner }) {
    const btn = document.createElement("button");
    btn.id = id;
    btn.type = "button";
    btn.title = title;
    // Slightly larger padding and a faint persistent background (vs. the
    // site's own icon buttons, which are only tinted on hover) so ours read
    // as visually distinct at a glance, not just another native control.
    btn.className = `p-2.5 rounded-lg bg-white/5 hover:bg-white/15 focus-ring transition-all duration-200 relative ${colorClass}`;
    btn.innerHTML =
      `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" ` +
      `stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" style="font-size:24px;" width="1em" height="1em">` +
      `${svgInner}</svg>` +
      `<span class="${STATUS_DOT_CLASS}" style="display:none;"></span>`;
    return btn;
  }

  function ensureToolbarButtons() {
    if (document.getElementById(TOOLBAR_WRAP_ID)) return; // already mounted, nothing to do

    const located = findToolbarContainer();
    if (!located) return; // header not ready yet — a later fallback tick will retry

    log("Mounting PMV Nexus controls into the site's top bar.");

    const wrap = document.createElement("div");
    wrap.id = TOOLBAR_WRAP_ID;
    wrap.className = "flex items-center gap-1";

    const downloadBtn = buildIconButton({
      id: BUTTON_ID,
      title: STATUS_TITLES.idle,
      colorClass: "text-orange-400",
      svgInner:
        `<polyline points="7 10 12 15 17 10"></polyline>` +
        `<line x1="12" y1="15" x2="12" y2="3"></line>` +
        `<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>`,
    });
    downloadBtn.classList.add("hidden"); // only shown on video pages, see syncButtonVisibility()
    downloadBtn.addEventListener("click", onButtonClick);

    const statusBtn = buildIconButton({
      id: BACKEND_STATUS_ID,
      title: BACKEND_STATUS_STYLES.unknown.title,
      colorClass: BACKEND_STATUS_STYLES.unknown.color,
      svgInner: `<path d="M22 12h-4l-3 9L9 3l-3 9H2"></path>`, // pulse/heartbeat icon
    });
    statusBtn.addEventListener("click", onBackendStatusClick);

    const clearBtn = buildIconButton({
      id: CLEAR_CACHE_BUTTON_ID,
      title: "Clear cached download status and re-check",
      colorClass: "text-gray-400",
      svgInner:
        `<path d="M3 6h18"></path>` +
        `<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"></path>` +
        `<path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>` +
        `<line x1="10" y1="11" x2="10" y2="17"></line>` +
        `<line x1="14" y1="11" x2="14" y2="17"></line>`,
    });
    clearBtn.addEventListener("click", () => {
      if (
        !confirm("Clear all cached download status info? This forces fresh checks against PMV Nexus.")
      ) {
        return;
      }
      clearCache();
    });

    wrap.appendChild(downloadBtn);
    wrap.appendChild(statusBtn);
    wrap.appendChild(clearBtn);

    if (located.anchor) {
      located.toolbar.insertBefore(wrap, located.anchor);
    } else {
      located.toolbar.appendChild(wrap);
    }

    // If we're re-mounting after the framework wiped the header out from
    // under us, the fresh buttons start neutral — refresh them immediately
    // so they don't silently show a stale state.
    if (isVideoPage()) {
      checkExists(window.location.href);
    }
    applyBackendStatusToIcon(readBackendStatus());
    refreshBackendStatus(false);
  }

  function syncButtonVisibility() {
    const btn = document.getElementById(BUTTON_ID);
    if (!btn) return;
    if (isVideoPage()) {
      btn.classList.remove("hidden");
    } else {
      btn.classList.add("hidden");
    }
  }

  // The whole site is client-rendered, so the header (and our anchor icon)
  // may not exist yet at document-idle. Watch the DOM and mount the instant
  // it shows up, rather than waiting on the 500ms fallback poll. Disconnects
  // itself once mounting succeeds — ongoing resilience (in case the
  // framework later tears the header down and rebuilds it) is still handled
  // by ensureToolbarButtons() being re-called from the fallback poll.
  function watchForToolbarMount() {
    if (document.getElementById(TOOLBAR_WRAP_ID)) return;

    const observer = new MutationObserver(() => {
      ensureToolbarButtons();
      syncButtonVisibility();
      if (document.getElementById(TOOLBAR_WRAP_ID)) {
        observer.disconnect();
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  // =========================================================================
  // --- PAGE ROUTING & SPA MONITORING ---
  // =========================================================================

  function isVideoPage() {
    return /^\/video\//.test(window.location.pathname);
  }

  let lastKnownHref = location.href;

  function cleanUrl() {
    const href = window.location.href;
    let cutIdx = href.indexOf("?from=");
    if (cutIdx === -1) cutIdx = href.indexOf("&from=");

    if (cutIdx !== -1) {
      const cleaned = href.substring(0, cutIdx);
      log(`Cleaning tracking params from URL: ${href} -> ${cleaned}`);
      history.replaceState(history.state, "", cleaned);
      return true;
    }
    return false;
  }

  function onUrlChange() {
    if (location.href === lastKnownHref) {
      // Nothing actually navigated — this fires every 500ms as a fallback
      // safety net. We only use it to keep the header controls mounted and
      // visible; we deliberately do NOT re-trigger a status check here, or
      // we'd hammer the server (and flicker the button) continuously. The
      // rating item, the tag list, and the default-volume check are all
      // cheap DOM-mount retries (no network call for volume; the rating +
      // tags fetch is already deduped by video id), so it's safe to keep
      // nudging them here in case the player/meta row rendered late.
      ensureToolbarButtons();
      syncButtonVisibility();
      scheduleGridScan();
      if (isVideoPage()) {
        tryMountPendingRating();
        tryMountPendingTags();
        applyDefaultVolumeIfNeeded();
      }
      return;
    }

    log(`Navigation detected: ${lastKnownHref} -> ${location.href}`);
    lastKnownHref = location.href;

    cleanUrl();
    setTimeout(() => {
      ensureToolbarButtons();
      syncButtonVisibility();
      if (isVideoPage()) {
        checkExists(window.location.href);
        loadAndDisplayRating(window.location.href);
        applyDefaultVolumeIfNeeded();
      } else {
        resetRatingDisplay();
        resetVolumeTracking();
      }
      scheduleGridScan();
    }, 150);
  }

  // SPA Navigation Patching
  const _pushState = history.pushState;
  const _replaceState = history.replaceState;

  history.pushState = function (...args) {
    _pushState.apply(this, args);
    window.dispatchEvent(new Event("pmv-manager:locationchange"));
  };
  history.replaceState = function (...args) {
    _replaceState.apply(this, args);
    window.dispatchEvent(new Event("pmv-manager:locationchange"));
  };
  window.addEventListener("popstate", () => {
    window.dispatchEvent(new Event("pmv-manager:locationchange"));
  });

  window.addEventListener("pmv-manager:locationchange", onUrlChange);

  // Polling fallback for edge cases (also doubles as our header re-mount check)
  setInterval(onUrlChange, 500);

  // Backend status: local tick just decides whether THIS tab's cached view
  // of the shared value is stale enough to warrant a real ping (see
  // refreshBackendStatus). Cross-tab pushes via GM_addValueChangeListener
  // keep every tab's icon current in between ticks.
  setInterval(() => refreshBackendStatus(false), BACKEND_STATUS_LOCAL_TICK_MS);

  // Menu command register & initialization
  GM_registerMenuCommand("Send this video to PMV Nexus", sendToLibrary);
  GM_registerMenuCommand("Sync Downloaded List Now", () => syncIndex(true));
  GM_registerMenuCommand("Clear Download Status Cache", clearCache);
  GM_registerMenuCommand("Check Backend Status Now", () => refreshBackendStatus(true));
  // v2.18: the address and key were only asked once; change them here (e.g. the app's port changed).
  GM_registerMenuCommand("Change PMV Nexus Address / API Key", () => {
    const { baseUrl, apiKey } = getConfig();
    const newUrl = prompt("PMV Nexus URL (e.g. http://localhost:8676):", baseUrl);
    if (newUrl === null) return;
    const newKey = prompt("PMV Nexus API key (Server settings -> Credentials -> External API):", apiKey);
    if (newKey === null) return;
    GM_setValue("pmv_manager_base_url", newUrl.trim().replace(/\/+$/, "") || DEFAULT_BASE_URL);
    GM_setValue("pmv_manager_api_key", newKey.trim());
    showToast("Saved - checking PMV Nexus...");
    refreshBackendStatus(true);
  });
  GM_registerMenuCommand("Toggle Verbose User-ID Sniffer Logging", () => {
    userIdSnifferVerbose = !userIdSnifferVerbose;
    showToast(`User-ID sniffer verbose logging ${userIdSnifferVerbose ? "ON" : "OFF"} (check console)`);
  });

  // The fetch/XHR sniffer above is installed synchronously as soon as this
  // script runs (document-start), so it's already listening well before any
  // of this DOM-touching setup happens. Everything below, though, genuinely
  // needs a real document.head/document.body to attach to, which isn't
  // guaranteed yet at document-start — so it's deferred until the DOM is
  // actually parseable.
  function initUI() {
    log("PMV Nexus userscript initialized.");
    cleanUrl();
    injectGlobalStyles();
    ensureToolbarButtons();
    syncButtonVisibility();
    watchForToolbarMount();
    setupBackendStatusSync();
    setupIndexSync();
    if (GM_getValue(LEGACY_CACHE_KEY, "")) GM_setValue(LEGACY_CACHE_KEY, ""); // replaced by the downloaded list
    syncIndex(false); // once a day (whichever tab gets here first)
    if (isVideoPage()) {
      checkExists(window.location.href);
      loadAndDisplayRating(window.location.href);
      applyDefaultVolumeIfNeeded();
    }
    setupGridObserver();
    processGridCards();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initUI);
  } else {
    initUI();
  }
})();