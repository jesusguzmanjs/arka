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

import { onMounted, onUnmounted, ref } from "vue";
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

const { selectedPlaylist, selectedTrackPath } = useConfigState();

// §4.4 — read the shared isLoadingTrack concurrency lock directly from the
// player's singleton state. While true, every row-level interaction is
// inert so a second click can never race the first.
const { isLoadingTrack } = usePlayerState();
const { setAnalysisStatus } = useRunState();
const { runSingleTrack } = useCueGridSidecar();

const contextMenuVisible = ref(false);
const contextMenuX = ref(0);
const contextMenuY = ref(0);
const contextMenuTrack = ref<LibraryTrack | null>(null);

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
  <!-- §5.2 (v1.1 fix): the root element must NOT set overflow-y-auto — only
       the two inner columns do. A second overflow-y-auto on an ancestor
       intercepts the scroll gesture before it reaches the intended inner
       column, which was the root cause of the "tracklist doesn't scroll" bug.
       v1.2 fix: root carries flex-1 min-h-0 so it absorbs Block 1's height
       and constrains its children — without flex-1 the section collapses to
       its content height and the inner overflow-y-auto never engages. -->
  <section
    class="flex flex-col flex-1 min-h-0"
    :class="{ 'opacity-60 pointer-events-none': props.disabled }"
  >
    <div
      class="flex items-center gap-2 px-4 py-2 border-b border-zinc-800/80 border-l-2 border-l-teal-500/30"
    >
      <span class="text-xs uppercase tracking-widest text-muted">Library</span>
    </div>

    <!-- §5.2: shared flex row supplies min-h-0 so both children's
         overflow-y-auto actually take effect inside the flex container. -->
    <div class="flex-1 min-h-0 flex">
      <!-- ── Left column: Playlists (~1/3 width) ───────────────────── -->
      <!-- §4.4: the playlist list is NOT locked by isLoadingTrack —
           switching playlists never touches the player. -->
      <div
        class="w-1/3 shrink-0 border-r border-zinc-800/80 min-h-0 overflow-y-auto"
      >
        <div
          v-if="playlistsLoading && playlists.length === 0"
          class="px-3 py-2 text-sm text-dim"
        >
          Loading playlists…
        </div>
        <ul v-else>
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
            class="px-3 py-2 text-sm text-dim"
          >
            No playlists found in collection.nml.
          </li>
        </ul>
      </div>

      <!-- ── Right column: Tracklist table (~2/3 width) ─────────────
           v1.2 fix: this wrapper is a flex container (flex-1 min-h-0) that
           holds the error/loading/empty states AND a separate scroll div for
           the table. The scroll must NOT live on this wrapper itself — if it
           does, the sticky thead sticks to a box that also contains the
           non-table states, and the table grows the column to infinity. -->
      <div class="w-2/3 min-h-0 flex flex-col flex-1">
        <!-- Error state (AmbiguousPlaylistError / PlaylistNotFoundError) -->
        <div
          v-if="tracksError"
          class="px-3 py-3 text-sm text-warn font-mono"
        >
          {{ tracksError }}
        </div>

        <!-- Loading state -->
        <div
          v-else-if="tracksLoading"
          class="px-3 py-3 text-sm text-dim"
        >
          Loading tracks…
        </div>

        <!-- Empty-state: no playlist selected yet (expected boot state) -->
        <div
          v-else-if="!selectedPlaylist"
          class="px-3 py-3 text-sm text-dim"
        >
          Select a playlist to view its tracks.
        </div>

        <!-- Empty playlist: valid, not an error -->
        <div
          v-else-if="tracks.length === 0"
          class="px-3 py-3 text-sm text-dim"
        >
          This playlist is empty.
        </div>

        <!-- v1.2 fix: the table lives inside its own flex-1 overflow-y-auto
             min-h-0 div, NOT directly under the column wrapper. This is the
             only element that scrolls; the thead sticks to its top. -->
        <div v-else class="flex-1 overflow-y-auto min-h-0">
          <!-- Tracklist table: fixed Action and Stem columns keep metadata aligned. -->
          <table class="w-full table-fixed text-sm">
            <thead class="sticky top-0 z-10 bg-panel">
              <tr>
                <!-- Column order: Action -> Stem badge -> Artist -> Title. -->
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
                <!-- §3.5 v1.1: leading Action column with a Load icon -->
                <td class="px-2 py-1 text-center">
                  <button
                    type="button"
                    class="text-muted hover:text-accent disabled:opacity-40 disabled:hover:text-muted transition-colors"
                    :disabled="isLoadingTrack"
                    :aria-label="`Load ${track.artist} - ${track.title}`"
                    title="Load for preview"
                    @click.stop="!props.disabled && !isLoadingTrack && selectTrackForPreviewAndClearStatus(track)"
                  >
                    <!-- play-circle glyph -->
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
