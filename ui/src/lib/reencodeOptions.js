// Re-encode dialog (2.22.0): the server's settings (GET /api/reencode/defaults) as an
// editable state, and the request body with only what was changed -- the rest
// follows Server settings when the re-encode runs.
import { api } from "./api.js";

export const AV1_PRESETS = Array.from({ length: 14 }, (_, i) => ({
  value: String(i), label: i === 0 ? "0 — slowest, smallest" : i === 13 ? "13 — fastest" : i === 8 ? "8 — balanced" : String(i),
}));
export const CODEC_INFO = {
  hevc: { label: "HEVC (H.265)", help: "Plays almost everywhere (TVs, phones, browsers).", crf: [0, 51], crfHelp: "22–26 is typical" },
  av1: { label: "AV1", help: "Smaller at the same quality; slower, and some older devices can't play it.", crf: [1, 63], crfHelp: "28–35 is typical" },
};
const TOGGLES = ["copy_audio", "downscale", "remove_chapters", "keep_original_file"];

export async function loadReencodeDefaults() {
  return api("/api/reencode/defaults");
}

/** Editable state from the server's settings; `exclude` = the codec the video already is in. */
export function reencodeState(d, exclude = null) {
  const usable = (c) => c !== exclude && d.encoders?.[c] !== false;
  const codec = usable(d.codec) ? d.codec : ["hevc", "av1"].find(usable) || d.codec;
  return {
    codec,
    hevc: { ...d.hevc }, av1: { ...d.av1 },
    ...Object.fromEntries(TOGGLES.map((k) => [k, d[k]])),
  };
}

/** {codec, options?} for the request: options only where they differ from the server's. */
export function reencodeBody(state, d) {
  const options = {};
  const c = state.codec;
  if (Number(state[c].crf) !== Number(d[c].crf)) options.crf = Number(state[c].crf);
  if (String(state[c].preset) !== String(d[c].preset)) options.preset = String(state[c].preset);
  for (const k of TOGGLES) if (state[k] !== d[k]) options[k] = state[k];
  return Object.keys(options).length ? { codec: c, options } : { codec: c };
}

export function isChanged(state, d) {
  return !!reencodeBody(state, d).options;
}
