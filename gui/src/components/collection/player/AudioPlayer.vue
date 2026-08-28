<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from "vue";
import { convertFileSrc } from "@tauri-apps/api/core";
import Peaks from "peaks.js";
import { useConfigState } from "../../../composables/core/useConfigState.ts";
import { useLibraryState } from "../../../composables/collection/useLibraryState.ts";
import {
  fetchTrackMetadata,
  registerMetadataRefresh,
  usePlayerState,
} from "../../../composables/player/useTrackMetadata.ts";
import { useRunState } from "../../../composables/core/useRunState.ts";
import { beatMs, snapToGrid, type GridTrackData } from "../../../composables/player/useGridMath.ts";
import { usePeaksMarkers, type PlayerCue } from "../../../composables/player/usePeaksMarkers.ts";
import { useWaveformSync } from "../../../composables/player/useWaveformSync.ts";
import { usePlayerKeyboard } from "../../../composables/player/usePlayerKeyboard.ts";
import { useSaveStore } from "../../../stores/useSaveStore.ts";
import type { SuperJSON } from "../../../types/trackMetadata.ts";
import CueContextMenu from "../CueContextMenu.vue";
import PlayerHeader from "./PlayerHeader.vue";
import PlayerTransport from "./PlayerTransport.vue";
import PlayerWaveform from "./PlayerWaveform.vue";
import PlayerGridControls from "../../ui/PlayerGridControls.vue";

interface TrackData extends GridTrackData { track_path: string; cues: PlayerCue[]; }
interface WaveformShell { zoomviewElement: HTMLDivElement | null; overviewElement: HTMLDivElement | null; zoomGradientElement: HTMLDivElement | null; gradientMaskElement: HTMLDivElement | null; }

const props = defineProps<{ trackPath?: string | null; disabled?: boolean }>();
const { selectedTrackPath, clearExisting } = useConfigState();
const { collection, patchTrackInCollection } = useLibraryState();
const { status, logs, isSystemBusy, completePlayerLoad } = useRunState();
const saveStore = useSaveStore();
const { previewCache, isLoadingTrack, setLoadingTrack, setLoadedMetadata } = usePlayerState();
const { createPointMarker, paintAllMarkers, notifyGridAnchorDrag, getGridLines } = usePeaksMarkers(handleGridAnchorDrag);

const EMPTY_TRACK: TrackData = { track_path: "", bpm: 0, key: "", grid_anchor_ms: 0, duration_ms: 0, cues: [] };
const MIN_BPM = 50;
const MAX_BPM = 200;
const ZOOM_LEVELS = [64, 256, 512, 1024, 2048, 4096];
const HEX_PRIMARY = "#eaa900";
const HEX_SECONDARY = "#d27b00";
const HEX_ACCENT = "#facf25";
const HEX_GRAY = "#52525b";
const PAD_COUNT = 8;

const trackData = ref<TrackData>({ ...EMPTY_TRACK });
const savedBpm = ref(EMPTY_TRACK.bpm);
const savedGridAnchorMs = ref(EMPTY_TRACK.grid_anchor_ms);
const currentPreview = shallowRef<SuperJSON | null>(null);
const cueState = ref<PlayerCue[]>([]);
const peaks = shallowRef<any>(null);
const audioRef = ref<HTMLAudioElement | null>(null);
const waveformRef = ref<WaveformShell | null>(null);
const currentTime = ref(0);
const isPlaying = ref(false);
const loadError = ref<string | null>(null);
const isGridEditMode = ref(false);
const activeZoomLevelIndex = ref(0);
const activeCue = ref<PlayerCue | null>(null);
const activePad = ref<number | null>(null);
const isAnalysisRunning = computed(() => status.value === "running");
const isAppBlocked = computed(() => isSystemBusy.value);
const contextMenu = ref({ visible: false, x: 0, y: 0, cue: null as PlayerCue | null });
let loadToken = 0;
let peaksInitToken = 0;
let isPlayPromisePending = false;
const { trackCssGradient, startSyncLoop, stopSyncLoop } = useWaveformSync({
  peaks,
  trackData,
  preview: currentPreview,
  getZoomviewElement: () => waveformRef.value?.zoomviewElement ?? null,
  getGradientElement: () => waveformRef.value?.zoomGradientElement ?? null,
  getGradientMaskElement: () => waveformRef.value?.gradientMaskElement ?? null,
  getGridLines,
});

