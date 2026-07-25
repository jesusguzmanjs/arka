<script setup lang="ts">
import { useVirtualizer } from "@tanstack/vue-virtual";
import { computed, nextTick, onMounted, onUnmounted, shallowRef, useTemplateRef, ref, watch } from "vue";
import { ALL_TRACKS_CONTEXT, useLibraryState } from "../composables/useLibraryState";
import { useConfigState } from "../composables/useConfigState";
import { usePlayerState } from "../composables/useTrackMetadata";
import { useColumnResize } from "../composables/useColumnResize";
import { useSaveStore } from "../stores/useSaveStore";
import type { LibraryTrack } from "../types/library";
import AutoCueModal from "./AutoCueModal.vue";
import MetadataEditModal from "./MetadataEditModal.vue";
import SmartPlaylistModal from "./SmartPlaylistModal.vue";
import TrackContextMenu from "./TrackContextMenu.vue";
import PlaylistContextMenu from "./PlaylistContextMenu.vue";

const ROW_HEIGHT = 40;
const TRACK_COLUMNS = ["action", "artist", "title", "bpm", "key", "duration"] as const;
const SORTABLE_COLUMNS = ["artist", "title", "bpm", "key", "duration"] as const;

type TrackColumn = (typeof TRACK_COLUMNS)[number];
type SortColumn = (typeof SORTABLE_COLUMNS)[number];
type SortOrder = "asc" | "desc";

const props = defineProps<{ disabled?: boolean }>();

const {
  playlistLeaves,
  collection,
  selectedContext,
  selectedLibraryPaths,
  libraryLoading,
  currentViewTracks,
  libraryError,
  loadLibrary,
  selectContext,
  selectLibraryTrack,
  selectOnlyLibraryTrack,
  selectAllLibraryTracks,
  selectTrackForPreview,
  updatePlaylist,
  deletePlaylist,
} = useLibraryState();
const { selectedTrackPath } = useConfigState();
const { isLoadingTrack } = usePlayerState();
const saveStore = useSaveStore();

const trackScrollElement = useTemplateRef<HTMLDivElement>("trackScrollElement");
const trackTableContainer = useTemplateRef<HTMLDivElement>("trackTableContainer");
const searchInputRef = useTemplateRef<HTMLInputElement>("searchInputRef");
const searchQuery = ref("");
const isSearchVisible = ref(false);
const sortColumn = shallowRef<SortColumn | null>(null);
const sortOrder = shallowRef<SortOrder>("asc");
const contextMenu = ref<{
  x: number;
  y: number;
  target: { kind: "track" } | { kind: "playlist"; name: string };
} | null>(null);
const playlistContextMenu = ref<{ x: number; y: number; uuid: string } | null>(null);
const editingPlaylistUuid = shallowRef<string | null>(null);
const editingPlaylistName = ref("");
const dragOverPlaylistUuid = shallowRef<string | null>(null);
const isMetadataModalOpen = shallowRef(false);
const isAutoCueModalOpen = shallowRef(false);
const isSmartPlaylistModalOpen = shallowRef(false);
const toastMessage = shallowRef<string | null>(null);
let toastTimer: number | undefined;

// --- POINTER DRAG STATE ---
const isPointerDragging = shallowRef(false);
const dragMousePos = ref({ x: 0, y: 0 });
const pointerDraggedPaths = shallowRef<string[]>([]);
let startPointerPos = { x: 0, y: 0 };

const selectedMetadataTracks = computed(() => selectedLibraryPaths.value
    .map((path) => collection.value[path])
    .filter((track): track is LibraryTrack => track !== undefined));

const existingPlaylistNames = computed(() => playlistLeaves.value.map((playlist) => playlist.name));
const activePlaylist = computed(() => playlistLeaves.value.find((playlist) => playlist.name === selectedContext.value || playlist.uuid === selectedContext.value));

