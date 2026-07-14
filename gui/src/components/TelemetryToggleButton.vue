<script setup lang="ts">
// TelemetryToggleButton.vue
// Small, unobtrusive fixed-position launcher for the floating TelemetryConsole
// overlay. See .openspec/3-gui-spec.md §4 (TelemetryToggleButton / overlay).
//
// Sits outside the flex flow at the bottom-right corner, on top of the rack
// and SummaryBadge. Recedes when idle (muted, low opacity), brightens on
// hover, and shows a subtle dot indicator when new log lines have arrived
// while the console is closed — so a running/finished job is never silently
// invisible.

import { computed, ref, watch } from "vue";
import { useRunState } from "../composables/useRunState";

const props = defineProps<{
  open: boolean;
}>();

const emit = defineEmits<{
  (e: "toggle"): void;
}>();

const { logs, status } = useRunState();

// Track how many logs were present the last time the console was open.
// Any growth while closed counts as "new" telemetry the user hasn't seen.
const seenCount = ref(0);

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) seenCount.value = logs.value.length;
  },
  { immediate: true },
);

const newCount = computed(() => {
  if (props.open) return 0;
  const delta = logs.value.length - seenCount.value;
  return delta > 0 ? delta : 0;
});

const hasUnseen = computed(() => newCount.value > 0);
const isRunning = computed(() => status.value === "running");
</script>

<template>
  <button
    type="button"
    class="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border border-zinc-700/80 bg-zinc-900/80 text-muted text-xs font-mono backdrop-blur-sm transition-all duration-200 hover:text-primary hover:border-secondary/60 hover:bg-zinc-800/90"
    :class="{ 'opacity-60 hover:opacity-100': !hasUnseen && !isRunning }"
    :title="open ? 'Close telemetry console' : 'Open telemetry console'"
    @click="emit('toggle')"
  >
    <span aria-hidden="true">⧉</span>
    <span
      v-if="hasUnseen"
      class="inline-block w-2 h-2 rounded-full bg-blue-500"
      aria-label="New telemetry messages"
    />
    <span
      v-else-if="isRunning"
      class="inline-block w-1.5 h-1.5 rounded-full bg-accent animate-pulse"
      aria-hidden="true"
    />
  </button>
</template>
