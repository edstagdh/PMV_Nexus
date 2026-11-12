// Live updates from /api/events (Server-Sent Events). Keeps the library
// summary (counts, filter options) current and tells subscribers which
// library items changed, so pages refresh only when something they show
// actually changed -- no polling.
import { reactive } from "vue";

export const live = reactive({
  connected: false,
  summary: null,
  version: null,          // server version when this tab loaded
  serverVersion: null,    // latest version reported by the server
  get updateAvailable() { return !!(this.version && this.serverVersion && this.serverVersion !== this.version); },
});

const listeners = new Set();
let source = null;
let retryTimer = null;

/** fn(changedIds: string[] | null) -- null means "too much changed, refresh everything". */
export function onLibraryChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit(changed) {
  listeners.forEach((fn) => { try { fn(changed); } catch (e) { console.error(e); } });
}

// One stream for all open tabs: browsers allow only ~6 connections to the app
// at a time, and every tab holding its own never-ending stream leaves fewer
// for videos, previews and thumbnails. One tab (the "leader", picked with the
// Web Locks API) keeps the stream and relays it to the others over a
// BroadcastChannel. Where those APIs are missing (plain http on a LAN
// address isn't a "secure context") every tab simply opens its own stream.
const channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("pmv-live") : null;
const canShare = !!(channel && navigator.locks);
let active = false;
let isLeader = false;
let releaseLeadership = null;
let lockAbort = null;
let lastHello = null;

function onHello(data) {
  live.connected = true;
  live.summary = data.summary;
  live.serverVersion = data.version;
  if (!live.version) live.version = data.version;
  else emit(null); // reconnected (e.g. server restarted): refresh everything
}
function onLibrary(data) {
  live.summary = data.summary;
  emit(data.changed);
}
function relay(type, data) { if (isLeader) channel.postMessage({ type, data }); }

function openSource() {
  source = new EventSource("/api/events");
  source.addEventListener("hello", (e) => {
    lastHello = JSON.parse(e.data);
    onHello(lastHello);
    relay("hello", lastHello);
  });
  source.addEventListener("library", (e) => {
    const data = JSON.parse(e.data);
    if (lastHello) lastHello.summary = data.summary;
    onLibrary(data);
    relay("library", data);
  });
  source.onerror = () => {
    live.connected = false;
    relay("down", null);
    // EventSource retries by itself unless the server refused the stream
    // (e.g. signed out); in that case try again a bit later.
    if (source && source.readyState === EventSource.CLOSED) {
      source = null;
      clearTimeout(retryTimer);
      retryTimer = setTimeout(() => { if (active && (isLeader || !canShare)) openSource(); }, 5000);
    }
  };
}

function onChannelMessage(e) {
  const { type, data } = e.data || {};
  if (type === "need-state") { if (isLeader && lastHello) channel.postMessage({ type: "state", data: lastHello }); return; }
  if (isLeader) return;
  if (type === "state") {
    live.connected = true;
    live.summary = data.summary;
    live.serverVersion = data.version;
    if (!live.version) live.version = data.version;
  } else if (type === "hello") onHello(data);
  else if (type === "library") onLibrary(data);
  else if (type === "down") live.connected = false;
}

export function connectLive() {
  if (active) return;
  active = true;
  if (!canShare) { openSource(); return; }
  channel.onmessage = onChannelMessage;
  lockAbort = new AbortController();
  navigator.locks.request("pmv-live-leader", { signal: lockAbort.signal }, () => new Promise((resolve) => {
    isLeader = true;
    releaseLeadership = resolve;
    openSource();
  })).catch(() => { /* aborted: signed out before becoming leader */ });
  channel.postMessage({ type: "need-state" });
}

export function disconnectLive() {
  active = false;
  clearTimeout(retryTimer);
  if (source) source.close();
  source = null;
  live.connected = false;
  lastHello = null;
  if (canShare) {
    channel.onmessage = null;
    lockAbort?.abort();
    isLeader = false;
    releaseLeadership?.();
    releaseLeadership = null;
  }
}
