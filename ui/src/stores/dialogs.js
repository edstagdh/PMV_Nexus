// One dialog at a time (rename, thumbnail, share, remove), opened from any
// page; rendered by components/DialogHost.vue.
import { reactive } from "vue";

export const dialog = reactive({ name: null, item: null, options: {} });
export function openDialog(name, item, options = {}) { Object.assign(dialog, { name, item, options }); }
export function closeDialog() { Object.assign(dialog, { name: null, item: null, options: {} }); }
