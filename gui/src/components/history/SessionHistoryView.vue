<script setup lang="ts">
import { computed, shallowRef, useTemplateRef, watch } from "vue";
import {
  renderedOffsetSeconds,
  timelineBlockStyle,
  useSessionHistory,
  type DeckId,
  type HistoryEvent,
} from "../../composables/history/useSessionHistory.ts";
import { useCueGridSidecar } from "../../composables/core/useCueGridSidecar.ts";
import { useLibraryState } from "../../composables/collection/useLibraryState.ts";

const { files, filterDate, filteredFiles, selectedFile, timeline, status, errorMessage, warningCount, selectHistoryFile } = useSessionHistory();
const { createStaticPlaylist } = useCueGridSidecar();
const { loadLibrary, collection } = useLibraryState();
const isImportModalOpen = shallowRef(false);
const playlistName = shallowRef("");
const onlyPublicPlayback = shallowRef(true);
const isImporting = shallowRef(false);
const importError = shallowRef<string | null>(null);

const pixelsPerSecond = shallowRef(2);
const MIN_PIXELS_PER_SECOND = 0.5;
const MAX_PIXELS_PER_SECOND = 12;
const ZOOM_STEP = 1.15;
const MIN_TICK_LABEL_SPACING_PX = 72;
const RULER_INTERVALS_SECONDS = [5, 10, 15, 30, 60, 120, 300, 600, 900, 1800];
const timelineScroll = useTemplateRef<HTMLDivElement>("timelineScroll");
const timelineViewportWidth = shallowRef(0);
const isDragging = shallowRef(false);
const dragStartX = shallowRef(0);
const dragScrollLeftStart = shallowRef(0);

const deckLanes: { deck: DeckId; label: string }[] = [
  { deck: 0, label: "Deck A" },
  { deck: 1, label: "Deck B" },
  { deck: 2, label: "Deck C" },
  { deck: 3, label: "Deck D" },
];

const sessionDurationLabel = computed(() => formatDuration(timeline.value?.durationSeconds ?? 0));
const canvasDurationSeconds = computed(() => Math.max(
  timeline.value?.renderedDurationSeconds ?? 0,
  timelineViewportWidth.value / pixelsPerSecond.value,
));
const timelineWidth = computed(() => `${canvasDurationSeconds.value * pixelsPerSecond.value}px`);
const rulerTicks = computed(() => {
  const duration = canvasDurationSeconds.value;
  const intervalSeconds = rulerIntervalSeconds(pixelsPerSecond.value);
  const ticks: { timeSeconds: number; label: string; leftPx: number }[] = [];

  for (let seconds = 0; seconds <= duration; seconds += intervalSeconds) {
    ticks.push({
      timeSeconds: seconds,
      label: formatRulerTime(seconds),
      leftPx: renderedOffsetSeconds(seconds, timeline.value!) * pixelsPerSecond.value,
    });
  }

  return ticks;
});

watch(timelineScroll, (container, _previousContainer, onCleanup) => {
  if (!container) {
    timelineViewportWidth.value = 0;
    return;
  }

  const updateViewportWidth = () => {
    timelineViewportWidth.value = container.clientWidth;
  };
  const observer = new ResizeObserver(updateViewportWidth);
  observer.observe(container);
  updateViewportWidth();
  onCleanup(() => observer.disconnect());
}, { flush: "post" });

function eventsForDeck(deck: DeckId): HistoryEvent[] {
  return timeline.value?.events.filter((event) => event.deck === deck) ?? [];
}

function blockStyle(event: HistoryEvent) {
  return timeline.value ? timelineBlockStyle(event, timeline.value, pixelsPerSecond.value) : {};
}

