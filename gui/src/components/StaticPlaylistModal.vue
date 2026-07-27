<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, shallowRef, useTemplateRef } from "vue";
import { useCueGridSidecar } from "../composables/useCueGridSidecar";

const props = defineProps<{
  existingPlaylists: string[];
}>();

const emit = defineEmits<{
  close: [];
  saved: [name: string];
}>();

const playlistName = shallowRef("");
const isSaving = shallowRef(false);
const errorMessage = shallowRef<string | null>(null);
const nameInputRef = useTemplateRef<HTMLInputElement>("nameInputRef");
const { createStaticPlaylist } = useCueGridSidecar();

const isNameTaken = computed(() => {
  const normalizedName = playlistName.value.trim().toLowerCase();
  return normalizedName.length > 0 && props.existingPlaylists.some(
    (name) => name.trim().toLowerCase() === normalizedName,
  );
});

const isFormValid = computed(() => playlistName.value.trim().length > 0 && !isNameTaken.value);

async function createPlaylist(): Promise<void> {
  if (!isFormValid.value || isSaving.value) return;

  isSaving.value = true;
  errorMessage.value = null;
  const result = await createStaticPlaylist({ name: playlistName.value.trim(), entries: [] })
    .catch((error: unknown) => ({ ok: false as const, error: String(error) }));
  isSaving.value = false;

  if (!result.ok) {
    errorMessage.value = result.error;
    return;
  }

  emit("saved", result.result.name);
}

function requestClose(): void {
  if (!isSaving.value) emit("close");
}

function onKeyDown(event: KeyboardEvent): void {
  if (event.key === "Escape") requestClose();
}

onMounted(() => {
  document.addEventListener("keydown", onKeyDown);
  void nextTick(() => nameInputRef.value?.focus());
});

onUnmounted(() => document.removeEventListener("keydown", onKeyDown));
</script>

<template>
  <Teleport to="body">
    <div
      class="fixed inset-0 z-[70] flex items-center justify-center bg-zinc-950/85 p-4 backdrop-blur-sm"
      role="presentation"
      @click.self="requestClose"
    >
      <section
        class="w-full max-w-md rounded-lg border border-primary/30 bg-zinc-900 shadow-2xl shadow-black/60"
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-playlist-title"
      >
        <form @submit.prevent="createPlaylist">
          <header class="flex items-start justify-between gap-4 border-b border-zinc-700 px-5 py-4">
            <div>
              <p class="text-[10px] font-semibold uppercase tracking-[0.18em] text-secondary">Collection tools</p>
              <h2 id="new-playlist-title" class="mt-1 text-lg font-semibold text-zinc-100">New Playlist</h2>
            </div>
            <button
              type="button"
              class="rounded p-2 text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-50"
              aria-label="Close new playlist dialog"
              :disabled="isSaving"
              @click="requestClose"
            >
              <span aria-hidden="true">×</span>
            </button>
          </header>

          <div class="px-5 py-4">
            <label class="block text-xs font-medium text-zinc-300" for="new-playlist-name">
              <span class="mb-1.5 block">Playlist name</span>
              <input
                id="new-playlist-name"
                ref="nameInputRef"
                v-model="playlistName"
                type="text"
                name="playlist-name"
                autocomplete="off"
                placeholder="e.g. Late night"
                class="block w-full rounded border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary disabled:cursor-not-allowed disabled:opacity-50"
                :disabled="isSaving"
                :aria-invalid="isNameTaken"
                :aria-describedby="isNameTaken ? 'new-playlist-name-warning' : undefined"
              >
            </label>
            <p
              v-if="isNameTaken"
              id="new-playlist-name-warning"
              class="mt-1.5 text-xs text-warning"
              role="alert"
            >
              A playlist with this name already exists.
            </p>
            <p v-if="errorMessage" class="mt-3 text-sm text-warning" role="alert" aria-live="polite">{{ errorMessage }}</p>
          </div>

          <footer class="flex items-center justify-end gap-3 border-t border-zinc-800 px-5 py-4">
            <button type="button" class="rounded px-3 py-2 text-sm text-zinc-300 transition-colors hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-50" :disabled="isSaving" @click="requestClose">Cancel</button>
            <button type="submit" class="rounded bg-primary px-4 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-secondary disabled:cursor-not-allowed disabled:opacity-50" :disabled="!isFormValid || isSaving">
              {{ isSaving ? 'Creating…' : 'Create' }}
            </button>
          </footer>
        </form>
      </section>
    </div>
  </Teleport>
</template>
