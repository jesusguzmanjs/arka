<script setup lang="ts">
import { onMounted, onUnmounted, useTemplateRef } from "vue";
import { useConfigState } from "../composables/useConfigState";
import { useCueGridSidecar } from "../composables/useCueGridSidecar";
import type { LibraryTrack } from "../types/library";
import ClearExistingSwitch from "./ClearExistingSwitch.vue";
import MaxCuesSelect from "./MaxCuesSelect.vue";
import SensitivitySelect from "./SensitivitySelect.vue";

const props = defineProps<{
  tracks: LibraryTrack[];
}>();

const emit = defineEmits<{
  close: [];
}>();

const { clearExisting: clearCues, sensitivity, maxCues, update } = useConfigState();
const { runSelectedTracks } = useCueGridSidecar();
const dialogRef = useTemplateRef<HTMLElement>("dialogRef");

function close(): void {
  emit("close");
}

async function runAnalysis(): Promise<void> {
  emit("close");
  await runSelectedTracks(props.tracks);
}

function onKeyDown(event: KeyboardEvent): void {
  if (event.key === "Escape") close();
}

onMounted(() => {
  document.addEventListener("keydown", onKeyDown);
  dialogRef.value?.focus();
});

onUnmounted(() => document.removeEventListener("keydown", onKeyDown));
</script>

<template>
  <Teleport to="body">
    <div
      class="fixed inset-0 z-[70] flex items-center justify-center bg-zinc-950/85 p-4 backdrop-blur-sm"
      role="presentation"
      @click.self="close"
    >
      <section
        ref="dialogRef"
        class="w-full max-w-xl rounded-lg border border-primary/30 bg-zinc-900 shadow-2xl shadow-black/60 outline-none"
        role="dialog"
        aria-modal="true"
        aria-labelledby="auto-cue-title"
        tabindex="-1"
      >
        <header class="border-b border-zinc-700 px-5 py-4">
          <p class="text-[10px] font-semibold uppercase tracking-[0.18em] text-secondary">Collection tools</p>
          <h2 id="auto-cue-title" class="mt-1 text-lg font-semibold text-zinc-100">Auto Cue</h2>
          <p class="mt-1 text-xs text-muted">
            Configure analysis for {{ props.tracks.length }} selected {{ props.tracks.length === 1 ? "track" : "tracks" }}.
          </p>
        </header>

        <div class="space-y-5 p-5">
          <div class="rounded border border-zinc-800 bg-zinc-950/45 p-4">
            <div class="flex items-center justify-between gap-4">
              <div>
                <h3 class="text-sm font-medium text-zinc-100">Clear current cues</h3>
                <p class="mt-1 text-xs text-muted">Remove existing HotCues before writing new ones.</p>
              </div>
              <ClearExistingSwitch
                :model-value="clearCues"
                @update:model-value="(value) => update('clearExisting', value)"
              />
            </div>
          </div>

          <fieldset class="space-y-2">
            <legend class="text-xs font-semibold uppercase tracking-[0.14em] text-dim">Sensitivity</legend>
            <SensitivitySelect
              :model-value="sensitivity"
              @update:model-value="(value) => update('sensitivity', value)"
            />
          </fieldset>

          <fieldset class="space-y-2">
            <legend class="text-xs font-semibold uppercase tracking-[0.14em] text-dim">Max Cues</legend>
            <MaxCuesSelect
              :model-value="maxCues"
              @update:model-value="(value) => update('maxCues', value)"
            />
          </fieldset>
        </div>

        <footer class="flex items-center justify-end gap-3 border-t border-zinc-800 px-5 py-4">
          <button
            type="button"
            class="rounded px-3 py-2 text-sm text-zinc-300 transition-colors hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            @click="close"
          >
            Cancel
          </button>
          <button
            type="button"
            class="rounded bg-primary px-4 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-secondary"
            @click="runAnalysis"
          >
            Run Analysis
          </button>
        </footer>
      </section>
    </div>
  </Teleport>
</template>
