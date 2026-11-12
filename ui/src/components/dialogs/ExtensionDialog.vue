<script setup>
// An extension's action on one video or on the picked videos (2.30.0): the
// extension's forms (components/ExtensionForm.vue) in a dialog.
import { computed, ref } from "vue";
import ModalDialog from "../ModalDialog.vue";
import ExtensionForm from "../ExtensionForm.vue";
import { toast } from "../../stores/toast.js";

// item: one video, or { ids: [...] } for picked videos; options.action: { id, label }.
const props = defineProps({ item: Object, options: Object });
const emit = defineEmits(["close"]);
const formRef = ref(null);
const title = ref(props.options?.action?.label || "");
const ids = computed(() => (props.item?.ids ? props.item.ids : props.item ? [props.item.id] : []));
const endpoint = computed(() => `/api/extensions/actions/${encodeURIComponent(props.options.action.id)}`);

function done(msg) {
  if (msg?.text) toast(msg.text, { type: msg.level === "error" ? "error" : "success", timeout: 6000 });
  emit("close");
}
</script>

<template>
  <ModalDialog :title="title || options.action.label" @close="emit('close')">
    <ExtensionForm ref="formRef" :endpoint="endpoint" :item-ids="ids" @done="done" @title="(t) => t && (title = t)" />
    <template #footer>
      <button class="btn" @click="emit('close')">Cancel</button>
      <button v-if="formRef?.form" class="btn btn-primary" :form="'xf-' + endpoint" :disabled="formRef?.busy">
        {{ formRef?.busy ? "Please wait…" : formRef.form.submit || "Continue" }}
      </button>
    </template>
  </ModalDialog>
</template>
