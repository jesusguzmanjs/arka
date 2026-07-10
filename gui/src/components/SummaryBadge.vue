<script setup lang="ts">
// SummaryBadge.vue
// Final "N/M tracks · K cues written" chip once status is success/error.
// See .openspec/3-gui-spec.md §3.3, §4.
import { computed } from "vue";
import { useRunState } from "../composables/useRunState";

const { status, summary, logs } = useRunState();

// Derive total cues written from the log stream (cue_written messages),
// since RunSummary only carries track counts.
const cuesWritten = computed(() =>
  logs.value.filter((l) => l.msg.type === "cue_written").length,
);

const visible = computed(
  () => status.value === "success" || status.value === "error",
);

const chipColor = computed(() =>
  status.value === "success" ? "bg-success/15 text-success border-success/40" : "bg-error/15 text-error border-error/40",
);

const label = computed(() => {
  if (!summary.value) return status.value === "success" ? "done" : "failed";
  const s = summary.value;
  return `${s.succeeded}/${s.total} tracks · ${cuesWritten.value} cues written`;
});
</script>

<template>
  <div v-if="visible" class="px-6 py-2 bg-panel border-t border-border">
    <span
      class="inline-flex items-center gap-2 px-3 py-1 rounded-md border text-xs font-mono"
      :class="chipColor"
    >
      <span>{{ label }}</span>
      <span class="uppercase tracking-wider text-[10px] opacity-70">
        [{{ status }}]
      </span>
    </span>
  </div>
</template>
