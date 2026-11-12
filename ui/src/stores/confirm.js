// The app's own "Are you sure?" window (2.23.0), instead of the browser's confirm():
// browsers offer "don't ask again" on theirs, after which confirm() answers by
// itself without showing anything. Usage:
//   if (!(await askConfirm("Delete it?", { ok: "Delete", danger: true }))) return;
// askPassword() is the same window with a password field (2.25.0): the password, or null.
import { reactive } from "vue";

export const confirmState = reactive({
  open: false, title: "", message: "", ok: "OK", cancel: "Cancel", danger: false, resolve: null,
  input: false, value: "", error: "",
});

export function askConfirm(message, { title = "Are you sure?", ok = "OK", cancel = "Cancel", danger = false } = {}) {
  return new Promise((resolve) => {
    confirmState.resolve?.(false); // only one at a time: an older one counts as "Cancel"
    Object.assign(confirmState, { open: true, title, message, ok, cancel, danger, resolve, input: false, value: "", error: "" });
  });
}

export function askPassword(message, { title = "Password", ok = "OK", cancel = "Cancel", error = "" } = {}) {
  return new Promise((resolve) => {
    confirmState.resolve?.(false);
    Object.assign(confirmState, { open: true, title, message, ok, cancel, danger: false, input: true, value: "", error,
      resolve: (yes) => resolve(yes && confirmState.value ? confirmState.value : null) });
  });
}

export function settleConfirm(answer) {
  const resolve = confirmState.resolve;
  confirmState.open = false;
  confirmState.resolve = null;
  resolve?.(!!answer);
  confirmState.value = ""; // a password typed into it isn't kept
}
