<script setup lang="ts">
import { computed, onMounted, onUnmounted, watch } from "vue";
import { storeToRefs } from "pinia";
import { type UnlistenFn } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import AppHeader from "./components/AppHeader.vue";
import CollectionView from "./components/CollectionView.vue";
import SessionHistoryView from "./components/SessionHistoryView.vue";
import RemixStudioView from "./components/RemixStudioView.vue";
import TraktorSafetyOverlay from "./components/TraktorSafetyOverlay.vue";
import { useTraktorStatus } from "./composables/useTraktorStatus";
import { useUnsavedChangesGuard } from "./composables/useUnsavedChangesGuard";
import { useRunState } from "./composables/useRunState";
import { useSaveStore } from "./stores/useSaveStore";
import { useLibraryState } from "./composables/useLibraryState";
import { useAppToast } from "./composables/useAppToast";
import { useConfigState } from "./composables/useConfigState";
import { syncActiveRemixSet } from "./composables/useRemixAudio";
import { type WorkspaceTab, useWorkspaceStore } from "./stores/useWorkspaceStore";

const workspaceStore = useWorkspaceStore();
const { activeTab } = storeToRefs(workspaceStore);
const activeView = computed(() => ({
  collection: CollectionView,
  history: SessionHistoryView,
  "remix-studio": RemixStudioView,
})[activeTab.value]);
const saveStore = useSaveStore();
const { isTraktorRunning } = useTraktorStatus();
const { isSystemBusy } = useRunState();
const { resolveUnsavedChanges } = useUnsavedChangesGuard();
const { loadLibrary } = useLibraryState();
const { nmlPathOverride } = useConfigState();
const { message: toastMessage, kind: toastKind, showAppToast } = useAppToast();
let unlistenCloseRequested: UnlistenFn | undefined;
let isForceClosing = false;
let traktorCloseReloadToken = 0;

function selectTab(tab: WorkspaceTab) {
  if (isSystemBusy.value) return;
  workspaceStore.selectTab(tab);
}

watch(isTraktorRunning, async (isRunning, wasRunning) => {
  if (wasRunning !== true || isRunning !== false) return;

  const reloadToken = ++traktorCloseReloadToken;
  try {
    const reloaded = await loadLibrary({
      preserveTrackPaths: saveStore.modifiedTracks,
      preservePlaylistUuids: saveStore.modifiedPlaylists,
    });
    if (reloadToken !== traktorCloseReloadToken) return;
    if (!reloaded) throw new Error("The collection could not be read.");
    showAppToast("Traktor closed. Collection synced in background.");
    await syncActiveRemixSet(nmlPathOverride.value);
  } catch (error) {
    if (reloadToken !== traktorCloseReloadToken) return;
    showAppToast(
      `Traktor closed, but collection data could not be reloaded: ${error instanceof Error ? error.message : String(error)}`,
      "error",
    );
  }
});

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
        <button
          id="remix-studio-tab"
          type="button"
          role="tab"
          :aria-selected="activeTab === 'remix-studio'"
          aria-controls="workspace-view"
          :aria-disabled="isSystemBusy"
          :disabled="isSystemBusy"
          class="relative h-full px-3 text-sm font-medium text-muted transition-[color,background-color] duration-150 hover:bg-elevated hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-secondary/70 disabled:cursor-not-allowed"
          :class="activeTab === 'remix-studio' ? 'text-primary' : ''"
          @click="selectTab('remix-studio')"
        >
          Remix Studio
          <span v-if="activeTab === 'remix-studio'" class="absolute inset-x-3 bottom-0 h-0.5 bg-primary" aria-hidden="true" />
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

    <div
      v-if="toastMessage"
      class="fixed bottom-5 right-5 z-[10000] max-w-sm rounded border px-4 py-3 text-sm shadow-xl"
      :class="toastKind === 'error' ? 'border-warning/70 bg-zinc-900 text-zinc-100' : 'border-success/50 bg-zinc-900 text-zinc-100'"
      :role="toastKind === 'error' ? 'alert' : 'status'"
      :aria-live="toastKind === 'error' ? 'assertive' : 'polite'"
    >
      <span class="mr-2" :class="toastKind === 'error' ? 'text-warning' : 'text-success'" aria-hidden="true">{{ toastKind === "error" ? "!" : "✓" }}</span>{{ toastMessage }}
    </div>
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
