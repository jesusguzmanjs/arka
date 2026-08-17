<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, useTemplateRef, watch } from "vue";
import { storeToRefs } from "pinia";
import { convertFileSrc } from "@tauri-apps/api/core";
import Peaks from "peaks.js";
import * as Tone from "tone";
import { fetchTrackMetadata } from "../composables/useTrackMetadata";
import { usePeaksMarkers, type PlayerCue } from "../composables/usePeaksMarkers";
import { usePlayerKeyboard } from "../composables/usePlayerKeyboard";
import { useRemixAudio } from "../composables/useRemixAudio";
import { activeAudioEngine } from "../composables/useGlobalAudio";
import { STEM_LANES, useStemPeaks } from "../composables/useStemPeaks";
import { beatMs, snapToGrid, type GridTrackData } from "../composables/useGridMath";
import { useWorkspaceStore } from "../stores/useWorkspaceStore";
import type { CollectionTrack } from "../types/library";
import PlayerGridControls from "./PlayerGridControls.vue";

const props = defineProps<{ track: CollectionTrack | null; stemTracks: string[] }>();
const workspaceStore = useWorkspaceStore();
const { activeLoopRange, editorMode, editingPadId, remixPads } = storeToRefs(workspaceStore);
const { isDeckPlaying, updatePlayerLoopRange, updatePlayerTranspose } = useRemixAudio();
const waveformElement = useTemplateRef<HTMLDivElement>("waveform");
const stemWaveformElements = useTemplateRef<HTMLDivElement[]>("stemWaveform");
const peaks = shallowRef<any>(null);
const isLoading = shallowRef(false);
const loadError = shallowRef<string | null>(null);
const isPlaying = shallowRef(false);
const currentTime = shallowRef(0);
const duration = shallowRef(0);
const preciseMetadata = shallowRef<any>(null);
const selectionOverlayViewportVersion = shallowRef(0);
let syncRaf: number | null = null;
let stemGridBpm = 0;
let initToken = 0;
let watchToken = 0;
let singleTonePlayer: Tone.GrainPlayer | null = null;
let singlePlayerEmitter: any = null;
let singlePlayerIsPlaying = false;
let singlePlayerAnimationFrame: number | null = null;
let singlePlayerStoppedByAudioMutex = false;
let waveformResizeObserver: ResizeObserver | null = null;
const PAD_ZOOM_LEVELS = [8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192];
const TRACK_ZOOM_LEVELS = [64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384];
const MIN_VISIBLE_SECONDS = 0.1;
let singlePlayerSavedTime = 0;

interface StemTimeline {
  container: HTMLElement;
  startTime: number;
  visibleSeconds: number;
  duration: number;
  bpm: number;
  anchorMs: number;
}

type SelectionDrag =
  | { mode: "draw"; timeline: StemTimeline; startRawTime: number; didCreateLoop: boolean }
  | { mode: "move"; timeline: StemTimeline; startClientX: number; range: NonNullable<typeof activeLoopRange.value> }
  | { mode: "resize-left" | "resize-right"; timeline: StemTimeline; range: NonNullable<typeof activeLoopRange.value> };

let selectionDrag: SelectionDrag | null = null;

// 1. Instancia de marcadores para el modo de pista ÚNICA (normal)
const singleMarker = usePeaksMarkers();
const { createPointMarker, paintAllMarkers } = singleMarker;

// 2. Creamos 4 instancias independientes para el modo MULTI-TRACK (Stems)
const stemMarkers = STEM_LANES.map(() => usePeaksMarkers());

const title = computed(() => isPadEditMode.value ? (editingPad.value?.pad.settings.name || editingPadId.value || "Remix Pad") : (props.track?.title || "Untitled track"));
const artist = computed(() => isPadEditMode.value ? "Trim, grid and pitch" : (props.track?.artist || "Unknown artist"));
const bpm = computed(() => {
  const value = isPadEditMode.value ? editBpm.value : props.track?.bpm;
  return value === null || value === undefined ? "—" : value.toFixed(2);
});
const keyLabel = computed(() => {
  if (isPadEditMode.value) {
    return editingPad.value?.pad.audio?.originalKey?.trim() || "—";
  }
  return props.track?.key?.trim() || "—";
});
const timeLabel = computed(() => `${formatTime(currentTime.value)} / ${formatTime(duration.value)}`);
const playLabel = computed(() => isPlaying.value ? "Pause" : "Play");
const isMultiTrackMode = computed(() => !isPadEditMode.value && props.stemTracks.length === 4);
const stemLanes = computed(() => STEM_LANES.map((lane, index) => ({
  ...lane,
  path: props.stemTracks[index],
})));
const isPadEditMode = computed(() => editorMode.value === "pad");
const editingPad = computed(() => {
  const id = editingPadId.value;
  if (!id) return null;
  for (let columnIndex = 0; columnIndex < remixPads.value.length; columnIndex += 1) {
    const padIndex = remixPads.value[columnIndex].findIndex((pad) => pad.settings.id === id);
    if (padIndex >= 0) return { pad: remixPads.value[columnIndex][padIndex], columnIndex, padIndex };
  }
  return null;
});
const editorSourcePath = computed(() => (
  isPadEditMode.value ? editingPad.value?.pad.audio?.filePath ?? null : props.track?.location_path ?? null
));
const editBpm = shallowRef(120);
const editAnchorMs = shallowRef(0);
const editTranspose = shallowRef(0);
const isPadGridEditMode = ref(false);

