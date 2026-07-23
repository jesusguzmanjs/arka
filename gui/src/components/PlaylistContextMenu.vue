<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";

const props = defineProps<{ x: number; y: number; visible: boolean }>();
const emit = defineEmits<{ close: []; rename: []; delete: []; autoCue: [] }>();
const menuElement = ref<HTMLElement | null>(null);

function closeOutside(event: PointerEvent): void {
  if (props.visible && !menuElement.value?.contains(event.target as Node)) emit("close");
}

function closeOnEscape(event: KeyboardEvent): void {
  if (event.key === "Escape") emit("close");
}

onMounted(() => {
  document.addEventListener("pointerdown", closeOutside);
  document.addEventListener("keydown", closeOnEscape);
});
onUnmounted(() => {
  document.removeEventListener("pointerdown", closeOutside);
  document.removeEventListener("keydown", closeOnEscape);
});
</script>

<template>
  <div
      v-if="visible"
      ref="menuElement"
      class="fixed z-50 flex w-fit min-w-0 flex-col rounded border border-zinc-700 bg-zinc-900/95 p-1 shadow-2xl backdrop-blur-md"
      :style="{ left: `${x}px`, top: `${y}px` }"
      role="menu"
  >
    <button class="whitespace-nowrap rounded-sm px-2.5 py-1.5 text-left text-xs text-zinc-200 transition-colors hover:bg-zinc-800 hover:text-primary" role="menuitem" @click="emit('autoCue')">
      Auto Cue Playlist
    </button>
    <button class="whitespace-nowrap rounded-sm px-2.5 py-1.5 text-left text-xs text-zinc-200 transition-colors hover:bg-zinc-800 hover:text-primary" role="menuitem" @click="emit('rename')">
      Rename
    </button>
    <div class="my-1 h-px w-full bg-zinc-700/50"></div>
    <button class="whitespace-nowrap rounded-sm px-2.5 py-1.5 text-left text-xs text-red-300 transition-colors hover:bg-red-950/50" role="menuitem" @click="emit('delete')">
      Delete
    </button>
  </div>
</template>
