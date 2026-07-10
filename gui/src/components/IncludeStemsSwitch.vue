<script setup lang="ts">
import ToggleSwitch from "./ToggleSwitch.vue";

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
    ariaLabel="Include stems"
    :title="modelValue ? 'Stems included' : 'Master audio only'"
    @update:model-value="(value) => emit('update:modelValue', value)"
  />
</template>
