<script setup lang="ts">
// AppHeader.vue
// See .openspec/3-gui-spec.md §3.3 — title/branding + NML path indicator.
import { computed } from "vue";
import { open } from "@tauri-apps/plugin-dialog";
import { useConfigState } from "../../composables/core/useConfigState.ts";
import { useRunState } from "../../composables/core/useRunState.ts";
import { useUnsavedChangesGuard } from "../../composables/core/useUnsavedChangesGuard.ts";
import BugReportModal from "../ui/BugReportModal.vue";

const { customNmlPath, setCustomNmlPath } = useConfigState();
const { isSystemBusy } = useRunState();
const { resolveUnsavedChanges } = useUnsavedChangesGuard();

const collectionButtonLabel = computed(() => {
  const path = customNmlPath.value;
  if (!path) return "No collection found";

  const versionDirectory = path.match(/(?:^|[\\/])(Traktor\s+[^\\/]+)(?:[\\/]|$)/i);
  return versionDirectory ? `${versionDirectory[1]} NML` : "Traktor NML";
});

const hasActiveCollection = computed(() => customNmlPath.value !== null);

async function selectCustomCollection(): Promise<void> {
  if (isSystemBusy.value) return;
  if (!await resolveUnsavedChanges()) return;

  const selection = await open({
    multiple: false,
    filters: [{ name: "Traktor Collection", extensions: ["nml"] }],
  });

  if (typeof selection === "string") setCustomNmlPath(selection);
}
</script>

<template>
  <header
      class="flex items-center justify-between px-6 py-3 bg-panel border-b border-border select-none"
  >
    <div class="flex items-center gap-2">
      <span class="text-accent text-lg font-semibold tracking-wide">Arka</span>
      <span class="text-xs text-dim uppercase tracking-widest">v2.0</span>
    </div>

    <div class="flex items-center gap-3">
      <BugReportModal />
      <button
          type="button"
          class="flex min-w-0 max-w-md items-center gap-2 rounded border border-border px-2.5 py-1 text-xs font-mono text-muted transition-colors hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-secondary disabled:cursor-not-allowed disabled:opacity-50"
          :title="hasActiveCollection ? customNmlPath ?? undefined : 'No collection.nml is active. Choose it manually.'"
          :aria-label="hasActiveCollection ? `Change ${collectionButtonLabel} collection` : 'Locate collection.nml manually'"
          :disabled="isSystemBusy"
          @click="selectCustomCollection"
      >
        <span
            class="inline-block w-2 h-2 rounded-full"
            :class="hasActiveCollection ? 'bg-success shadow-[0_0_5px_var(--tw-shadow-color)] shadow-success/50' : 'bg-warn shadow-[0_0_5px_var(--tw-shadow-color)] shadow-warn/50'"
            aria-hidden="true"
        />
        <span class="min-w-0 truncate" :class="hasActiveCollection ? '' : 'text-warn'">{{ collectionButtonLabel }}</span>
      </button>
    </div>
  </header>
</template>