function handleTimelineWheel(event: WheelEvent): void {
  const container = timelineScroll.value;
  if (!container) return;

  if (event.ctrlKey || event.metaKey) {
    const previousScale = pixelsPerSecond.value;
    const nextScale = event.deltaY < 0 ? previousScale * ZOOM_STEP : previousScale / ZOOM_STEP;
    const clampedScale = Math.min(MAX_PIXELS_PER_SECOND, Math.max(MIN_PIXELS_PER_SECOND, nextScale));
    const pointerOffset = event.clientX - container.getBoundingClientRect().left;
    const timelinePosition = (container.scrollLeft + pointerOffset) / previousScale;

    pixelsPerSecond.value = clampedScale;
    container.scrollLeft = timelinePosition * clampedScale - pointerOffset;
    return;
  }

  container.scrollLeft += event.shiftKey ? event.deltaY : event.deltaY || event.deltaX;
}

function handlePointerDown(event: MouseEvent): void {
  const container = timelineScroll.value;
  if (event.button !== 0 || !container) return;

  isDragging.value = true;
  dragStartX.value = event.clientX;
  dragScrollLeftStart.value = container.scrollLeft;
  event.preventDefault();
}

function handlePointerMove(event: MouseEvent): void {
  const container = timelineScroll.value;
  if (!isDragging.value || !container) return;

  container.scrollLeft = dragScrollLeftStart.value + dragStartX.value - event.clientX;
  event.preventDefault();
}

function stopDragging(): void {
  isDragging.value = false;
}

function rulerIntervalSeconds(scale: number): number {
  return RULER_INTERVALS_SECONDS.find((interval) => interval * scale >= MIN_TICK_LABEL_SPACING_PX)
    ?? RULER_INTERVALS_SECONDS[RULER_INTERVALS_SECONDS.length - 1];
}

function formatDuration(seconds: number): string {
  const wholeSeconds = Math.max(0, Math.round(seconds));
  const hours = Math.floor(wholeSeconds / 3600);
  const minutes = Math.floor((wholeSeconds % 3600) / 60);
  const remainingSeconds = wholeSeconds % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`
    : `${minutes}:${String(remainingSeconds).padStart(2, "0")}`;
}

function formatRulerTime(seconds: number): string {
  const wholeSeconds = Math.max(0, Math.round(seconds));
  const hours = Math.floor(wholeSeconds / 3600);
  const minutes = Math.floor((wholeSeconds % 3600) / 60);
  const remainingSeconds = wholeSeconds % 60;
  return hours > 0
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`;
}

function blockLabel(event: HistoryEvent): string {
  const offset = timeline.value ? event.startTimeSeconds - timeline.value.originSeconds : 0;
  const metadata = trackMetadataLabel(event);
  return `${event.artist ? `${event.artist} — ` : ""}${event.title || "Untitled track"}${metadata ? `; ${metadata}` : ""}; Deck ${"ABCD"[event.deck]}; starts ${formatDuration(offset)}; duration ${formatDuration(event.durationSeconds)}`;
}

function trackMetadataLabel(event: HistoryEvent): string {
  const metadata: string[] = [];
  if (event.bpm !== null) metadata.push(`${event.bpm.toFixed(2)} BPM`);
  if (event.key) metadata.push(event.key);
  return metadata.join(" · ");
}
const importEvents = computed(() => (timeline.value?.events ?? []).filter(
  (event) => !onlyPublicPlayback.value || event.playedPublic,
));

function openImportModal(): void {
  if (!selectedFile.value || !timeline.value?.events.length) return;
  playlistName.value = `Session - ${selectedFile.value.displayLabel}`;
  importError.value = null;
  isImportModalOpen.value = true;
}

// 1. Calculamos en tiempo real qué canciones existen y cuáles faltan
const resolvedImportData = computed(() => {
  const activeTracks = Object.values(collection.value);
  const paths: string[] = [];
  const missing: typeof importEvents.value = [];

  for (const event of importEvents.value) {
    let match = activeTracks.find(t =>
        t.artist === event.artist &&
        t.title === event.title &&
        t.title !== ""
    );

    if (!match) {
      const filename = event.primaryKey.split('/:').pop();
      if (filename) {
        match = activeTracks.find(t => t.location_path.endsWith(filename));
      }
    }

    if (match) {
      paths.push(match.location_path);
    } else {
      missing.push(event);
    }
  }

  return { paths, missing };
});

