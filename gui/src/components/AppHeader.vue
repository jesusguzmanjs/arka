<script setup lang="ts">
// AppHeader.vue
// See .openspec/3-gui-spec.md §3.3 — title/branding + NML path indicator.
// Reads nmlPathOverride from useConfigState; shows "auto-discover" when null.
import { computed } from "vue";
import { useConfigState } from "../composables/useConfigState";

const { nmlPathOverride } = useConfigState();

const nmlLabel = computed(() => {
  const p = nmlPathOverride.value;
  if (!p || p.trim().length === 0) return "auto-discover";
  return p;
});

const isAuto = computed(() => nmlLabel.value === "auto-discover");
</script>

<template>
  <header
    class="flex items-center justify-between px-6 py-3 bg-panel border-b border-border"
  >
    <div class="flex items-center gap-2">
      <span class="text-accent text-lg font-semibold tracking-wide">CueGrid</span>
      <span class="text-xs text-dim uppercase tracking-widest">v2.0</span>
    </div>
    <div class="flex items-center gap-2 text-xs">
      <span class="text-muted">collection.nml:</span>
      <span
        class="flex items-center gap-1.5 font-mono"
        :class="isAuto ? 'text-warn' : 'text-success'"
        :title="isAuto ? 'Path will be auto-discovered at run time' : nmlLabel"
      >
        <span
          class="inline-block w-2 h-2 rounded-full"
          :class="isAuto ? 'bg-warn' : 'bg-success'"
        />
        <span class="max-w-[280px] truncate">{{ nmlLabel }}</span>
      </span>
    </div>
  </header>
</template>
