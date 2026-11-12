// Panic keys (2.24.0, My settings -> Video page): pressing the chosen keys together
// stops all playback and switches to an empty page. Keys are physical keys
// (KeyboardEvent.code), so the keyboard layout doesn't matter.
//
// The chosen keys belong to the app (2.24.3), like YouTube's shortcuts: their
// keydown is claimed (preventDefault), so the browser doesn't act on them --
// e.g. Firefox's "search as you type" no longer opens and swallows the second
// key. Only when no other modifier is held (Ctrl + A, Ctrl + F... still reach the
// browser) and not while typing in a text field. Keys the browser reserves
// (Ctrl + W / T / N...) can't be claimed by any site.
// While typing in a text field it only reacts when the keys include Ctrl / Alt / Meta,
// so typing "ad" quickly can't set it off.
const MODIFIERS = /^(Control|Alt|Meta|OS)(Left|Right)?$/;
const MOD_FLAGS = { ctrlKey: /^Control/, altKey: /^Alt/, shiftKey: /^Shift/, metaKey: /^(Meta|OS)/ };
/** No modifier is held that isn't one of the panic keys (or this key itself). */
const onlyOurModifiers = (e, keys) => Object.entries(MOD_FLAGS)
  .every(([flag, re]) => !e[flag] || re.test(e.code) || keys.some((k) => re.test(k)));
const isTyping = (el) => !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)
  && !["checkbox", "radio", "range", "button", "submit"].includes(el.type));

// Off while new keys are being recorded in My settings (they may include the current ones).
let paused = false;
export function pausePanic(on) { paused = !!on; }

export function installPanic(getKeys, onPanic) {
  const down = new Set();
  const onDown = (e) => {
    down.add(e.code);
    if (paused) return;
    const keys = getKeys();
    if (!keys?.length) return;
    const typing = isTyping(document.activeElement) && !keys.some((k) => MODIFIERS.test(k));
    if (!typing && keys.includes(e.code) && onlyOurModifiers(e, keys)) e.preventDefault(); // the app's key
    if (!keys.every((k) => down.has(k))) return;
    if (typing) return;
    down.clear();
    onPanic();
  };
  const onUp = (e) => down.delete(e.code);
  const reset = () => down.clear(); // keys released while the window wasn't looking
  window.addEventListener("keydown", onDown, true);
  window.addEventListener("keyup", onUp, true);
  window.addEventListener("blur", reset);
  document.addEventListener("visibilitychange", reset);
  return () => {
    window.removeEventListener("keydown", onDown, true);
    window.removeEventListener("keyup", onUp, true);
    window.removeEventListener("blur", reset);
    document.removeEventListener("visibilitychange", reset);
  };
}

// The player's own shortcuts: a panic key on one of these replaces it.
const PLAYER_KEYS = {
  Space: "play / pause", KeyK: "play / pause", KeyJ: "skip back", KeyL: "skip forward", KeyF: "fullscreen",
  KeyM: "mute", KeyC: "captions", KeyI: "picture-in-picture", ArrowLeft: "skip back", ArrowRight: "skip forward",
  ArrowUp: "volume up", ArrowDown: "volume down", Home: "go to the start", End: "go to the end", Comma: "slower", Period: "faster",
};
/** [{label, does}] for chosen keys that are also player shortcuts. */
export function playerKeyClashes(keys) {
  return keys.filter((k) => PLAYER_KEYS[k.code] || /^Digit\d$/.test(k.code))
    .map((k) => ({ label: k.label, does: PLAYER_KEYS[k.code] || "jump to that tenth of the video" }));
}

/** "Ctrl + A", "Space"... for showing a key while recording. */
export function keyLabel(e) {
  if (MODIFIERS.test(e.code) || e.key === "Shift") return e.key === "Meta" ? "⌘ / Win" : e.key === "Control" ? "Ctrl" : e.key;
  if (e.code === "Space") return "Space";
  if (/^Key[A-Z]$/.test(e.code)) return e.code.slice(3);
  if (/^Digit\d$/.test(e.code)) return e.code.slice(5);
  return e.key.length === 1 ? e.key.toUpperCase() : e.key;
}
