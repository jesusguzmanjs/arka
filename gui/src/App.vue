<script setup lang="ts">
import { computed, onMounted, onUnmounted, shallowRef } from "vue";
import { type UnlistenFn } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import AppHeader from "./components/AppHeader.vue";
import CollectionView from "./components/CollectionView.vue";
import SessionHistoryView from "./components/SessionHistoryView.vue";
import TraktorSafetyOverlay from "./components/TraktorSafetyOverlay.vue";
import { useTraktorStatus } from "./composables/useTraktorStatus";
import { useUnsavedChangesGuard } from "./composables/useUnsavedChangesGuard";
import { useRunState } from "./composables/useRunState";
import { useSaveStore } from "./stores/useSaveStore";

type TabId = "collection" | "history";

const activeTab = shallowRef<TabId>("collection");
const activeView = computed(() =>
  activeTab.value === "collection" ? CollectionView : SessionHistoryView,
);
const saveStore = useSaveStore();
const { isTraktorRunning } = useTraktorStatus();
const { isSystemBusy } = useRunState();
const { resolveUnsavedChanges } = useUnsavedChangesGuard();
let unlistenCloseRequested: UnlistenFn | undefined;
let isForceClosing = false;

function selectTab(tab: TabId) {
  if (isSystemBusy.value) return;
  activeTab.value = tab;
}

onMounted(async () => {
  const appWindow = getCurrentWindow();
  unlistenCloseRequested = await appWindow.onCloseRequested(async (event) => {
    if (isForceClosing || !saveStore.isDirty) return;

    event.preventDefault();
    if (!await resolveUnsavedChanges()) return;

    isForceClosing = true;
    await appWindow.destroy();
  });
});

onUnmounted(() => {
  unlistenCloseRequested?.();
});
</script>

<template>
  <div class="h-screen w-screen overflow-hidden flex flex-col bg-zinc-950 text-primary font-ui" @contextmenu.prevent>
    <AppHeader />

    <nav class="flex shrink-0 items-center border-b border-border bg-panel px-4" aria-label="Vistas principales">
      <div
        class="flex h-11 items-end gap-1"
        :class="isSystemBusy ? 'pointer-events-none opacity-50 grayscale' : ''"
        role="tablist"
        aria-orientation="horizontal"
      >
        <button
          id="collection-tab"
          type="button"
          role="tab"
          :aria-selected="activeTab === 'collection'"
          aria-controls="workspace-view"
          :aria-disabled="isSystemBusy"
          :disabled="isSystemBusy"
          class="relative h-full px-3 text-sm font-medium text-muted transition-[color,background-color] duration-150 hover:bg-elevated hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-secondary/70 disabled:cursor-not-allowed"
          :class="activeTab === 'collection' ? 'text-primary' : ''"
          @click="selectTab('collection')"
        >
          Collection
          <span v-if="activeTab === 'collection'" class="absolute inset-x-3 bottom-0 h-0.5 bg-primary" aria-hidden="true" />
        </button>
        <button
          id="history-tab"
          type="button"
          role="tab"
          :aria-selected="activeTab === 'history'"
          aria-controls="workspace-view"
          :aria-disabled="isSystemBusy"
          :disabled="isSystemBusy"
          class="relative h-full px-3 text-sm font-medium text-muted transition-[color,background-color] duration-150 hover:bg-elevated hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-secondary/70 disabled:cursor-not-allowed"
          :class="activeTab === 'history' ? 'text-primary' : ''"
          @click="selectTab('history')"
        >
          Session History
          <span v-if="activeTab === 'history'" class="absolute inset-x-3 bottom-0 h-0.5 bg-primary" aria-hidden="true" />
        </button>
      </div>

      <button
        v-if="saveStore.isDirty"
        type="button"
        class="ml-auto rounded border border-primary bg-primary px-3 py-1 text-xs font-semibold text-zinc-950 transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-secondary disabled:cursor-not-allowed disabled:opacity-50"
        :disabled="saveStore.isSaving"
        @click="saveStore.saveAll"
      >
        {{ saveStore.isSaving ? "Saving..." : "Save Changes" }}
      </button>
    </nav>

    <main id="workspace-view" class="flex-1 min-h-0 flex flex-col overflow-hidden" role="tabpanel" :aria-labelledby="`${activeTab}-tab`">
      <component :is="activeView" />
    </main>

    <TraktorSafetyOverlay :is-running="isTraktorRunning" />
  </div>
</template>

<style>
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  color-scheme: dark;
}

html,
body,
#app {
  height: 100%;
  margin: 0;
  overflow: hidden;
}

*::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
*::-webkit-scrollbar-track {
  background: #121212;
}
*::-webkit-scrollbar-thumb {
  background: #7a4a00;
  border-radius: 999px;
}
*::-webkit-scrollbar-thumb:hover {
  background: #d27b00;
}

* {
  scrollbar-width: thin;
  scrollbar-color: #7a4a00 #121212;
}

.scrollbar-amber {
  scrollbar-color: #7a4a00 #121212;
}
</style>
