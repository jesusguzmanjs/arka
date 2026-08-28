<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";

const props = defineProps<{
  x: number;
  y: number;
  visible: boolean;
  actionLabel: string;
  actionEnabled?: boolean;
  metadataEnabled?: boolean;
  selectAllEnabled?: boolean;
  removeFromPlaylistEnabled?: boolean;
}>();

const menuElement = ref<HTMLElement | null>(null);
const emit = defineEmits<{
  close: [];
  action: [];
  selectAll: [];
  editMetadata: [];
  sendToRemixStudio: [];
  removeFromPlaylist: [];
}>();

function onPointerDown(event: PointerEvent): void {
  if (props.visible && !menuElement.value?.contains(event.target as Node)) {
    emit("close");
  }
}

function onKeyDown(event: KeyboardEvent): void {
  if (event.key === "Escape") emit("close");
}

function onScroll(): void {
  if (props.visible) emit("close");
}

onMounted(() => {
  document.addEventListener("pointerdown", onPointerDown);
  document.addEventListener("keydown", onKeyDown);
  document.addEventListener("scroll", onScroll, true);
});

onUnmounted(() => {
  document.removeEventListener("pointerdown", onPointerDown);
  document.removeEventListener("keydown", onKeyDown);
  document.removeEventListener("scroll", onScroll, true);
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
    <button
        v-if="removeFromPlaylistEnabled"
        type="button"
        class="whitespace-nowrap rounded-sm px-2.5 py-1.5 text-left text-xs text-red-300 transition-colors hover:bg-red-950/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary"
        role="menuitem"
        @click="emit('removeFromPlaylist')"
    >
      Remove from Playlist
    </button>
    <button
        type="button"
        class="whitespace-nowrap rounded-sm px-2.5 py-1.5 text-left text-xs transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary disabled:cursor-not-allowed disabled:text-zinc-600 enabled:text-zinc-200 enabled:hover:bg-zinc-800 enabled:hover:text-primary"
        role="menuitem"
        :disabled="!selectAllEnabled"
        @click="emit('selectAll')"
    >
      Select All Songs
    </button>
    <button
        type="button"
        class="whitespace-nowrap rounded-sm px-2.5 py-1.5 text-left text-xs transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary disabled:cursor-not-allowed disabled:text-zinc-600 enabled:text-zinc-200 enabled:hover:bg-zinc-800 enabled:hover:text-primary"
        role="menuitem"
        :disabled="actionEnabled === false"
        @click="emit('action')"
    >
      {{ actionLabel }}
    </button>
    <button
        type="button"
        class="whitespace-nowrap rounded-sm px-2.5 py-1.5 text-left text-xs transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary disabled:cursor-not-allowed disabled:text-zinc-600 enabled:text-zinc-200 enabled:hover:bg-zinc-800 enabled:hover:text-primary"
        role="menuitem"
        :disabled="!metadataEnabled"
        @click="emit('sendToRemixStudio')"
    >
      Send to Remix Studio
    </button>
    <button
        type="button"
        class="whitespace-nowrap rounded-sm px-2.5 py-1.5 text-left text-xs transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary disabled:cursor-not-allowed disabled:text-zinc-600 enabled:text-zinc-200 enabled:hover:bg-zinc-800 enabled:hover:text-primary"
        role="menuitem"
        :disabled="!metadataEnabled"
        @click="emit('editMetadata')"
    >
      Edit Metadata
    </button>
  </div>
</template>