// 2. La función de importar ahora es mucho más limpia
async function importSession(): Promise<void> {
  const name = playlistName.value.trim();
  const pathsToImport = resolvedImportData.value.paths;

  if (!name || pathsToImport.length === 0) return;

  isImporting.value = true;
  importError.value = null;

  const result = await createStaticPlaylist({ name, entries: pathsToImport });

  if (!result.ok) {
    importError.value = result.error;
    isImporting.value = false;
    return;
  }

  await loadLibrary();
  isImporting.value = false;
  isImportModalOpen.value = false;
}
</script>

<template>
  <section class="session-history" aria-labelledby="session-history-title">
    <aside class="history-sidebar" aria-label="Available session history files">
      <div class="sidebar-header flex items-center justify-between gap-2">
        <div>
          <h1 id="session-history-title" class="sidebar-title">Sessions</h1>
        </div>
        <div class="flex items-center gap-1">
          <input v-model="filterDate" type="date" aria-label="Filter sessions by date" class="w-32 rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 text-xs text-zinc-300 focus:border-amber-300 focus:outline-none focus:ring-2 focus:ring-amber-300/40">
          <button v-if="filterDate" type="button" class="rounded px-1.5 py-1 text-sm leading-none text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300" aria-label="Clear date filter" title="Clear date filter" @click="filterDate = null">×</button>
        </div>
      </div>

      <p v-if="status === 'discovering'" class="sidebar-message">Searching for History files…</p>
      <p v-else-if="status === 'empty'" class="sidebar-message">No Traktor history files found.</p>
      <p v-else-if="status === 'error' && !selectedFile" class="sidebar-message sidebar-error">{{ errorMessage }}</p>

      <p v-else-if="files.length > 0 && filteredFiles.length === 0" class="sidebar-message">No sessions found for this date.</p>

      <div v-else class="history-file-list">
        <button
          v-for="file in filteredFiles"
          :key="file.path"
          type="button"
          class="history-file"
          :class="{ 'history-file-selected': selectedFile?.path === file.path }"
          :aria-pressed="selectedFile?.path === file.path"
          :title="file.filename"
          @click="selectHistoryFile(file)"
        >
          <span class="history-file-date">{{ file.displayLabel }}</span>
        </button>
      </div>
    </aside>

    <main class="timeline-panel" aria-live="polite">
      <header class="timeline-header">
        <div>
          <p class="eyebrow">Session timeline</p>
          <h2 class="timeline-title">{{ selectedFile?.displayLabel ?? "Choose a session" }}</h2>
        </div>
        <div class="flex items-center gap-2">
          <button
            v-if="timeline"
            type="button"
            class="rounded border border-primary bg-primary px-3 py-1.5 text-xs font-semibold text-zinc-950 transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-secondary disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="timeline.events.length === 0 || isImporting"
            @click="openImportModal"
          >
            Import as Playlist
          </button>
          <span v-if="timeline" class="duration-badge">{{ sessionDurationLabel }}</span>
        </div>
      </header>

      <div v-if="status === 'loading'" class="timeline-state">Reading and matching session events…</div>
      <div v-else-if="status === 'error'" class="timeline-state timeline-state-error">{{ errorMessage }}</div>
      <div v-else-if="status === 'no-events'" class="timeline-state">This file has no renderable history events.</div>
      <div v-else-if="!selectedFile && status !== 'discovering'" class="timeline-state">Choose a history file to view its four-deck session timeline.</div>

      <div
        v-else-if="timeline"
        class="timeline-viewport"
        aria-label="Four-deck session timeline"
        @wheel.prevent="handleTimelineWheel"
      >
        <div class="lane-labels" aria-hidden="true">
          <div class="ruler-label">Time</div>
          <div v-for="lane in deckLanes" :key="lane.deck" class="lane-label">{{ lane.label }}</div>
        </div>
        <div
          ref="timelineScroll"
          class="timeline-scroll"
          :class="{ 'timeline-scroll-dragging': isDragging }"
          @mousedown="handlePointerDown"
          @mousemove="handlePointerMove"
          @mouseup="stopDragging"
          @mouseleave="stopDragging"
        >
          <div class="timeline-canvas" :style="{ width: timelineWidth }">
            <div class="time-ruler" aria-label="Time ruler">
              <div v-for="gap in timeline.gaps" :key="gap.realStartOffsetSeconds" class="timeline-gap-break timeline-gap-ruler" :style="{ left: `${gap.renderedStartOffsetSeconds * pixelsPerSecond}px`, width: `${gap.renderedDurationSeconds * pixelsPerSecond}px` }" :aria-label="`Compressed inactivity gap: ${formatDuration(gap.realDurationSeconds)}`">
                <span>Gap: {{ formatDuration(gap.realDurationSeconds) }}</span>
              </div>
              <div
                v-for="tick in rulerTicks"
                :key="tick.timeSeconds"
                class="ruler-tick"
                :style="{ left: `${tick.leftPx}px` }"
              >
                <span class="ruler-tick-label">{{ tick.label }}</span>
              </div>
            </div>
            <div v-for="lane in deckLanes" :key="lane.deck" class="deck-lane">
              <div v-for="gap in timeline.gaps" :key="`${lane.deck}-${gap.realStartOffsetSeconds}`" class="timeline-gap-break" :style="{ left: `${gap.renderedStartOffsetSeconds * pixelsPerSecond}px`, width: `${gap.renderedDurationSeconds * pixelsPerSecond}px` }" aria-hidden="true" />
              <div
                v-for="event in eventsForDeck(lane.deck)"
                :key="`${event.sourceOrder}-${event.primaryKey}`"
                class="track-block"
                :class="event.playedPublic ? 'track-block-public' : 'track-block-cue'"
                :style="blockStyle(event)"
                :aria-label="`${blockLabel(event)}; Public: ${event.playedPublic ? 'Yes' : 'No (Cue)'}`"
                :title="`${blockLabel(event)}; Public: ${event.playedPublic ? 'Yes' : 'No (Cue)'}`"
              >
                <span class="track-block-artist">{{ event.artist || "Unknown artist" }}</span>
                <span class="track-block-title">{{ event.title || "Untitled track" }}</span>
                <span v-if="trackMetadataLabel(event)" class="track-block-metadata">{{ trackMetadataLabel(event) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <p v-if="timeline && warningCount > 0" class="warning-summary">
        {{ warningCount }} malformed or unmatched {{ warningCount === 1 ? "entry was" : "entries were" }} omitted.
      </p>

      <div v-if="isImportModalOpen" class="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" role="presentation" @mousedown.self="!isImporting && (isImportModalOpen = false)">
        <form class="w-full max-w-md rounded-lg border border-zinc-700 bg-zinc-900 p-5 shadow-xl" aria-label="Import session as playlist" @submit.prevent="importSession">
          <h3 class="text-base font-semibold text-zinc-100">Import session as playlist</h3>
          <label class="mt-4 block text-xs font-medium text-zinc-400" for="history-playlist-name">Playlist name</label>
          <input id="history-playlist-name" v-model="playlistName" class="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-amber-300 focus:outline-none focus:ring-2 focus:ring-amber-300/40" :disabled="isImporting" required>
          <label class="mt-3 flex items-center gap-2 text-sm text-zinc-300"><input v-model="onlyPublicPlayback" type="checkbox" :disabled="isImporting"> Only public playback</label>
          <p class="mt-2 text-xs text-zinc-400">{{ resolvedImportData.paths.length }} track {{ resolvedImportData.paths.length === 1 ? 'occurrence' : 'occurrences' }} will be imported.</p>
          <p v-if="resolvedImportData.missing.length > 0" class="mt-2 text-xs text-amber-400/90">
            ⚠️ {{ resolvedImportData.missing.length }} track(s) could not be found in your current collection and will be skipped.
          </p>
          <p v-if="importError" class="mt-2 text-sm text-red-400">{{ importError }}</p>
          <div class="mt-5 flex justify-end gap-2">
            <button type="button" class="rounded px-3 py-2 text-sm text-zinc-300 hover:bg-zinc-800" :disabled="isImporting" @click="isImportModalOpen = false">Cancel</button>
            <button type="submit" class="rounded bg-primary px-3 py-2 text-sm font-semibold text-zinc-950 disabled:opacity-50" :disabled="isImporting || !playlistName.trim() || resolvedImportData.paths.length === 0">{{ isImporting ? 'Importing…' : 'Import' }}</button>
          </div>
        </form>
      </div>
    </main>
  </section>
</template>

<style scoped>
.session-history {
  display: grid;
  grid-template-columns: minmax(13rem, 18rem) minmax(0, 1fr);
  flex: 1;
  min-height: 0;
  margin: 0.375rem;
  overflow: hidden;
  border: 1px solid rgb(39 39 42 / 0.8);
  border-radius: 0.5rem;
  background: #1c1c1e;
  box-shadow: inset 0 1px 1px rgb(0 0 0 / 0.25);
}

.history-sidebar {
  display: flex;
  min-height: 0;
  flex-direction: column;
  border-right: 1px solid #2a2a2e;
  background: #18181a;
}

.sidebar-header,
.timeline-header {
  padding: 1rem;
  border-bottom: 1px solid #2a2a2e;
}

.eyebrow {
  margin: 0;
  color: #f7d15f;
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  font-size: 0.6875rem;
  font-weight: 600;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.sidebar-title,
.timeline-title {
  margin: 0.25rem 0 0;
  color: #f2f2f2;
  font-size: 1rem;
  font-weight: 600;
}

.sidebar-message,
.timeline-state,
.warning-summary {
  margin: 0;
  color: #8a8a8e;
  font-size: 0.8125rem;
  line-height: 1.5;
}

.sidebar-message {
  padding: 1rem;
}

.sidebar-error,
.timeline-state-error {
  color: #e05c5c;
}

.history-file-list {
  min-height: 0;
  overflow-y: auto;
  padding: 0.375rem;
}

.history-file {
  display: flex;
  width: 100%;
  flex-direction: column;
  gap: 0.2rem;
  padding: 0.625rem 0.75rem;
  border: 1px solid transparent;
  border-radius: 0.375rem;
  background: transparent;
  color: #f2f2f2;
  cursor: pointer;
  text-align: left;
}

.history-file:hover {
  background: #232326;
}

.history-file:focus-visible {
  outline: 2px solid #f7d15f;
  outline-offset: -2px;
}

.history-file-selected {
  border-color: rgb(237 180 11 / 0.5);
  background: rgb(170 130 8 / 0.16);
}

.history-file-date {
  color: #f7d15f;
  font-size: 0.8125rem;
  font-weight: 600;
}

.timeline-panel {
  display: flex;
  min-width: 0;
  min-height: 0;
  flex-direction: column;
}

.timeline-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.duration-badge {
  border: 1px solid rgb(237 180 11 / 0.4);
  border-radius: 999px;
  padding: 0.25rem 0.5rem;
  color: #f7d15f;
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  font-size: 0.75rem;
}

.timeline-state {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: center;
  padding: 2rem;
  text-align: center;
}

.timeline-viewport {
  display: grid;
  min-height: 0;
  flex: 1;
  grid-template-columns: 4.75rem minmax(0, 1fr);
  overflow: hidden;
}

.lane-labels {
  display: grid;
  grid-template-rows: 2rem repeat(4, minmax(5rem, 1fr));
  border-right: 1px solid #2a2a2e;
  background: #18181a;
}

.ruler-label {
  display: flex;
  align-items: center;
  padding-left: 0.75rem;
  border-bottom: 1px solid #2a2a2e;
  color: #8a8a8e;
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  font-size: 0.6875rem;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.lane-label {
  display: flex;
  align-items: center;
  padding-left: 0.75rem;
  border-bottom: 1px solid #2a2a2e;
  color: #8a8a8e;
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  font-size: 0.75rem;
  font-weight: 600;
}

.lane-label:last-child {
  border-bottom: 0;
}

.timeline-scroll {
  min-width: 0;
  overflow: auto;
  cursor: grab;
}

.timeline-scroll-dragging {
  cursor: grabbing;
  user-select: none;
}

.timeline-canvas {
  display: grid;
  min-width: 100%;
  min-height: 100%;
  grid-template-rows: 2rem repeat(4, minmax(5rem, 1fr));
}

.time-ruler {
  position: relative;
  min-width: 100%;
  border-bottom: 1px solid #2a2a2e;
  background: #18181a;
}

.ruler-tick {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 1px;
  background: rgb(247 209 95 / 0.45);
}

.ruler-tick-label {
  position: absolute;
  top: 0.375rem;
  left: 0.375rem;
  color: #8a8a8e;
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  font-size: 0.625rem;
  white-space: nowrap;
}

.deck-lane {
  position: relative;
  min-height: 5rem;
  border-bottom: 1px solid #2a2a2e;
  background-color: #1c1c1e;
  background-image: linear-gradient(to right, rgb(237 180 11 / 0.07) 1px, transparent 1px);
  background-size: 12.5% 100%;
}

.deck-lane:last-child {
  border-bottom: 0;
}

.track-block {
  position: absolute;
  top: 0.875rem;
  bottom: 0.875rem;
  display: flex;
  min-width: 0;
  flex-direction: column;
  justify-content: center;
  overflow: hidden;
  box-sizing: border-box;
  border: 1px solid rgb(247 209 95 / 0.72);
  border-radius: 0.25rem;
  padding: 0.375rem 0.5rem;
  background: linear-gradient(135deg, rgb(170 130 8 / 0.72), rgb(237 180 11 / 0.26));
  color: #f2f2f2;
}

.track-block-cue {
  border-style: dashed;
  border-color: #71717a;
  background: rgb(39 39 42 / 0.78);
  opacity: 0.6;
}

.timeline-gap-break {
  position: absolute;
  top: 0;
  bottom: 0;
  z-index: 1;
  min-width: 1.5rem;
  background: repeating-linear-gradient(120deg, transparent 0 8px, rgb(247 209 95 / 0.6) 8px 10px, transparent 10px 16px);
  border-inline: 1px dashed rgb(247 209 95 / 0.7);
  pointer-events: none;
}

.timeline-gap-ruler span {
  position: absolute;
  top: 0.3rem;
  left: 50%;
  color: #f7d15f;
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  font-size: 0.625rem;
  transform: translateX(-50%);
  white-space: nowrap;
}

.track-block-artist,
.track-block-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.track-block-artist {
  color: #f9dc83;
  font-size: 0.6875rem;
}

.track-block-title {
  font-size: 0.75rem;
  font-weight: 600;
}

.track-block-metadata {
  margin-top: 0.125rem;
  color: #b0a27a;
  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
  font-size: 0.625rem;
}

.warning-summary {
  border-top: 1px solid #2a2a2e;
  padding: 0.5rem 1rem;
  color: #e0a72e;
}

@media (max-width: 40rem) {
  .session-history {
    grid-template-columns: minmax(10rem, 35%) minmax(0, 1fr);
  }
}
</style>