function finiteNumber(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value); }
function isPadIndex(value: number): boolean { return Number.isInteger(value) && value >= 0 && value < PAD_COUNT; }
function createPeaksWaveformData(data: number[]) { return { json: { version: 2, channels: 1, sample_rate: 11025, samples_per_pixel: 64, bits: 8, length: Math.floor(data.length / 2), data } }; }
function toCueId(value: unknown): number | null { if (value === null || value === undefined || value === "") return null; const numberValue = Number(value); return Number.isFinite(numberValue) && Number.isInteger(numberValue) ? numberValue : null; }
function markCurrentTrackDirty(): void {
  const path = trackData.value.track_path;
  if (!path) return;
  patchTrackInCollection(path, {
    bpm: trackData.value.bpm,
    grid_anchor_ms: trackData.value.grid_anchor_ms,
    existing_cues: cueState.value.map((cue) => ({
      name: `Cue ${cue.id + 1}`,
      type: "CUE",
      start_ms: cue.position_ms,
      hotcue: cue.id,
    })),
  });
  saveStore.markTrackDirty(path);
}
watch(isAppBlocked, (blocked) => {
  if (blocked && isPlaying.value) {
    peaks.value?.player.pause();
    isPlaying.value = false;
  }
});

watch(isLoadingTrack, (loading) => {
  if (!loading) completePlayerLoad();
});

function mergeCues(...sources: ReadonlyArray<PlayerCue>[]): PlayerCue[] {
  const byId = new Map<number, PlayerCue>();
  for (const source of sources) for (const cue of source) {
    if (!isPadIndex(cue.id) || !finiteNumber(cue.position_ms)) continue;
    byId.set(cue.id, { id: cue.id, position_ms: cue.position_ms, is_valid: cue.is_valid === true });
  }
  return [...byId.values()].sort((a, b) => a.position_ms - b.position_ms);
}
function setCueState(...sources: ReadonlyArray<PlayerCue>[]): void { cueState.value = mergeCues(...sources); }
function metadataCuesForTrack(metadata: Awaited<ReturnType<typeof fetchTrackMetadata>>["metadata"]): PlayerCue[] {
  if (!metadata) return [];
  const beat = beatMs(metadata.bpm);
  return metadata.existing_cues.flatMap((cue) => {
    const id = toCueId(cue.hotcue);
    const positionMs = Number(cue.start_ms);
    if (id === null || !Number.isFinite(positionMs)) return [];
    const nearest = beat > 0 ? metadata.grid_anchor_ms + Math.round((positionMs - metadata.grid_anchor_ms) / beat) * beat : positionMs;
    return [{ id, position_ms: positionMs, is_valid: Math.abs(positionMs - nearest) <= 1 }];
  });
}
const currentLibraryTrack = computed(() => {
  return trackData.value.track_path
    ? collection.value[trackData.value.track_path]
    : undefined;
});
const trackHeaderLabel = computed(() => {
  const track = currentLibraryTrack.value;
  return track ? `ARTIST: ${track.artist} - TITLE: ${track.title}` : "Track metadata unavailable";
});
const bpmLabel = computed(() => trackData.value.bpm > 0 ? trackData.value.bpm.toFixed(1) : "");
const keyLabel = computed(() => {
  const track = currentLibraryTrack.value;
  return track && track.key ? track.key : "";
});
const remainingLabel = computed(() => {
  if (!trackData.value.track_path || !trackData.value.duration_ms) return "";
  const remaining = Math.max(0, trackData.value.duration_ms / 1000 - currentTime.value);
  return `-${String(Math.floor(remaining / 60)).padStart(2, "0")}:${String(Math.floor(remaining % 60)).padStart(2, "0")}`;
});
const padSlots = computed(() => Array.from({ length: PAD_COUNT }, (_, index) => cueState.value.find((cue) => cue.id === index) ?? null));

const nudgeResolution = computed(() => {
  const resolutions = [
    { multiplier: 0.03125, label: "1/32 Beat" },
    { multiplier: 0.0625, label: "1/16 Beat" },
    { multiplier: 0.25, label: "1/4 Beat" },
    { multiplier: 0.5, label: "1/2 Beat" },
    { multiplier: 1, label: "1 Beat" },
    { multiplier: 2, label: "2 Beats" },
    { multiplier: 4, label: "4 Beats" },
  ] as const;

  return resolutions[activeZoomLevelIndex.value] ?? resolutions[0];
});
const dynamicNudgeMs = computed(() => nudgeResolution.value.multiplier * beatMs(trackData.value.bpm));
const isFlexGrid = computed(() => (currentPreview.value?.existing_cues.filter((cue) => cue.type === "GRID").length ?? 0) > 1);

