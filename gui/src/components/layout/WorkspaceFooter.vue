<script setup lang="ts">
import TelemetryToggleButton from "../ui/TelemetryToggleButton.vue";
import { useRunState } from "../../composables/core/useRunState.ts";
import { useCueGridSidecar } from "../../composables/core/useCueGridSidecar.ts";

defineProps<{
  telemetryAvailable: boolean;
  telemetryOpen: boolean;
}>();

const emit = defineEmits<{
  export: [];
  toggleTelemetry: [];
}>();

const { status, analysisStatus, progress } = useRunState();
const { cancel } = useCueGridSidecar();

async function cancelAnalysis(): Promise<void> {
  await cancel();
}
</script>

<template>
  <footer class="flex h-12 shrink-0 items-center justify-between gap-2 rounded-lg border border-zinc-800/80 bg-zinc-900 px-3 shadow-inner">
    <div
      v-if="status === 'running'"
      class="flex min-w-0 flex-1 items-center gap-2 text-xs text-primary"
      role="status"
      aria-live="polite"
    >
      <svg class="h-3.5 w-3.5 shrink-0 animate-spin" viewBox="0 0 20 20" fill="none" aria-hidden="true">
        <circle class="opacity-25" cx="10" cy="10" r="8" stroke="currentColor" stroke-width="2" />
        <path class="opacity-90" d="M18 10a8 8 0 0 1-8 8" stroke="currentColor" stroke-linecap="round" stroke-width="2" />
      </svg>
      <button
        type="button"
        class="shrink-0 rounded border border-warn/70 px-2 py-0.5 text-xs font-semibold text-warn transition-colors hover:border-warn hover:bg-warn/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-warn"
        @click="cancelAnalysis"
      >
        Cancel
      </button>
      <span class="truncate">
        {{ progress ? `Analyzing track ${progress.current} of ${progress.total}…` : (analysisStatus ?? "Analyzing…") }}
      </span>
    </div>
    <div v-else class="flex-1" aria-hidden="true" />

    <button
      type="button"
      :disabled="!telemetryAvailable"
      class="flex items-center gap-1.5 rounded-md border border-zinc-700/80 bg-zinc-900/80 px-2.5 py-1.5 font-mono text-xs text-muted backdrop-blur-sm transition-[color,background-color,border-color] duration-200 enabled:hover:border-secondary/60 enabled:hover:bg-zinc-800/90 enabled:hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-secondary/70 disabled:cursor-not-allowed disabled:opacity-50"
      @click="emit('export')"
    >
      Export
    </button>
    <TelemetryToggleButton :open="telemetryOpen" @toggle="emit('toggleTelemetry')" />
  </footer>
</template>
