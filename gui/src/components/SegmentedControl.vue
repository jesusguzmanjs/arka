<script setup lang="ts">
// SegmentedControl.vue
// Generic segmented control shared by SensitivitySelect and other future
// multi-option controls. See .openspec/3-gui-spec.md §3.4.

interface Option<T extends string> {
  value: T;
  label: string;
}

const props = withDefaults(
  defineProps<{
    modelValue: string;
    options: Option<string>[];
    disabled?: boolean;
  }>(),
  { disabled: false },
);

const emit = defineEmits<{
  (e: "update:modelValue", value: string): void;
}>();

function select(value: string) {
  if (props.disabled) return;
  emit("update:modelValue", value);
}
</script>

<template>
  <div
    class="inline-flex rounded-md border border-border-strong bg-base overflow-hidden"
    :class="{ 'opacity-50 cursor-not-allowed': disabled }"
    role="radiogroup"
  >
    <button
      v-for="opt in options"
      :key="opt.value"
      type="button"
      role="radio"
      :aria-checked="modelValue === opt.value"
      :disabled="disabled"
      class="px-3 py-1.5 text-sm transition-colors border-r border-border-strong last:border-r-0"
      :class="
        modelValue === opt.value
          ? 'bg-accent text-base font-medium'
          : 'bg-transparent text-muted hover:bg-elevated hover:text-primary'
      "
      @click="select(opt.value)"
    >
      {{ opt.label }}
    </button>
  </div>
</template>