function paintMarkers(): void { paintAllMarkers(peaks.value, trackData.value, cueState.value, isGridEditMode.value); }
function addCue(padIndex: number): void {
  if (isAnalysisRunning.value || isLoadingTrack.value || !trackData.value.track_path) return;
  if (!isPadIndex(padIndex)) return;
  if (cueState.value.some((cue) => cue.id === padIndex)) return;

  const currentSeconds = peaks.value?.player.getCurrentTime();
  const playheadMs = typeof currentSeconds === "number" ? currentSeconds * 1000 : Number.NaN;
  const { bpm, grid_anchor_ms: gridAnchorMs, duration_ms: durationMs } = trackData.value;
  const snappedMs = snapToGrid(playheadMs, bpm, gridAnchorMs);
  if (
    !finiteNumber(playheadMs)
    || !finiteNumber(bpm)
    || bpm <= 0
    || !finiteNumber(gridAnchorMs)
    || !finiteNumber(snappedMs)
    || !finiteNumber(durationMs)
    || durationMs <= 0
    || snappedMs < 0
    || snappedMs > durationMs
  ) return;

  cueState.value = [...cueState.value, { id: padIndex, position_ms: snappedMs, is_valid: true }];
  markCurrentTrackDirty();
  paintMarkers();
}
function deleteCue(padIndex: number): void {
  if (!isPadIndex(padIndex)) return;
  const nextCues = cueState.value.filter((cue) => cue.id !== padIndex);
  if (nextCues.length === cueState.value.length) return;

  cueState.value = nextCues;
  markCurrentTrackDirty();
  paintMarkers();
}
function toggleGridEditMode(): void {
  if (isFlexGrid.value) return;
  isGridEditMode.value = !isGridEditMode.value;
  paintMarkers();
}
function multiplyBpm(): void {
  if (!isGridEditMode.value || !trackData.value.track_path) return;
  const currentBpm = trackData.value.bpm;
  const nextBpm = currentBpm * 2;
  if (!finiteNumber(currentBpm) || !finiteNumber(nextBpm) || nextBpm < MIN_BPM || nextBpm > MAX_BPM) return;

  trackData.value.bpm = nextBpm;
  markCurrentTrackDirty();
  paintMarkers();
}
function divideBpm(): void {
  if (!isGridEditMode.value || !trackData.value.track_path) return;
  const currentBpm = trackData.value.bpm;
  const nextBpm = currentBpm / 2;
  if (!finiteNumber(currentBpm) || !finiteNumber(nextBpm) || nextBpm < MIN_BPM || nextBpm > MAX_BPM) return;

  trackData.value.bpm = nextBpm;
  markCurrentTrackDirty();
  paintMarkers();
}
function shiftGrid(deltaMs: number, dragStartAnchorMs: number | undefined = undefined, repaint = true): void {
  if (!Number.isFinite(deltaMs) || !trackData.value.track_path) return;

  const requestedDelta = dragStartAnchorMs === undefined
      ? deltaMs
      : deltaMs - (trackData.value.grid_anchor_ms - dragStartAnchorMs);

  const minimumDelta = Math.max(
    -trackData.value.grid_anchor_ms,
    ...cueState.value.map((cue) => -cue.position_ms),
  );

  const appliedDelta = Math.max(requestedDelta, minimumDelta);
  if (appliedDelta === 0) {
    if (repaint) paintMarkers();
    return;
  }

  trackData.value.grid_anchor_ms += appliedDelta;
  cueState.value = cueState.value.map((cue) => ({ ...cue, position_ms: cue.position_ms + appliedDelta }));
  markCurrentTrackDirty();
  if (repaint) paintMarkers();
}
function updateActiveZoomLevelIndex(currentZoom: number): void {
  if (!Number.isFinite(currentZoom)) return;

  const exactIndex = ZOOM_LEVELS.indexOf(currentZoom);
  if (exactIndex !== -1) {
    activeZoomLevelIndex.value = exactIndex;
    return;
  }

  activeZoomLevelIndex.value = ZOOM_LEVELS.reduce(
    (nearestIndex, level, index) =>
      Math.abs(level - currentZoom) < Math.abs(ZOOM_LEVELS[nearestIndex] - currentZoom)
        ? index
        : nearestIndex,
    0,
  );
}
function setGridToPlayhead(): void {
  const currentSeconds = peaks.value?.player.getCurrentTime();
  if (!isGridEditMode.value || !Number.isFinite(currentSeconds)) return;
  shiftGrid(currentSeconds * 1000 - trackData.value.grid_anchor_ms);
}
function handleGridAnchorDrag({ deltaMs, startAnchorMs, isFinal }: { deltaMs: number; startAnchorMs: number; isFinal: boolean }): void {
  // Keep Konva's native drag responsive; rebuild the marker layer only at drag end.
  shiftGrid(deltaMs, startAnchorMs, isFinal);
}
function openContextMenu(event: MouseEvent, cue: PlayerCue): void { contextMenu.value = { visible: true, x: event.clientX, y: event.clientY, cue }; }
function closeContextMenu(): void { contextMenu.value.visible = false; contextMenu.value.cue = null; }
function deleteSelectedCue(): void {
  if (!contextMenu.value.cue) return;
  deleteCue(contextMenu.value.cue.id);
  closeContextMenu();
}