const {
  getMasterPeaks,
  getStemPeaks,
  isLoading: isStemLoading, // <--- RECUPERADO: Faltaba extraelo aquí
  isReady: isStemReady,
  muted: mutedStems,
  soloed: soloedStem,
  initialize: initializeStemPeaks,
  pauseForAudioMutex,
  resumeFromAudioMutex,
  toggleMute,
  toggleSolo,
  destroy: destroyStemPeaks
} = useStemPeaks({
  getWaveformContainers: () => stemWaveformElements.value ?? [],
  createPointMarker: (markerOptions, laneIndex) => {
    const isLastLane = laneIndex === STEM_LANES.length - 1;
    return stemMarkers[laneIndex].createPointMarker(markerOptions, { showLabel: isLastLane });
  },
  onDuration: (seconds) => { duration.value = seconds || duration.value; },
  onMixStateChanged: (muted, soloed) => workspaceStore.setStemMixState(muted, soloed),
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
  // Esta línea fuerza a Vue a recalcular cuando hacemos refreshCustomSelectionOverlay()
  void selectionOverlayViewportVersion.value;

  // The inline display style takes precedence over utility classes, so hide the
  // trim overlay here while the grid anchor is being edited.
  if (isPadEditMode.value && isPadGridEditMode.value) {
    return { display: "none" };
  }

  const range = activeLoopRange.value;
  const activeInstance = activePeaks.value;
  const view = activeInstance?.views?.getView("zoomview");

  if (!range || !view) return { display: "none" };

  // Usamos el API de Peaks para obtener los segundos exactos visibles ahora mismo
  const startTime = view.getStartTime();
  const endTime = view.getEndTime();
  const visibleSeconds = endTime - startTime;

  if (!Number.isFinite(startTime) || !Number.isFinite(visibleSeconds) || visibleSeconds <= 0) {
    return { display: "none" };
  }

  // Calculamos el porcentaje exacto de dónde cae el inicio del bucle dentro de la ventana visible
  const rawLeftPercent = ((range.start - startTime) / visibleSeconds) * 100;

  // Calculamos qué porcentaje de la ventana visible ocupa la duración del bucle
  const rawWidthPercent = (range.duration / visibleSeconds) * 100;

  // Si la caja está completamente fuera de la vista por la izquierda o por la derecha, la ocultamos (opcional pero limpio)
  // Keep the overlay within the visible viewport while Peaks recalculates after a resize.
  const leftPercent = Math.max(0, Math.min(100, rawLeftPercent));
  const endPercent = Math.max(leftPercent, Math.min(100, rawLeftPercent + rawWidthPercent));
  const widthPercent = endPercent - leftPercent;

  if (widthPercent <= 0) return { display: "none" };

  return {
    display: "block",
    left: `${leftPercent}%`,
    width: `${widthPercent}%`,
    top: 0,
    bottom: 0,
    height: "100%",
  };
});
const trimMaskGeometry = computed(() => {
  void selectionOverlayViewportVersion.value;

  if (isPadEditMode.value && isPadGridEditMode.value) return null;

  const range = activeLoopRange.value;
  const view = activePeaks.value?.views?.getView("zoomview");
  if (!range || !view) return null;

  const startTime = view.getStartTime();
  const visibleSeconds = view.getEndTime() - startTime;
  if (!Number.isFinite(startTime) || !Number.isFinite(visibleSeconds) || visibleSeconds <= 0) return null;

  const rawLeftPercent = ((range.start - startTime) / visibleSeconds) * 100;
  const rawWidthPercent = (range.duration / visibleSeconds) * 100;
  if (rawLeftPercent + rawWidthPercent < 0 || rawLeftPercent > 100) return null;

  const leftPercent = Math.max(0, Math.min(100, rawLeftPercent));
  const selectionEndPercent = Math.max(leftPercent, Math.min(100, rawLeftPercent + rawWidthPercent));
  return {
    leftPercent,
    widthPercent: selectionEndPercent - leftPercent,
    rightPercent: Math.max(0, 100 - selectionEndPercent),
  };
});
const gridTrack = computed<GridTrackData>(() => ({
  bpm: isPadEditMode.value ? editBpm.value : (preciseMetadata.value?.bpm ?? props.track?.bpm ?? 0),
  key: props.track?.key ?? "",
  grid_anchor_ms: isPadEditMode.value ? editAnchorMs.value : (preciseMetadata.value?.grid_anchor_ms ?? props.track?.grid_anchor_ms ?? 0),
  duration_ms: duration.value * 1000,
}));
const gridCues = computed<PlayerCue[]>(() => {
  if (isPadEditMode.value) return [];
  const cuesSource = preciseMetadata.value?.existing_cues ?? props.track?.existing_cues ?? [];
  return cuesSource
      .filter((cue: any) => Number.isInteger(cue.hotcue) && cue.hotcue >= 0 && cue.hotcue < 8 && Number.isFinite(cue.start_ms))
      .map((cue: any) => ({ id: cue.hotcue, position_ms: cue.start_ms, is_valid: true }))
      .sort((first: PlayerCue, second: PlayerCue) => first.id - second.id);
});

watch(isStemReady, (ready) => {
  if (ready) {
    startOpacitySync();
  } else {
    stopOpacitySync();
  }
}, { immediate: true });

watch(activeLoopRange, (range) => {
  if (range) {
    Tone.Transport.setLoopPoints(range.start, range.end);
    Tone.Transport.loop = true;
  } else {
    Tone.Transport.loop = false;
  }
});

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

function setPadEditSelection(): void {
  const target = editingPad.value?.pad;
  if (!isPadEditMode.value || !target || duration.value <= 0) return;

  const start = Math.max(0, Math.min(target.settings.loopStart ?? 0, duration.value));
  const end = Math.max(start, Math.min(target.settings.loopEnd ?? duration.value, duration.value));
  const beatLength = 60 / Math.max(editBpm.value, 1);
  workspaceStore.setActiveLoopRange({
    start,
    end: end > start ? end : Math.min(duration.value, start + beatLength),
    duration: Math.max(0, end - start) || Math.min(beatLength, duration.value - start),
    beatCount: Math.max(1, Math.round(Math.max(beatLength, end - start) / beatLength)),
  });
}

function nudgePadGrid(deltaMs: number): void {
  editAnchorMs.value += deltaMs;
}

function setPadGridToPlayhead(): void {
  editAnchorMs.value = Math.round(currentTime.value * 1000);
}

function multiplyPadBpm(): void {
  editBpm.value = Math.min(300, editBpm.value * 2);
}

function dividePadBpm(): void {
  editBpm.value = Math.max(20, editBpm.value / 2);
}

function savePadEdit(): void {
  const target = editingPad.value;
  const range = activeLoopRange.value;
  if (!target || !range) return;

  const { pad } = target;
  const transpose = Math.max(-12, Math.min(12, Number(editTranspose.value) || 0));
  pad.settings = {
    ...pad.settings,
    transpose,
    loopStart: range.start,
    loopEnd: range.end,
  };
  updatePlayerTranspose(pad.settings.id, transpose);
  updatePlayerLoopRange(pad.settings.id, range.start, range.end);
  workspaceStore.setActiveStudioTrack(null);
  workspaceStore.exitPadEditMode();
}

function cancelPadEdit(): void {
  workspaceStore.setActiveStudioTrack(null);
  workspaceStore.exitPadEditMode();
}

watch(editingPad, (target) => {
  if (!isPadEditMode.value || !target?.pad.audio) return;
  isPadGridEditMode.value = false;
  editBpm.value = target.pad.audio.originalBpm || 120;
  editAnchorMs.value = target.pad.audio.gridAnchorMs || 0;
  editTranspose.value = target.pad.settings.transpose ?? target.pad.audio.pitchShift ?? 0;
  setPadEditSelection();
}, { immediate: true });

