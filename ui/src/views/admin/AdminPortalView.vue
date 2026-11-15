<script setup>
// Management (sidebar: Server -> Management): the admin pages that have no tabs of their own, as tabs of
// one page (/admin/portal/<tab>) -- Users, Duplicates, Maintenance,
// Scheduler, Backups, Stats, Logs, Tools. Each tab is the page it always
// was; only the tabs you may open are shown. Server Settings stays separate.
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import UsersView from "./UsersView.vue";
import DuplicatesView from "./DuplicatesView.vue";
import MaintenanceView from "./MaintenanceView.vue";
import NamesView from "./NamesView.vue";
import SongsView from "./SongsView.vue";
import SchedulerView from "./SchedulerView.vue";
import BackupsView from "./BackupsView.vue";
import StatsView from "./StatsView.vue";
import LogsView from "./LogsView.vue";
import ToolsView from "./ToolsView.vue";
import { hasPermission, isAdmin, hasAny } from "../../stores/session.js";
import { PORTAL_TABS } from "../../lib/portalTabs.js";
import { orderTabs } from "../../lib/tabOrder.js";
import TabBar from "../../components/TabBar.vue";

const VIEWS = {
  users: UsersView, duplicates: DuplicatesView, names: NamesView, songs: SongsView, maintenance: MaintenanceView, scheduler: SchedulerView,
  backups: BackupsView, stats: StatsView, logs: LogsView, tools: ToolsView,
};

const route = useRoute();
const router = useRouter();
// In your order (3.4.0): the first one opens when you come to Management.
const tabs = computed(() => orderTabs(PORTAL_TABS.filter((t) => (t.admin ? isAdmin() : hasAny(t.permission))), "portal"));
const current = computed(() => tabs.value.find((t) => t.key === route.params.tab) || tabs.value[0]);
function open(key) { router.push({ name: "portal", params: { tab: key } }); }
</script>

<template>
  <div class="portal">
    <header class="portal-head">
      <h1>Management</h1>
      <TabBar :tabs="tabs" :active="current?.key" page="portal" label="Management" @select="open" />
    </header>
    <p v-if="!tabs.length" class="empty">This part is for the admin.</p>
    <div v-else class="portal-body"><component :is="VIEWS[current.key]" :key="current.key" /></div>
  </div>
</template>

<style scoped>
.portal-head { padding: 1rem 1.5rem 0; display: flex; flex-direction: column; gap: 0.5rem; }
.portal-head h1 { font-size: 1.4rem; }
/* The tab names the page: hide each page's own title (its description stays). */
.portal-body :deep(h1) { display: none; }
@media (max-width: 56rem) { .portal-head { padding: 0.75rem 1rem 0; } }
</style>
