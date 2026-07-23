<script setup lang="ts">
import { defineAsyncComponent, onMounted, shallowRef } from "vue";
import LibraryBrowser from "./LibraryBrowser.vue";
import TelemetryConsole from "./TelemetryConsole.vue";
import WorkspaceFooter from "./WorkspaceFooter.vue";
import { useCueGridSidecar } from "../composables/useCueGridSidecar";
import { useRunState } from "../composables/useRunState";

const { discoverAndSetDefaultNml, exportTelemetry } = useCueGridSidecar();
const { status, analysisStatus } = useRunState();
const AudioPlayer = defineAsyncComponent(() => import("./AudioPlayer.vue"));
const isTelemetryOpen = shallowRef(false);

onMounted(async () => {
  await discoverAndSetDefaultNml();
});
</script>

<template>
  <div class="flex-1 min-h-0 flex flex-col overflow-hidden">
    <div class="flex-1 min-h-0 flex flex-col gap-1.5 overflow-hidden p-1.5">
      <section class="h-[320px] shrink-0 bg-zinc-900 border border-zinc-800/80 rounded-lg shadow-inner overflow-hidden flex flex-col">
        <div class="h-full shrink-0">
          <AudioPlayer :disabled="status === 'running'" />
        </div>
      </section>

      <section class="flex flex-1 min-h-[280px] flex-col overflow-hidden bg-zinc-900 border border-zinc-800/80 rounded-lg shadow-inner">
        <LibraryBrowser :disabled="status === 'running'" />
      </section>

      <WorkspaceFooter
        class="h-13 min-h-13"
        :telemetry-available="Boolean(analysisStatus)"
        :telemetry-open="isTelemetryOpen"
        @export="exportTelemetry"
        @toggle-telemetry="isTelemetryOpen = !isTelemetryOpen"
      />
    </div>
    <TelemetryConsole :open="isTelemetryOpen" @close="isTelemetryOpen = false" />
  </div>
</template>
