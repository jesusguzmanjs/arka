<script setup lang="ts">
import { useVirtualizer } from "@tanstack/vue-virtual";
import { computed, nextTick, onMounted, onUnmounted, useTemplateRef, ref, watch } from "vue";
import { ALL_TRACKS_CONTEXT, useLibraryState } from "../composables/useLibraryState";
import { useConfigState } from "../composables/useConfigState";
import { usePlayerState } from "../composables/useTrackMetadata";
import { useRunState } from "../composables/useRunState";
import { useCueGridSidecar } from "../composables/useCueGridSidecar";
import type { LibraryTrack } from "../types/library";

const ROW_HEIGHT = 40;

const props = defineProps<{ disabled?: boolean }>();

const {
  playlistLeaves,
  selectedContext,
  libraryLoading,
  currentViewTracks,
  libraryError,
  loadLibrary,
  selectContext,
  selectTrackForPreview,
} = useLibraryState();
const { selectedPlaylist, selectedTrackPath, isValid } = useConfigState();
const { isLoadingTrack } = usePlayerState();
const { status, analysisStatus, setAnalysisStatus } = useRunState();
const { run, runSingleTrack, cancel, resetRun } = useCueGridSidecar();

const trackScrollElement = useTemplateRef<HTMLDivElement>("trackScrollElement");
const searchInputRef = useTemplateRef<HTMLInputElement>("searchInputRef");
const contextMenuVisible = ref(false);
const contextMenuX = ref(0);
const contextMenuY = ref(0);
const contextMenuTrack = ref<LibraryTrack | null>(null);
const searchQuery = ref("");
const isSearchVisible = ref(false);

const filteredTracks = computed(() => {
  const query = searchQuery.value.trim().toLowerCase();
  const baseTracks = currentViewTracks.value;

  if (!query) return baseTracks;

  return baseTracks.filter((track) =>
      (track.artist && track.artist.toLowerCase().includes(query)) ||
      (track.title && track.title.toLowerCase().includes(query)) ||
      (track.bpm !== null && String(track.bpm).includes(query))
  );
});

async function toggleSearch() {
  if (isSearchVisible.value) {
    clearSearch();
  } else {
    isSearchVisible.value = true;
    await nextTick();
    searchInputRef.value?.focus();
  }
}

function clearSearch() {
  searchQuery.value = "";
  isSearchVisible.value = false;
}

const virtualizer = useVirtualizer<HTMLDivElement, HTMLButtonElement>(
    computed(() => ({
      count: filteredTracks.value.length, // <- AHORA USA LA LISTA FILTRADA
      getScrollElement: () => trackScrollElement.value,
      estimateSize: () => ROW_HEIGHT,
      overscan: 10,
    })),
);

const currentTrackRecord = computed(() => {
  if (!selectedTrackPath.value) return null;
  return currentViewTracks.value.find(
    (track) => track.location_path === selectedTrackPath.value,
  ) ?? null;
});

const currentTrackTitle = computed(() => currentTrackRecord.value?.title ?? "Current Track");
const canRunPlaylist = computed(
  () => isValid.value && status.value !== "running" && selectedPlaylist.value !== null,
);
const canRunCurrentTrack = computed(
  () => status.value !== "running" && Boolean(selectedTrackPath.value) && !currentTrackRecord.value?.is_flex_grid,
);

const flexGridTooltip = "Variable BPM (Flex Grid) is unsupported; analysis is disabled.";

function bpmLabel(track: LibraryTrack): string {
  return track.bpm === null ? "—" : track.bpm.toFixed(1);
}

function gridLabel(track: LibraryTrack): string {
  return track.is_flex_grid ? "Flex" : "Grid";
}

function selectTrackForPreviewAndClearStatus(track: LibraryTrack): void {
  if (track.is_flex_grid) return;
  setAnalysisStatus(null);
  selectTrackForPreview(track);
}

