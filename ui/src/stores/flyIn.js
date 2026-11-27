// A picture that flies into the video player of the page being opened (3.9.1: the wall's "Pick
// for me" ends with the chosen video's thumbnail growing into the player). components/FlyIn.vue
// shows it: it starts at `rect` (screen pixels), waits for the video page's player, moves onto
// it in `ms`, then fades. Nothing waits on it: the page opens underneath as usual.
import { reactive } from "vue";

export const flyIn = reactive({ src: null, rect: null, ms: 600, id: 0 });

export function startFlyIn(src, rect, ms) {
  flyIn.src = src;
  flyIn.rect = rect;
  flyIn.ms = Math.max(150, ms || 600);
  flyIn.id += 1;
}

export function endFlyIn() { flyIn.src = null; flyIn.rect = null; }
