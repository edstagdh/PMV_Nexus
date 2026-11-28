// 3.12.0: the app is upgrading its data (backend/upgrades). While long steps run, the API answers
// 503 {upgrading: true}; App.vue shows components/UpgradingScreen.vue and reloads when it's done.
import { reactive } from "vue";

export const upgrade = reactive({ active: false, current: null, done: 0, total: 0, step_done: null, step_total: null, error: null });
let timer = null;

export async function checkUpgrade() {
  let s = null;
  try {
    const resp = await fetch("/api/system/upgrade", { credentials: "same-origin", cache: "no-store" });
    if (resp.ok) s = await resp.json();
  } catch { /* the server is restarting: try again */ }
  const was = upgrade.active;
  if (s) Object.assign(upgrade, s);
  clearTimeout(timer);
  if (upgrade.active || (!s && was)) timer = setTimeout(checkUpgrade, 2000);
  else if (was) window.location.reload();   // done: start the app again with the upgraded data
}

/** A 503 "upgrading" from any request (lib/api.js). */
export function upgradingSeen() {
  if (!upgrade.active) { upgrade.active = true; checkUpgrade(); }
}
