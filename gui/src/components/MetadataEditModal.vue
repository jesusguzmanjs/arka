<script setup lang="ts">
import { computed, reactive, shallowRef } from "vue";
import { useLibraryState } from "../composables/useLibraryState";
import { useSaveStore } from "../stores/useSaveStore";
import type {
  EditableMetadataField,
  LibraryTrack,
} from "../types/library";

type FieldValue = string | number;
type FieldDefinition = {
  key: EditableMetadataField;
  source: keyof LibraryTrack;
  label: string;
  multiline?: boolean;
  rating?: boolean;
};

const props = defineProps<{ tracks: readonly LibraryTrack[] }>();
const emit = defineEmits<{ close: []; saved: [] }>();

const FIELDS: readonly FieldDefinition[] = [
  { key: "title", source: "title", label: "Title" },
  { key: "release", source: "album", label: "Release (Album)" },
  { key: "artist", source: "artist", label: "Artist" },
  { key: "remixer", source: "remixer", label: "Remixer" },
  { key: "producer", source: "producer", label: "Producer" },
  { key: "genre", source: "genre", label: "Genre" },
  { key: "label", source: "label", label: "Label" },
  { key: "comment", source: "comment", label: "Comment" },
  { key: "comment2", source: "comment2", label: "Comment 2" },
  { key: "lyrics", source: "lyrics", label: "Lyrics", multiline: true },
  { key: "mix", source: "mix", label: "Mix" },
  { key: "rating", source: "rating", label: "Rating", rating: true },
];

function sharedValue(field: FieldDefinition): FieldValue | null {
  const [first, ...rest] = props.tracks;
  if (!first) return null;
  const value = first[field.source] as FieldValue;
  return rest.every((track) => track[field.source] === value) ? value : null;
}

const values = reactive<Record<EditableMetadataField, FieldValue | "">>(
  Object.fromEntries(FIELDS.map((field) => [field.key, sharedValue(field) ?? ""])) as Record<EditableMetadataField, FieldValue | "">,
);
const multipleFields = new Set(
  FIELDS.filter((field) => sharedValue(field) === null).map((field) => field.key),
);
const modifiedFields = reactive(new Set<EditableMetadataField>());
const writeToFiles = shallowRef(false);
const isApplying = shallowRef(false);
const errorMessage = shallowRef<string | null>(null);
const { patchTrackInCollection } = useLibraryState();
const saveStore = useSaveStore();

const canApply = computed(() => modifiedFields.size > 0 && !isApplying.value);

function markModified(field: EditableMetadataField): void {
  modifiedFields.add(field);
  errorMessage.value = null;
}

function fieldPlaceholder(field: FieldDefinition): string {
  return multipleFields.has(field.key) ? "(multiple values)" : "";
}

async function applyChanges(): Promise<void> {
  if (!canApply.value) return;
  isApplying.value = true;
  errorMessage.value = null;

  try {
    const updates: Record<string, string | number> = {};
    for (const field of FIELDS) {
      if (!modifiedFields.has(field.key)) continue;
      const target = field.key === "release" ? "album" : field.key;
      updates[target] = field.rating ? Number(values[field.key]) : String(values[field.key]);
    }
    for (const track of props.tracks) {
      patchTrackInCollection(track.location_path, updates);
      saveStore.markTrackDirty(track.location_path);
    }
    saveStore.setWriteMetadataToFiles(writeToFiles.value);
    emit("saved");
    emit("close");
  } catch (error) {
    errorMessage.value = `Could not apply metadata changes: ${String(error)}`;
  } finally {
    isApplying.value = false;
  }
}
</script>

<template>
  <div
    class="fixed inset-0 z-[60] flex items-center justify-center bg-zinc-950/85 p-4 backdrop-blur-sm"
    role="presentation"
    @click.self="!isApplying && emit('close')"
  >
    <section
      class="flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-primary/30 bg-zinc-900 shadow-2xl shadow-black/60"
      role="dialog"
      aria-modal="true"
      aria-labelledby="metadata-edit-title"
    >
      <header class="flex items-center justify-between border-b border-zinc-700 px-5 py-4">
        <div class="min-w-0">
          <p class="text-[10px] font-semibold uppercase tracking-[0.18em] text-secondary">Library tools</p>
          <h2 id="metadata-edit-title" class="text-pretty text-lg font-semibold text-zinc-100">Edit Metadata</h2>
          <p class="mt-1 text-xs text-muted">Apply changes to {{ tracks.length }} selected {{ tracks.length === 1 ? 'track' : 'tracks' }}.</p>
        </div>
        <button
          type="button"
          class="rounded p-2 text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          aria-label="Close metadata editor"
          :disabled="isApplying"
          @click="emit('close')"
        >
          <span aria-hidden="true">×</span>
        </button>
      </header>

      <form class="min-h-0 overflow-y-auto overscroll-contain p-5" @submit.prevent="applyChanges">
        <p class="mb-4 text-xs leading-5 text-dim">Fields marked “(multiple values)” differ across the selected tracks. Leave them untouched to preserve each track’s current value.</p>
        <div class="grid gap-x-4 gap-y-3 sm:grid-cols-2">
          <label v-for="field in FIELDS" :key="field.key" class="min-w-0 text-xs font-medium text-zinc-300" :class="field.multiline ? 'sm:col-span-2' : ''">
            <span class="mb-1.5 block">{{ field.label }}</span>
            <textarea
              v-if="field.multiline"
              v-model="values[field.key]"
              :name="field.key"
              :placeholder="fieldPlaceholder(field)"
              autocomplete="off"
              rows="3"
              class="block w-full resize-y rounded border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              @input="markModified(field.key)"
            />
            <select
              v-else-if="field.rating"
              v-model="values[field.key]"
              :name="field.key"
              class="block w-full rounded border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              @change="markModified(field.key)"
            >
              <option v-if="multipleFields.has(field.key)" value="" disabled>(multiple values)</option>
              <option v-for="rating in 6" :key="rating - 1" :value="rating - 1">{{ rating - 1 }}</option>
            </select>
            <input
              v-else
              v-model="values[field.key]"
              type="text"
              :name="field.key"
              :placeholder="fieldPlaceholder(field)"
              autocomplete="off"
              class="block w-full rounded border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              @input="markModified(field.key)"
            />
          </label>
        </div>

        <label class="mt-5 flex cursor-pointer items-center gap-3 rounded border border-zinc-700/80 bg-zinc-950/60 px-3 py-3 text-sm text-zinc-200 focus-within:ring-2 focus-within:ring-primary">
          <input v-model="writeToFiles" type="checkbox" name="write-to-files" class="h-4 w-4 accent-primary" />
          <span>Write changes to physical audio files</span>
        </label>

        <p v-if="errorMessage" class="mt-3 text-sm text-warn" role="alert" aria-live="polite">{{ errorMessage }}</p>

        <footer class="mt-5 flex items-center justify-end gap-3 border-t border-zinc-800 pt-4">
          <button type="button" class="rounded px-3 py-2 text-sm text-zinc-300 transition-colors hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary" :disabled="isApplying" @click="emit('close')">Cancel</button>
          <button type="submit" class="rounded bg-primary px-4 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-secondary disabled:cursor-not-allowed disabled:opacity-50" :disabled="!canApply">
            {{ isApplying ? 'Applying…' : 'Apply Changes' }}
          </button>
        </footer>
      </form>
    </section>
  </div>
</template>
