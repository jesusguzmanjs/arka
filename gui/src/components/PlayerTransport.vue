<script setup lang="ts">
import type { PlayerCue } from "../composables/usePeaksMarkers";

defineProps<{
  hasTrack: boolean;
  isPlaying: boolean;
  padSlots: readonly (PlayerCue | null)[];
  activePad: number | null;
}>();

const emit = defineEmits<{
  play: [];
  stop: [];
  jump: [padIndex: number];
  previewStart: [padIndex: number];
  previewEnd: [padIndex: number];
  contextMenu: [event: MouseEvent, cue: PlayerCue];
}>();
</script>

<template>
  <div class="flex items-center gap-1 px-1" role="group" aria-label="Player transport and hotcue jump pads">
    <button type="button" :disabled="!hasTrack" class="text-xs font-mono font-semibold px-2 py-1 rounded border bg-zinc-800 text-zinc-100 border-zinc-700 hover:bg-zinc-700 hover:text-accent disabled:opacity-40 disabled:hover:text-zinc-100 disabled:hover:bg-zinc-800 disabled:cursor-not-allowed transition-colors" :title="isPlaying ? 'Pause' : 'Play'" @click="emit('play')">
      {{ isPlaying ? "❚❚" : "▶" }}
    </button>
    <button type="button" :disabled="!isPlaying" class="text-xs font-mono font-semibold px-2 py-1 rounded border bg-zinc-800 text-zinc-100 border-zinc-700 hover:bg-zinc-700 hover:text-accent disabled:opacity-40 disabled:hover:text-zinc-100 disabled:hover:bg-zinc-800 disabled:cursor-not-allowed transition-colors" title="Stop" @click="emit('stop')">■</button>
    <template v-for="n in 8" :key="`pad-${n}`">
      <button v-if="padSlots[n - 1]" type="button" class="flex items-center justify-center text-xs font-mono font-bold w-8 h-8 rounded border transition-all duration-75 transform" :class="[
        padSlots[n - 1]!.is_valid !== true ? 'bg-zinc-800 text-zinc-500 border-zinc-800 cursor-not-allowed opacity-60' : activePad === n ? 'bg-accent text-zinc-950 border-accent scale-95 brightness-110 shadow-inner cursor-pointer' : 'bg-primary text-zinc-950 border-primary hover:bg-accent hover:border-accent shadow-sm cursor-pointer'
      ]" :title="`Jump to cue ${n} (${(padSlots[n - 1]!.position_ms / 1000).toFixed(2)}s)`" @mousedown.left.prevent="emit('previewStart', n)" @mouseup.left="emit('previewEnd', n)" @mouseleave="emit('previewEnd', n)" @click.left="emit('jump', n)" @contextmenu.prevent="emit('contextMenu', $event, padSlots[n - 1]!)">{{ n }}</button>
      <button v-else type="button" disabled class="flex items-center justify-center text-xs font-mono font-semibold w-8 h-8 rounded border bg-zinc-900/50 text-zinc-700 border-zinc-800/50 cursor-not-allowed" title="Empty Cue">{{ n }}</button>
    </template>
  </div>
</template>
