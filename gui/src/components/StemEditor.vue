<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, shallowRef, useTemplateRef, watch } from "vue";
import { storeToRefs } from "pinia";
import { convertFileSrc } from "@tauri-apps/api/core";
import Peaks from "peaks.js";
import { fetchTrackMetadata } from "../composables/useTrackMetadata";
import { usePeaksMarkers, type PlayerCue } from "../composables/usePeaksMarkers";
import { usePlayerKeyboard } from "../composables/usePlayerKeyboard";
import { useStudioLoopSelection, type StudioLoopSelection } from "../composables/useStudioLoopSelection";
import { STEM_LANES, useStemPeaks } from "../composables/useStemPeaks";
import type { GridTrackData } from "../composables/useGridMath";
import { useWorkspaceStore } from "../stores/useWorkspaceStore";
import type { CollectionTrack } from "../types/library";

const props = defineProps<{ track: CollectionTrack | null; stemTracks: string[] }>();
const workspaceStore = useWorkspaceStore();
const { activeLoopRange } = storeToRefs(workspaceStore);
const waveformElement = useTemplateRef<HTMLDivElement>("waveform");
const audioElement = useTemplateRef<HTMLAudioElement>("audio");
const stemWaveformElements = useTemplateRef<HTMLDivElement[]>("stemWaveform");
const stemAudioElements = useTemplateRef<HTMLAudioElement[]>("stemAudio");
const peaks = shallowRef<any>(null);
const audioContext = shallowRef<AudioContext | null>(null);
const isLoading = shallowRef(false);
const loadError = shallowRef<string | null>(null);
const isPlaying = shallowRef(false);
const currentTime = shallowRef(0);
const duration = shallowRef(0);
const selectionOverlayViewportVersion = shallowRef(0);
let syncRaf: number | null = null;
let stemGridBpm = 0;
let initToken = 0;
let loopSelections: StudioLoopSelection[] = [];
let isSyncingLoop = false;
const ZOOM_LEVELS = [64, 256, 512, 1024, 2048, 4096];

interface StemTimeline {
  container: HTMLElement;
  startTime: number;
  visibleSeconds: number;
  duration: number;
  bpm: number;
  anchorMs: number;
}

type SelectionDrag =
  | { mode: "draw"; timeline: StemTimeline; startRawTime: number }
  | { mode: "move"; timeline: StemTimeline; startClientX: number; range: NonNullable<typeof activeLoopRange.value> }
  | { mode: "resize-left" | "resize-right"; timeline: StemTimeline; range: NonNullable<typeof activeLoopRange.value> };

let selectionDrag: SelectionDrag | null = null;

// 1. Instancia de marcadores para el modo de pista ÚNICA (normal)
const singleMarker = usePeaksMarkers();
const { createPointMarker, paintAllMarkers } = singleMarker;

// 2. Creamos 4 instancias independientes para el modo MULTI-TRACK (Stems)
const stemMarkers = STEM_LANES.map(() => usePeaksMarkers());

const title = computed(() => props.track?.title || "Untitled track");
const artist = computed(() => props.track?.artist || "Unknown artist");
const bpm = computed(() => props.track?.bpm === null || props.track?.bpm === undefined ? "—" : props.track.bpm.toFixed(2));
const key = computed(() => props.track?.key?.trim() || "—");
const timeLabel = computed(() => `${formatTime(currentTime.value)} / ${formatTime(duration.value)}`);
const playLabel = computed(() => isPlaying.value ? "Pause" : "Play");
const isMultiTrackMode = computed(() => props.stemTracks.length === 4);
const stemLanes = computed(() => STEM_LANES.map((lane, index) => ({
  ...lane,
  path: props.stemTracks[index],
})));

const {
  getMasterPeaks,
  getStemPeaks,
  isLoading: isStemLoading, // <--- RECUPERADO: Faltaba extraelo aquí
  isReady: isStemReady,
  muted: mutedStems,
  soloed: soloedStem,
  initialize: initializeStemPeaks,
  toggleMute,
  toggleSolo,
  destroy: destroyStemPeaks
} = useStemPeaks({
  getWaveformContainers: () => stemWaveformElements.value ?? [],
  getAudioElements: () => stemAudioElements.value ?? [],
  createPointMarker: (markerOptions, laneIndex) => {
    const isLastLane = laneIndex === STEM_LANES.length - 1;
    return stemMarkers[laneIndex].createPointMarker(markerOptions, { showLabel: isLastLane });
  },
  onPlayingChange: (playing) => { isPlaying.value = playing; },
  onDuration: (seconds) => { duration.value = seconds || duration.value; },
  onViewportChanged: () => {
    syncGridOpacity();
    refreshCustomSelectionOverlay();
  },
});