watch(editTranspose, (newValue) => {
  if (singleTonePlayer) {
    singleTonePlayer.detune = (Number(newValue) || 0) * 100;
  }
});

/*function createPeaksWaveformData(data: number[]) {
  return { json: { version: 2, channels: 1, sample_rate: 11025, samples_per_pixel: 64, bits: 8, length: Math.floor(data.length / 2), data } };
}*/

function startOpacitySync(): void {
  if (syncRaf === null) {
    syncRaf = requestAnimationFrame(opacitySyncLoop);
  }
}

function opacitySyncLoop(): void {
  if (activeAudioEngine.value !== "stems") {
    syncRaf = null;
    return;
  }

  syncGridOpacity();
  refreshCustomSelectionOverlay();
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

function observeWaveformResize(container: HTMLDivElement | null): void {
  waveformResizeObserver?.disconnect();
  waveformResizeObserver = null;
  if (!container) return;

  waveformResizeObserver = new ResizeObserver(() => {
    const view = peaks.value?.views?.getView("zoomview");
    if (view) {
      const currentStartTime = view.getStartTime();
      view.fitToContainer();
      view.setStartTime(currentStartTime);
    }
    refreshCustomSelectionOverlay();
    syncGridOpacity();
  });
  waveformResizeObserver.observe(container);
}

watch(waveformElement, observeWaveformResize, { flush: "post" });

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
  refreshCustomSelectionOverlay();
  syncGridOpacity();
}

function destroyPeaks(): void {
  initToken += 1;
  stopSelectionDrag();
  stopOpacitySync();
  destroyStemPeaks();
  workspaceStore.setActiveLoopRange(null);
  isPlaying.value = false;
  currentTime.value = 0;
  const instance = peaks.value;
  peaks.value = null;
  try { instance?.destroy?.(); } catch (error) { console.warn("[StemEditor] Peaks destroy failed:", error); }
  if (singlePlayerAnimationFrame !== null) cancelAnimationFrame(singlePlayerAnimationFrame);
  singlePlayerAnimationFrame = null;
  singlePlayerEmitter = null;
  singlePlayerIsPlaying = false;
  singlePlayerStoppedByAudioMutex = false;
  singleTonePlayer?.dispose();
  singleTonePlayer = null;
}

function wirePlayerEvents(instance: any): void {
  instance.on("player.playing", () => {
    if (activeAudioEngine.value === "stems") isPlaying.value = true;
  });
  instance.on("player.pause", () => { isPlaying.value = false; });
  instance.on("player.ended", () => { isPlaying.value = false; });

  // Lo dejamos súper limpio, solo actualiza la interfaz
  instance.on("player.timeupdate", (time: number) => {
    if (activeAudioEngine.value !== "stems") return;
    currentTime.value = time;
  });
  instance.on("zoomview.update", syncGridOpacity);
  instance.on("zoomview.panned", syncGridOpacity);
  instance.on("zoom.update", syncGridOpacity);
}

function stopSingleTimeUpdates(): void {
  if (singlePlayerAnimationFrame !== null) cancelAnimationFrame(singlePlayerAnimationFrame);
  singlePlayerAnimationFrame = null;
}

function emitSingleTimeUpdate(): void {
  if (activeAudioEngine.value !== "stems" || !singlePlayerIsPlaying) {
    singlePlayerAnimationFrame = null;
    return;
  }
  singlePlayerEmitter?.emit("player.timeupdate", Tone.Transport.seconds);
  singlePlayerAnimationFrame = requestAnimationFrame(emitSingleTimeUpdate);
}

function createSingleCustomPlayer(): any {
  return {
    init: async (eventEmitter: any) => {
      singlePlayerEmitter = eventEmitter;
    },
    destroy: () => {
      singlePlayerEmitter = null;
      stopSingleTimeUpdates();
    },
    play: async () => {
      await Tone.start();
      if (activeAudioEngine.value !== "stems") return;
      // Inyectamos el tiempo congelado a Tone justo antes de arrancar
      Tone.Transport.seconds = singlePlayerSavedTime;
      Tone.Transport.start();
      singlePlayerIsPlaying = true;
      singlePlayerEmitter?.emit("player.playing", Tone.Transport.seconds);
      stopSingleTimeUpdates();
      emitSingleTimeUpdate();
    },
    pause: () => {
      if (activeAudioEngine.value === "stems") Tone.Transport.pause();
      singlePlayerIsPlaying = false;
      singlePlayerEmitter?.emit("player.pause", Tone.Transport.seconds);
      stopSingleTimeUpdates();
    },
    isPlaying: () => singlePlayerIsPlaying,
    isSeeking: () => false,
    // FIX: Devolvemos el tiempo congelado si no somos el motor activo
    getCurrentTime: () => activeAudioEngine.value === "stems" ? Tone.Transport.seconds : singlePlayerSavedTime,
    getDuration: () => singleTonePlayer?.buffer.duration ?? 0,
    seek: (time: number) => {
      singlePlayerSavedTime = time;
      // Solo manipulamos Tone.js si está reproduciendo para evitar que se corrompa en pausa
      if (activeAudioEngine.value === "stems" && singlePlayerIsPlaying) {
        Tone.Transport.seconds = time;
      }
      singlePlayerEmitter?.emit("player.seeked", time);
      singlePlayerEmitter?.emit("player.timeupdate", time);
    },
  };
}

function pauseStemPlaybackForAudioMutex(): void {
  activePeaks.value?.player.pause();
  if (isMultiTrackMode.value) {
    pauseForAudioMutex();
  } else {
    if (singleTonePlayer) {
      // FIX: Guardamos el tiempo exacto antes de parar
      singlePlayerSavedTime = Tone.Transport.seconds;
      singleTonePlayer.unsync();
      singleTonePlayer.stop();
      singleTonePlayer.mute = true;
      singlePlayerStoppedByAudioMutex = true;
    }
    singlePlayerIsPlaying = false;
    singlePlayerEmitter?.emit("player.pause", singlePlayerSavedTime);
    stopSingleTimeUpdates();
  }
  isPlaying.value = false;
}

function activateStemPlayback(): void {
  activeAudioEngine.value = "stems";
  startOpacitySync();
  if (isMultiTrackMode.value) {
    resumeFromAudioMutex();
  } else if (singleTonePlayer) {
    if (singlePlayerStoppedByAudioMutex) {
      // FIX: Restauramos el reloj y forzamos el inicio en 0
      Tone.Transport.seconds = singlePlayerSavedTime;
      singleTonePlayer.sync().start(0);
      singlePlayerStoppedByAudioMutex = false;
    }
    singleTonePlayer.mute = false;
  }
}

