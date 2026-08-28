<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, useTemplateRef, watch } from "vue";
import { useUpdater } from "../../composables/core/useUpdater.ts";

interface Props {
  open: boolean;
}

const props = defineProps<Props>();
const emit = defineEmits<{ close: [] }>();
const dialogRef = useTemplateRef<HTMLElement>("dialogRef");
const {
  currentAppVersion,
  isChecking,
  isUpdating,
  updateInfo,
  checkManually,
  executeUpdate,
} = useUpdater();

function closeModal(): void {
  if (!isUpdating.value) emit("close");
}

function onKeyDown(event: KeyboardEvent): void {
  if (event.key === "Escape" && props.open) closeModal();
}

watch(
  () => props.open,
  async (isOpen) => {
    if (!isOpen) return;
    await nextTick();
    dialogRef.value?.focus();
  },
);

onMounted(() => window.addEventListener("keydown", onKeyDown));
onUnmounted(() => window.removeEventListener("keydown", onKeyDown));
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-[80] flex items-center justify-center bg-zinc-950/85 p-4 backdrop-blur-sm"
      role="presentation"
      @click.self="closeModal"
    >
      <section
        ref="dialogRef"
        class="w-full max-w-md overscroll-contain rounded-lg border border-primary/30 bg-zinc-900 shadow-2xl shadow-black/60 outline-none"
        role="dialog"
        aria-modal="true"
        aria-labelledby="about-update-title"
        aria-describedby="about-update-description"
        tabindex="-1"
      >
        <header class="border-b border-zinc-700 px-5 py-4">
          <p class="text-[10px] font-semibold uppercase tracking-[0.18em] text-secondary">Arka</p>
          <h2 id="about-update-title" class="mt-1 text-lg font-semibold text-zinc-100">About Arka</h2>
          <p id="about-update-description" class="mt-2 text-sm text-zinc-400">
            Version <span class="font-mono text-zinc-200">{{ currentAppVersion || "Loading…" }}</span>
          </p>
        </header>

        <div class="space-y-4 p-5">
          <p v-if="isUpdating" class="text-sm text-secondary" role="status" aria-live="polite">
            Downloading and installing…
          </p>
          <template v-else-if="updateInfo !== null">
            <p class="text-sm text-success" role="status" aria-live="polite">
              Version {{ updateInfo.version }} is ready to install.
            </p>
            <button
              type="button"
              class="w-full rounded bg-success px-4 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-green-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-green-300 disabled:cursor-not-allowed disabled:opacity-50"
              @click="executeUpdate(updateInfo)"
            >
              Install and Restart
            </button>
          </template>
          <button
            v-else
            type="button"
            class="w-full rounded border border-primary/60 px-4 py-2 text-sm font-semibold text-primary transition-[color,background-color,border-color] hover:border-secondary hover:bg-primary/10 hover:text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-secondary disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="isChecking"
            @click="checkManually"
          >
            {{ isChecking ? "Checking for Updates…" : "Check for Updates" }}
          </button>
        </div>

        <footer class="flex justify-end border-t border-zinc-800 px-5 py-3">
          <button
            type="button"
            class="rounded px-3 py-2 text-sm text-zinc-300 transition-colors hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="isUpdating"
            @click="closeModal"
          >
            Close
          </button>
        </footer>
      </section>
    </div>
  </Teleport>
</template>
