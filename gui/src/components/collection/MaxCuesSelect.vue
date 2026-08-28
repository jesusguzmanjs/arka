<script setup lang="ts">
// MaxCuesSelect.vue
// Compact 1–8 segmented control for the per-track cue limit.
// See .openspec/3-gui-spec.md §3.3, §5.2, §5.3.
import SegmentedControl from "../ui/SegmentedControl.vue";

const props = defineProps<{
  modelValue: number;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", value: number): void;
}>();

const options = Array.from({ length: 8 }, (_, index) => {
  const value = index + 1;
  return { value: String(value), label: String(value) };
});

function onUpdate(value: string) {
  const maxCues = Number(value);
  if (Number.isInteger(maxCues) && maxCues >= 1 && maxCues <= 8) {
    emit("update:modelValue", maxCues);
  }
}
</script>

<template>
  <SegmentedControl
    :model-value="String(props.modelValue)"
    :options="options"
    :disabled="disabled"
    @update:model-value="onUpdate"
  />
</template>