async function destroyPeaks(): Promise<void> {
  stopSyncLoop();
  await nextTick();
  peaksInitToken += 1;
  isPlaying.value = false;
  const instance = peaks.value;
  peaks.value = null;
  currentTime.value = 0;
  try { instance?.destroy?.(); } catch (error) { console.warn("[AudioPlayer] Peaks destroy failed:", error); }
  try { audioRef.value?.pause(); audioRef.value?.removeAttribute("src"); audioRef.value?.load(); } catch (error) { console.warn("[AudioPlayer] audio cleanup failed:", error); }
}

function handleWaveformResize(): void {
  const instance = peaks.value;
  if (!instance) return;
  instance.views.getView("zoomview")?.fitToContainer();
  instance.views.getView("overview")?.fitToContainer();
}

async function initPeaks(): Promise<void> {
  await nextTick();
  const zoomview = waveformRef.value?.zoomviewElement;
  const overview = waveformRef.value?.overviewElement;
  const audio = audioRef.value;
  if (!zoomview || !overview || !audio || !trackData.value.track_path || peaks.value) return;
  const preview = currentPreview.value;
  if (!preview) { loadError.value = "Track preview data is unavailable."; return; }
  audio.src = convertFileSrc(trackData.value.track_path);
  audio.preload = "auto";
  audio.load();
  const initToken = ++peaksInitToken;
  Peaks.init({
    zoomview: { container: zoomview, waveformColor: "#ffffff", playedWaveformColor: HEX_SECONDARY, playheadColor: HEX_ACCENT, showPlayheadTime: true, showAxisLabels: false, axisGridlineColor: "transparent", axisLabelColor: "transparent", enablePoints: true },
    overview: { container: overview, highlightOffset: 0, waveformColor: HEX_GRAY, playedWaveformColor: HEX_SECONDARY, highlightColor: HEX_PRIMARY, highlightStrokeColor: HEX_ACCENT, highlightOpacity: 0.10, playheadColor: HEX_ACCENT, showPlayheadTime: false, showAxisLabels: false, axisGridlineColor: "transparent", axisLabelColor: "transparent", enablePoints: true },
    mediaElement: audio, waveformData: createPeaksWaveformData(preview.waveform_peaks), webAudio: false as never, zoomLevels: ZOOM_LEVELS, keyboard: false, pointMarkerColor: HEX_ACCENT, createPointMarker,
  }, (error: Error | null, instance: unknown) => {
    if (initToken !== peaksInitToken) { try { (instance as any)?.destroy?.(); } catch { /* stale Peaks callback */ } return; }
    if (error || !instance) { loadError.value = `Peaks init failed: ${error?.message ?? "no instance"}`; return; }
    peaks.value = instance;
    try {
      const view = peaks.value.views.getView("zoomview");
      wirePlayerEvents();
      const waveformCanvas = zoomview.querySelector("canvas");
      if (waveformCanvas) { waveformCanvas.style.backgroundColor = "#18181b"; waveformCanvas.style.mixBlendMode = "darken"; }
      const overviewCanvas = overview.querySelector("canvas");
      if (overviewCanvas) {
        // Aquí pones el color de fondo que quieras para la pequeña
        overviewCanvas.style.backgroundColor = "#17171a";
        // overviewCanvas.style.mixBlendMode = "darken"; // Descomenta esto si quieres el mismo efecto de fusión
      }
      if (view) {
        if (!trackData.value.duration_ms) trackData.value.duration_ms = peaks.value.player.getDuration() * 1000;
        view.setZoom({ seconds: trackData.value.duration_ms / 1500 });
      }
      paintMarkers();
      wireDragEvents();
      startSyncLoop();
    } catch (decorationError) {
      console.error("[AudioPlayer] marker setup failed:", decorationError);
      loadError.value = `Marker setup failed: ${String(decorationError)}`;
    }
  });
}