function paintBeatGrid(instance: any): void {
  paintAllMarkers(instance, gridTrack.value, gridCues.value, isPadEditMode.value && isPadGridEditMode.value);
  syncGridOpacity();
}

function wirePadGridAnchorDrag(instance: any): void {
  instance.on("points.dragmove", (event: { point: { id: string; time: number } }) => {
    if (event.point.id !== "grid-anchor") return;

    if (isPadEditMode.value && isPadGridEditMode.value) {
      editAnchorMs.value = event.point.time * 1000;
      return;
    }

    instance.points.getPoint("grid-anchor")?.update({ time: editAnchorMs.value / 1000 });
  });
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

  const visibleBeats = viewDuration / (beatMs(currentBpm) / 1000);

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

// Le quitamos el argumento "container: HTMLElement"
function getStemTimeline(): StemTimeline | null {
  const activeInstance = activePeaks.value;
  const view = activeInstance?.views?.getView("zoomview");

  // Magia: Elegimos automáticamente el contenedor de la onda pura ignorando la barra lateral
  const container = isMultiTrackMode.value
      ? stemWaveformElements.value?.[0]
      : waveformElement.value;

  const startTime = view?.getStartTime();
  const visibleSeconds = view ? view.getEndTime() - startTime : 0;
  const trackDuration = activeInstance?.player?.getDuration() || duration.value;
  const bpmValue = gridTrack.value.bpm;

  if (
      !view
      || !container
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
    container, // Ahora este contenedor es 100% preciso
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

function setStemLoopRange(startRawTime: number, endRawTime: number, timeline: StemTimeline): void {
  const beatLength = beatMs(timeline.bpm) / 1000;
  let start = Math.max(0, snapToGrid(Math.min(startRawTime, endRawTime) * 1000, timeline.bpm, timeline.anchorMs) / 1000);
  let end = Math.max(0, snapToGrid(Math.max(startRawTime, endRawTime) * 1000, timeline.bpm, timeline.anchorMs) / 1000);

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
  if (event.shiftKey) return;
  const target = event.target;
  if (event.button !== 0 || (target instanceof Element && target.closest(".stem-controls"))) return;

  const container = event.currentTarget;
  if (!(container instanceof HTMLElement)) return;
  const timeline = getStemTimeline();
  if (!timeline) return;

  event.preventDefault();

  // 1. Calculamos el tiempo crudo donde el usuario ha hecho clic
  const rawTime = rawTimeAtClientX(event.clientX, timeline);

  // 2. Lo forzamos INMEDIATAMENTE al compás más cercano (Imán)
  const snappedStart = Math.max(0, snapToGrid(rawTime * 1000, timeline.bpm, timeline.anchorMs) / 1000);
  // 3. Pintamos al instante una selección de 1 beat perfecto
  activePeaks.value?.player.seek(snappedStart);

  // 4. Guardamos ese punto exacto del grid como origen del arrastre
  selectionDrag = {
    mode: "draw",
    timeline,
    startRawTime: snappedStart,
    didCreateLoop: false,
  };

  startSelectionDrag();
}

function startMoveSelection(event: MouseEvent): void {
  if (event.button !== 0 || !activeLoopRange.value) return;

  const container = event.currentTarget;
  if (!(container instanceof HTMLElement)) return;
  const timeline = getStemTimeline();
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
  const timeline = getStemTimeline();
  if (!timeline) return;

  event.preventDefault();
  event.stopPropagation();
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
  const beatLength = beatMs(drag.timeline.bpm) / 1000;

  if (drag.mode === "draw") {
    if (Math.abs(currentRawTime - drag.startRawTime) < beatLength) return;
    setStemLoopRange(drag.startRawTime, currentRawTime, drag.timeline);
    drag.didCreateLoop = true;
    return;
  }

  if (drag.mode === "move") {
    const rect = drag.timeline.container.getBoundingClientRect();
    const deltaTime = ((event.clientX - drag.startClientX) / rect.width) * drag.timeline.visibleSeconds;
    const selectionDuration = drag.range.duration;
    const snappedStart = Math.max(0, snapToGrid((drag.range.start + deltaTime) * 1000, drag.timeline.bpm, drag.timeline.anchorMs) / 1000);
    const start = Math.max(0, Math.min(snappedStart, drag.timeline.duration - selectionDuration));
    const end = start + selectionDuration;
    workspaceStore.setActiveLoopRange({ ...drag.range, start, end });
    return;
  }

  if (drag.mode === "resize-left") {
    const snappedStart = Math.max(0, snapToGrid(currentRawTime * 1000, drag.timeline.bpm, drag.timeline.anchorMs) / 1000);
    const start = Math.max(0, Math.min(snappedStart, drag.range.end - beatLength));
    setStemLoopRange(start, drag.range.end, drag.timeline);
    return;
  }

  const snappedEnd = Math.max(0, snapToGrid(currentRawTime * 1000, drag.timeline.bpm, drag.timeline.anchorMs) / 1000);
  const minimumEnd = Math.min(drag.timeline.duration, drag.range.start + beatLength);
  const end = Math.max(minimumEnd, Math.min(snappedEnd, drag.timeline.duration));
  const rangeDuration = end - drag.range.start;
  workspaceStore.setActiveLoopRange({
    ...drag.range,
    end,
    duration: rangeDuration,
    beatCount: Math.max(1, Math.round(rangeDuration / beatLength)),
  });
}

function stopSelectionDrag(): void {
  window.removeEventListener("mousemove", handleSelectionDrag);
  window.removeEventListener("mouseup", stopSelectionDrag);
  const drag = selectionDrag;
  selectionDrag = null;

  if (drag?.mode !== "draw") return;

  if (drag.didCreateLoop) {
    const loopRange = activeLoopRange.value;
    if (loopRange) activePeaks.value?.player.seek(loopRange.start);
    return;
  }

  workspaceStore.setActiveLoopRange(null);
}

async function loadTrack(track: CollectionTrack, sourcePath = track.location_path): Promise<void> {
  destroyPeaks();
  const token = ++initToken;
  isLoading.value = true;
  loadError.value = null;
  duration.value = (track.duration_ms ?? 0) / 1000;
  await nextTick();
  const container = waveformElement.value;
  if (!container || token !== initToken) return;

  await Tone.start();
  singleTonePlayer = new Tone.GrainPlayer({
    url: convertFileSrc(sourcePath),
    grainSize: 0.05,
    overlap: 0.05,
    loop: false,
  }).toDestination();
  singleTonePlayer.detune = (Number(editTranspose.value) || 0) * 100;
  singleTonePlayer.sync().start(0);

  // 1. Carga en paralelo del audio + metadatos
  await Tone.loaded();
  if (track && (track.bpm || track.grid_anchor_ms)) {
    preciseMetadata.value = {
      bpm: track.bpm,
      grid_anchor_ms: track.grid_anchor_ms,
      existing_cues: track.existing_cues ?? [],
    };
  }

  if (token !== initToken || !singleTonePlayer) return;

  // 2. Guardamos metadatos (una sola vez)
  //preciseMetadata.value = metaResult.ok && metaResult.metadata ? metaResult.metadata : null;

  if (activeAudioEngine.value !== "stems") pauseStemPlaybackForAudioMutex();
  const audioBuffer = singleTonePlayer.buffer.get();
  if (!audioBuffer) throw new Error("Tone.js did not load the track audio buffer.");

  if (token !== initToken) return;

  // 3. Opciones de Peaks directas con webAudio + audioContext
  const peaksOptions: any = {
    zoomview: {
      container,
      waveformColor: "#ada17b",
      playedWaveformColor: "#eaa900",
      playheadColor: "#ffffff",
      showPlayheadTime: true,
      showAxisLabels: false,
      axisGridlineColor: "transparent",
      axisLabelColor: "transparent",
      enablePoints: true
    },
    player: createSingleCustomPlayer(),
    webAudio: {
      audioContext: new (window.AudioContext || (window as any).webkitAudioContext)(),
      audioBuffer,
    },
    zoomLevels: isPadEditMode.value ? PAD_ZOOM_LEVELS : TRACK_ZOOM_LEVELS,
    keyboard: false,
    pointMarkerColor: "#facf25",
    createPointMarker,
  };

  await new Promise<void>((resolve, reject) => {
    Peaks.init(peaksOptions, (error: Error | null, instance: unknown) => {
      if (token !== initToken) { try { (instance as any)?.destroy?.(); } catch { /* stale callback */ } resolve(); return; }
      if (error || !instance) { reject(error ?? new Error("Peaks did not create an instance.")); return; }
      peaks.value = instance;
      wirePlayerEvents(instance);
      wirePadGridAnchorDrag(instance);
      duration.value = (instance as any).player.getDuration() || duration.value;
      const view = (instance as any).views?.getView("zoomview");
      if (view && duration.value > 0) {
        view.setZoom({ seconds: duration.value });
        view.setStartTime(0);
      }
      paintBeatGrid(instance);
      startOpacitySync();
      resolve();
    });
  });
}

function handleLanesMousedown(event: MouseEvent): void {
  if (isPadEditMode.value) return;

  const target = event.target;
  if (!(target instanceof Element)) return;

  // 1. Si hace clic en Mute/Solo, o en la caja amarilla para moverla/redimensionarla,
  // dejamos que pase (no bloqueamos nada).
  if (target.closest(".stem-controls, .custom-selection-overlay, .selection-handle")) {
    return;
  }

  // 2. Si tiene Shift pulsado, dejamos que el evento llegue a Peaks.js para hacer Panning.
  if (event.shiftKey) {
    return;
  }

  // 3. Si NO hay Shift, bloqueamos el evento para que Peaks no haga panning...
  event.stopPropagation();

  // 4. ...y lanzamos nosotros manualmente la selección.
  startDrawSelection(event);
}

async function restartLoop(): Promise<void> {
  const instance = activePeaks.value;
  const loopRange = activeLoopRange.value;
  if (!instance || !loopRange) return;

  instance.player.seek(loopRange.start);
  if (isPlaying.value) return;

  activateStemPlayback();
  try { await instance.player.play(); } catch (error) { loadError.value = `Playback failed: ${String(error)}`; }
}

async function togglePlayback(): Promise<void> {
  if (isDeckPlaying.value) return;
  const instance = activePeaks.value;
  if (!instance) return;
  if (isPlaying.value) {
    instance.player.pause();
    return;
  }

  activateStemPlayback();
  // Novedad: Si hay un bucle activo, forzamos que el play empiece desde su inicio
  try { await instance.player.play(); } catch (error) { loadError.value = `Playback failed: ${String(error)}`; }
}

function seekBeats(beats: number): void {
  const instance = activePeaks.value;
  const currentBpm = gridTrack.value.bpm;
  if (!instance || currentBpm <= 0) return;

  const shiftSeconds = (60 / currentBpm) * beats;
  const newTime = Math.max(0, Math.min(duration.value, instance.player.getCurrentTime() + shiftSeconds));
  instance.player.seek(newTime);
}

function seekToCue(cue: PlayerCue): void {
  activePeaks.value?.player.seek(cue.position_ms / 1000);
}

function zoomIn(): void {
  const view = activePeaks.value?.views.getView("zoomview");
  if (view) {
    view.setZoom({ seconds: Math.max(MIN_VISIBLE_SECONDS, (view.getEndTime() - view.getStartTime()) * 0.8) });
  }
}

function zoomOut(): void {
  const view = activePeaks.value?.views.getView("zoomview");
  if (view) {
    view.setZoom({ seconds: Math.min(duration.value, (view.getEndTime() - view.getStartTime()) * 1.2) });
  }
}

usePlayerKeyboard({
  togglePlay: togglePlayback,
  onSeekBeats: seekBeats,
  zoomIn,
  zoomOut,
});

watch([editorSourcePath, () => props.track, () => props.stemTracks, isPadEditMode], async ([sourcePath, track, stemTracks, padMode], [oldSourcePath]) => {
  // 1. ESCUDO REACTIVO: Esperamos un 'tick' a que el componente padre termine de mandar todos los props
  const currentToken = ++watchToken;
  await new Promise(resolve => setTimeout(resolve, 50));
  if (currentToken !== watchToken) return;

  if (!sourcePath || (!track && !padMode)) {
    destroyPeaks(); preciseMetadata.value = null; duration.value = 0; isLoading.value = false; loadError.value = null;
    return;
  }

  if (sourcePath !== oldSourcePath) {
    destroyPeaks();
  }
  isLoading.value = true;
  loadError.value = null;
  preciseMetadata.value = null;

  const sourceTrack = track ?? ({ location_path: sourcePath, duration_ms: 0 } as CollectionTrack);

// Si estamos editando un pad, sacamos los metadatos del estado del pad
  if (padMode && editingPad.value?.pad.audio) {
    const padAudio = editingPad.value.pad.audio;
    preciseMetadata.value = {
      bpm: padAudio.originalBpm,
      grid_anchor_ms: padAudio.gridAnchorMs,
      existing_cues: [],
    };
  }
  // Si venimos de la colección y la pista ya tiene metadatos en Vue, los reutilizamos
  else if (track && (track.bpm || track.grid_anchor_ms)) {
    preciseMetadata.value = {
      bpm: track.bpm,
      grid_anchor_ms: track.grid_anchor_ms,
      existing_cues: track.existing_cues ?? [],
    };
  }
  // Solo si es un archivo totalmente desconocido en Vue, llamamos a Python
  else {
    try {
      const metaResult = await fetchTrackMetadata(sourcePath);
      if (currentToken !== watchToken) return;
      preciseMetadata.value = metaResult.ok && metaResult.metadata ? metaResult.metadata : null;
    } catch {
      preciseMetadata.value = null;
    }
  }

  // 2. Cargamos Stems
  if (!padMode && stemTracks.length === 4) {
    duration.value = (sourceTrack.duration_ms ?? 0) / 1000;
    loadError.value = null;
    await nextTick();
    try {
      // ¡PARALELIZAMOS!: Carga de los 4 stems + lectura de metadatos desde Rust
      const [metaResult, master] = await Promise.all([
        fetchTrackMetadata(sourcePath).catch(() => ({ ok: false, metadata: null })),
        initializeStemPeaks(stemTracks),
      ]);

      if (currentToken !== watchToken || !master) return;

      // Asignamos metadatos
      preciseMetadata.value = metaResult.ok && metaResult.metadata ? metaResult.metadata : null;

      duration.value = master.player.getDuration() || duration.value;
      const masterView = master.views?.getView("zoomview");
      if (masterView && duration.value > 0) {
        masterView.setZoom({ seconds: duration.value });
        masterView.setStartTime(0);
      }
      wirePlayerEvents(master);
      paintStemBeatGrids();
      refreshCustomSelectionOverlay();
    } catch (error) {
      if (currentToken === watchToken) loadError.value = `Stem waveform unavailable: ${String(error)}`;
    } finally {
      if (currentToken === watchToken) isLoading.value = false;
    }
    return;
  }

  // 3. Cargamos pista normal
  try {
    await loadTrack(sourceTrack, sourcePath);
    if (currentToken !== watchToken) return;
    if (padMode) setPadEditSelection();
  }
  catch (error) {
    if (currentToken === watchToken) loadError.value = `Waveform unavailable: ${String(error)}`;
  }
  finally {
    // SOLO apagamos el estado de carga si este proceso NO ha sido cancelado
    if (currentToken === watchToken) isLoading.value = false;
  }
}, { immediate: true });

watch([gridTrack, gridCues, isPadGridEditMode], () => {
  if (isMultiTrackMode.value) paintStemBeatGrids();
  else if (activePeaks.value) paintBeatGrid(activePeaks.value);
}, { deep: true });

watch(activeAudioEngine, (newEngine) => {
  if (newEngine !== "stems") pauseStemPlaybackForAudioMutex();
}, { flush: "sync" });

onBeforeUnmount(() => {
  waveformResizeObserver?.disconnect();
  waveformResizeObserver = null;
  stopSelectionDrag();
  destroyPeaks();
});
</script>

<template>
  <section class="studio-zone stem-editor" aria-labelledby="stem-editor-heading">
    <template v-if="track || isPadEditMode">
      <header class="track-header">
        <div class="track-identification"><h2 id="stem-editor-heading" class="track-title">{{ artist }} - {{ title }}</h2></div>
        <div class="track-header-actions">
          <div v-if="isPadEditMode" class="flex gap-2 shrink-0">
            <button type="button" class="save-pad-button" :disabled="!activeLoopRange" @click="savePadEdit">Save Changes</button>
            <button type="button" class="cancel-pad-button" @click="cancelPadEdit">Cancel</button>
          </div>
          <dl class="track-details" aria-label="Track details"><div><dt>BPM</dt><dd>{{ bpm }}</dd></div><div><dt>Key</dt><dd>{{ keyLabel }}</dd></div></dl>
        </div>
      </header>
      <div v-if="isMultiTrackMode" class="stem-lanes" aria-label="Stem tracks" @mousedown.capture="handleLanesMousedown">
        <article v-for="(stem, index) in stemLanes" :key="stem.path" class="stem-lane" :style="{ '--stem-color': stem.color }">
          <div class="stem-controls" :aria-label="`${stem.name} controls`">
            <span class="stem-name">{{ stem.name }}</span>
            <div class="stem-actions">
              <button type="button" class="stem-control" :class="{ 'is-active': mutedStems[index] }" :aria-pressed="mutedStems[index]" :aria-label="`${mutedStems[index] ? 'Unmute' : 'Mute'} ${stem.name}`" @click="toggleMute(index)">[M]</button>
              <button type="button" class="stem-control" :class="{ 'is-active': soloedStem === index }" :aria-pressed="soloedStem === index" :aria-label="`${soloedStem === index ? 'Disable solo for' : 'Solo'} ${stem.name}`" @click="toggleSolo(index)">[S]</button>
            </div>
          </div>
          <div
              ref="stemWaveform"
              class="stem-waveform"
              :class="{ 'is-dimmed': mutedStems[index] || (soloedStem !== null && soloedStem !== index) }"
              :aria-label="`${stem.name} waveform`"
              @wheel.prevent="handleZoomWheel"
          />
        </article>
        <div class="overlay-wrapper">
          <div class="custom-selection-overlay" :style="customSelectionOverlayStyle" @mousedown.stop="startMoveSelection" @wheel.prevent="handleZoomWheel">
            <div class="selection-handle handle-left" @mousedown.stop="startResizeLeft" />
            <div class="selection-handle handle-right" role="separator" aria-label="Trim end" @mousedown.stop="startResizeRight" />
          </div>
        </div>
      </div>

      <div v-else class="waveform-shell" :class="{ 'is-loading': isLoading }" @mousedown.capture="handleLanesMousedown">
        <div ref="waveform" @wheel.prevent="handleZoomWheel" class="waveform" aria-label="Audio waveform. Click or drag to seek." />
        <template v-if="isPadEditMode && trimMaskGeometry">
          <div class="trim-mask mask-left" :style="{ width: `${trimMaskGeometry.leftPercent}%` }" />
          <div class="trim-mask mask-right" :style="{ left: `${trimMaskGeometry.leftPercent + trimMaskGeometry.widthPercent}%`, width: `${trimMaskGeometry.rightPercent}%` }" />
        </template>
        <div class="custom-selection-overlay" :class="{ 'is-trim-selection': isPadEditMode, hidden: isPadEditMode && isPadGridEditMode, 'pointer-events-none': isPadEditMode }" :style="customSelectionOverlayStyle" @mousedown.stop="startMoveSelection" @wheel.prevent="handleZoomWheel">
          <div class="selection-handle handle-left" @mousedown.stop="startResizeLeft" />
          <div class="selection-handle handle-right" role="separator" aria-label="Trim end" @mousedown.stop="startResizeRight" />
        </div>
        <p v-if="isLoading && props.stemTracks.length !== 4" class="waveform-status">Loading waveform…</p>
      </div>

      <p v-if="isMultiTrackMode && (isStemLoading || isLoading)" class="stem-loading">Loading stem data...</p>
      <p v-if="loadError" class="waveform-error">{{ loadError }}</p>
      <div class="transport">
        <button type="button" class="play-button" :disabled="!activePeaks" :aria-label="playLabel" @click="togglePlayback">{{ playLabel }}</button>
        <button v-if="activeLoopRange && isPlaying" type="button" class="restart-button" @click="restartLoop">Restart</button>
        <output class="time-display" aria-label="Elapsed time and total duration">{{ timeLabel }}</output>
        <template v-if="isPadEditMode">
          <button
            type="button"
            class="grid-edit-toggle"
            :aria-pressed="isPadGridEditMode"
            @click="isPadGridEditMode = !isPadGridEditMode"
          >
            {{ isPadGridEditMode ? "Exit Grid Edit" : "Edit Grid" }}
          </button>
          <PlayerGridControls
            v-if="isPadGridEditMode"
            :has-track="true"
            :is-flex-grid="false"
            :is-grid-edit-mode="true"
            :show-modifiers="false"
            :dynamic-label="'1 ms'"
            :dynamic-step-ms="1"
            @nudge="nudgePadGrid"
            @set-to-playhead="setPadGridToPlayhead"
            @multiply-bpm="multiplyPadBpm"
            @divide-bpm="dividePadBpm"
          />
          <label class="transpose-control">
            <span>Transpose</span>
            <input v-model.number="editTranspose" type="number" min="-12" max="12" step="1" aria-label="Transpose in semitones">
            <small>st</small>
          </label>
          <label v-if="isPadGridEditMode" class="bpm-control">
            <span>BPM</span>
            <input v-model.number="editBpm" type="number" min="20" max="300" step="0.01" aria-label="Pad BPM">
          </label>
        </template>
        <div v-else-if="gridCues.length" class="cue-pads" aria-label="Track Hotcues">
          <button v-for="cue in gridCues" :key="cue.id" type="button" class="cue-pad" :aria-label="`Seek to Hotcue ${cue.id + 1}`" @click="seekToCue(cue)">{{ cue.id + 1 }}</button>
        </div>
        <p v-if="loopLabel && !isPadEditMode" class="loop-feedback">{{ loopLabel }}</p>
      </div>
    </template>
    <p v-else id="stem-editor-heading" class="empty-state">Load a track from the library</p>
  </section>
</template>

<style scoped>
.stem-editor { --stem-controls-width: 70px; display: flex; min-width: 0; min-height: 0; padding: 1.5rem; flex-direction: column; overflow-x: hidden; overflow-y: auto; background: #232326; }
.stem-editor { position: relative; }
.track-header { display: flex; min-width: 0; margin-top: 0; align-items: center; justify-content: space-between; gap: 1rem; }.track-identification { min-width: 0; }.track-title { margin: 0; overflow: hidden; color: #f2f2f2; font-size: 1.125rem; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }.empty-state { margin-top: .75rem; color: #8a8a8e; font-size: .875rem; }
.track-header-actions { display: flex; flex: 0 0 auto; flex-direction: column; align-items: flex-end; gap: .5rem; }
.track-details { display: flex; margin: 0; gap: 1rem; font-variant-numeric: tabular-nums; }.track-details div { display: grid; gap: .15rem; }.track-details dt { color: #8a8a8e; font-size: .625rem; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; }.track-details dd { margin: 0; color: #f7d15f; font-family: ui-monospace, "Cascadia Code", monospace; font-size: .8125rem; }
.waveform-shell { position: relative; min-height: 240px; margin-top: .375rem; flex: 1; overflow: hidden; border: 1px solid #3a3a3e; background: #17171a; }.waveform-shell.is-loading { opacity: .6; }.waveform { width: 100%; height: 100%; min-height: 150px; }.waveform-status { position: absolute; inset: 0; display: grid; margin: 0; place-items: center; color: #8a8a8e; font-size: .75rem; pointer-events: none; }
.stem-lanes { position: relative; display: grid; min-width: 0; min-height: 0; margin-top: .375rem; flex: 1 1 0; grid-template-rows: repeat(4, minmax(0, 1fr)); gap: 0; overflow: hidden; border: 1px solid #3a3a3e; background: #17171a; }.stem-lane { display: grid; min-height: 0; grid-template-columns: var(--stem-controls-width) minmax(0, 1fr); overflow: hidden; background: #17171a; }.stem-lane + .stem-lane { border-top: 1px solid #3a3a3e; }.stem-controls { display: grid; width: var(--stem-controls-width); min-width: var(--stem-controls-width); align-content: center; gap: .125rem; padding: .125rem .375rem; border-right: 1px solid #3a3a3e; background: #202024; }.stem-actions { display: flex; min-width: 0; gap: .1875rem; }.stem-control { min-width: 0; flex: 1; padding: .125rem 0; border: 1px solid #5a5a5e; border-radius: .125rem; background: #2a2a2e; color: #f2f2f2; cursor: pointer; font-family: ui-monospace, "Cascadia Code", monospace; font-size: .625rem; font-weight: 800; }.stem-control:hover { border-color: #f7d15f; color: #f7d15f; }.stem-control.is-active { border-color: var(--stem-color); background: var(--stem-color); color: #17171a; }.stem-control:focus-visible { outline: 2px solid #fff; outline-offset: -2px; }.stem-waveform { min-width: 0; height: 100%; overflow: hidden; background: linear-gradient(90deg, rgb(237 180 11 / 5%), transparent 35%); }.stem-waveform.is-dimmed { pointer-events: none; opacity: .3; transition: opacity .2s ease-in-out; }.stem-name { color: var(--stem-color); font-size: .5625rem; font-weight: 700; letter-spacing: .08em; text-align: center; text-transform: uppercase; }.stem-loading { margin: .5rem 0 0; color: #8a8a8e; font-size: .75rem; }
.trim-mask { position: absolute; z-index: 5; top: 0; bottom: 0; background: rgba(23, 23, 26, 0.6); backdrop-filter: grayscale(100%); pointer-events: none; }.custom-selection-overlay { position: absolute; z-index: 10; pointer-events: auto; background-color: rgba(247, 209, 95, 0.25); box-shadow: -0.5px 0 0 0.5px rgba(247, 209, 95, 0.8), 0.5px 0 0 0.5px rgba(247, 209, 95, 0.8); cursor: move; }.custom-selection-overlay.is-trim-selection { background: transparent; box-shadow: none; pointer-events: none !important; cursor: default !important; }.custom-selection-overlay.is-trim-selection .selection-handle { pointer-events: auto !important; cursor: ew-resize; }.selection-handle { position: absolute; top: 0; bottom: 0; width: 10px; cursor: ew-resize; background: transparent; }.handle-left { left: -5px; }.handle-right { right: -5px; }
.transport { display: flex; flex: 0 0 auto; flex-wrap: wrap; margin-top: .75rem; align-items: center; gap: .75rem; }.play-button, .restart-button { min-width: 4.5rem; padding: .45rem .75rem; border: 1px solid #edb40b; border-radius: .1875rem; background: #edb40b; color: #17171a; cursor: pointer; font-size: .75rem; font-weight: 800; text-transform: uppercase; }.play-button:hover:not(:disabled), .restart-button:hover { background: #f7d15f; }.play-button:focus-visible, .restart-button:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }.play-button:disabled { cursor: not-allowed; opacity: .5; }.time-display { color: #f2f2f2; font-family: ui-monospace, "Cascadia Code", monospace; font-size: .75rem; font-variant-numeric: tabular-nums; }.cue-pads { display: flex; gap: .25rem; }.cue-pad { min-width: 1.75rem; padding: .35rem .5rem; border: 1px solid #aa8208; border-radius: .1875rem; background: #2a2a2e; color: #f7d15f; cursor: pointer; font-family: ui-monospace, "Cascadia Code", monospace; font-size: .75rem; font-weight: 800; }.cue-pad:hover { border-color: #f7d15f; background: #3a3a3e; }.cue-pad:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }.waveform-error { margin: .5rem 0 0; color: #f87171; font-size: .75rem; }
.transpose-control { display: inline-flex; align-items: center; gap: .35rem; color: #f7d15f; font-family: ui-monospace, "Cascadia Code", monospace; font-size: .6875rem; font-weight: 700; text-transform: uppercase; }.transpose-control input { width: 3.5rem; padding: .35rem; border: 1px solid #5a5a5e; border-radius: .1875rem; background: #17171a; color: #f2f2f2; font: inherit; text-align: right; }.transpose-control input:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }.transpose-control small { color: #8a8a8e; font-size: inherit; }.save-pad-button, .cancel-pad-button { padding: .45rem .75rem; border: 1px solid #5a5a5e; border-radius: .1875rem; background: #2a2a2e; color: #f2f2f2; cursor: pointer; font-size: .75rem; font-weight: 800; text-transform: uppercase; }.save-pad-button { border-color: #f87171; background: #f87171; color: #17171a; }.save-pad-button:disabled { cursor: not-allowed; opacity: .5; }.save-pad-button:hover:not(:disabled), .cancel-pad-button:hover { border-color: #f7d15f; }.save-pad-button:focus-visible, .cancel-pad-button:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }
.bpm-control { display: inline-flex; align-items: center; gap: .35rem; color: #f7d15f; font-family: ui-monospace, "Cascadia Code", monospace; font-size: .6875rem; font-weight: 700; text-transform: uppercase; }.bpm-control input { width: 4.5rem; padding: .35rem; border: 1px solid #5a5a5e; border-radius: .1875rem; background: #17171a; color: #f2f2f2; font: inherit; text-align: right; }.bpm-control input:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }
.grid-edit-toggle { height: 34px; padding: 0 .75rem; border: 1px solid #5a5a5e; border-radius: .375rem; background: #2a2a2e; color: #f2f2f2; cursor: pointer; font-size: .75rem; font-weight: 700; text-transform: uppercase; }.grid-edit-toggle:hover, .grid-edit-toggle[aria-pressed="true"] { border-color: #f7d15f; background: rgb(247 209 95 / 18%); color: #f7d15f; }.grid-edit-toggle:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }
.custom-selection-overlay.is-trim-selection .selection-handle {
  width: 14px;
  background: transparent;
}

/* 2. Izquierda: Lo pegamos al borde al 0 y pintamos una línea sólida usando el borde izquierdo */
.custom-selection-overlay.is-trim-selection .handle-left {
  left: 0;
  border-left: 3px solid rgb(247 209 95 / 90%);
}

/* 3. Derecha: Lo pegamos al borde al 0 y pintamos una línea sólida usando el borde derecho */
.custom-selection-overlay.is-trim-selection .handle-right {
  right: 0;
  border-right: 3px solid rgb(247 209 95 / 90%);
}
.selection-handle { pointer-events: auto; }
:deep(.peaks-view-container) { height: 100% !important; overflow: hidden !important; } .stem-waveform :deep(canvas) { height: 100% !important; max-height: 100% !important; overflow: hidden !important; } @media (max-width: 520px) { .track-header { align-items: flex-start; flex-direction: column; } .track-header-actions { align-items: flex-start; } }
.loop-feedback {
  margin: 0 0 0 auto; /* El 'auto' a la izquierda lo empuja al extremo derecho de la barra */
  padding: .35rem .5rem;
  border: 1px solid rgb(247 209 95 / 55%);
  border-radius: .1875rem; /* Le damos las esquinas redondeadas como los cues */
  background: rgb(23 23 26 / 88%);
  color: #f7d15f;
  font-family: ui-monospace, "Cascadia Code", monospace;
  font-size: .6875rem;
  font-variant-numeric: tabular-nums;
}.stem-waveform {
  position: relative;
  min-width: 0;
  height: 100%;
}
.stem-lanes > .overlay-wrapper {
  position: absolute;
  top: 0;
  bottom: 0;
  left: var(--stem-controls-width);
  right: 0;
  pointer-events: none; /* Vital para no bloquear los clics de la onda */
}

/* Escala mágicamente la onda un 25% hacia el centro para dejar espacio (headroom),
   sin afectar a las líneas del Grid ni a los Cues que están en otra capa */
:deep(.konvajs-content canvas:first-child) {
  transform: scaleY(0.75) !important;
  transform-origin: center !important;
  opacity: 0.95 !important;
}

</style>
