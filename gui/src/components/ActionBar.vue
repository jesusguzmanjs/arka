<script setup lang="ts">
// ActionBar.vue
// Primary CTA button + run status. Idle / running (spinner + Cancel) /
// success / error / cancelled. See .openspec/3-gui-spec.md §3.3, §4.
import { computed } from "vue";
import { useConfigState } from "../composables/useConfigState";
import { useRunState } from "../composables/useRunState";
import { useCueGridSidecar } from "../composables/useCueGridSidecar";

const { isValid } = useConfigState();
const { status, analysisStatus } = useRunState();
const { run, cancel, resetRun } = useCueGridSidecar();

const canRun = computed(
  () => isValid.value && status.value !== "running",
);

function onPrimary() {
  if (status.value === "running") return;
  if (status.value === "success" || status.value === "error" || status.value === "cancelled") {
    resetRun();
  }
  run();
}

</script>

<template>
  <section
    class="bg-panel border-b border-border px-6 py-4 flex flex-col items-center gap-2"
  >
    <div class="flex items-center gap-3">
      <button
        type="button"
        :disabled="!canRun"
        class="inline-flex items-center gap-2 px-6 py-2.5 rounded-md text-base font-medium transition-colors"
        :class="
          canRun
            ? 'bg-accent text-base hover:bg-accent-hover active:bg-accent-pressed'
            : 'bg-elevated text-dim cursor-not-allowed'
        "
        @click="onPrimary"
      >
        <span
          v-if="status === 'running'"
          class="inline-block w-3.5 h-3.5 border-2 border-base/40 border-t-base rounded-full animate-spin"
        />
        <span v-else class="text-base">▶</span>
        <span>{{ status === "running" ? "Running…" : "Analyze & Inject" }}</span>
      </button>

      <button
        v-if="status === 'running'"
        type="button"
        class="px-3 py-2 text-sm rounded-md border border-border-strong text-muted hover:text-error hover:border-error transition-colors"
        @click="cancel"
      >
        Cancel
      </button>
    </div>

    <div
      v-if="analysisStatus"
      class="min-h-4 text-center text-xs font-mono text-muted"
      aria-live="polite"
    >
      {{ analysisStatus }}
    </div>
  </section>
</template>
