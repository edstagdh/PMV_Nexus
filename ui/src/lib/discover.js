import { api } from "./api.js";
import { toast } from "../stores/toast.js";
import { router } from "../router.js";

let busy = false;
/** Opens a random video this user hasn't been shown yet (server keeps the history). */
export async function discover() {
  if (busy) return;
  busy = true;
  try {
    const pick = await api("/api/library/discover", { method: "POST" });
    if (pick.reset) toast("You've seen every video — starting a new round");
    router.push({ name: "video", params: { id: pick.id } });
  } catch (err) {
    toast(err.message || "Could not pick a video", { type: "error" });
  } finally {
    busy = false;
  }
}