const filteredTracks = computed(() => {
  const query = searchQuery.value.trim().toLowerCase();
  const baseTracks = currentViewTracks.value;

  if (!query) return baseTracks;

  return baseTracks.filter((track) =>
      (track.artist && track.artist.toLowerCase().includes(query)) ||
      (track.title && track.title.toLowerCase().includes(query)) ||
      (track.key && track.key.toLowerCase().includes(query)) ||
      (track.bpm !== null && String(track.bpm).includes(query))
  );
});

const sortedTracks = computed(() => {
  const activeColumn = sortColumn.value;
  if (activeColumn === null) return filteredTracks.value;

  const direction = sortOrder.value === "asc" ? 1 : -1;
  return [...filteredTracks.value].sort((left, right) => {
    if (activeColumn === "bpm" || activeColumn === "duration") {
      const leftValue = activeColumn === "bpm" ? left.bpm : left.duration_ms;
      const rightValue = activeColumn === "bpm" ? right.bpm : right.duration_ms;
      if (leftValue === null) return rightValue === null ? 0 : 1;
      if (rightValue === null) return -1;
      return (leftValue - rightValue) * direction;
    }

    const leftValue = activeColumn === "key" ? left.key ?? "" : left[activeColumn];
    const rightValue = activeColumn === "key" ? right.key ?? "" : right[activeColumn];
    return leftValue.localeCompare(rightValue, undefined, { sensitivity: "base" }) * direction;
  });
});

const { columnWidths, startResize } = useColumnResize({
  columns: TRACK_COLUMNS,
  initialWidths: { action: 6, artist: 22, title: 35, bpm: 11, key: 11, duration: 15 },
  minWidths: { action: 5, artist: 12, title: 16, bpm: 8, key: 8, duration: 10 },
  getContainer: () => trackTableContainer.value,
});

const trackGridStyle = computed(() => ({
  gridTemplateColumns: TRACK_COLUMNS.map((column) => `${columnWidths[column]}%`).join(" "),
}));

function columnWidth(column: TrackColumn): string {
  return `${columnWidths[column]}%`;
}

function toggleSort(column: SortColumn): void {
  if (sortColumn.value === column) {
    if (sortOrder.value === "asc") sortOrder.value = "desc";
    else { sortColumn.value = null; sortOrder.value = "asc"; }
    return;
  }
  sortColumn.value = column;
  sortOrder.value = "asc";
}

function isSortColumn(column: TrackColumn): column is SortColumn {
  return (SORTABLE_COLUMNS as readonly string[]).includes(column);
}

function columnLabel(column: TrackColumn): string {
  return column === "action" ? "Status" : column.toUpperCase();
}

