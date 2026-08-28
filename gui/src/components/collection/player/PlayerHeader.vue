<script setup lang="ts">
defineProps<{
  hasTrack: boolean;
  trackHeaderLabel: string;
  bpmLabel: string;
  keyLabel?: string;
  remainingLabel: string;
}>();
</script>

<template>
  <div class="flex items-center gap-3 px-1">
    <span class="text-xs uppercase tracking-widest text-zinc-500 font-semibold">Player</span>
    <span v-if="hasTrack" class="text-sm font-mono text-zinc-100 truncate max-w-[520px]" :title="trackHeaderLabel">{{ trackHeaderLabel }}</span>
    <span v-else class="text-sm font-mono text-zinc-500">No track loaded</span>
    <div class="flex-1" />
    <div class="flex items-center gap-3 text-xs font-mono text-zinc-400">
      <span v-if="bpmLabel"><span class="text-zinc-500">BPM</span> <span class="text-accent">{{ bpmLabel }}</span></span>
      <span v-if="keyLabel"><span class="text-zinc-500">KEY</span> <span class="text-accent">{{ keyLabel }}</span></span>
      <span v-if="remainingLabel"><span class="text-zinc-500">TIME</span> <span class="text-zinc-100">{{ remainingLabel }}</span></span>
    </div>
  </div>
</template>