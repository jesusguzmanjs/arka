<script setup lang="ts">
import { computed, ref } from "vue";
import { getHarmonicMatches, normalizeHarmonicKey } from "../../utils/harmonicKeys.ts";
import { useWorkspaceStore } from "../../stores/useWorkspaceStore.ts";
import type { CollectionTrack } from "../../types/library.ts";

const props = defineProps<{
  tracks: readonly CollectionTrack[];
  loadedTrack: CollectionTrack | null;
}>();

const emit = defineEmits<{
  select: [track: CollectionTrack];
}>();

const searchQuery = ref("");
const workspaceStore = useWorkspaceStore();
const isPadEditMode = computed(() => workspaceStore.editorMode === "pad");
const compatibleKeys = computed(() => getHarmonicMatches(props.loadedTrack?.key));
const filteredMiniLibraryTracks = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase();
  if (!query) return props.tracks;

  return props.tracks.filter((track) =>
    track.title.toLocaleLowerCase().includes(query) ||
    track.artist.toLocaleLowerCase().includes(query),
  );
});

function bpmLabel(track: CollectionTrack): string {
  return track.bpm === null ? "— BPM" : `${track.bpm.toFixed(2)} BPM`;
}

function keyLabel(track: CollectionTrack): string {
  return track.key?.trim() || "—";
}

function keyClass(track: CollectionTrack): string {
  const key = normalizeHarmonicKey(track.key);
  if (!key) return "key-muted";
  if (compatibleKeys.value.direct.includes(key)) return "key-direct-compatible";
  if (compatibleKeys.value.adjacent.includes(key)) return "key-adjacent-compatible";
  return "key-muted";
}

function hasAvailableStems(track: CollectionTrack): boolean {
  return typeof track.flags === "number" && (track.flags & 0x40) === 0x40;
}

function selectTrack(track: CollectionTrack): void {
  if (isPadEditMode.value) return;
  emit("select", track);
}
</script>

<template>
  <section
    class="mini-library-content"
    :class="{ 'opacity-50 pointer-events-none grayscale': isPadEditMode }"
    :aria-disabled="isPadEditMode"
  >
    <div class="search-controls">
      <input
        v-model="searchQuery"
        class="search-input"
        type="search"
        placeholder="Search title or artist..."
        aria-label="Search Mini Library by title or artist"
      >
      <button
        v-if="searchQuery"
        type="button"
        class="clear-search-button"
        aria-label="Clear Mini Library search"
        @click="searchQuery = ''"
      >
        ×
      </button>
    </div>

    <ul v-if="filteredMiniLibraryTracks.length > 0" class="track-list" aria-label="Collection tracks">
      <li v-for="track in filteredMiniLibraryTracks" :key="track.location_path" class="track-list-item">
        <button
          type="button"
          class="track-tile"
          :class="{ 'is-loaded': track.location_path === loadedTrack?.location_path }"
          :aria-label="`Load ${track.artist} – ${track.title} in the Stem Editor`"
          @click="selectTrack(track)"
        >
          <span class="track-title">

            <span class="track-title-text">{{ track.title || "Untitled track" }}</span>
          </span>
          <span class="track-artist">{{ track.artist || "Unknown artist" }}</span>
          <span class="track-metadata">
            <span>{{ bpmLabel(track) }}</span>
            <span aria-hidden="true">•</span>
            <span :class="keyClass(track)">{{ keyLabel(track) }}</span>
            <span aria-hidden="true"  v-if="hasAvailableStems(track)">•</span>
             <svg
                 v-if="hasAvailableStems(track)"
                 class="stem-indicator"
                 viewBox="0 0 20 20"
                 fill="none"
                 role="img"
                 aria-label="Stems available"
                 title="Stems available"
             >
              <path d="M4 5.25h12M4 10h12M4 14.75h12" stroke="currentColor" stroke-linecap="round" stroke-width="2" />
              <path d="M6 3.5v3.5M10 8.25v3.5M14 13v3.5" stroke="currentColor" stroke-linecap="round" stroke-width="2" />
            </svg>
          </span>
        </button>
      </li>
    </ul>

    <p v-else class="empty-state">
      {{ searchQuery ? "No tracks match your search." : "No tracks found in collection.nml." }}
    </p>
  </section>
</template>

<style scoped>
.mini-library-content {
  display: flex;
  min-width: 0;
  min-height: 0;
  flex: 1;
  flex-direction: column;
}

.track-list {
  display: flex;
  min-height: 0;
  margin: 0.75rem 0 0;
  padding: 0;
  flex: 1;
  flex-direction: column;
  gap: 0.25rem;
  overflow-y: auto;
  list-style: none;
}

.search-controls {
  position: relative;
  margin-top: 0;
  flex-shrink: 0;
}

.search-input {
  width: 100%;
  padding: 0.5rem 2rem 0.5rem 0.625rem;
  border: 1px solid #3a3a3e;
  border-radius: 0.25rem;
  background: #1c1c1e;
  color: #f2f2f2;
  font: inherit;
  font-size: 0.75rem;
}

.search-input::placeholder {
  color: #8a8a8e;
}

.search-input:focus-visible {
  outline: 2px solid #f7d15f;
  outline-offset: -1px;
}

.clear-search-button {
  position: absolute;
  top: 50%;
  right: 0.25rem;
  display: grid;
  width: 1.5rem;
  height: 1.5rem;
  padding: 0;
  place-items: center;
  border: 0;
  border-radius: 0.1875rem;
  background: transparent;
  color: #8a8a8e;
  cursor: pointer;
  font-size: 1rem;
  line-height: 1;
  transform: translateY(-50%);
}

.clear-search-button:hover {
  background: #2a2a2e;
  color: #f2f2f2;
}

.clear-search-button:focus-visible {
  outline: 2px solid #f7d15f;
  outline-offset: -1px;
}

.track-list-item {
  min-width: 0;
}

.track-tile {
  display: flex;
  width: 100%;
  min-width: 0;
  padding: 0.625rem 0.75rem;
  flex-direction: column;
  align-items: stretch;
  border: 1px solid transparent;
  border-radius: 0.25rem;
  background: transparent;
  color: #f2f2f2;
  cursor: pointer;
  text-align: left;
  transition: background-color 150ms ease, border-color 150ms ease;
}

.track-tile:hover {
  background: #2a2a2e;
}

.track-tile:focus-visible {
  outline: 2px solid #f7d15f;
  outline-offset: -2px;
}

.track-tile.is-loaded {
  border-color: #edb40b;
  background: rgb(237 180 11 / 10%);
}

.track-title,
.track-artist {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.track-title {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.875rem;
  font-weight: 650;
}

.track-title-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.stem-indicator {
  width: 0.875rem;
  height: 0.875rem;
  flex: 0 0 auto;
  color: #8a8a8e;
}

.track-artist {
  margin-top: 0.125rem;
  color: #8a8a8e;
  font-size: 0.75rem;
}

.track-metadata {
  display: flex;
  margin-top: 0.5rem;
  align-items: center;
  gap: 0.375rem;
  color: #8a8a8e;
  font-family: ui-monospace, "Cascadia Code", "Fira Code", "JetBrains Mono", Consolas, monospace;
  font-size: 0.6875rem;
  font-variant-numeric: tabular-nums;
}

.key-muted {
  color: #8a8a8e;
}

.key-direct-compatible {
  color: #edb40b;
}

.key-adjacent-compatible {
  color: rgb(247 209 95 / 70%);
}

.empty-state {
  margin: 0.75rem 0 0;
}
</style>