function wirePlayerEvents(): void {
  const instance = peaks.value;
  if (!instance) return;
  instance.on("zoom.update", (event: { currentZoom: number }) => {
    updateActiveZoomLevelIndex(event.currentZoom);
  });
  instance.on("player.playing", () => { isPlaying.value = true; });
  instance.on("player.pause", () => { isPlaying.value = false; });
  instance.on("player.ended", () => { isPlaying.value = false; });
  instance.on("player.timeupdate", (time: number) => { currentTime.value = time; });
}
function wireDragEvents(): void {
  const instance = peaks.value;
  if (!instance) return;
  const snap = (time: number) => Math.max(0, Math.min(snapToGrid(time * 1000, trackData.value.bpm, trackData.value.grid_anchor_ms), trackData.value.duration_ms));
  let gridAnchorDragStartMs: number | null = null;
  instance.on("points.dragmove", (event: { point: { id: string; time: number } }) => {
    if (event.point.id === "grid-anchor") {
      const startAnchorMs = gridAnchorDragStartMs ?? trackData.value.grid_anchor_ms;
      gridAnchorDragStartMs = startAnchorMs;
      notifyGridAnchorDrag({ deltaMs: event.point.time * 1000 - startAnchorMs, startAnchorMs, isFinal: false });
      return;
    }
    if (!event.point.id.startsWith("cue-")) return;
    const snapped = snap(event.point.time) / 1000;
    if (event.point.time !== snapped) instance.points.getPoint(event.point.id)?.update({ time: snapped });
  });
  instance.on("points.dragend", (event: { point: { id: string; time: number } }) => {
    if (event.point.id === "grid-anchor") {
      const startAnchorMs = gridAnchorDragStartMs ?? trackData.value.grid_anchor_ms;
      notifyGridAnchorDrag({ deltaMs: event.point.time * 1000 - startAnchorMs, startAnchorMs, isFinal: true });
      gridAnchorDragStartMs = null;
      return;
    }
    if (!event.point.id.startsWith("cue-")) return;
    const cue = cueState.value.find((candidate) => candidate.id === Number(event.point.id.slice(4)));
    if (!cue || !cue.is_valid) return;
    const snapped = snap(event.point.time);
    instance.points.getPoint(event.point.id)?.update({ time: snapped / 1000 });
    if (cue.position_ms !== snapped) { cue.position_ms = snapped; markCurrentTrackDirty(); }
  });
}

async function togglePlay(): Promise<void> { if (isAnalysisRunning.value) return; const instance = peaks.value; if (!instance) return; if (isPlaying.value) { instance.player.pause(); return; } try { await instance.player.play(); } catch (error) { loadError.value = `Playback failed: ${String(error)}`; } }
function stop(): void { if (isAnalysisRunning.value) return; const instance = peaks.value; if (!instance) return; instance.player.pause(); instance.player.seek(0); isPlaying.value = false; }
function jumpToCue(padIndex: number): void { if (isAnalysisRunning.value) return; const cue = padSlots.value[padIndex - 1]; if (peaks.value && cue) peaks.value.player.seek(cue.position_ms / 1000); }


async function startCuePreview(padIndex: number): Promise<void> {
  if (isAnalysisRunning.value || isPlayPromisePending) return;
  const cue = padSlots.value[padIndex - 1];
  const instance = peaks.value;
  if (!instance || !cue || activeCue.value) return;
  activePad.value = padIndex;
  activeCue.value = cue;
  if (isPlaying.value) {
    instance.player.pause();
  }
  instance.player.seek(cue.position_ms / 1000);
  try {
    isPlayPromisePending = true; // Bloquea nuevas solicitudes
    // WebKit workaround: Micro-pausa para permitir que el buffer asimile el 'seek' antes de 'play'
    await new Promise(resolve => setTimeout(resolve, 20));
    if (activeCue.value?.id !== cue.id) {
      isPlayPromisePending = false;
      return;
    }
    await instance.player.play();
    if (activeCue.value?.id !== cue.id) {
      instance.player.pause();
      instance.player.seek(cue.position_ms / 1000);
    }
  } catch (error: any) {
    activeCue.value = null;
    if (error.name !== "AbortError") loadError.value = `Cue preview failed: ${String(error)}`;
  } finally {
    isPlayPromisePending = false;
  }
}
function endCuePreview(padIndex: number): void {
  if (activePad.value !== padIndex) return;
  const cue = padSlots.value[padIndex - 1];
  if (peaks.value && cue && activeCue.value?.id === cue.id) {
    peaks.value.player.pause();
    setTimeout(() => {
      peaks.value?.player.seek(cue.position_ms / 1000);
    }, 15);
  }
  activeCue.value = null; activePad.value = null; isPlaying.value = false;
}
function seekBeats(beatsToSeek: number): void {
  if (isAnalysisRunning.value) return; // Si está analizando, bloqueamos
  const instance = peaks.value;
  if (!instance || trackData.value.bpm <= 0) return;

  // Calculamos cuánto dura un beat en segundos y lo multiplicamos
  const beatDurationSec = 60 / trackData.value.bpm;
  const timeShiftSec = beatsToSeek * beatDurationSec;

  const currentSec = instance.player.getCurrentTime();
  const totalSec = trackData.value.duration_ms / 1000;

  // Calculamos el nuevo tiempo asegurándonos de no salirnos de la pista
  const newTime = Math.max(0, Math.min(currentSec + timeShiftSec, totalSec));

  instance.player.seek(newTime);
}
usePlayerKeyboard({
  togglePlay,
  stop,
  onPadTrigger: startCuePreview,
  onPadRelease: endCuePreview,
  onSeekBeats: seekBeats,
});