function formatDuration(durationMs: number | null): string {
  if (durationMs === null || durationMs < 0) return "—";
  const totalSeconds = Math.floor(durationMs / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

async function toggleSearch() {
  if (isSearchVisible.value) { clearSearch(); }
  else { isSearchVisible.value = true; await nextTick(); searchInputRef.value?.focus(); }
}

function clearSearch() {
  searchQuery.value = "";
  isSearchVisible.value = false;
}

const virtualizer = useVirtualizer<HTMLDivElement, HTMLDivElement>(
    computed(() => ({
      count: sortedTracks.value.length,
      getScrollElement: () => trackScrollElement.value,
      estimateSize: () => ROW_HEIGHT,
      overscan: 10,
    })),
);

const flexGridTooltip = "Variable BPM (Flex Grid) is unsupported for Auto Cue.";

function bpmLabel(track: LibraryTrack): string {
  return track.bpm === null ? "—" : track.bpm.toFixed(1);
}

function previewTrack(track: LibraryTrack): void {
  if (track.is_flex_grid) return;
  selectTrackForPreview(track);
}

function contextMenuPosition(event: MouseEvent): Pick<NonNullable<typeof contextMenu.value>, "x" | "y"> {
  return {
    x: Math.max(8, Math.min(event.clientX, window.innerWidth - 192)),
    y: Math.max(8, Math.min(event.clientY, window.innerHeight - 48)),
  };
}

function openTrackContextMenu(event: MouseEvent, track: LibraryTrack): void {
  if (!selectedLibraryPaths.value.includes(track.location_path)) {
    selectOnlyLibraryTrack(track);
  }
  contextMenu.value = { ...contextMenuPosition(event), target: { kind: "track" } };
}

function openPlaylistContextMenu(event: MouseEvent, name: string): void {
  const playlist = playlistLeaves.value.find((candidate) => candidate.name === name);
  if (playlist) playlistContextMenu.value = { ...contextMenuPosition(event), uuid: playlist.uuid };
}

function openAutoCuePlaylist(): void {
  const uuid = playlistContextMenu.value?.uuid;
  const playlist = playlistLeaves.value.find((candidate) => candidate.uuid === uuid);
  if (!playlist || props.disabled) {
    playlistContextMenu.value = null;
    return;
  }

  selectContext(playlist.name);
  const playlistTracks = playlist.track_paths
      .map((path) => collection.value[path])
      .filter((track): track is LibraryTrack => track !== undefined);
  selectAllLibraryTracks(playlistTracks);
  playlistContextMenu.value = null;
  isAutoCueModalOpen.value = true;
}

function closeContextMenu(): void {
  contextMenu.value = null;
}

function startPlaylistRename(): void {
  const uuid = playlistContextMenu.value?.uuid;
  const playlist = playlistLeaves.value.find((candidate) => candidate.uuid === uuid);
  playlistContextMenu.value = null;
  if (!playlist) return;
  editingPlaylistUuid.value = playlist.uuid;
  editingPlaylistName.value = playlist.name;
}

function commitPlaylistRename(): void {
  const uuid = editingPlaylistUuid.value;
  const playlist = playlistLeaves.value.find((candidate) => candidate.uuid === uuid);
  const name = editingPlaylistName.value.trim();
  editingPlaylistUuid.value = null;
  if (!uuid || !playlist || !name || name === playlist.name) return;

  const oldName = playlist.name;
  updatePlaylist(uuid, { name, track_paths: playlist.track_paths });
  saveStore.markPlaylistDirty(uuid);

  if (selectedContext.value === oldName) {
    selectContext(name);
  }
}

function deletePlaylistFromMenu(): void {
  const uuid = playlistContextMenu.value?.uuid;
  playlistContextMenu.value = null;
  if (!uuid || !deletePlaylist(uuid)) return;
  saveStore.markPlaylistDirty(uuid);
}

// --- CUSTOM POINTER DRAG LOGIC ---
function onTrackPointerDown(event: PointerEvent, track: LibraryTrack): void {
  // Solo iniciar drag con clic izquierdo
  if (event.button !== 0) return;

  const paths = selectedLibraryPaths.value.includes(track.location_path)
      ? selectedLibraryPaths.value
      : [track.location_path];

  pointerDraggedPaths.value = paths;
  startPointerPos = { x: event.clientX, y: event.clientY };

  window.addEventListener("pointermove", onGlobalPointerMove);
  window.addEventListener("pointerup", onGlobalPointerUp);
}

function onGlobalPointerMove(event: PointerEvent): void {
  const dx = Math.abs(event.clientX - startPointerPos.x);
  const dy = Math.abs(event.clientY - startPointerPos.y);

  // Umbral de movimiento para no dispararlo en clics accidentales
  if (!isPointerDragging.value && (dx > 5 || dy > 5)) {
    isPointerDragging.value = true;
  }

  if (isPointerDragging.value) {
    dragMousePos.value = { x: event.clientX, y: event.clientY };

    // Detectar si el puntero está sobre una playlist
    const targetEl = document.elementFromPoint(event.clientX, event.clientY);
    const playlistEl = targetEl?.closest("[data-playlist-uuid]");
    const targetUuid = playlistEl?.getAttribute("data-playlist-uuid") ?? null;

    dragOverPlaylistUuid.value = targetUuid;
  }
}

function onGlobalPointerUp(): void {
  window.removeEventListener("pointermove", onGlobalPointerMove);
  window.removeEventListener("pointerup", onGlobalPointerUp);

  if (isPointerDragging.value && dragOverPlaylistUuid.value) {
    const playlistUuid = dragOverPlaylistUuid.value;
    const paths = [...pointerDraggedPaths.value];

    const playlist = playlistLeaves.value.find((candidate) => candidate.uuid === playlistUuid);
    if (playlist && paths.length > 0) {
      const existingPaths = new Set(playlist.track_paths);
      const newPaths = paths.filter((path) => !existingPaths.has(path));

      if (newPaths.length > 0) {
        updatePlaylist(playlist.uuid, {
          name: playlist.name,
          track_paths: [...playlist.track_paths, ...newPaths],
        });
        saveStore.markPlaylistDirty(playlist.uuid);
        showToast(`Added ${newPaths.length} track(s) to ${playlist.name}`);
      } else {
        showToast("Tracks are already in this playlist.");
      }
    }
  }

  // Reset del estado del drag
  isPointerDragging.value = false;
  dragOverPlaylistUuid.value = null;
  pointerDraggedPaths.value = [];
}
// --- END CUSTOM DRAG ---

function removeSelectedTracksFromPlaylist(): void {
  const playlist = activePlaylist.value;
  if (!playlist) return;
  const selected = new Set(selectedLibraryPaths.value);

  updatePlaylist(playlist.uuid, {
    name: playlist.name,
    track_paths: playlist.track_paths.filter((path) => !selected.has(path)),
  });
  saveStore.markPlaylistDirty(playlist.uuid);
  closeContextMenu();
}

function openAutoCueModal(): void {
  closeContextMenu();
  if (selectedMetadataTracks.value.length === 0 || props.disabled) return;
  isAutoCueModalOpen.value = true;
}

function runContextMenuAction(): void {
  const target = contextMenu.value?.target;
  if (!target) return;

  if (target.kind === "playlist") {
    selectContext(target.name);
  }

  openAutoCueModal();
}

function openMetadataEditor(): void {
  if (selectedMetadataTracks.value.length === 0) return;
  closeContextMenu();
  isMetadataModalOpen.value = true;
}

function showToast(message: string): void {
  toastMessage.value = message;
  if (toastTimer !== undefined) window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    toastMessage.value = null;
  }, 4000);
}