function closeContextMenu(): void {
  contextMenuVisible.value = false;
  contextMenuTrack.value = null;
}

function openTrackContextMenu(event: MouseEvent, track: LibraryTrack): void {
  if (props.disabled || isLoadingTrack.value || track.is_flex_grid) return;
  contextMenuTrack.value = track;
  contextMenuX.value = event.clientX;
  contextMenuY.value = event.clientY;
  contextMenuVisible.value = true;
}

async function analyzeContextTrack(): Promise<void> {
  const track = contextMenuTrack.value;
  closeContextMenu();
  await nextTick();

  if (!track || props.disabled || isLoadingTrack.value || track.is_flex_grid) return;
  await runSingleTrack(track.location_path, track.title);
}

function onAnalyzePlaylist(): void {
  if (status.value === "running") return;
  if (status.value === "success" || status.value === "error" || status.value === "cancelled") {
    resetRun();
  }
  run();
}

function onAnalyzeCurrentTrack(): void {
  if (status.value === "running" || !selectedTrackPath.value || currentTrackRecord.value?.is_flex_grid) return;
  if (status.value === "success" || status.value === "error" || status.value === "cancelled") {
    resetRun();
  }
  void runSingleTrack(selectedTrackPath.value, currentTrackTitle.value);
}

function onWindowKeyDown(event: KeyboardEvent): void {
  if (event.key === "Escape") closeContextMenu();
}

watch(selectedContext, () => {
  clearSearch();
  void nextTick(() => trackScrollElement.value?.scrollTo({ top: 0 }));
});

onMounted(() => {
  window.addEventListener("keydown", onWindowKeyDown);
  void loadLibrary();
});

onUnmounted(() => window.removeEventListener("keydown", onWindowKeyDown));
</script>

<template>
  <section
    class="flex h-full min-h-0 flex-1 flex-col overflow-hidden bg-zinc-900"
    :class="{ 'pointer-events-none opacity-60': props.disabled }"
  >
    <div class="flex shrink-0 items-center justify-between border-b border-zinc-800 px-4 py-2.5">
      <div class="flex min-w-0 items-center gap-2">
        <span class="h-2 w-2 rounded-full bg-primary shadow-[0_0_8px_rgba(234,169,0,0.65)]" aria-hidden="true" />
        <h2 class="truncate text-xs font-semibold uppercase tracking-[0.18em] text-primary">Library</h2>
      </div>
      <span class="font-mono text-[11px] tabular-nums text-dim">{{ currentViewTracks.length }} tracks</span>
    </div>

    <div class="flex min-h-0 flex-1 overflow-hidden">
      <aside class="flex w-64 min-h-0 shrink-0 flex-col overflow-y-auto border-r border-zinc-800 bg-zinc-950/35">
        <div class="shrink-0 border-b border-zinc-800/80 px-4 py-2">
          <span class="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted">Navigate</span>
        </div>

        <nav class="min-h-0 flex-1 p-2" aria-label="Library contexts">
          <button
            type="button"
            class="mb-1 flex w-full items-center gap-2 rounded px-3 py-2 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            :class="selectedContext === ALL_TRACKS_CONTEXT
              ? 'border-l-2 border-primary bg-primary/10 pl-[10px] text-primary'
              : 'border-l-2 border-transparent text-muted hover:bg-zinc-800/70 hover:text-primary'"
            :aria-current="selectedContext === ALL_TRACKS_CONTEXT ? 'page' : undefined"
            @click="selectContext(ALL_TRACKS_CONTEXT)"
          >
            <svg class="h-4 w-4 shrink-0" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path d="M3 3.5A1.5 1.5 0 014.5 2h11A1.5 1.5 0 0117 3.5v13a1.5 1.5 0 01-1.5 1.5h-11A1.5 1.5 0 013 16.5v-13zM5 5v2h10V5H5zm0 4v2h6V9H5zm0 4v2h8v-2H5z" />
            </svg>
            <span class="truncate">All Tracks</span>
