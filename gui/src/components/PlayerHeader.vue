<script setup lang="ts">
defineProps<{
  hasTrack: boolean;
  trackHeaderLabel: string;
  bpmLabel: string;
  remainingLabel: string;
  cueCount: number;
  validCueCount: number;
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
      <span v-if="remainingLabel"><span class="text-zinc-500">LEN</span> <span class="text-zinc-100">{{ remainingLabel }}</span></span>
      <span v-if="hasTrack"><span class="text-zinc-500">CUES</span> <span class="text-zinc-100">{{ cueCount }}</span><span class="text-zinc-500">/</span><span class="text-accent">{{ validCueCount }} valid</span></span>
    </div>
  </div>
</template>
