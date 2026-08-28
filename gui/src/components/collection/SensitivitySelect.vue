<script setup lang="ts">
// SensitivitySelect.vue
// 3-way segmented control: soft | medium | hard.
// See .openspec/3-gui-spec.md §3.3, §3.4.
import SegmentedControl from "../ui/SegmentedControl.vue";
import type { Sensitivity } from "../../types/config.ts";

const props = defineProps<{
  modelValue: Sensitivity;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", value: Sensitivity): void;
}>();

const options: { value: Sensitivity; label: string }[] = [
  { value: "soft", label: "Granular" },
  { value: "medium", label: "Balanced" },
  { value: "hard", label: "Strict" },
];

function onUpdate(v: string) {
  emit("update:modelValue", v as Sensitivity);
}
</script>

<template>
  <SegmentedControl
    :model-value="props.modelValue"
    :options="options"
    :disabled="disabled"
    @update:model-value="onUpdate"
  />
</template>