const activePeaks = computed(() => isMultiTrackMode.value ? (isStemReady.value ? getMasterPeaks() : null) : peaks.value);
const loopLabel = computed(() => {
  const range = activeLoopRange.value;
  return range ? `Selected: ${range.beatCount} ${range.beatCount === 1 ? "Beat" : "Beats"} | ${formatDuration(range.duration)}` : null;
});
const customSelectionOverlayStyle = computed(() => {
  // Peaks owns the viewport, so its change callback invalidates this derived style.
  void selectionOverlayViewportVersion.value;

  const range = activeLoopRange.value;
  const view = getMasterPeaks()?.views?.getView("zoomview");
  if (!range || !view) return { display: "none" };

  const startTime = view.getStartTime();
  const visibleSeconds = view.getEndTime() - startTime;
  if (!Number.isFinite(startTime) || !Number.isFinite(visibleSeconds) || visibleSeconds <= 0) {
    return { display: "none" };
  }

  const leftPercent = ((range.start - startTime) / visibleSeconds) * 100;
  const widthPercent = (range.duration / visibleSeconds) * 100;
  return {
    display: "block",
    left: `${leftPercent}%`,
    width: `${widthPercent}%`,
    top: 0,
    bottom: 0,
    height: "100%",
  };
});
const gridTrack = computed<GridTrackData>(() => ({
  bpm: props.track?.bpm ?? 0,
  key: props.track?.key ?? "",
  grid_anchor_ms: props.track?.grid_anchor_ms ?? 0,
  duration_ms: duration.value * 1000,
}));
const gridCues = computed<PlayerCue[]>(() => (props.track?.existing_cues ?? [])
    .filter((cue) => Number.isFinite(cue.start_ms))
    .map((cue, index) => ({ id: Number.isInteger(cue.hotcue) ? cue.hotcue : index, position_ms: cue.start_ms, is_valid: true })));

watch(isStemReady, (ready) => {
  if (ready) {
    startOpacitySync();
  } else {
    stopOpacitySync();
  }
}, { immediate: true });

function formatTime(seconds: number): string {
  const safeSeconds = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  return `${Math.floor(safeSeconds / 60)}:${String(safeSeconds % 60).padStart(2, "0")}`;
}