function zoomIn(): void { const view = peaks.value?.views.getView("zoomview"); if (view) view.setZoom({ seconds: Math.max(1, (view.getEndTime() - view.getStartTime()) * 0.8) }); }
function zoomOut(): void { const view = peaks.value?.views.getView("zoomview"); if (view) view.setZoom({ seconds: Math.min(trackData.value.duration_ms / 1000, (view.getEndTime() - view.getStartTime()) * 1.2) }); }
function handleZoomWheel(event: WheelEvent): void {
  const view = peaks.value?.views.getView("zoomview");
  const container = waveformRef.value?.zoomviewElement;
  if (!view || !container) return;
  const totalSeconds = (trackData.value.duration_ms || 300000) / 1000;
  const startTime = view.getStartTime();
  const visibleSeconds = view.getEndTime() - startTime;
  const rect = container.getBoundingClientRect();
  if (event.altKey && isGridEditMode.value) {
    shiftGrid(event.deltaY < 0 ? -dynamicNudgeMs.value : dynamicNudgeMs.value);
    return;
  }
  if (event.shiftKey) { view.setStartTime(Math.max(0, Math.min(startTime + event.deltaY * (visibleSeconds / rect.width) * 1.5, totalSeconds - visibleSeconds))); return; }
  if (!event.ctrlKey && !event.metaKey) return;
  const ratio = (event.clientX - rect.left) / rect.width;
  const newSeconds = event.deltaY < 0 ? Math.max(1, visibleSeconds * 0.8) : Math.min(totalSeconds, visibleSeconds * 1.2);
  if (newSeconds === visibleSeconds) return;
  const timeAtCursor = startTime + ratio * visibleSeconds;
  view.setZoom({ seconds: newSeconds });
  const actualSeconds = view.getEndTime() - view.getStartTime();
  view.setStartTime(Math.max(0, Math.min(timeAtCursor - ratio * actualSeconds, totalSeconds - actualSeconds)));
}

/* Historical note: local persistence is intentionally disabled; global save owns persistence.
  if (!trackData.value.track_path) return;
  isSaving.value = true; loadError.value = null;
  try {
    const cuePoints = toCuePointPayload(cueState.value);
    const result = await legacyCueCommit(
      trackData.value.track_path,
      cuePoints,
      hasGridAnchorChanges.value ? trackData.value.grid_anchor_ms : undefined,
      hasBpmChanges.value ? trackData.value.bpm : undefined,
    );
    if (!result.ok) throw new Error(result.error);
// 1. Actualizamos el estado actual del reproductor
    trackData.value.cues = JSON.parse(JSON.stringify(cueState.value));
    savedBpm.value = trackData.value.bpm;
    savedGridAnchorMs.value = trackData.value.grid_anchor_ms;
    legacyDirtyState.value = false;

    patchTrackInCollection(trackData.value.track_path, {
      bpm: trackData.value.bpm,
    });

    // 2. ACTUALIZACIÓN DE LA CACHÉ EN CALIENTE
    const cachedMetadata = previewCache.value.get(trackData.value.track_path);
    if (cachedMetadata) {
      // Reutilizamos el 'cuePoints' que ya está perfectamente mapeado para Python (hotcue 0-7)
      // para reconstruir el formato que espera la caché de Vue
      const updatedCacheCues = cuePoints.map((cp) => ({
        name: cp.name,
        type: "CUE",
        start_ms: cp.start_ms,
        hotcue: cp.hotcue,
      }));

      // Inyectamos también el marcador del Grid actualizado para que no se pierda al recargar
      updatedCacheCues.push({
        name: "Grid",
        type: "GRID",
        start_ms: trackData.value.grid_anchor_ms,
        hotcue: -1, // Traktor usa -1 para marcadores no asignados a un Pad
      });

      // Sobrescribimos el Map con los metadatos frescos
      previewCache.value.set(trackData.value.track_path, {
        ...cachedMetadata,
        bpm: trackData.value.bpm,
        grid_anchor_ms: trackData.value.grid_anchor_ms,
        existing_cues: updatedCacheCues as any, // Hacemos cast a any o al tipo exacto si lo tienes tipado estricto
      });
    }
  } catch (error) { loadError.value = `Failed to save changes: ${error instanceof Error ? error.message : String(error)}`; }
  finally { isSaving.value = false; }
}
function discardChanges(): void {
  setCueState(trackData.value.cues);
  trackData.value.bpm = savedBpm.value;
  trackData.value.grid_anchor_ms = savedGridAnchorMs.value;
  legacyDirtyState.value = false;
  try { paintMarkers(); } catch (error) { console.error("[AudioPlayer] marker repaint failed:", error); }
*/

