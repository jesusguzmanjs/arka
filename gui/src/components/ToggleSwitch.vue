<script setup lang="ts">
interface Props {
  modelValue: boolean;
  disabled?: boolean;
  ariaLabel: string;
  title?: string;
}

const props = defineProps<Props>();
const emit = defineEmits<{
  (event: "update:modelValue", value: boolean): void;
}>();

function toggle() {
  if (props.disabled) return;
  emit("update:modelValue", !props.modelValue);
}
</script>

<template>
  <button
    type="button"
    role="switch"
    :aria-checked="modelValue"
    :aria-label="ariaLabel"
    :title="title"
    :disabled="disabled"
    class="relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border border-zinc-700 transition-colors focus:outline-none focus:ring-2 focus:ring-teal-400/50 disabled:cursor-not-allowed disabled:opacity-50"
    :class="modelValue ? 'bg-teal-500/80' : 'bg-zinc-800'"
    @click="toggle"
  >
    <span
      class="inline-block h-4 w-4 rounded-full bg-white shadow transition-transform"
      :class="modelValue ? 'translate-x-6' : 'translate-x-1'"
    />
  </button>
</template>
