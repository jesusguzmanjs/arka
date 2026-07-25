<script setup lang="ts">
defineProps<{
  isRunning: boolean;
}>();
</script>

<template>
  <section
    v-if="isRunning"
    class="fixed inset-0 z-[9999] flex items-center justify-center bg-zinc-950/95 p-6 text-center backdrop-blur-md"
    role="alertdialog"
    aria-modal="true"
    aria-labelledby="traktor-safety-title"
    aria-describedby="traktor-safety-message"
  >
    <div class="max-w-xl rounded-lg border border-warning/70 bg-panel p-8 shadow-2xl shadow-black/60">
      <div class="mx-auto flex h-16 w-16 items-center justify-center rounded-full border-2 border-warning text-4xl text-warning" aria-hidden="true">
        !
      </div>
      <h1 id="traktor-safety-title" class="mt-5 text-xl font-semibold text-foreground">Traktor is currently running</h1>
      <p id="traktor-safety-message" class="mt-3 text-sm leading-6 text-muted">
        Please close Traktor to safely manage your collection and prevent data loss.
      </p>
      <p class="mt-5 text-xs text-muted">CueGrid will unlock automatically when Traktor has closed.</p>
    </div>
  </section>
</template>