async function loadTrack(path: string): Promise<void> {
  const trimmed = path.trim(); if (!trimmed) return;
  const token = ++loadToken;
  setLoadingTrack(true);
  loadError.value = null; currentPreview.value = null; trackData.value = { ...EMPTY_TRACK };
  await destroyPeaks();
  try {
    const metadataResult = await fetchTrackMetadata(trimmed);
    if (token !== loadToken) return;
    if (!metadataResult.ok || !metadataResult.metadata) throw new Error(metadataResult.error ? String(metadataResult.error) : "No se pudieron obtener los metadatos de la pista desde el NML.");
    const metadata = metadataResult.metadata;
    savedBpm.value = metadata.bpm;
    previewCache.value.set(trimmed, metadata);
    setLoadedMetadata(trimmed, metadata, collection.value[trimmed]?.key ?? null);
    currentPreview.value = metadata;
    const metadataCues = metadataCuesForTrack(metadata);
    const metadataDuration = Number((metadata as unknown as { duration_ms?: unknown }).duration_ms) || 0;
    const metadataKey = typeof (metadata as unknown as { key?: unknown }).key === "string"
      ? (metadata as unknown as { key: string }).key
      : "";
    trackData.value = { track_path: trimmed, bpm: metadata.bpm || 0, key: metadataKey, grid_anchor_ms: metadata.grid_anchor_ms || 0, duration_ms: metadataDuration, cues: metadataCues };
    savedGridAnchorMs.value = trackData.value.grid_anchor_ms;
    setCueState(metadataCues); isGridEditMode.value = false; activeZoomLevelIndex.value = 0;
    await nextTick(); if (token !== loadToken) return;
    await initPeaks();
  } catch (error) {
    if (token !== loadToken) return;
    loadError.value = error instanceof Error ? error.message : String(error);
    trackData.value = { ...EMPTY_TRACK }; savedBpm.value = EMPTY_TRACK.bpm; savedGridAnchorMs.value = EMPTY_TRACK.grid_anchor_ms; currentPreview.value = null; cueState.value = []; isGridEditMode.value = false; activeZoomLevelIndex.value = 0;
    await destroyPeaks();
  } finally { if (token === loadToken) setLoadingTrack(false); }
}

async function resetPlayerState(): Promise<void> {
  const currentPath = trackData.value.track_path || selectedTrackPath.value;
  if (!currentPath) return;
  previewCache.value.delete(currentPath);
  await loadTrack(currentPath);
}

const unregisterMetadataRefresh = registerMetadataRefresh(async () => {
  await resetPlayerState();
});

defineExpose({ loadTrack, resetPlayerState });
onMounted(() => { const initialPath = props.trackPath ?? selectedTrackPath.value; if (initialPath) void loadTrack(initialPath); });
onBeforeUnmount(() => { unregisterMetadataRefresh(); loadToken += 1; void destroyPeaks(); });

