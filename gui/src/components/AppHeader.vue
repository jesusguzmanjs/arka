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
      class="flex items-center justify-between px-6 py-3 bg-panel border-b border-border select-none"
  >
    <div class="flex items-center gap-2">
      <span class="text-accent text-lg font-semibold tracking-wide">CueGrid</span>
      <span class="text-xs text-dim uppercase tracking-widest">v2.0</span>
    </div>

    <div
        class="flex items-center gap-2 px-2.5 py-1 rounded border border-border text-xs font-mono text-muted cursor-help transition-colors hover:bg-white/5"
        :title="isAuto ? 'Path will be auto-discovered at run time' : nmlLabel"
    >
      <span
          class="inline-block w-2 h-2 rounded-full"
          :class="isAuto ? 'bg-warn shadow-[0_0_5px_var(--tw-shadow-color)] shadow-warn/50' : 'bg-success shadow-[0_0_5px_var(--tw-shadow-color)] shadow-success/50'"
          aria-hidden="true"
      />
      <span>Traktor NML</span>
    </div>
  </header>
</template>