function formatDuration(seconds: number): string {
  const safeSeconds = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
  const minutes = Math.floor(safeSeconds / 60);
  const remainingSeconds = safeSeconds - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${remainingSeconds.toFixed(2).padStart(5, "0")}`;
}

function createPeaksWaveformData(data: number[]) {
  return { json: { version: 2, channels: 1, sample_rate: 11025, samples_per_pixel: 64, bits: 8, length: Math.floor(data.length / 2), data } };
}

function startOpacitySync(): void {
  if (syncRaf === null) {
    syncRaf = requestAnimationFrame(opacitySyncLoop);
  }
}

function opacitySyncLoop(): void {
  syncGridOpacity();
  syncRaf = requestAnimationFrame(opacitySyncLoop);
}

function stopOpacitySync(): void {
  if (syncRaf !== null) {
    cancelAnimationFrame(syncRaf);
    syncRaf = null;
  }
}

function refreshCustomSelectionOverlay(): void {
  selectionOverlayViewportVersion.value += 1;
}

function handleZoomWheel(event: WheelEvent): void {
  // Usamos activePeaks.value (sirve tanto para mono-pista como para stems)
  const activeInstance = activePeaks.value;
  const view = activeInstance?.views?.getView("zoomview");

  // Seleccionamos el contenedor adecuado según el modo
  const container = isMultiTrackMode.value
      ? stemWaveformElements.value?.[0]
      : waveformElement.value;

  if (!view || !container || duration.value <= 0) return;

  const totalSeconds = duration.value;
  const startTime = view.getStartTime();
  const visibleSeconds = view.getEndTime() - startTime;
  const rect = container.getBoundingClientRect();

  if (rect.width <= 0) return;

  const ratio = (event.clientX - rect.left) / rect.width;
  const MIN_VISIBLE_SECONDS = 2;

  let newSeconds = event.deltaY < 0
      ? visibleSeconds * 0.8
      : visibleSeconds * 1.2;

  newSeconds = Math.max(MIN_VISIBLE_SECONDS, Math.min(totalSeconds, newSeconds));

  if (Math.abs(newSeconds - visibleSeconds) < 0.01) return;

  const timeAtCursor = startTime + ratio * visibleSeconds;
  view.setZoom({ seconds: newSeconds });

  const actualSeconds = view.getEndTime() - view.getStartTime();
  const newStartTime = Math.max(0, Math.min(timeAtCursor - ratio * actualSeconds, totalSeconds - actualSeconds));

  view.setStartTime(newStartTime);
}

function destroyPeaks(): void {
  initToken += 1;
  stopSelectionDrag();
  stopOpacitySync();
  loopSelections.forEach((selection) => selection.destroy());
  loopSelections = [];
  destroyStemPeaks();
  workspaceStore.setActiveLoopRange(null);
  isPlaying.value = false;
  currentTime.value = 0;
  const instance = peaks.value;
  peaks.value = null;
  try { instance?.destroy?.(); } catch (error) { console.warn("[StemEditor] Peaks destroy failed:", error); }
  void audioContext.value?.close();
  audioContext.value = null;
  if (audioElement.value) audioElement.value.onloadedmetadata = null;
  try { audioElement.value?.pause(); audioElement.value?.removeAttribute("src"); audioElement.value?.load(); } catch { /* best-effort media cleanup */ }
}

function wirePlayerEvents(instance: any): void {
  const syncView = () => syncGridOpacity();
  instance.on("zoomview.update", syncView);
  instance.on("zoomview.panned", syncView);
  instance.on("zoom.update", syncView);
  instance.on("player.playing", () => { isPlaying.value = true; });
  instance.on("player.pause", () => { isPlaying.value = false; });
  instance.on("player.ended", () => { isPlaying.value = false; });
  instance.on("player.timeupdate", (time: number) => { currentTime.value = time; });
}

function paintBeatGrid(instance: any): void {
  paintAllMarkers(instance, gridTrack.value, gridCues.value);
}

function paintStemBeatGrids(): void {
  stemGridBpm = gridTrack.value.bpm;
  getStemPeaks().forEach((instance, index) => {
    stemMarkers[index].paintAllMarkers(instance, gridTrack.value, gridCues.value);
  });
  syncGridOpacity();
}

function syncGridOpacity(): void {
  // Obtenemos la instancia activa (sirve tanto para pista única como para master de stems)
  const activeInstance = activePeaks.value;
  const view = activeInstance?.views?.getView("zoomview");
  const currentBpm = gridTrack.value.bpm || stemGridBpm;

  if (!view || currentBpm <= 0) return;

  const startTime = view.getStartTime();
  const endTime = view.getEndTime();
  const viewDuration = endTime - startTime;

  if (!Number.isFinite(viewDuration) || viewDuration <= 0) return;

  const visibleBeats = viewDuration / (60 / currentBpm);

  const fade = (startAt: number, range: number) =>
      Math.max(0.2, Math.min(1, 1 - (visibleBeats - startAt) / range));

  let fadeBeats = fade(60, 40);
  let fadeBars = fade(150, 100);
  let fade16 = fade(300, 200);
  let fade32 = fade(600, 300);

  if (visibleBeats > 300) fadeBeats = 0;
  if (visibleBeats > 600) fadeBars = 0;
  if (visibleBeats > 1000) fade16 = 0;
  if (visibleBeats > 1000) fade32 = 0;

  // Si estamos en stems usaremos getStemPeaks() y stemMarkers.
  // Si estamos en pista única usaremos [peaks.value] y [singleMarker].
  const instancesToSync = isMultiTrackMode.value
      ? getStemPeaks()
      : (peaks.value ? [peaks.value] : []);

  const markersToSync = isMultiTrackMode.value
      ? stemMarkers
      : [singleMarker];

  instancesToSync.forEach((instance, index) => {
    let needsRedraw = false;
    const markerInstance = markersToSync[index];
    const gridLines = markerInstance?.getGridLines(instance) ?? [];

    for (const { line, offset } of gridLines) {
      const absOffset = Math.abs(offset);
      const isBar = absOffset % 4 === 0;
      const baseOpacity = isBar ? 0.9 : 0.45;

      let opacity = absOffset % 64 === 0
          ? baseOpacity
          : absOffset % 32 === 0
              ? baseOpacity * fade32
              : absOffset % 16 === 0
                  ? baseOpacity * fade16
                  : isBar
                      ? baseOpacity * fadeBars
                      : baseOpacity * fadeBeats;

      opacity = Math.round(opacity * 1000) / 1000;

      if (line.opacity() !== opacity) {
        line.opacity(opacity);
        line.visible(opacity > 0);
        needsRedraw = true;
      }
    }

    if (needsRedraw && gridLines.length > 0) {
      gridLines[0].line.getLayer()?.batchDraw();
    }
  });
}

function mirrorLoopRange(range: import("../stores/useWorkspaceStore").ActiveLoopRange): void {
  if (isSyncingLoop) return;
  isSyncingLoop = true;
  try {
    loopSelections.forEach((selection) => selection.setRange(range));
  } finally {
    isSyncingLoop = false;
  }
}

function commitLoopRange(range: import("../stores/useWorkspaceStore").ActiveLoopRange | null): void {
  if (range) mirrorLoopRange(range);
  workspaceStore.setActiveLoopRange(range);
}

function installLoopSelection(instance: any, container: HTMLElement): void {
  loopSelections.push(
      useStudioLoopSelection({
        instance,
        waveformElement: container,
        grid: gridTrack.value, // <--- Volvemos al objeto directo
        onPreview: mirrorLoopRange,
        onChange: commitLoopRange,
      })
  );
}

function snapTimeToBeat(timeSeconds: number, bpm: number, anchorMs = 0): number {
  if (!Number.isFinite(timeSeconds) || !Number.isFinite(bpm) || bpm <= 0) return 0;
  const beatLength = 60 / bpm;
  const anchorSec = anchorMs / 1000;
  const relativeTime = timeSeconds - anchorSec;
  const nearestBeat = Math.round(relativeTime / beatLength);
  return Math.max(0, anchorSec + (nearestBeat * beatLength));
}

function getStemTimeline(container: HTMLElement): StemTimeline | null {
  const master = getMasterPeaks();
  const view = master?.views?.getView("zoomview");
  const startTime = view?.getStartTime();
  const visibleSeconds = view ? view.getEndTime() - startTime : 0;
  const trackDuration = master?.player?.getDuration() || duration.value;
  const bpmValue = gridTrack.value.bpm;
  if (
    !view
    || container.getBoundingClientRect().width <= 0
    || !Number.isFinite(startTime)
    || !Number.isFinite(visibleSeconds)
    || visibleSeconds <= 0
    || !Number.isFinite(trackDuration)
    || trackDuration <= 0
    || !Number.isFinite(bpmValue)
    || bpmValue <= 0
  ) return null;

  return {
    container,
    startTime,
    visibleSeconds,
    duration: trackDuration,
    bpm: bpmValue,
    anchorMs: gridTrack.value.grid_anchor_ms,
  };
}

function rawTimeAtClientX(clientX: number, timeline: StemTimeline): number {
  const rect = timeline.container.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
  return timeline.startTime + ratio * timeline.visibleSeconds;
}

function beatLengthSeconds(timeline: StemTimeline): number {
  return 60 / timeline.bpm;
}

function setStemLoopRange(startRawTime: number, endRawTime: number, timeline: StemTimeline): void {
  const beatLength = beatLengthSeconds(timeline);
  let start = snapTimeToBeat(Math.min(startRawTime, endRawTime), timeline.bpm, timeline.anchorMs);
  let end = snapTimeToBeat(Math.max(startRawTime, endRawTime), timeline.bpm, timeline.anchorMs);

  if (end - start < beatLength) {
    if (endRawTime < startRawTime) {
      end = Math.max(beatLength, end);
      start = end - beatLength;
    } else {
      start = Math.min(start, Math.max(0, timeline.duration - beatLength));
      end = start + beatLength;
    }
  }

  start = Math.max(0, Math.min(start, timeline.duration - beatLength));
  end = Math.min(timeline.duration, Math.max(start + beatLength, end));
  const rangeDuration = end - start;
  workspaceStore.setActiveLoopRange({
    start,
    end,
    duration: rangeDuration,
    beatCount: Math.max(1, Math.round(rangeDuration / beatLength)),
  });
}

function startDrawSelection(event: MouseEvent): void {
  const target = event.target;
  if (event.button !== 0 || (target instanceof Element && target.closest(".stem-controls"))) return;

  const container = event.currentTarget;
  if (!(container instanceof HTMLElement)) return;
  const timeline = getStemTimeline(container);
  if (!timeline) return;

  event.preventDefault();
  selectionDrag = {
    mode: "draw",
    timeline,
    startRawTime: rawTimeAtClientX(event.clientX, timeline),
  };
  startSelectionDrag();
}

function startMoveSelection(event: MouseEvent): void {
  if (event.button !== 0 || !activeLoopRange.value) return;

  const container = event.currentTarget;
  if (!(container instanceof HTMLElement)) return;
  const timeline = getStemTimeline(container.parentElement ?? container);
  if (!timeline) return;

  event.preventDefault();
  selectionDrag = {
    mode: "move",
    timeline,
    startClientX: event.clientX,
    range: activeLoopRange.value,
  };
  startSelectionDrag();
}

function startResizeLeft(event: MouseEvent): void {
  startResizeSelection(event, "resize-left");
}

function startResizeRight(event: MouseEvent): void {
  startResizeSelection(event, "resize-right");
}

function startResizeSelection(event: MouseEvent, mode: "resize-left" | "resize-right"): void {
  if (event.button !== 0 || !activeLoopRange.value) return;

  const handle = event.currentTarget;
  if (!(handle instanceof HTMLElement)) return;
  const overlay = handle.parentElement;
  if (!(overlay instanceof HTMLElement)) return;
  const timeline = getStemTimeline(overlay.parentElement ?? overlay);
  if (!timeline) return;

  event.preventDefault();
  selectionDrag = { mode, timeline, range: activeLoopRange.value };
  startSelectionDrag();
}

function startSelectionDrag(): void {
  window.addEventListener("mousemove", handleSelectionDrag);
  window.addEventListener("mouseup", stopSelectionDrag);
}

function handleSelectionDrag(event: MouseEvent): void {
  const drag = selectionDrag;
  if (!drag) return;

  event.preventDefault();
  const currentRawTime = rawTimeAtClientX(event.clientX, drag.timeline);
  const beatLength = beatLengthSeconds(drag.timeline);

  if (drag.mode === "draw") {
    setStemLoopRange(drag.startRawTime, currentRawTime, drag.timeline);
    return;
  }

  if (drag.mode === "move") {
    const rect = drag.timeline.container.getBoundingClientRect();
    const deltaTime = ((event.clientX - drag.startClientX) / rect.width) * drag.timeline.visibleSeconds;
    const selectionDuration = drag.range.duration;
    const snappedStart = snapTimeToBeat(drag.range.start + deltaTime, drag.timeline.bpm, drag.timeline.anchorMs);
    const start = Math.max(0, Math.min(snappedStart, drag.timeline.duration - selectionDuration));
    const end = start + selectionDuration;
    workspaceStore.setActiveLoopRange({ ...drag.range, start, end });
    return;
  }

  if (drag.mode === "resize-left") {
    const snappedStart = snapTimeToBeat(currentRawTime, drag.timeline.bpm, drag.timeline.anchorMs);
    const start = Math.max(0, Math.min(snappedStart, drag.range.end - beatLength));
    setStemLoopRange(start, drag.range.end, drag.timeline);
    return;
  }

  const snappedEnd = snapTimeToBeat(currentRawTime, drag.timeline.bpm, drag.timeline.anchorMs);
  const end = Math.min(drag.timeline.duration, Math.max(snappedEnd, drag.range.start + beatLength));
  setStemLoopRange(drag.range.start, end, drag.timeline);
}

function stopSelectionDrag(): void {
  window.removeEventListener("mousemove", handleSelectionDrag);
  window.removeEventListener("mouseup", stopSelectionDrag);
  selectionDrag = null;
}

async function loadTrack(track: CollectionTrack): Promise<void> {
  destroyPeaks();
  const token = ++initToken;
  isLoading.value = true;
  loadError.value = null;
  duration.value = (track.duration_ms ?? 0) / 1000;
  await nextTick();
  const container = waveformElement.value;
  const audio = audioElement.value;
  if (!container || !audio || token !== initToken) return;
  audio.src = convertFileSrc(track.location_path);
  audio.preload = "auto";
  audio.onloadedmetadata = () => { duration.value = audio.duration || duration.value; };
  audio.load();

  const metadataResult = await fetchTrackMetadata(track.location_path);
  if (token !== initToken) return;
  const waveformData = metadataResult.ok && metadataResult.metadata
      ? createPeaksWaveformData(metadataResult.metadata.waveform_peaks)
      : undefined;
  const AudioContextConstructor = window.AudioContext
      ?? (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!waveformData && !AudioContextConstructor) throw new Error("Web Audio is unavailable in this environment.");
  if (!waveformData) audioContext.value = new AudioContextConstructor!();

  await new Promise<void>((resolve, reject) => {
    Peaks.init({
      zoomview: { container, waveformColor: "#edb40b", playedWaveformColor: "#f7d15f", playheadColor: "#ffffff", showPlayheadTime: true, showAxisLabels: false, axisGridlineColor: "transparent", axisLabelColor: "transparent", enablePoints: true },
      mediaElement: audio,
      ...(waveformData ? { waveformData } : { webAudio: { audioContext: audioContext.value! } }),
      zoomLevels: ZOOM_LEVELS,
      keyboard: false,
      pointMarkerColor: "#facf25",
      createPointMarker, // Funciona para el reproductor simple
    } as any, (error: Error | null, instance: unknown) => {
      if (token !== initToken) { try { (instance as any)?.destroy?.(); } catch { /* stale callback */ } resolve(); return; }
      if (error || !instance) { reject(error ?? new Error("Peaks did not create an instance.")); return; }
      peaks.value = instance;
      wirePlayerEvents(instance);
      duration.value = (instance as any).player.getDuration() || duration.value;
      paintBeatGrid(instance); // Funciona para el reproductor simple
      installLoopSelection(instance, container);
      startOpacitySync();
      resolve();
    });
  });
}

async function togglePlayback(): Promise<void> {
  const instance = activePeaks.value;
  if (!instance) return;
  if (isPlaying.value) { instance.player.pause(); return; }
  try { await instance.player.play(); } catch (error) { loadError.value = `Playback failed: ${String(error)}`; }
}

function stop(): void {
  const instance = activePeaks.value;
  if (!instance) return;
  instance.player.pause();
  instance.player.seek(0);
  isPlaying.value = false;
}

function zoomIn(): void {
  const view = activePeaks.value?.views.getView("zoomview");
  if (view) view.setZoom({ seconds: Math.max(1, (view.getEndTime() - view.getStartTime()) * 0.8) });
}

function zoomOut(): void {
  const view = activePeaks.value?.views.getView("zoomview");
  if (view) view.setZoom({ seconds: Math.min(Math.max(1, duration.value), (view.getEndTime() - view.getStartTime()) * 1.2) });
}

usePlayerKeyboard({
  togglePlay: togglePlayback,
  stop,
  zoomIn,
  zoomOut,
  enablePadShortcuts: false,
});

watch([() => props.track, () => props.stemTracks], async ([track, stemTracks]) => {
  if (!track) { destroyPeaks(); duration.value = 0; isLoading.value = false; loadError.value = null; return; }
  if (stemTracks.length === 4) {
    destroyPeaks();
    duration.value = (track.duration_ms ?? 0) / 1000;
    isLoading.value = false;
    loadError.value = null;
    await nextTick();
    try {
      const master = await initializeStemPeaks(stemTracks);
      if (!master || props.track?.location_path !== track.location_path || props.stemTracks !== stemTracks) return;
      duration.value = master.player.getDuration() || duration.value;
      paintStemBeatGrids();
      refreshCustomSelectionOverlay();
    } catch (error) {
      if (props.track?.location_path === track.location_path) loadError.value = `Stem waveform unavailable: ${String(error)}`;
    }
    return;
  }
  try { await loadTrack(track); }
  catch (error) { if (props.track?.location_path === track.location_path) loadError.value = `Waveform unavailable: ${String(error)}`; }
  finally { if (props.track?.location_path === track.location_path) isLoading.value = false; }
}, { immediate: true });

watch([gridTrack, gridCues], () => {
  if (isMultiTrackMode.value) paintStemBeatGrids();
  else if (activePeaks.value) paintBeatGrid(activePeaks.value);
}, { deep: true });

onBeforeUnmount(() => {
  stopSelectionDrag();
  destroyPeaks();
});
</script>

<template>
  <section class="studio-zone stem-editor" aria-labelledby="stem-editor-heading">
    <p class="zone-label">Stem Editor</p>
    <template v-if="track">
      <header class="track-header">
        <div class="track-identification"><h2 id="stem-editor-heading" class="track-title">{{ title }}</h2><p class="track-artist">{{ artist }}</p></div>
        <dl class="track-details" aria-label="Track details"><div><dt>BPM</dt><dd>{{ bpm }}</dd></div><div><dt>Key</dt><dd>{{ key }}</dd></div></dl>
      </header>
      <div v-if="isMultiTrackMode" class="stem-lanes" aria-label="Stem tracks" @mousedown="startDrawSelection">
        <article v-for="(stem, index) in stemLanes" :key="stem.path" class="stem-lane" :style="{ '--stem-color': stem.color }">
          <div class="stem-controls" :aria-label="`${stem.name} controls`">
            <span class="stem-name">{{ stem.name }}</span>
            <button type="button" class="stem-control" :class="{ 'is-active': mutedStems[index] }" :aria-pressed="mutedStems[index]" :aria-label="`${mutedStems[index] ? 'Unmute' : 'Mute'} ${stem.name}`" @click="toggleMute(index)">[M]</button>
            <button type="button" class="stem-control" :class="{ 'is-active': soloedStem === index }" :aria-pressed="soloedStem === index" :aria-label="`${soloedStem === index ? 'Disable solo for' : 'Solo'} ${stem.name}`" @click="toggleSolo(index)">[S]</button>
          </div>
          <div
              ref="stemWaveform"
              class="stem-waveform"
              :aria-label="`${stem.name} waveform`"
              @wheel.prevent="handleZoomWheel"
          />
          <audio ref="stemAudio" class="audio-element" preload="none" />
        </article>
        <div class="custom-selection-overlay" :style="customSelectionOverlayStyle" @mousedown.stop="startMoveSelection">
          <div class="selection-handle handle-left" @mousedown.stop="startResizeLeft" />
          <div class="selection-handle handle-right" @mousedown.stop="startResizeRight" />
        </div>
      </div>
      <div v-else class="waveform-shell" :class="{ 'is-loading': isLoading }"><div ref="waveform" @wheel.prevent="handleZoomWheel" class="waveform" aria-label="Audio waveform. Click or drag to seek." /><p v-if="isLoading" class="waveform-status">Loading waveform…</p></div>
      <p v-if="loopLabel" class="loop-feedback">{{ loopLabel }}</p>
      <p v-if="isMultiTrackMode && isStemLoading" class="stem-loading">Loading stem data...</p>
      <p v-if="loadError" class="waveform-error">{{ loadError }}</p>
      <div class="transport"><button type="button" class="play-button" :disabled="!activePeaks" :aria-label="playLabel" @click="togglePlayback">{{ playLabel }}</button><output class="time-display" aria-label="Elapsed time and total duration">{{ timeLabel }}</output></div>
      <audio v-if="!isMultiTrackMode" ref="audio" class="audio-element" preload="none" />
    </template>
    <p v-else id="stem-editor-heading" class="empty-state">Load a track from the library</p>
  </section>
</template>

<style scoped>
.stem-editor { display: flex; min-width: 0; min-height: 0; padding: 1.5rem; flex-direction: column; background: #232326; }
.stem-editor { position: relative; }
.zone-label { margin: 0; color: #f7d15f; font-size: .6875rem; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; }
.track-header { display: flex; min-width: 0; margin-top: .75rem; align-items: flex-end; justify-content: space-between; gap: 1rem; }.track-identification { min-width: 0; }.track-title { margin: 0; overflow: hidden; color: #f2f2f2; font-size: 1.5rem; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }.track-artist, .empty-state { margin: .35rem 0 0; color: #8a8a8e; font-size: .875rem; }.empty-state { margin-top: .75rem; }
.track-details { display: flex; margin: 0; gap: 1rem; font-variant-numeric: tabular-nums; }.track-details div { display: grid; gap: .15rem; }.track-details dt { color: #8a8a8e; font-size: .625rem; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; }.track-details dd { margin: 0; color: #f7d15f; font-family: ui-monospace, "Cascadia Code", monospace; font-size: .8125rem; }
.waveform-shell { position: relative; min-height: 0; margin-top: 1.25rem; flex: 1; overflow: hidden; border: 1px solid #3a3a3e; background: #17171a; }.waveform-shell.is-loading { opacity: .6; }.waveform { width: 100%; height: 100%; min-height: 150px; }.waveform-status { position: absolute; inset: 0; display: grid; margin: 0; place-items: center; color: #8a8a8e; font-size: .75rem; pointer-events: none; }
.stem-lanes { position: relative; display: grid; min-height: 0; margin-top: 1.25rem; flex: 1; grid-template-rows: repeat(4, minmax(0, 1fr)); gap: 0; overflow: hidden; border: 1px solid #3a3a3e; background: #17171a; }.stem-lane { display: grid; min-height: 0; grid-template-columns: 60px minmax(0, 1fr); overflow: hidden; background: #17171a; }.stem-lane + .stem-lane { border-top: 1px solid #3a3a3e; }.stem-controls { display: grid; min-width: 60px; width: 60px; flex-shrink: 0; align-content: center; gap: .25rem; padding: .375rem; border-right: 1px solid #3a3a3e; background: #202024; }.stem-control { padding: .25rem 0; border: 1px solid #5a5a5e; border-radius: .125rem; background: #2a2a2e; color: #f2f2f2; cursor: pointer; font-family: ui-monospace, "Cascadia Code", monospace; font-size: .625rem; font-weight: 800; }.stem-control:hover { border-color: #f7d15f; color: #f7d15f; }.stem-control.is-active { border-color: var(--stem-color); background: var(--stem-color); color: #17171a; }.stem-control:focus-visible { outline: 2px solid #fff; outline-offset: -2px; }.stem-waveform { min-width: 0; background: linear-gradient(90deg, rgb(237 180 11 / 5%), transparent 35%); }.stem-name { color: var(--stem-color); font-size: .5625rem; font-weight: 700; letter-spacing: .08em; text-align: center; text-transform: uppercase; }.stem-loading { margin: .5rem 0 0; color: #8a8a8e; font-size: .75rem; }
.custom-selection-overlay { position: absolute; z-index: 5; pointer-events: auto; background-color: rgba(247, 209, 95, 0.25); border-left: 1px solid rgba(247, 209, 95, 0.8); border-right: 1px solid rgba(247, 209, 95, 0.8); cursor: move; }.selection-handle { position: absolute; top: 0; bottom: 0; width: 8px; cursor: ew-resize; background: rgba(247, 209, 95, 0.8); }.handle-left { left: -4px; }.handle-right { right: -4px; }
.transport { display: flex; margin-top: .75rem; align-items: center; gap: .75rem; }.play-button { min-width: 4.5rem; padding: .45rem .75rem; border: 1px solid #edb40b; border-radius: .1875rem; background: #edb40b; color: #17171a; cursor: pointer; font-size: .75rem; font-weight: 800; text-transform: uppercase; }.play-button:hover:not(:disabled) { background: #f7d15f; }.play-button:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }.play-button:disabled { cursor: not-allowed; opacity: .5; }.time-display { color: #f2f2f2; font-family: ui-monospace, "Cascadia Code", monospace; font-size: .75rem; font-variant-numeric: tabular-nums; }.waveform-error { margin: .5rem 0 0; color: #f87171; font-size: .75rem; }.audio-element { display: none; }
:deep(.peaks-view-container) { overflow: hidden !important; } @media (max-width: 520px) { .track-header { align-items: flex-start; flex-direction: column; } }
.loop-feedback { position: absolute; right: 1.5rem; bottom: 3.75rem; z-index: 2; margin: 0; padding: .3rem .45rem; border: 1px solid rgb(247 209 95 / 55%); background: rgb(23 23 26 / 88%); color: #f7d15f; font-family: ui-monospace, "Cascadia Code", monospace; font-size: .6875rem; font-variant-numeric: tabular-nums; pointer-events: none; }
/* 1. Ocultar por completo los tiradores grises flotantes (SVG y HTML) */
:deep(.peaks-segment-handle),
:deep(.peaks-segment-handle-marker),
:deep([class*="peaks-segment-handle"]) {
  display: none !important;
  visibility: hidden !important;
  opacity: 0 !important;
  pointer-events: none !important;
}

/* 2. Hacer que la franja amarilla ocupe el 100% de la altura de la pista */
:deep(.peaks-segment),
:deep([class*="peaks-segment"]) {
  /* Propiedades para HTML Divs */
  top: 0 !important;
  bottom: 0 !important;
  height: 100% !important;
  background-color: rgba(247, 209, 95, 0.25) !important;
  border-left: 1px solid rgba(247, 209, 95, 0.8) !important;
  border-right: 1px solid rgba(247, 209, 95, 0.8) !important;

  /* Propiedades para SVG (Lo que usa Peaks.js en ZoomView) */
  y: 0 !important;
  fill: rgba(247, 209, 95, 0.25) !important;       /* Fondo amarillo traslúcido */
  stroke: rgba(247, 209, 95, 0.8) !important;     /* Borde amarillo */
  stroke-width: 1px !important;
}

.stem-waveform {
  position: relative;
  min-width: 0;
  height: 100%;
}

</style>