watch(status, async (nextStatus, previousStatus) => {
  if (nextStatus !== "success" || previousStatus !== "running" || !trackData.value.track_path) return;
  const currentPath = trackData.value.track_path;

  // 1. Extraemos los cues del log del último análisis
  const newCues = logs.value.flatMap((log) =>
    log.msg?.type === "cue_written"
      ? [{ id: Number(log.msg.hotcue), position_ms: Number(log.msg.start_ms), is_valid: true }]
      : []
  );

  // 2. Si hay más de 8 cues, es un análisis de Playlist masivo.
  // Lo ignoramos para no inyectar cues de otras canciones en el reproductor actual.
  if (newCues.length > 8) return;

  // 3. Si la pista seleccionada en la UI es la que tenemos abierta en el reproductor
  if (selectedTrackPath.value === currentPath) {
    // EL PURGADOR: Si clearExisting está activo, vaciamos la memoria base
    const baseCues = clearExisting.value ? [] : cueState.value;
    const merged = mergeCues(baseCues, newCues);

    trackData.value.cues = merged;
    setCueState(merged);

    // ACTUALIZACIÓN DE CACHÉ: Sobreescribimos la memoria de Vue en vivo
    // para que si el usuario redimensiona la ventana no reaparezcan los fantasmas
    const cachedMeta = previewCache.value.get(currentPath);
    if (cachedMeta) {
      cachedMeta.existing_cues = merged.map(c => ({
        hotcue: c.id,
        name: "Cue",
        start_ms: c.position_ms,
        type: "CUE"
      }));
    }

    try { paintMarkers(); } catch { /* ignore */ }
  }
});
watch(() => props.trackPath ?? selectedTrackPath.value, (next, previous) => {
  if (next === previous) return;
  if (next) void loadTrack(next);
  else { trackData.value = { ...EMPTY_TRACK }; savedBpm.value = EMPTY_TRACK.bpm; savedGridAnchorMs.value = EMPTY_TRACK.grid_anchor_ms; cueState.value = []; loadError.value = null; setLoadingTrack(false); void destroyPeaks(); }
});
</script>

<template>
  <section class="bg-zinc-900 border border-zinc-800 rounded-md p-2 flex flex-col gap-2 select-none overflow-y-auto">
    <PlayerHeader :has-track="Boolean(trackData.track_path)" :track-header-label="trackHeaderLabel" :bpm-label="bpmLabel" :remaining-label="remainingLabel" :key-label="keyLabel"/>
    <div v-if="loadError" class="text-xs font-mono text-red-500 bg-zinc-950 border border-zinc-800 rounded px-2 py-1">{{ loadError }}</div>
    <audio ref="audioRef" class="hidden" preload="none" aria-hidden="true" />
    <div
        class="transition-opacity duration-300 flex-1 min-h-0 flex flex-col"
        :class="{ 'opacity-50 pointer-events-none grayscale': isAppBlocked }"
        >
    <PlayerWaveform ref="waveformRef" :has-track="Boolean(trackData.track_path)" :is-loading="isLoadingTrack" :is-saving="saveStore.isSaving" :track-css-gradient="trackCssGradient" @resize="handleWaveformResize" @wheel="handleZoomWheel" @zoom-in="zoomIn" @zoom-out="zoomOut" />
        </div>
    <div
        class="transition-opacity duration-300 shrink-0 flex items-center gap-2"
        :class="{ 'opacity-50 pointer-events-none grayscale': isAppBlocked }"
    >
      <div class="flex-1 min-w-0">
        <PlayerTransport
            :has-track="Boolean(trackData.track_path)"
            :is-playing="isPlaying"
            :pad-slots="padSlots"
            :active-pad="activePad"
            @play="togglePlay"
            @stop="stop"
            @jump="jumpToCue"
            @add-cue="addCue"
            @preview-start="startCuePreview"
            @preview-end="endCuePreview"
            @context-menu="openContextMenu"
        />
      </div>

      <PlayerGridControls
          :has-track="Boolean(trackData.track_path)"
          :is-flex-grid="isFlexGrid"
          :is-grid-edit-mode="isGridEditMode"
          :dynamic-label="nudgeResolution.label"
          :dynamic-step-ms="dynamicNudgeMs"
          @nudge="shiftGrid"
          @set-to-playhead="setGridToPlayhead"
          @multiply-bpm="multiplyBpm"
          @divide-bpm="divideBpm"
      />

      <button
          v-if="trackData.track_path"
          type="button"
          class="text-xs font-semibold uppercase px-4 h-[34px] rounded border transition-colors shrink-0 flex items-center justify-center disabled:cursor-not-allowed disabled:opacity-50"
          :class="isGridEditMode ? 'bg-accent text-zinc-950 border-accent' : 'bg-zinc-800 text-zinc-300 border-zinc-700 hover:bg-zinc-700'"
          :disabled="isFlexGrid"
          :title="isFlexGrid ? 'Variable BPM (Flex Grid) is unsupported; grid editing is disabled.' : 'Enables or disables editing the Grid Anchor.'"
          @click="toggleGridEditMode"
      >
        {{ isGridEditMode ? 'Exit Grid Edit' : 'Edit Grid' }}
      </button>
    </div>
    <CueContextMenu :x="contextMenu.x" :y="contextMenu.y" :visible="contextMenu.visible" @close="closeContextMenu" @delete="deleteSelectedCue" />
  </section>
</template>
