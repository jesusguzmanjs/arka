<script setup lang="ts">
import { nextTick, shallowRef, useTemplateRef, watch } from "vue";
import { useErrorReporter } from "../../composables/core/useErrorReporter.ts";

const { activeError, dismissError, reportToDiscord } = useErrorReporter();
const userNotes = shallowRef("");
const isSubmitting = shallowRef(false);
const submitError = shallowRef<string | null>(null);
const submitSuccess = shallowRef(false);
const dialogRef = useTemplateRef<HTMLElement>("dialogRef");

watch(activeError, async (error) => {
  userNotes.value = "";
  submitError.value = null;
  submitSuccess.value = false;

  if (error !== null) {
    await nextTick();
    dialogRef.value?.focus();
  }
});

async function sendReport(): Promise<void> {
  if (activeError.value === null || isSubmitting.value) return;

  isSubmitting.value = true;
  submitError.value = null;
  try {
    await reportToDiscord(activeError.value, userNotes.value);
    submitSuccess.value = true;

    // Novedad: Esperamos 1.5 segundos para que el usuario lea el mensaje de éxito y cerramos
    setTimeout(() => {
      dismiss();
    }, 1500);

  } catch (error) {
    submitError.value = error instanceof Error ? error.message : String(error);
  } finally {
    isSubmitting.value = false;
  }
}

function dismiss(): void {
  if (isSubmitting.value) return;
  dismissError();
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="activeError !== null"
      class="fixed inset-0 z-[10001] flex items-center justify-center bg-zinc-950/90 p-4 backdrop-blur-sm"
      role="presentation"
    >
      <section
        ref="dialogRef"
        class="max-h-full w-full max-w-2xl overflow-y-auto overscroll-contain rounded-lg border border-error/70 bg-zinc-900 shadow-2xl shadow-black/60 outline-none"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="crash-report-title"
        aria-describedby="crash-report-description"
        tabindex="-1"
      >
        <header class="border-b border-error/40 px-5 py-4">
          <p class="text-[10px] font-semibold uppercase tracking-[0.18em] text-error">Action Required</p>
          <h2 id="crash-report-title" class="mt-1 text-lg font-semibold text-zinc-100">Arka Could Not Complete This Operation</h2>
          <p id="crash-report-description" class="mt-2 text-sm text-zinc-300">Review the error below, then send it to support or dismiss this report and try again.</p>
        </header>

        <form class="space-y-4 p-5" @submit.prevent="sendReport">
          <div>
            <p class="text-xs font-semibold uppercase tracking-wide text-zinc-400">Error Details</p>
            <pre class="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-words rounded border border-zinc-700 bg-zinc-950 p-3 font-mono text-xs leading-5 text-zinc-100">{{ activeError }}</pre>
          </div>

          <div>
            <label class="block text-sm font-medium text-zinc-200" for="crash-report-notes">What were you trying to do? <span class="text-zinc-500">Optional</span></label>
            <textarea
              id="crash-report-notes"
              v-model="userNotes"
              name="crash-report-notes"
              autocomplete="off"
              class="mt-2 block min-h-[110px] w-full resize-y rounded border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary disabled:cursor-not-allowed disabled:opacity-50"
              placeholder="For example: I was trying to save metadata for a playlist…"
              :disabled="isSubmitting"
            />
          </div>

          <p v-if="submitSuccess" class="text-sm text-success" role="status" aria-live="polite">Report sent. You can now dismiss this message.</p>
          <p v-else-if="submitError" class="text-sm text-error" role="alert">{{ submitError }}</p>

          <footer class="flex flex-wrap items-center justify-end gap-3 border-t border-zinc-800 pt-4">
            <button
              type="button"
              class="rounded px-3 py-2 text-sm text-zinc-300 transition-colors hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-50"
              :disabled="isSubmitting"
              @click="dismiss"
            >
              Dismiss
            </button>
            <button
              type="submit"
              class="rounded bg-error px-4 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-red-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300 disabled:cursor-not-allowed disabled:opacity-50"
              :disabled="isSubmitting || submitSuccess"
            >
              {{ isSubmitting ? "Sending…" : "Send Report" }}
            </button>
          </footer>
        </form>
      </section>
    </div>
  </Teleport>
</template>
