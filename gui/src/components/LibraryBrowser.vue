<script setup lang="ts">
// LibraryBrowser.vue
// Two-column playlist/tracklist browser replacing the deprecated
// TargetSelector.vue. See .openspec/4-library-spec.md §3 (Component
// Contract), §3.5 (Split-column layout, revised v1.1), §4.4 (Concurrency
// Lock — isLoadingTrack), §5 (Visual Integration & Anti-Clip).
//
// Left column (w-1/3): vertical list of playlist names from
//   --list-playlists, single-click selects + fetches tracks.
// Right column (w-2/3): Action | Artist | Title table of the selected
//   playlist's tracks from --get-playlist-tracks; double-click a row OR
//   click its leading Load icon to preview it in AudioPlayer.vue via
//   selectedTrackPath.
//
// Self-contained: reads useLibraryState()/useConfigState()/usePlayerState()
// directly, accepts a single `disabled` prop matching every other top-level
// panel.

import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { useLibraryState } from "../composables/useLibraryState";
import { useConfigState } from "../composables/useConfigState";
import { usePlayerState } from "../composables/useTrackMetadata";
import { useRunState } from "../composables/useRunState";
import { useCueGridSidecar } from "../composables/useCueGridSidecar";
import type { LibraryTrack } from "../types/library";

const props = defineProps<{ disabled?: boolean }>();

const {
  playlists,
  playlistsLoading,
  tracks,
  tracksLoading,
  tracksError,
  loadPlaylists,
  selectPlaylist,
  selectTrackForPreview,
} = useLibraryState();

// Extremos 'isValid' para controlar cuándo se puede ejecutar el análisis
const { selectedPlaylist, selectedTrackPath, isValid } = useConfigState();

// §4.4 — read the shared isLoadingTrack concurrency lock directly from the
// player's singleton state. While true, every row-level interaction is
// inert so a second click can never race the first.
const { isLoadingTrack } = usePlayerState();

// Extraemos los estados reactivos de la ejecución global
const { status, analysisStatus, setAnalysisStatus } = useRunState();

// Extraemos los métodos de control del proceso por lote e individual de Python
const { run, runSingleTrack, cancel, resetRun } = useCueGridSidecar();

const contextMenuVisible = ref(false);
const contextMenuX = ref(0);
const contextMenuY = ref(0);
const contextMenuTrack = ref<LibraryTrack | null>(null);

// Condiciones de validación para activar o desactivar los botones
const canRunPlaylist = computed(
  () => isValid.value && status.value !== "running" && selectedPlaylist.value,
);

const canRunCurrentTrack = computed(
  () => status.value !== "running" && selectedTrackPath.value,
);

// Buscamos los metadatos de la canción actual para mandárselos a la CLI de Python
const currentTrackRecord = computed(() => {
  if (!selectedTrackPath.value) return null;
  return tracks.value.find(t => t.location_path === selectedTrackPath.value) || null;
});

const currentTrackTitle = computed(() => currentTrackRecord.value?.title || "Current Track");

// Gestores de clics para los botones integrados
function onAnalyzePlaylist() {
  if (status.value === "running") return;
  if (status.value === "success" || status.value === "error" || status.value === "cancelled") {
    resetRun();
  }
  run();
}

function onAnalyzeCurrentTrack() {
  if (status.value === "running" || !selectedTrackPath.value) return;
  if (status.value === "success" || status.value === "error" || status.value === "cancelled") {
    resetRun();
  }
  void runSingleTrack(selectedTrackPath.value, currentTrackTitle.value);
}

function selectTrackForPreviewAndClearStatus(track: LibraryTrack): void {
  setAnalysisStatus(null);
  selectTrackForPreview(track);
}

function closeContextMenu(): void {
  contextMenuVisible.value = false;
  contextMenuTrack.value = null;
}

