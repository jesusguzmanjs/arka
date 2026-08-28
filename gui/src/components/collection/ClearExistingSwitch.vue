<script setup lang="ts">
import ToggleSwitch from "../ui/ToggleSwitch.vue";

interface Props {
  modelValue: boolean;
  disabled?: boolean;
}

defineProps<Props>();
const emit = defineEmits<{
  (event: "update:modelValue", value: boolean): void;
}>();
</script>

<template>
  <ToggleSwitch
    :model-value="modelValue"
    :disabled="disabled"
    ariaLabel="Clear current cues"
    :title="
      modelValue
        ? 'Clears existing HotCues before writing'
        : 'Existing HotCues are preserved'
    "
    @update:model-value="(value) => emit('update:modelValue', value)"
  />
</template>
