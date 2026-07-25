<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";

defineProps<{
  hasTrack: boolean;
  isLoading: boolean;
  isSaving: boolean;
  trackCssGradient: string;
}>();
const emit = defineEmits<{
  resize: [];
  wheel: [event: WheelEvent];
  zoomIn: [];
  zoomOut: [];
}>();

const zoomviewElement = ref<HTMLDivElement | null>(null);
const overviewElement = ref<HTMLDivElement | null>(null);
const zoomGradientElement = ref<HTMLDivElement | null>(null);
const gradientMaskElement = ref<HTMLDivElement | null>(null);
let resizeObserver: ResizeObserver | null = null;
let resizeTimeout: ReturnType<typeof setTimeout> | null = null;

function scheduleResize(): void {
  if (resizeTimeout) clearTimeout(resizeTimeout);
  resizeTimeout = setTimeout(() => emit("resize"), 100);
}

onMounted(() => {
  window.addEventListener("resize", scheduleResize);
  if (zoomviewElement.value) {
    resizeObserver = new ResizeObserver(scheduleResize);
    resizeObserver.observe(zoomviewElement.value);
  }
});
onBeforeUnmount(() => {
  window.removeEventListener("resize", scheduleResize);
  resizeObserver?.disconnect();
  if (resizeTimeout) clearTimeout(resizeTimeout);
});

defineExpose({ zoomviewElement, overviewElement, zoomGradientElement, gradientMaskElement });
</script>

<template>
  <div class="bg-zinc-950/50 border border-zinc-800 rounded h-10 w-full overflow-hidden">
    <div ref="overviewElement" class="h-full w-full" />
  </div>
  <div class="flex items-center gap-2 w-full h-40">
    <div class="relative flex-1 bg-zinc-950/50 border border-zinc-800 rounded h-full overflow-hidden" @wheel.prevent="emit('wheel', $event)">
      <div ref="gradientMaskElement" class="absolute top-0 bottom-0 left-0 overflow-hidden">
        <div ref="zoomGradientElement" class="absolute top-0 bottom-0 left-0 h-full bg-no-repeat origin-left transition-opacity duration-150" :class="isLoading ? 'opacity-0' : 'opacity-100'" :style="{ backgroundImage: trackCssGradient }" />
      </div>
      <div ref="zoomviewElement" class="absolute inset-0" />
      <div v-if="isLoading || isSaving" class="absolute inset-0 flex items-center justify-center bg-zinc-950/50 z-10"><span class="text-xs font-mono animate-pulse text-zinc-400">{{ isSaving ? "Saving changes to NML…" : "Loading track…" }}</span></div>
      <div v-else-if="!hasTrack" class="absolute inset-0 flex items-center justify-center bg-zinc-950/50"><span class="text-xs font-mono text-zinc-600">Double-click a track in the library to preview</span></div>
    </div>
    <div v-if="hasTrack" class="flex flex-col gap-1.5 select-none pr-0.5">
      <button type="button" class="w-7 h-7 flex items-center justify-center text-sm font-mono font-bold rounded border bg-zinc-800 text-zinc-100 border-zinc-700 hover:bg-zinc-700 hover:text-accent transition-colors" @click="emit('zoomIn')">+</button>
      <button type="button" class="w-7 h-7 flex items-center justify-center text-sm font-mono font-bold rounded border bg-zinc-800 text-zinc-100 border-zinc-700 hover:bg-zinc-700 hover:text-accent transition-colors" @click="emit('zoomOut')">−</button>
    </div>
  </div>
</template>

<style scoped>
:deep(.peaks-view-container) { overflow: hidden !important; }
</style>