function openTrackContextMenu(event: MouseEvent, track: LibraryTrack): void {
  if (props.disabled || isLoadingTrack.value) return;
  contextMenuTrack.value = track;
  contextMenuX.value = event.clientX;
  contextMenuY.value = event.clientY;
  contextMenuVisible.value = true;
}

async function analyzeContextTrack(): Promise<void> {
  const track = contextMenuTrack.value;
  closeContextMenu();

  // Let Vue remove the menu overlay before the analysis updates shared state.
  // This keeps the menu's VNodes out of the same patch cycle as the sidecar
  // status changes triggered by runSingleTrack().
  await nextTick();

  if (!track || props.disabled || isLoadingTrack.value) return;
  await runSingleTrack(track.location_path, track.title);
}

function onWindowKeyDown(event: KeyboardEvent): void {
  if (event.key === "Escape") closeContextMenu();
}

onMounted(() => {
  window.addEventListener("keydown", onWindowKeyDown);
  // §3.3 — replaces TargetSelector.vue's former onMounted --list-playlists
  // fetch. The left column is always freshly repopulated on boot.
  void loadPlaylists();
});

onUnmounted(() => window.removeEventListener("keydown", onWindowKeyDown));
</script>

<template>
  <section
    class="flex h-full min-h-0 flex-1 flex-col overflow-hidden"
    :class="{ 'opacity-60 pointer-events-none': props.disabled }"
  >
    <div
      class="flex items-center gap-2 px-4 py-2 border-b border-zinc-800/80 border-l-2 border-l-secondary/30"
    >
      <span class="text-xs uppercase tracking-widest text-muted">Library</span>
      <span class="text-xs text-dim">{{ playlists.length }} playlists</span>
    </div>

    <div class="flex-1 min-h-0 flex">
      <div
        class="flex w-[min(22rem,34%)] min-h-0 shrink-0 flex-col border-r border-zinc-800/80"
      >
        <div class="shrink-0 border-b border-zinc-800/60 px-4 py-2">
          <span class="text-xs font-semibold uppercase tracking-wide text-muted">Playlists</span>
        </div>
        <div class="flex-1 min-h-0 overflow-y-auto scrollbar-amber">
          <div
            v-if="playlistsLoading && playlists.length === 0"
            class="flex h-full items-center justify-center px-4 text-center text-sm text-dim"
          >
            Loading playlists…
          </div>
          <ul v-else class="h-full py-2">
            <li
              v-for="name in playlists"
              :key="name"
              class="px-3 py-1.5 text-sm cursor-pointer truncate"
              :class="
                name === selectedPlaylist
                  ? 'bg-elevated text-accent'
                  : 'text-muted hover:bg-zinc-800/60 hover:text-primary'
              "
              @click="!props.disabled && selectPlaylist(name)"
            >
              {{ name }}
            </li>
            <li
              v-if="!playlistsLoading && playlists.length === 0"
              class="flex h-full min-h-40 items-center justify-center px-4 text-center text-sm text-dim"
            >
              No playlists found in collection.nml.
            </li>
          </ul>
        </div>

        <div class="hidden">

          <div
            v-if="analysisStatus"
            class="min-h-4 text-center text-[11px] font-mono text-zinc-400 border border-zinc-800/60 rounded p-1.5 bg-zinc-950 truncate"
            aria-live="polite"
            :title="analysisStatus"
          >
            {{ analysisStatus }}
          </div>

          <div class="flex flex-col gap-1.5">
            <button
              v-if="status !== 'running'"
              type="button"
              :disabled="!canRunPlaylist"
              class="w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-md text-xs font-medium transition-colors"
              :class="
                canRunPlaylist
                  ? 'bg-primary text-zinc-950 hover:bg-accent active:bg-primary-pressed font-semibold'
                  : 'bg-zinc-800/50 text-zinc-600 cursor-not-allowed'
              "
              @click="onAnalyzePlaylist"
            >
              <span>▶</span>
              <span>Analyze Playlist</span>
            </button>

            <button
              v-if="status !== 'running'"
              type="button"
              :disabled="!canRunCurrentTrack"
              class="w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-md text-xs font-medium transition-colors border"
              :class="
                canRunCurrentTrack
                  ? 'border-secondary/30 bg-zinc-900 text-accent hover:bg-zinc-800 hover:text-accent'
                  : 'border-zinc-800/80 bg-transparent text-zinc-600 cursor-not-allowed'
              "
              @click="onAnalyzeCurrentTrack"
            >
              <span>🎯</span>
              <span>Analyze Current Track</span>
            </button>

            <button
              v-if="status === 'running'"
              type="button"
              class="w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-md text-xs font-medium border border-red-500/40 bg-zinc-900 text-red-400 hover:bg-red-950/40 hover:text-red-300 transition-colors animate-pulse"
              @click="cancel"
            >
              <span class="inline-block w-2.5 h-2.5 border-2 border-red-400/30 border-t-red-400 rounded-full animate-spin" />
              <span>Cancel Analysis…</span>
            </button>
          </div>
        </div>
        </div>

      <div class="flex min-w-0 flex-1 min-h-0 flex-col">
        <div class="flex shrink-0 items-center justify-between border-b border-zinc-800/60 px-4 py-2">
          <span class="text-xs font-semibold uppercase tracking-wide text-muted">Tracks</span>
          <span v-if="selectedPlaylist" class="max-w-[60%] truncate text-xs text-dim" :title="selectedPlaylist">
            {{ selectedPlaylist }}
          </span>
        </div>
        <div
          v-if="tracksError"
          class="flex flex-1 min-h-0 items-center justify-center px-4 text-center text-sm text-warn font-mono"
        >
          {{ tracksError }}
        </div>

        <div
          v-else-if="tracksLoading"
          class="flex flex-1 min-h-0 items-center justify-center px-4 text-center text-sm text-dim"
        >
          Loading tracks…
        </div>

        <div
          v-else-if="!selectedPlaylist"
          class="flex flex-1 min-h-0 items-center justify-center px-4 text-center text-sm text-dim"
        >
          Select a playlist to view its tracks.
        </div>

        <div
          v-else-if="tracks.length === 0"
          class="flex flex-1 min-h-0 items-center justify-center px-4 text-center text-sm text-dim"
        >
          This playlist is empty.
        </div>

        <div v-else class="flex-1 min-h-0 overflow-y-auto scrollbar-amber">
          <table class="w-full table-fixed text-sm">
            <thead class="sticky top-0 z-10 bg-panel">
              <tr>
                <th class="w-10 px-2 py-1.5"></th>
                <th class="w-10 px-2 py-1.5"></th>
                <th class="text-left px-3 py-1.5 text-muted font-normal">
                  Artist
                </th>
                <th class="text-left px-3 py-1.5 text-muted font-normal">
                  Title
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="track in tracks"
                :key="track.location_path"
                class="cursor-pointer"
                :class="[
                  track.location_path === selectedTrackPath
                    ? 'bg-elevated'
                    : 'hover:bg-zinc-800/60',
                  // §4.4 — isLoadingTrack lock: dim + inert rows while a
                  // load is in flight, layered independently of the run-lock
                  // `disabled` prop on the root <section>.
                  isLoadingTrack ? 'opacity-50 pointer-events-none' : '',
                ]"
                @dblclick="!props.disabled && !isLoadingTrack && selectTrackForPreviewAndClearStatus(track)"
                @contextmenu.prevent="openTrackContextMenu($event, track)"
              >
                <td class="px-2 py-1 text-center">
                  <button
                    type="button"
                    class="text-muted hover:text-accent disabled:opacity-40 disabled:hover:text-muted transition-colors"
                    :disabled="isLoadingTrack"
                    :aria-label="`Load ${track.artist} - ${track.title}`"
                    title="Load for preview"
                    @click.stop="!props.disabled && !isLoadingTrack && selectTrackForPreviewAndClearStatus(track)"
                  >
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      viewBox="0 0 20 20"
                      fill="currentColor"
                      class="w-4 h-4 inline-block"
                      aria-hidden="true"
                    >
                      <path
                        fill-rule="evenodd"
                        d="M2 10a8 8 0 1116 0 8 8 0 01-16 0zm6.5-3.25a.75.75 0 00-1.5 0v6.5a.75.75 0 001.2.6l4.5-3.25a.75.75 0 000-1.2l-4.5-3.25a.75.75 0 00-.3-.1z"
                        clip-rule="evenodd"
                      />
                    </svg>
                  </button>
                </td>
                <td class="w-10 px-2 py-1 text-center">
                  <span
                    v-if="track.flags != null && (Number(track.flags) & 0x40) === 0x40"
                    class="inline-flex text-emerald-400 transition-colors hover:text-emerald-300"
                    title="Stems available"
                    aria-label="Stems available"
                  >
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      viewBox="0 0 20 20"
                      fill="currentColor"
                      class="h-3.5 w-3.5"
                      aria-hidden="true"
                    >
                      <path d="M3 5.25A1.25 1.25 0 014.25 4h11.5A1.25 1.25 0 0117 5.25v1.5A1.25 1.25 0 0115.75 8H4.25A1.25 1.25 0 013 6.75v-1.5zM3 10.25A1.25 1.25 0 014.25 9h11.5A1.25 1.25 0 0117 10.25v1.5A1.25 1.25 0 0115.75 13H4.25A1.25 1.25 0 013 11.75v-1.5zM3 15.25A1.25 1.25 0 014.25 14h11.5A1.25 1.25 0 0117 15.25v1.5A1.25 1.25 0 0115.75 18H4.25A1.25 1.25 0 013 16.75v-1.5z" />
                    </svg>
                  </span>
                </td>
                <td class="px-3 py-1 text-primary truncate">{{ track.artist }}</td>
                <td class="px-3 py-1 text-primary truncate">{{ track.title }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <div class="shrink-0 border-t border-zinc-800/60 bg-zinc-950/40 p-2">
      <div class="flex flex-wrap items-center gap-2">
        <div
          v-if="analysisStatus"
          class="min-w-0 flex-1 truncate rounded border border-zinc-800/60 bg-zinc-950 px-2 py-1 text-center text-[11px] font-mono text-zinc-400"
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
          class="inline-flex min-w-40 items-center justify-center gap-2 rounded-md px-3 py-2 text-xs font-semibold transition-colors"
          :class="
            canRunPlaylist
              ? 'bg-primary text-zinc-950 hover:bg-accent active:bg-primary-pressed'
              : 'cursor-not-allowed bg-zinc-800/50 text-zinc-600'
          "
          @click="onAnalyzePlaylist"
        >
          <span aria-hidden="true">▶</span>
          <span>Analyze Playlist</span>
        </button>

        <button
          v-if="status !== 'running'"
          type="button"
          :disabled="!canRunCurrentTrack"
          class="inline-flex min-w-44 items-center justify-center gap-2 rounded-md border px-3 py-2 text-xs font-semibold transition-colors"
          :class="
            canRunCurrentTrack
              ? 'border-secondary/30 bg-zinc-900 text-accent hover:bg-zinc-800'
              : 'cursor-not-allowed border-zinc-800/80 bg-transparent text-zinc-600'
          "
          @click="onAnalyzeCurrentTrack"
        >
          <span aria-hidden="true">🎯</span>
          <span>Analyze Current Track</span>
        </button>

        <button
          v-if="status === 'running'"
          type="button"
          class="w-full rounded-md border border-red-500/40 bg-zinc-900 px-3 py-2 text-xs font-medium text-red-400 transition-colors hover:bg-red-950/40"
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
      @click.stop
    >
      <button
        type="button"
        class="w-full px-3 py-2 text-left hover:bg-zinc-800 focus:bg-zinc-800 focus:outline-none"
        role="menuitem"
        @click="analyzeContextTrack"
      >
        Analyze track
      </button>
    </div>
  </div>
</template>
