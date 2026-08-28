<script setup lang="ts">
import { onMounted, onUnmounted } from "vue";

defineProps<{
  x: number;
  y: number;
  visible: boolean;
}>();

const emit = defineEmits<{
  close: [];
  delete: [];
}>();

function onKeyDown(event: KeyboardEvent): void {
  if (event.key === "Escape") emit("close");
}

onMounted(() => window.addEventListener("keydown", onKeyDown));
onUnmounted(() => window.removeEventListener("keydown", onKeyDown));
</script>

<template>
  <template v-if="visible">
    <button
      type="button"
      class="fixed inset-0 z-40 h-full w-full cursor-default bg-transparent"
      aria-label="Close cue menu"
      @click="emit('close')"
    />
    <div
      class="fixed z-50 min-w-36 rounded-md border border-zinc-700 bg-zinc-900 py-1 text-sm text-zinc-200 shadow-xl"
      :style="{ left: `${x}px`, top: `${y}px` }"
      role="menu"
      @click.stop
    >
      <button
        type="button"
        class="w-full px-3 py-2 text-left hover:text-red-500 focus:outline-none focus:text-red-500"
        role="menuitem"
        @click="emit('delete')"
      >
        Delete Cue
      </button>
    </div>
  </template>
</template>
