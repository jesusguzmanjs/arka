<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, shallowRef, useTemplateRef } from "vue";
import { useSmartFilterStore, type Track } from "../../stores/useSmartFilterStore.ts";
import { useWorkspaceStore } from "../../stores/useWorkspaceStore.ts";
import type { LibraryTrack } from "../../types/library.ts";

const props = defineProps<{
  targetTrack: LibraryTrack;
}>();

const emit = defineEmits<{
  close: [];
  applied: [];
}>();

const smartFilterStore = useSmartFilterStore();
const workspaceStore = useWorkspaceStore();
const dialogRef = useTemplateRef<HTMLElement>("dialogRef");
const bpmTolerance = shallowRef(6);
const keyJumps = shallowRef(2);

const targetBpm = computed(() => props.targetTrack.bpm ?? 0);
const bpmRange = computed(() => {
  const tolerance = targetBpm.value * (bpmTolerance.value / 100);
  return {
    min: targetBpm.value - tolerance,
    max: targetBpm.value + tolerance,
  };
});

function close(): void {
  emit("close");
}

function applyFilter(): void {
  const track: Track = {
    id: props.targetTrack.location_path,
    title: props.targetTrack.title,
    bpm: targetBpm.value,
    key: props.targetTrack.key ?? "",
  };

  smartFilterStore.applyFilter(track, bpmTolerance.value, keyJumps.value);
  emit("close");
  workspaceStore.selectTab("collection");
  emit("applied");
}

function onKeyDown(event: KeyboardEvent): void {
  if (event.key === "Escape") close();
}

onMounted(() => {
  document.addEventListener("keydown", onKeyDown);
  void nextTick(() => dialogRef.value?.focus());
});

onUnmounted(() => document.removeEventListener("keydown", onKeyDown));
</script>

<template>
  <Teleport to="body">
    <div
        class="fixed inset-0 z-[70] flex items-center justify-center bg-zinc-950/85 p-4 backdrop-blur-sm"
        role="presentation"
        @click.self="close"
    >
      <section
          ref="dialogRef"
          class="w-full max-w-lg rounded-lg border border-primary/30 bg-zinc-900 shadow-2xl shadow-black/60 outline-none"
          role="dialog"
          aria-modal="true"
          aria-labelledby="smart-filter-title"
          tabindex="-1"
      >
        <header class="border-b border-zinc-700 px-5 py-4">
          <p class="text-[10px] font-semibold uppercase tracking-[0.18em] text-secondary">Related tracks</p>
          <h2 id="smart-filter-title" class="mt-1 truncate text-lg font-semibold text-zinc-100">
            {{ targetTrack.title }}
          </h2>
          <p class="mt-1 font-mono text-xs tabular-nums text-muted">
            {{ targetBpm.toFixed(1) }} BPM · {{ targetTrack.key ?? "No key" }}
          </p>
        </header>

        <div class="space-y-6 p-5">
          <div class="rounded border border-primary/25 bg-zinc-950/50 p-4">
            <label class="block text-sm font-medium text-zinc-100" for="smart-filter-bpm-tolerance">
              BPM tolerance
            </label>
            <div class="mt-2 flex items-center gap-4">
              <input
                  id="smart-filter-bpm-tolerance"
                  v-model.number="bpmTolerance"
                  type="range"
                  name="bpm-tolerance"
                  min="0"
                  max="50"
                  step="1"
                  class="h-2 min-w-0 flex-1 cursor-pointer accent-primary"
              >
              <output class="w-12 text-right font-mono text-sm tabular-nums text-primary" for="smart-filter-bpm-tolerance">
                ±{{ bpmTolerance }}%
              </output>
            </div>
            <p class="mt-2 font-mono text-xs tabular-nums text-muted">
              Match range: {{ bpmRange.min.toFixed(1) }} – {{ bpmRange.max.toFixed(1) }} BPM
            </p>
          </div>

          <div class="rounded border border-zinc-800 bg-zinc-950/35 p-4">
            <label class="block text-sm font-medium text-zinc-100" for="smart-filter-key-jumps">
              Maximum harmonic key jumps
            </label>
            <div class="mt-2 flex items-center gap-4">
              <input
                  id="smart-filter-key-jumps"
                  v-model.number="keyJumps"
                  type="range"
                  name="key-jumps"
                  min="0"
                  max="6"
                  step="1"
                  class="h-2 min-w-0 flex-1 cursor-pointer accent-primary"
              >
              <output class="w-12 text-right font-mono text-sm tabular-nums text-primary" for="smart-filter-key-jumps">
                {{ keyJumps }}
              </output>
            </div>
            <p class="mt-2 text-xs text-muted">0 requires an exact key; 6 allows the widest harmonic range.</p>
          </div>
        </div>

        <footer class="flex items-center justify-end gap-3 border-t border-zinc-800 px-5 py-4">
          <button
              type="button"
              class="rounded px-3 py-2 text-sm text-zinc-300 transition-colors hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
              @click="close"
          >
            Cancel
          </button>
          <button
              type="button"
              class="rounded bg-primary px-4 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-secondary"
              @click="applyFilter"
          >
            Apply Filter
          </button>
        </footer>
      </section>
    </div>
  </Teleport>
</template>