<!--            <span class="ml-auto font-mono text-[10px] tabular-nums text-dim">{{ currentViewTracks.length }}</span>-->
          </button>

          <div class="mb-1 mt-4 px-3 text-[10px] font-semibold uppercase tracking-[0.16em] text-dim">
            Playlists
          </div>

          <div v-if="libraryLoading && playlistLeaves.length === 0" class="px-3 py-3 text-xs text-dim">
            Loading playlists…
          </div>
          <div v-else-if="playlistLeaves.length === 0" class="px-3 py-3 text-xs leading-5 text-dim">
            No playlists found in collection.nml.
          </div>
          <div v-else class="space-y-0.5">
            <button
              v-for="playlist in playlistLeaves"
              :key="playlist.name"
              type="button"
              class="flex w-full min-w-0 items-center gap-2 rounded px-3 py-2 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
              :class="selectedContext === playlist.name
                ? 'border-l-2 border-primary bg-primary/10 pl-[10px] text-primary'
                : 'border-l-2 border-transparent text-muted hover:bg-zinc-800/70 hover:text-primary'"
              :aria-current="selectedContext === playlist.name ? 'page' : undefined"
              @click="selectContext(playlist.name)"
            >
              <svg class="h-4 w-4 shrink-0 text-secondary" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                <path d="M3 4.5A1.5 1.5 0 014.5 3h4l1.25 1.5h5.75A1.5 1.5 0 0117 6v9.5a1.5 1.5 0 01-1.5 1.5h-11A1.5 1.5 0 013 15.5v-11z" />
              </svg>
              <span class="min-w-0 flex-1 truncate">{{ playlist.name }}</span>
            </button>
          </div>
        </nav>
      </aside>

      <main class="flex min-w-0 min-h-0 flex-1 flex-col bg-zinc-900">
        <div class="flex shrink-0 items-center justify-between border-b border-zinc-800 px-4 py-2.5 h-10">
          <div class="min-w-0">
            <h3 class="truncate text-sm font-semibold text-primary">
              {{ selectedContext === ALL_TRACKS_CONTEXT ? "All Tracks" : selectedContext }}
            </h3>
          </div>

          <div class="flex items-center gap-3">
            <span v-if="libraryLoading" class="font-mono text-[11px] text-primary" aria-live="polite">Loading…</span>

            <div class="relative flex items-center h-6">
              <button
                  v-if="!isSearchVisible"
                  type="button"
                  class="text-zinc-500 hover:text-zinc-300 transition-colors p-1"
                  @click="toggleSearch"
                  title="Search in current view"
              >
                <svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
              </button>

              <div v-else class="relative flex items-center">
                <input
                    ref="searchInputRef"
                    v-model="searchQuery"
                    type="search"
                    placeholder="Filter tracks..."
                    class="bg-zinc-800/80 text-xs text-zinc-200 pl-7 pr-6 py-1 rounded border border-zinc-700 focus:border-primary/50 focus:ring-1 focus:ring-primary w-48 transition-all outline-none"
                    @keydown.escape="clearSearch"
                    @blur="searchQuery === '' ? isSearchVisible = false : null"
                />
                <svg class="h-3.5 w-3.5 text-zinc-500 absolute left-2 top-1/2 -translate-y-1/2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
                <button
                    v-if="searchQuery !== ''"
                    type="button"
                    class="absolute right-1.5 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300"
                    @click="clearSearch"
                >
                  <svg class="h-3 w-3" viewBox="0 0 20 20" fill="currentColor">
                    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd" />
                  </svg>
                </button>
              </div>
            </div>
          </div>
        </div>

        <div
          v-if="libraryError"
          class="flex min-h-0 flex-1 items-center justify-center px-6 text-center text-sm text-warn"
          role="alert"
        >
          {{ libraryError }}
        </div>
        <div
          v-else-if="libraryLoading && currentViewTracks.length === 0"
          class="flex min-h-0 flex-1 items-center justify-center px-6 text-sm text-dim"
          aria-live="polite"
        >
          Loading tracks…
        </div>
        <div
            v-else-if="filteredTracks.length === 0"
            class="flex min-h-0 flex-1 items-center justify-center px-6 text-center text-sm text-dim"
        >
          {{ searchQuery ? "No tracks match your search." : (selectedContext === ALL_TRACKS_CONTEXT ? "No tracks found in collection.nml." : "This playlist is empty.") }}
        </div>
        <div v-else class="flex min-h-0 flex-1 flex-col">
          <div
            class="grid shrink-0 grid-cols-[2.75rem_minmax(0,1.1fr)_minmax(0,2fr)_5rem_5rem] items-center border-b border-zinc-800/80 bg-zinc-950/45 px-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-dim"
            role="row"
          >
            <span class="px-2 py-2" role="columnheader" aria-label="Status" />
            <span class="px-2 py-2" role="columnheader">Artist</span>
            <span class="px-2 py-2" role="columnheader">Title</span>
            <span class="px-2 py-2 text-right" role="columnheader">BPM</span>
            <span class="px-2 py-2 text-right" role="columnheader">Grid</span>
          </div>

          <!-- The scroll container must own the height and vertical overflow. -->
          <div
            ref="trackScrollElement"
            class="min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain scrollbar-amber"
            role="table"
            :aria-rowcount="filteredTracks.length"
            tabindex="0"
          >
            <!-- The relative spacer preserves the complete virtual scroll height. -->
            <div
              class="relative w-full"
              :style="{ height: `${virtualizer.getTotalSize()}px` }"
            >
              <button
                v-for="virtualRow in virtualizer.getVirtualItems()"
                :key="String(virtualRow.key)"
                type="button"
                class="absolute left-0 top-0 grid w-full grid-cols-[2.75rem_minmax(0,1.1fr)_minmax(0,2fr)_5rem_5rem] items-center border-b border-zinc-800/50 px-2 text-left text-sm transition-colors focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
                :class="[
                  filteredTracks[virtualRow.index].location_path === selectedTrackPath
                    ? 'bg-primary/10 text-primary'
                    : 'text-primary hover:bg-zinc-800/70',
                  filteredTracks[virtualRow.index].is_flex_grid ? 'cursor-not-allowed opacity-50' : '',
                  isLoadingTrack ? 'pointer-events-none opacity-50' : '',
                ]"
                :style="{ height: `${ROW_HEIGHT}px`, transform: `translateY(${virtualRow.start}px)` }"
                :aria-rowindex="virtualRow.index + 2"
                :aria-label="`Load ${filteredTracks[virtualRow.index].artist} – ${filteredTracks[virtualRow.index].title}`"
                :aria-disabled="filteredTracks[virtualRow.index].is_flex_grid || isLoadingTrack"
                :title="filteredTracks[virtualRow.index].is_flex_grid ? flexGridTooltip : undefined"
                @click="!isLoadingTrack && !filteredTracks[virtualRow.index].is_flex_grid && selectTrackForPreviewAndClearStatus(filteredTracks[virtualRow.index])"
                @contextmenu.prevent="openTrackContextMenu($event, filteredTracks[virtualRow.index])"
              >
                <span class="flex items-center px-2" role="cell">
                  <svg
                    v-if="filteredTracks[virtualRow.index].is_flex_grid"
                    class="h-4 w-4 text-primary"
                    viewBox="0 0 20 20"
                    fill="currentColor"
                    aria-hidden="true"
                  >
                    <path fill-rule="evenodd" d="M8.485 2.495a1.75 1.75 0 013.03 0l6.285 10.875A1.75 1.75 0 0116.285 16H3.715a1.75 1.75 0 01-1.515-2.63L8.485 2.495zM10 7a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5a.75.75 0 01-.75-1.5zM10 13a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd" />
                  </svg>
                  <svg v-else class="h-4 w-4 text-secondary" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                    <path d="M6.5 4.25A1.25 1.25 0 018.39 3.2l7.1 5.75a1.35 1.35 0 010 2.1l-7.1 5.75A1.25 1.25 0 016.5 15.83V4.25z" />
                  </svg>
                </span>
                <span class="min-w-0 truncate px-2" role="cell">{{ filteredTracks[virtualRow.index].artist }}</span>
                <span class="min-w-0 truncate px-2 font-medium" role="cell">{{ filteredTracks[virtualRow.index].title }}</span>
                <span class="px-2 text-right font-mono text-xs tabular-nums text-muted" role="cell">{{ bpmLabel(filteredTracks[virtualRow.index]) }}</span>
                <span
                  class="px-2 text-right text-[11px]"
                  :class="filteredTracks[virtualRow.index].is_flex_grid ? 'text-primary' : 'text-dim'"
                  role="cell"
                >
                  {{ gridLabel(filteredTracks[virtualRow.index]) }}
                </span>
              </button>
            </div>
          </div>
        </div>
      </main>
    </div>

    <div class="shrink-0 border-t border-zinc-800/80 bg-zinc-950/45 p-2">
      <div class="flex flex-wrap items-center gap-2">
        <div
          v-if="analysisStatus"
          class="min-w-0 flex-1 truncate rounded border border-zinc-800/80 bg-zinc-950 px-2 py-1 text-center font-mono text-[11px] text-zinc-400"
          aria-live="polite"
          :title="analysisStatus"
        >
          {{ analysisStatus }}
        </div>
        <div v-else class="flex-1" />

        <button
          v-if="status !== 'running'"
          type="button"
          :disabled="!canRunPlaylist"
          class="inline-flex min-w-40 items-center justify-center gap-2 rounded-md px-3 py-2 text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          :class="canRunPlaylist ? 'bg-primary text-zinc-950 hover:bg-accent' : 'cursor-not-allowed bg-zinc-800/50 text-zinc-600'"
          @click="onAnalyzePlaylist"
        >
          Analyze Playlist
        </button>
        <button
          v-if="status !== 'running'"
          type="button"
          :disabled="!canRunCurrentTrack"
          class="inline-flex min-w-44 items-center justify-center gap-2 rounded-md border px-3 py-2 text-xs font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          :class="canRunCurrentTrack ? 'border-secondary/30 bg-zinc-900 text-primary hover:bg-zinc-800' : 'cursor-not-allowed border-zinc-800/80 bg-transparent text-zinc-600'"
          @click="onAnalyzeCurrentTrack"
        >
          Analyze Current Track
        </button>
        <button
          v-if="status === 'running'"
          type="button"
          class="w-full rounded-md border border-red-500/40 bg-zinc-900 px-3 py-2 text-xs font-medium text-red-400 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400 hover:bg-red-950/40"
          @click="cancel"
        >
          Cancel Analysis…
        </button>
      </div>
    </div>
  </section>

  <div
    v-if="contextMenuVisible"
    class="fixed inset-0 z-40"
    aria-hidden="true"
    @click="closeContextMenu"
  >
    <div
      class="fixed z-50 min-w-40 rounded-md border border-zinc-700 bg-zinc-900 py-1 text-sm text-zinc-200 shadow-xl"
      :style="{ left: `${contextMenuX}px`, top: `${contextMenuY}px` }"
      role="menu"
      aria-label="Track actions"
      @click.stop
    >
      <button
        type="button"
        class="w-full px-3 py-2 text-left transition-colors hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
        role="menuitem"
        @click="analyzeContextTrack"
      >
        Analyze Track
      </button>
    </div>
  </div>
</template>
