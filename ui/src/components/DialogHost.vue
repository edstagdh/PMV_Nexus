<script setup>
import { dialog, closeDialog } from "../stores/dialogs.js";
import RenameDialog from "./dialogs/RenameDialog.vue";
import RemoveDialog from "./dialogs/RemoveDialog.vue";
import ThumbnailDialog from "./dialogs/ThumbnailDialog.vue";
import ExtensionDialog from "./dialogs/ExtensionDialog.vue";
import EditDetailsDialog from "./dialogs/EditDetailsDialog.vue";
import PlaylistDialog from "./dialogs/PlaylistDialog.vue";
import SongsDialog from "./dialogs/SongsDialog.vue";
import ReencodeDialog from "./dialogs/ReencodeDialog.vue";

const DIALOGS = { rename: RenameDialog, remove: RemoveDialog, thumbnail: ThumbnailDialog, extension: ExtensionDialog, details: EditDetailsDialog, playlist: PlaylistDialog, songs: SongsDialog, reencode: ReencodeDialog };
</script>

<template>
  <component :is="DIALOGS[dialog.name]" v-if="dialog.name" :key="dialog.item?.id + dialog.name"
             :item="dialog.item" :options="dialog.options" @close="closeDialog" />
</template>
