<script setup lang="ts">
withDefaults(defineProps<{
  hasTrack: boolean;
  isFlexGrid: boolean;
  isGridEditMode: boolean;
  dynamicLabel: string;
  dynamicStepMs: number;
  showModifiers?: boolean;
}>(), {
  showModifiers: true,
});

const emit = defineEmits<{
  nudge: [deltaMs: number];
  setToPlayhead: [];
  multiplyBpm: [];
  divideBpm: [];
}>();
</script>

<template>
  <div v-if="hasTrack && isGridEditMode && !isFlexGrid" class="flex items-center gap-1 bg-zinc-950/40 p-1 rounded-md border border-zinc-800/60 shrink-0 h-[34px]" aria-label="Grid editing controls">
    <button type="button" class="text-xs font-mono px-2 h-full rounded border bg-zinc-800 text-zinc-200 border-zinc-700 hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50" :disabled="dynamicStepMs <= 0" title="Moves the grid and all cues backward by the current zoom-based resolution." @click="emit('nudge', -dynamicStepMs)">− {{ dynamicLabel }}</button>
    <button type="button" class="text-xs font-mono px-2 h-full rounded border bg-zinc-800 text-zinc-200 border-zinc-700 hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50" :disabled="dynamicStepMs <= 0" title="Moves the grid and all cues forward by the current zoom-based resolution." @click="emit('nudge', dynamicStepMs)">+ {{ dynamicLabel }}</button>
    <button type="button" class="text-xs font-semibold px-2 h-full rounded border bg-zinc-800 text-zinc-200 border-zinc-700 hover:bg-zinc-700" title="Sets the grid anchor at the current playhead position." @click="emit('setToPlayhead')">Set to Playhead</button>
    <button v-if="showModifiers" type="button" class="text-xs font-mono px-2 h-full rounded border bg-zinc-800 text-zinc-300 border-zinc-700 hover:bg-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent" aria-label="Halve BPM" title="Halves the track BPM." @click="emit('divideBpm')">/2</button>
    <button v-if="showModifiers" type="button" class="text-xs font-mono px-2 h-full rounded border bg-zinc-800 text-zinc-300 border-zinc-700 hover:bg-zinc-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent" aria-label="Double BPM" title="Doubles the track BPM." @click="emit('multiplyBpm')">x2</button>
  </div>
</template>
