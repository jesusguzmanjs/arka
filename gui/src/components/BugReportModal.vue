<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, useTemplateRef, watch } from "vue";

const webhookUrl = import.meta.env.VITE_DISCORD_WEBHOOK_URL?.trim();

const isOpen = ref(false);
const reportText = ref("");
const isSubmitting = ref(false);
const showSuccess = ref(false);
const errorMessage = ref<string | null>(null);
const dialogRef = useTemplateRef<HTMLElement>("dialogRef");
let closeTimeout: ReturnType<typeof setTimeout> | undefined;

const canSubmit = computed(
  () => reportText.value.trim().length > 0 && !isSubmitting.value,
);

function openModal(): void {
  if (closeTimeout) window.clearTimeout(closeTimeout);
  showSuccess.value = false;
  errorMessage.value = null;
  isOpen.value = true;
}

function closeModal(): void {
  if (isSubmitting.value) return;
  if (closeTimeout) window.clearTimeout(closeTimeout);
  isOpen.value = false;
  showSuccess.value = false;
}

async function submitReport(): Promise<void> {
  const description = reportText.value.trim();
  if (!description || isSubmitting.value) return;

  isSubmitting.value = true;
  errorMessage.value = null;

  try {
    if (!webhookUrl) {
      await new Promise((resolve) => window.setTimeout(resolve, 1_000));
    } else {
      const response = await fetch(webhookUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: "🚨 **New Feedback / Bug Report**",
          embeds: [
            {
              description,
              color: 15578123,
            },
          ],
        }),
      });

      if (!response.ok) {
        throw new Error(`Discord webhook request failed (${response.status}).`);
      }
    }

    reportText.value = "";
    showSuccess.value = true;
    closeTimeout = window.setTimeout(() => {
      isOpen.value = false;
      showSuccess.value = false;
    }, 2_000);
  } catch (error) {
    console.error("Could not send bug report:", error);
    errorMessage.value = "Could not send your feedback. Please try again.";
  } finally {
    isSubmitting.value = false;
  }
}

function onKeyDown(event: KeyboardEvent): void {
  if (event.key === "Escape" && isOpen.value) closeModal();
}

watch(isOpen, async (open) => {
  if (open) {
    await nextTick();
    dialogRef.value?.focus();
  }
});

window.addEventListener("keydown", onKeyDown);

onUnmounted(() => {
  window.removeEventListener("keydown", onKeyDown);
  if (closeTimeout) window.clearTimeout(closeTimeout);
});
</script>

<template>
  <button
    type="button"
    class="rounded border border-zinc-700/80 bg-zinc-900/80 px-2.5 py-1.5 text-xs font-medium text-muted transition-[color,background-color,border-color] duration-200 hover:border-secondary/60 hover:bg-zinc-800/90 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-secondary/70"
    aria-haspopup="dialog"
    :aria-expanded="isOpen"
    @click="openModal"
  >
    Feedback
  </button>

  <Teleport to="body">
    <div
      v-if="isOpen"
      class="fixed inset-0 z-[70] flex items-center justify-center bg-zinc-950/85 p-4 backdrop-blur-sm"
      role="presentation"
      @click.self="closeModal"
    >
      <section
        ref="dialogRef"
        class="max-h-full w-full max-w-xl overflow-y-auto overscroll-contain rounded-lg border border-primary/30 bg-zinc-900 shadow-2xl shadow-black/60 outline-none"
        role="dialog"
        aria-modal="true"
        aria-labelledby="bug-report-title"
        tabindex="-1"
      >
        <header class="border-b border-zinc-700 px-5 py-4">
          <p class="text-[10px] font-semibold uppercase tracking-[0.18em] text-secondary">Arka feedback</p>
          <h2 id="bug-report-title" class="mt-1 text-lg font-semibold text-zinc-100">Report a Bug / Feedback</h2>
        </header>

        <form class="space-y-4 p-5" @submit.prevent="submitReport">
          <label class="block text-sm font-medium text-zinc-200" for="bug-report-text">
            What would you like us to know?
          </label>
          <textarea
            id="bug-report-text"
            v-model="reportText"
            name="bug-report-text"
            autocomplete="off"
            class="block min-h-[120px] w-full resize-y rounded border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            placeholder="Describe the issue or share your feedback…"
            :disabled="isSubmitting || showSuccess"
          />

          <p v-if="showSuccess" class="text-sm text-success" role="status" aria-live="polite">
            Thank you — your feedback has been sent.
          </p>
          <p v-else-if="errorMessage" class="text-sm text-warn" role="alert">
            {{ errorMessage }}
          </p>

          <footer class="flex items-center justify-end gap-3 border-t border-zinc-800 pt-4">
            <button
              type="button"
              class="rounded px-3 py-2 text-sm text-zinc-300 transition-colors hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-50"
              :disabled="isSubmitting"
              @click="closeModal"
            >
              Cancel
            </button>
            <button
              type="submit"
              class="rounded bg-primary px-4 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-secondary disabled:cursor-not-allowed disabled:opacity-50"
              :disabled="!canSubmit"
            >
              {{ isSubmitting ? "Sending…" : "Send" }}
            </button>
          </footer>
        </form>
      </section>
    </div>
  </Teleport>
</template>
