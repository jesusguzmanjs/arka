<script setup lang="ts">
import { computed } from "vue";
import { useConfigState } from "../composables/useConfigState";
import { useLibraryState } from "../composables/useLibraryState";
import { useRunState } from "../composables/useRunState";
import { useCueGridSidecar } from "../composables/useCueGridSidecar";
import ClearExistingSwitch from "./ClearExistingSwitch.vue";
import MaxCuesSelect from "./MaxCuesSelect.vue";
import SensitivitySelect from "./SensitivitySelect.vue";

const props = defineProps<{
  disabled?: boolean;
}>();

const { sensitivity, maxCues, clearExisting, isValid, selectedPlaylist, update } = useConfigState();
const { collection, currentViewTracks, selectedLibraryPaths } = useLibraryState();
const { status, analysisStatus, progress, summary, logs, clearSummary } = useRunState();
const { run, runSelectedTracks, cancel, resetRun } = useCueGridSidecar();

const locked = computed(() => props.disabled || status.value === "running");
const selectedTracks = computed(() =>
  selectedLibraryPaths.value
    .map((path) => collection.value[path])
    .filter((track) => track !== undefined && !track.is_flex_grid),
);
const isSelectedTarget = computed(() => selectedLibraryPaths.value.length > 0);
const actionLabel = computed(() => isSelectedTarget.value ? "Auto Cue Selected" : "Auto Cue Playlist");
const selectionLabel = computed(() => {
  const count = selectedLibraryPaths.value.length;
  return `${count} ${count === 1 ? "track" : "tracks"} selected`;
});
const cuesWritten = computed(() =>
  logs.value.filter((entry) => entry.msg.type === "cue_written").length,
);
const successSummary = computed(() => {
  if (status.value !== "success" || !summary.value) return null;
  return `${summary.value.succeeded}/${summary.value.total} tracks processed · ${cuesWritten.value} cues written`;
});
const displayedProgress = computed(() => {
  if (progress.value) return progress.value;
  if (status.value !== "running") return null;

  const total = isSelectedTarget.value
    ? selectedTracks.value.length
    : currentViewTracks.value.length;
  return total > 0 ? { current: 1, total } : null;
});
const canRun = computed(
  () => !locked.value && (isSelectedTarget.value
    ? selectedTracks.value.length > 0
    : isValid.value && selectedPlaylist.value !== null),
);

async function onPrimary(): Promise<void> {
  if (!canRun.value) return;

  if (status.value === "success" || status.value === "error" || status.value === "cancelled") {
    resetRun();
  }

  if (isSelectedTarget.value) {
    await runSelectedTracks(selectedTracks.value);
    return;
  }

  await run();
}
</script>

<template>
  <section class="shrink-0 overflow-hidden rounded-lg border border-zinc-800/80 bg-zinc-900 shadow-inner">
    <div class="flex items-center gap-2 border-b border-zinc-800/80 border-l-2 border-l-secondary/30 px-4 py-2">
      <span class="text-xs uppercase tracking-widest text-muted">Auto Cue</span>
    </div>

    <div class="bg-panel">
      <div class="flex flex-col">
        <div class="flex items-center justify-center px-4 py-3">
          <div class="mx-auto flex w-full max-w-2xl flex-col gap-3">
          <div class="flex flex-wrap items-center justify-center gap-x-2 gap-y-3">
            <div class="flex flex-col justify-center gap-2 border-r border-zinc-800 pr-6">
              <div class="grid min-h-6 grid-cols-[8rem_2.75rem] items-center gap-3">
                <span class="text-sm text-muted">Clear current cues</span>
                <ClearExistingSwitch
                  :model-value="clearExisting"
                  :disabled="locked"
                  @update:model-value="(value) => update('clearExisting', value)"
                />
              </div>
            </div>

            <div class="flex items-center gap-3 border-r border-zinc-800 px-6">
              <span class="w-24 shrink-0 text-sm text-muted">Sensitivity</span>
              <SensitivitySelect
                :model-value="sensitivity"
                :disabled="locked"
                @update:model-value="(value) => update('sensitivity', value)"
              />
            </div>

            <div class="flex items-center gap-3 pl-6">
              <span class="w-24 shrink-0 text-sm text-muted">Max Cues</span>
              <MaxCuesSelect
                :model-value="maxCues"
                :disabled="locked"
                @update:model-value="(value) => update('maxCues', value)"
              />
            </div>
          </div>

          <p v-if="!isValid" class="text-center text-xs text-warn">
            Select tracks in the Library Browser or choose a playlist to enable Auto Cue.
          </p>
          </div>
        </div>

        <div class="border-t border-border px-6 py-2">
          <div class="flex flex-col items-center gap-1">
            <div class="relative flex min-h-10 w-full items-center justify-center">
              <div class="flex items-center gap-3">
                <button
                  type="button"
                  :disabled="!canRun"
                  class="inline-flex items-center gap-2 rounded-md px-6 py-2 text-base font-medium transition-colors"
                  :class="canRun ? 'bg-accent text-base hover:bg-accent-hover active:bg-accent-pressed' : 'cursor-not-allowed bg-elevated text-dim'"
                  @click="onPrimary"
                >
                  <span
                    v-if="status === 'running'"
                    class="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-base/40 border-t-base"
                  />
                  <span v-else class="text-base">▶</span>
                  <span>{{ status === "running" ? "Auto Cue running…" : actionLabel }}</span>
                </button>

                <button
                  v-if="status === 'running'"
                  type="button"
                  class="rounded-md border border-border-strong px-3 py-2 text-sm text-muted transition-colors hover:border-error hover:text-error"
                  @click="cancel"
                >
                  Cancel
                </button>
              </div>

              <p
                v-if="displayedProgress"
                class="absolute right-0 text-sm font-medium text-secondary"
                aria-live="polite"
              >
                Analyzing track {{ displayedProgress.current }} of {{ displayedProgress.total }}…
              </p>

              <div
                v-if="successSummary"
                class="absolute right-0 inline-flex items-center gap-3 rounded-md border border-success/30 bg-success/10 px-4 py-2 text-success"
                role="status"
                aria-live="polite"
              >
                <div class="min-w-0 text-center">
                  <p class="text-sm font-semibold">Auto Cue complete</p>
                  <p class="text-xs">{{ successSummary }}</p>
                </div>
                <button
                  type="button"
                  class="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-sm text-success/70 transition-colors hover:bg-success/15 hover:text-success focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-success/70"
                  aria-label="Dismiss analysis summary"
                  @click="clearSummary"
                >
                  <svg viewBox="0 0 16 16" class="h-3.5 w-3.5" aria-hidden="true">
                    <path d="m4 4 8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.5" />
                  </svg>
                </button>
              </div>
            </div>

            <div v-if="analysisStatus && status !== 'success'" class="min-h-4 text-center font-mono text-xs text-muted" aria-live="polite">
              {{ analysisStatus }}
            </div>
            <p
              v-if="status === 'idle' && isSelectedTarget"
              class="text-center text-xs text-muted"
            >
              {{ selectionLabel }}
            </p>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
