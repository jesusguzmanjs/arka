<script setup lang="ts">
// ConfigPanel.vue
// Groups the config controls, shows validation state.
// See .openspec/3-gui-spec.md §3.3, revised by .openspec/4-library-spec.md §3.4.
//
// Phase 4: TargetSelector.vue is fully removed; target selection now lives
// in the sibling LibraryBrowser.vue. This panel keeps only the tuning
// controls (Include Stems / Sensitivity / Max Cues / Clear Existing) plus
// the validation hint.
import { computed } from "vue";
import { useConfigState } from "../composables/useConfigState";
import { useRunState } from "../composables/useRunState";
import IncludeStemsSwitch from "./IncludeStemsSwitch.vue";
import SensitivitySelect from "./SensitivitySelect.vue";
import MaxCuesSelect from "./MaxCuesSelect.vue";
import ClearExistingSwitch from "./ClearExistingSwitch.vue";

const props = defineProps<{
  disabled?: boolean;
}>();

const { includeStems, sensitivity, maxCues, clearExisting, isValid, update } =
  useConfigState();
const { status } = useRunState();

// The whole panel is locked while a run is active (§3.4).
const locked = computed(() => props.disabled || status.value === "running");
</script>

<template>
  <section class="flex flex-1 min-h-0 items-center justify-center overflow-y-auto scrollbar-amber bg-panel border-b border-border px-4 py-6">
    <div class="mx-auto flex w-full max-w-2xl flex-col gap-5">
      <div class="flex flex-wrap items-center justify-center gap-x-2 gap-y-4">
        <!-- Section 1: boolean controls share one vertical rhythm and switch primitive. -->
        <div class="flex flex-col justify-center gap-2 border-r border-zinc-800 pr-6">
          <div class="grid min-h-6 grid-cols-[8rem_2.75rem] items-center gap-3">
            <span class="text-sm text-muted">Include stems</span>
            <IncludeStemsSwitch
              :model-value="includeStems"
              :disabled="locked"
              @update:model-value="(v) => update('includeStems', v)"
            />
          </div>
          <div class="grid min-h-6 grid-cols-[8rem_2.75rem] items-center gap-3">
            <span class="text-sm text-muted">Clear current cues</span>
            <ClearExistingSwitch
              :model-value="clearExisting"
              :disabled="locked"
              @update:model-value="(v) => update('clearExisting', v)"
            />
          </div>
        </div>

        <!-- Section 2: sensitivity options. -->
        <div class="flex items-center gap-3 border-r border-zinc-800 px-6">
          <span class="w-24 shrink-0 text-sm text-muted">Sensitivity</span>
          <SensitivitySelect
            :model-value="sensitivity"
            :disabled="locked"
            @update:model-value="(v) => update('sensitivity', v)"
          />
        </div>

        <!-- Section 3: maximum cue count. -->
        <div class="flex items-center gap-3 pl-6">
          <span class="w-24 shrink-0 text-sm text-muted">Max Cues</span>
          <MaxCuesSelect
            :model-value="maxCues"
            :disabled="locked"
            @update:model-value="(v) => update('maxCues', v)"
          />
        </div>
      </div>

      <!-- validation hint (revised per 4-library-spec.md §3.4) -->
      <p v-if="!isValid" class="text-center text-xs text-warn">
        Select a playlist in the Library Browser above to enable the run.
      </p>
    </div>
  </section>
</template>