async function handleSmartPlaylistSaved(name: string): Promise<void> {
  isSmartPlaylistModalOpen.value = false;
  await loadLibrary();
  if (libraryError.value) {
    showToast(`Smart Playlist “${name}” was created, but the library could not refresh.`);
    return;
  }
  selectContext(name);
  showToast(`Smart Playlist “${name}” created.`);
}

function selectAllSongs(): void {
  selectAllLibraryTracks(currentViewTracks.value);
  closeContextMenu();
}

watch(selectedContext, () => {
  clearSearch();
  void nextTick(() => trackScrollElement.value?.scrollTo({ top: 0 }));
});

onMounted(() => void loadLibrary());
onUnmounted(() => {
  if (toastTimer !== undefined) window.clearTimeout(toastTimer);
});
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
      <div class="flex items-center gap-3">
        <span class="font-mono text-[11px] tabular-nums text-dim">{{ currentViewTracks.length }} tracks</span>
        <button
            type="button"
            class="rounded border border-primary/50 px-2.5 py-1 text-[11px] font-semibold text-primary transition-colors hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:border-zinc-700 disabled:text-zinc-600"
            :disabled="selectedMetadataTracks.length === 0 || props.disabled"
            @click="openMetadataEditor"
        >
          Edit Metadata<span v-if="selectedMetadataTracks.length"> ({{ selectedMetadataTracks.length }})</span>
        </button>
        <button
            type="button"
            class="rounded border border-primary bg-primary px-2.5 py-1 text-[11px] font-semibold text-zinc-950 transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-secondary disabled:cursor-not-allowed disabled:border-zinc-700 disabled:bg-zinc-800 disabled:text-zinc-600"
            :disabled="selectedMetadataTracks.length === 0 || props.disabled"
            @click="openAutoCueModal"
        >
          Auto Cue ({{ selectedMetadataTracks.length }})
        </button>
      </div>
    </div>

    <div class="flex min-h-0 flex-1 overflow-hidden">
      <aside class="flex w-64 min-h-0 shrink-0 flex-col overflow-hidden border-r border-zinc-800 bg-zinc-950/35">
        <div class="shrink-0 border-b border-zinc-800/80 px-4 py-2">
          <span class="text-[11px] font-semibold uppercase tracking-[0.16em] text-muted">Navigate</span>
        </div>

        <nav class="shrink-0 p-2" aria-label="Library contexts">
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
          </button>

        </nav>

        <div class="shrink-0 px-5 pb-2 pt-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-dim">
          <span>Playlists</span>
        </div>

        <div class="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 pb-2 scrollbar-amber" aria-label="Playlists">
          <div v-if="libraryLoading && playlistLeaves.length === 0" class="px-3 py-3 text-xs text-dim">
            Loading playlists…
          </div>
          <div v-else-if="playlistLeaves.length === 0" class="px-3 py-3 text-xs leading-5 text-dim">
            No playlists found in collection.nml.
          </div>
          <div v-else class="space-y-0.5">
            <div
                v-for="playlist in playlistLeaves"
                :key="playlist.uuid"
                :data-playlist-uuid="playlist.uuid"
                role="button"
                tabindex="0"
                class="flex w-full min-w-0 select-none items-center gap-2 rounded px-3 py-2 text-left text-sm transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary cursor-pointer"
                :class="[
                          selectedContext === playlist.name || selectedContext === playlist.uuid
                            ? 'border-l-2 border-primary bg-primary/10 pl-[10px] text-primary'
                            : 'border-l-2 border-transparent text-muted hover:bg-zinc-800/70 hover:text-primary',
                          dragOverPlaylistUuid === playlist.uuid ? 'bg-primary/30 ring-2 ring-primary text-primary' : '',
                          isPointerDragging ? 'cursor-default' : 'cursor-pointer' // Condicional para cursor Arrow durante drag.
                        ]"
                @click="selectContext(playlist.name)"
                @contextmenu.prevent="openPlaylistContextMenu($event, playlist.name)"
            >
              <svg class="pointer-events-none h-4 w-4 shrink-0 text-secondary" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                <path d="M3 4.5A1.5 1.5 0 014.5 3h4l1.25 1.5h5.75A1.5 1.5 0 0117 6v9.5a1.5 1.5 0 01-1.5 1.5h-11A1.5 1.5 0 013 15.5v-11z" />
              </svg>
              <input
                  v-if="editingPlaylistUuid === playlist.uuid"
                  v-model="editingPlaylistName"
                  class="min-w-0 flex-1 rounded bg-zinc-800 px-1 text-sm text-zinc-100 outline-none ring-1 ring-primary"
                  aria-label="Playlist name"
                  @click.stop
                  @keydown.enter.prevent="commitPlaylistRename"
                  @keydown.esc.prevent="editingPlaylistUuid = null"
                  @blur="commitPlaylistRename"
              >
              <span v-else class="pointer-events-none min-w-0 flex-1 truncate">{{ playlist.name }}</span>
            </div>
          </div>
        </div>

        <div class="shrink-0 border-t border-zinc-800/80 p-2">
          <button
              type="button"
              class="w-full rounded border border-primary/50 text-primary bg-zinc-900/70 px-3 py-2 text-left text-xs font-semibold  transition-colors hover:border-primary/50 hover:bg-primary/10 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-40"
              :disabled="props.disabled"
              @click="isSmartPlaylistModalOpen = true"
          >
            Create Smart Playlist
          </button>
        </div>
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
        <div ref="trackTableContainer" v-else class="flex min-h-0 flex-1 flex-col">
          <table class="w-full table-fixed border-collapse border-y border-zinc-700/90 bg-zinc-950/30 text-[10px] font-semibold uppercase tracking-[0.14em] text-dim shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]">
            <colgroup>
              <col v-for="column in TRACK_COLUMNS" :key="column" :style="{ width: columnWidth(column) }" />
            </colgroup>
            <thead>
            <tr>
              <th
                  v-for="column in TRACK_COLUMNS"
                  :key="column"
                  scope="col"
                  class="relative h-8 px-2 text-left font-semibold"
                  :class="isSortColumn(column) ? 'cursor-pointer hover:text-zinc-200' : ''"
                  :aria-sort="isSortColumn(column) && sortColumn === column ? (sortOrder === 'asc' ? 'ascending' : 'descending') : undefined"
                  @click="isSortColumn(column) && toggleSort(column)"
              >
                <button
                    v-if="isSortColumn(column)"
                    type="button"
                    class="group inline-flex max-w-full items-center gap-1 rounded px-0.5 py-1 text-inherit transition-colors hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary"
                >
                  <span class="truncate">{{ columnLabel(column) }}</span>
                  <span v-if="sortColumn === column" class="text-[9px] text-secondary" aria-hidden="true">
                      {{ sortOrder === 'asc' ? '↑' : '↓' }}
                    </span>
                </button>
                <span v-else class="sr-only">{{ columnLabel(column) }}</span>
                <span
                    v-if="column !== 'duration'"
                    class="absolute inset-y-1 right-0 z-10 w-2 cursor-col-resize border-r border-primary/90 transition-colors hover:border-primary"
                    role="separator"
                    aria-orientation="vertical"
                    :aria-label="`Resize ${columnLabel(column)} column`"
                    @mousedown="startResize(column, $event)"
                    @click.stop
                />
              </th>
            </tr>
            </thead>
          </table>

          <div
              ref="trackScrollElement"
              class="min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain scrollbar-amber"
              role="table"
              :aria-rowcount="sortedTracks.length"
              tabindex="0"
          >
            <div
                class="relative w-full"
                :style="{ height: `${virtualizer.getTotalSize()}px` }"
            >
              <div
                  v-for="virtualRow in virtualizer.getVirtualItems()"
                  :key="String(virtualRow.key)"
                  role="row"
                  tabindex="0"
                  class="absolute left-1 top-0 grid w-full select-none items-center border-b border-zinc-800/50 text-left text-sm transition-colors focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
                  :class="[
                  selectedLibraryPaths.includes(sortedTracks[virtualRow.index].location_path)
                    ? 'bg-secondary/20 text-zinc-100 ring-1 ring-inset ring-secondary/50'
                    : 'text-primary hover:bg-zinc-800/70',
                  sortedTracks[virtualRow.index].location_path === selectedTrackPath
                    ? 'border-l-2 border-l-primary bg-primary/5'
                    : '',
                  sortedTracks[virtualRow.index].is_flex_grid ? 'opacity-50' : '',
                  isPointerDragging ? 'cursor-default' : 'cursor-pointer' // Condicional para cursor Arrow durante drag.
                ]"
                  :style="{ ...trackGridStyle, height: `${ROW_HEIGHT}px`, transform: `translateY(${virtualRow.start}px)` }"
                  :aria-rowindex="virtualRow.index + 2"
                  :aria-label="`Load ${sortedTracks[virtualRow.index].artist} – ${sortedTracks[virtualRow.index].title}`"
                  :title="sortedTracks[virtualRow.index].is_flex_grid ? flexGridTooltip : undefined"
                  @click="selectLibraryTrack(sortedTracks[virtualRow.index], $event, sortedTracks)"
                  @dblclick="!isLoadingTrack && previewTrack(sortedTracks[virtualRow.index])"
                  @contextmenu.prevent="openTrackContextMenu($event, sortedTracks[virtualRow.index])"
                  @pointerdown="onTrackPointerDown($event, sortedTracks[virtualRow.index])"
              >
                <span class="pointer-events-none flex items-center px-2" role="cell">
                  <svg
                      v-if="sortedTracks[virtualRow.index].is_flex_grid"
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
                <span class="pointer-events-none min-w-0 truncate px-2" role="cell">{{ sortedTracks[virtualRow.index].artist }}</span>
                <span class="pointer-events-none min-w-0 truncate px-2 font-medium" role="cell">{{ sortedTracks[virtualRow.index].title }}</span>
                <span class="pointer-events-none px-2 text-left font-mono text-xs tabular-nums text-muted" role="cell">{{ bpmLabel(sortedTracks[virtualRow.index]) }}</span>
                <span class="pointer-events-none min-w-0 truncate px-2 text-muted" role="cell">{{ sortedTracks[virtualRow.index].key ?? "" }}</span>
                <span class="pointer-events-none px-2 text-left font-mono text-xs tabular-nums text-muted" role="cell">{{ formatDuration(sortedTracks[virtualRow.index].duration_ms) }}</span>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>

    <TrackContextMenu
        :x="contextMenu?.x ?? 0"
        :y="contextMenu?.y ?? 0"
        :visible="contextMenu !== null"
        :action-label="contextMenu?.target.kind === 'playlist' ? 'Auto Cue Playlist' : 'Auto Cue Selected'"
        :metadata-enabled="selectedMetadataTracks.length > 0 && !props.disabled"
        :selectAllEnabled="currentViewTracks.length > 0 && !props.disabled"
        :remove-from-playlist-enabled="activePlaylist !== undefined && selectedLibraryPaths.length > 0 && !props.disabled"
        @action="runContextMenuAction"
        @selectAll="selectAllSongs"
        @edit-metadata="openMetadataEditor"
        @remove-from-playlist="removeSelectedTracksFromPlaylist"
        @close="closeContextMenu"
    />

    <PlaylistContextMenu
        :x="playlistContextMenu?.x ?? 0"
        :y="playlistContextMenu?.y ?? 0"
        :visible="playlistContextMenu !== null"
        @rename="startPlaylistRename"
        @delete="deletePlaylistFromMenu"
        @auto-cue="openAutoCuePlaylist"
        @close="playlistContextMenu = null"
    />

    <MetadataEditModal
        v-if="isMetadataModalOpen"
        :tracks="selectedMetadataTracks"
        @close="isMetadataModalOpen = false"
    />

    <AutoCueModal
        v-if="isAutoCueModalOpen"
        :tracks="selectedMetadataTracks"
        @close="isAutoCueModalOpen = false"
    />

    <SmartPlaylistModal
        v-if="isSmartPlaylistModalOpen"
        :existing-playlists="existingPlaylistNames"
        @close="isSmartPlaylistModalOpen = false"
        @saved="handleSmartPlaylistSaved"
    />

    <div
        v-if="toastMessage"
        class="fixed bottom-5 right-5 z-[65] max-w-sm rounded border border-success/50 bg-zinc-900 px-4 py-3 text-sm text-zinc-100 shadow-xl"
        role="status"
        aria-live="polite"
    >
      <span class="mr-2 text-success" aria-hidden="true">✓</span>{{ toastMessage }}
    </div>

    <Teleport to="body">
      <div
          v-if="isPointerDragging"
          class="pointer-events-none fixed z-[9999] flex items-center gap-2 rounded-full border border-primary/50 bg-zinc-950/90 px-3 py-1.5 text-xs font-semibold text-primary shadow-2xl backdrop-blur-md"
          :style="{ left: `${dragMousePos.x + 12}px`, top: `${dragMousePos.y + 12}px` }"
      >
        <svg class="h-3.5 w-3.5 text-primary" viewBox="0 0 20 20" fill="currentColor">
          <path d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-8-3a1 1 0 00-.867.5 1 1 0 001.734 1A1 1 0 0010 7zm0 6a1 1 0 100-2 1 1 0 000 2z" />
        </svg>
        <span>Adding {{ pointerDraggedPaths.length }} track(s)</span>
      </div>
    </Teleport>
  </section>
</template>
