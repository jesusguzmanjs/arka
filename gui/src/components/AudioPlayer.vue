<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from "vue";
import { convertFileSrc } from "@tauri-apps/api/core";
import Peaks from "peaks.js";
import { useConfigState } from "../composables/useConfigState";
import { useLibraryState } from "../composables/useLibraryState";
import { fetchTrackMetadata, usePlayerState } from "../composables/useTrackMetadata";
import { useRunState } from "../composables/useRunState";
import { useCueGridSidecar } from "../composables/useCueGridSidecar";
import { beatMs, snapToGrid, type GridTrackData } from "../composables/useGridMath";
import { usePeaksMarkers, type PlayerCue } from "../composables/usePeaksMarkers";
import { useWaveformSync } from "../composables/useWaveformSync";
import { usePlayerKeyboard } from "../composables/usePlayerKeyboard";
import type { SuperJSON } from "../types/trackMetadata";
import CueContextMenu from "./CueContextMenu.vue";
import PlayerHeader from "./PlayerHeader.vue";
import PlayerTransport from "./PlayerTransport.vue";
import PlayerWaveform from "./PlayerWaveform.vue";

interface TrackData extends GridTrackData { track_path: string; cues: PlayerCue[]; }
interface LibraryTrackRecord { artist?: unknown; title?: unknown; location_path?: unknown; }
interface WaveformShell { zoomviewElement: HTMLDivElement | null; overviewElement: HTMLDivElement | null; zoomGradientElement: HTMLDivElement | null; gradientMaskElement: HTMLDivElement | null; }

const props = defineProps<{ trackPath?: string | null; disabled?: boolean }>();
const { selectedTrackPath, selectedPlaylist } = useConfigState();
const { tracks: libraryTracks } = useLibraryState();
const { status, logs } = useRunState();
const { updateTrackCues } = useCueGridSidecar();
const { previewCache, isLoadingTrack, setLoadingTrack } = usePlayerState();
const { createPointMarker, paintAllMarkers, getGridLines } = usePeaksMarkers();

const EMPTY_TRACK: TrackData = { track_path: "", bpm: 0, grid_anchor_ms: 0, duration_ms: 0, cues: [] };
const ZOOM_LEVELS = [128, 256, 512, 1024, 2048, 4096];
const HEX_PRIMARY = "#eaa900";
const HEX_SECONDARY = "#d27b00";
const HEX_ACCENT = "#facf25";
const HEX_GRAY = "#52525b";

const trackData = ref<TrackData>({ ...EMPTY_TRACK });
const currentPreview = shallowRef<SuperJSON | null>(null);
const cueState = ref<PlayerCue[]>([]);
const peaks = shallowRef<any>(null);
const audioRef = ref<HTMLAudioElement | null>(null);
const waveformRef = ref<WaveformShell | null>(null);
const currentTime = ref(0);
const isPlaying = ref(false);
const isSaving = ref(false);
const loadError = ref<string | null>(null);
const hasUnsavedChanges = ref(false);
const activeCue = ref<PlayerCue | null>(null);
const activePad = ref<number | null>(null);
const contextMenu = ref({ visible: false, x: 0, y: 0, cue: null as PlayerCue | null });
let loadToken = 0;
let peaksInitToken = 0;

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
function createPeaksWaveformData(data: number[]) { return { json: { version: 2, channels: 1, sample_rate: 11025, samples_per_pixel: 128, bits: 8, length: Math.floor(data.length / 2), data } }; }
function toCueId(value: unknown): number | null { if (value === null || value === undefined || value === "") return null; const numberValue = Number(value); return Number.isFinite(numberValue) && Number.isInteger(numberValue) ? numberValue : null; }
function normalizeTrackPath(path: string): string { return path.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase(); }

function mergeCues(...sources: ReadonlyArray<PlayerCue>[]): PlayerCue[] {
  const byId = new Map<number, PlayerCue>();
  for (const source of sources) for (const cue of source) {
    if (!Number.isInteger(cue.id) || !finiteNumber(cue.position_ms)) continue;
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
  const target = normalizeTrackPath(trackData.value.track_path);
  return target ? (libraryTracks.value as unknown as LibraryTrackRecord[]).find((track) => typeof track.location_path === "string" && normalizeTrackPath(track.location_path) === target) : undefined;
});
const trackHeaderLabel = computed(() => {
  const track = currentLibraryTrack.value;
  return track && typeof track.artist === "string" && typeof track.title === "string" ? `ARTIST: ${track.artist} | TITLE: ${track.title}` : "Track metadata unavailable";
});
const cueCount = computed(() => cueState.value.filter((cue) => cue.id !== undefined && cue.id !== null).length);
const validCueCount = computed(() => cueState.value.filter((cue) => cue.id !== undefined && cue.id !== null && cue.is_valid).length);
const bpmLabel = computed(() => trackData.value.bpm > 0 ? trackData.value.bpm.toFixed(1) : "");
const remainingLabel = computed(() => {
  if (!trackData.value.track_path || !trackData.value.duration_ms) return "";
  const remaining = Math.max(0, trackData.value.duration_ms / 1000 - currentTime.value);
  return `-${String(Math.floor(remaining / 60)).padStart(2, "0")}:${String(Math.floor(remaining % 60)).padStart(2, "0")}`;
});
const padSlots = computed(() => Array.from({ length: 8 }, (_, index) => cueState.value.find((cue) => cue.id === index) ?? null));

function paintMarkers(): void { paintAllMarkers(peaks.value, trackData.value, cueState.value); }
function openContextMenu(event: MouseEvent, cue: PlayerCue): void { contextMenu.value = { visible: true, x: event.clientX, y: event.clientY, cue }; }
function closeContextMenu(): void { contextMenu.value.visible = false; contextMenu.value.cue = null; }
function deleteSelectedCue(): void {
  if (!contextMenu.value.cue) return;
  cueState.value = cueState.value.filter((cue) => cue.id !== contextMenu.value.cue?.id);
  hasUnsavedChanges.value = true;
  try { paintMarkers(); } catch (error) { console.warn("Fallo al repintar tras borrar el cue", error); }
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
    zoomview: { container: zoomview, waveformColor: "#ffffff", playedWaveformColor: "#a1a1aa", playheadColor: HEX_ACCENT, showPlayheadTime: false, showAxisLabels: false, axisGridlineColor: "transparent", axisLabelColor: "transparent", enablePoints: true },
    overview: { container: overview, highlightOffset: 0, waveformColor: HEX_GRAY, playedWaveformColor: HEX_SECONDARY, highlightColor: HEX_PRIMARY, highlightStrokeColor: HEX_ACCENT, highlightOpacity: 0.15, playheadColor: HEX_ACCENT, showPlayheadTime: false, showAxisLabels: false, axisGridlineColor: "transparent", axisLabelColor: "transparent", enablePoints: true },
    mediaElement: audio, waveformData: createPeaksWaveformData(preview.waveform_peaks), webAudio: false as never, zoomLevels: ZOOM_LEVELS, keyboard: false, pointMarkerColor: HEX_ACCENT, createPointMarker,
  }, (error: Error | null, instance: unknown) => {
    if (initToken !== peaksInitToken) { try { (instance as any)?.destroy?.(); } catch { /* stale Peaks callback */ } return; }
    if (error || !instance) { loadError.value = `Peaks init failed: ${error?.message ?? "no instance"}`; return; }
    peaks.value = instance;
    try {
      const view = peaks.value.views.getView("zoomview");
      const waveformCanvas = zoomview.querySelector("canvas");
      if (waveformCanvas) { waveformCanvas.style.backgroundColor = "#18181b"; waveformCanvas.style.mixBlendMode = "darken"; }
      if (view) {
        if (!trackData.value.duration_ms) trackData.value.duration_ms = peaks.value.player.getDuration() * 1000;
        view.setZoom({ seconds: trackData.value.duration_ms / 2000 });
      }
      paintMarkers();
      wirePlayerEvents();
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
  instance.on("player.playing", () => { isPlaying.value = true; });
  instance.on("player.pause", () => { isPlaying.value = false; });
  instance.on("player.ended", () => { isPlaying.value = false; });
  instance.on("player.timeupdate", (time: number) => { currentTime.value = time; });
}
function wireDragEvents(): void {
  const instance = peaks.value;
  if (!instance) return;
  const snap = (time: number) => Math.max(0, Math.min(snapToGrid(time * 1000, trackData.value.bpm, trackData.value.grid_anchor_ms), trackData.value.duration_ms));
  instance.on("points.dragmove", (event: { point: { id: string; time: number } }) => {
    if (!event.point.id.startsWith("cue-")) return;
    const snapped = snap(event.point.time) / 1000;
    if (event.point.time !== snapped) instance.points.getPoint(event.point.id)?.update({ time: snapped });
  });
  instance.on("points.dragend", (event: { point: { id: string; time: number } }) => {
    if (!event.point.id.startsWith("cue-")) return;
    const cue = cueState.value.find((candidate) => candidate.id === Number(event.point.id.slice(4)));
    if (!cue || !cue.is_valid) return;
    const snapped = snap(event.point.time);
    instance.points.getPoint(event.point.id)?.update({ time: snapped / 1000 });
    if (cue.position_ms !== snapped) { cue.position_ms = snapped; hasUnsavedChanges.value = true; }
  });
}

async function togglePlay(): Promise<void> { const instance = peaks.value; if (!instance) return; if (isPlaying.value) { instance.player.pause(); return; } try { await instance.player.play(); } catch (error) { loadError.value = `Playback failed: ${String(error)}`; } }
function stop(): void { const instance = peaks.value; if (!instance) return; instance.player.pause(); instance.player.seek(0); isPlaying.value = false; }
function jumpToCue(padIndex: number): void { const cue = padSlots.value[padIndex - 1]; if (peaks.value && cue) peaks.value.player.seek(cue.position_ms / 1000); }
async function startCuePreview(padIndex: number): Promise<void> {
  const cue = padSlots.value[padIndex - 1];
  const instance = peaks.value;
  if (!instance || !cue || activeCue.value) return;
  activePad.value = padIndex;
  activeCue.value = cue;
  instance.player.seek(cue.position_ms / 1000);
  try { await instance.player.play(); if (activeCue.value?.id !== cue.id) { instance.player.pause(); instance.player.seek(cue.position_ms / 1000); } }
  catch (error: any) { activeCue.value = null; if (error.name !== "AbortError") loadError.value = `Cue preview failed: ${String(error)}`; }
}
function endCuePreview(padIndex: number): void {
  if (activePad.value !== padIndex) return;
  const cue = padSlots.value[padIndex - 1];
  if (peaks.value && cue && activeCue.value?.id === cue.id) { peaks.value.player.pause(); peaks.value.player.seek(cue.position_ms / 1000); }
  activeCue.value = null; activePad.value = null; isPlaying.value = false;
}
usePlayerKeyboard({ togglePlay, stop, hasPad: (padIndex) => Boolean(padSlots.value[padIndex - 1]), previewStart: startCuePreview, previewEnd: endCuePreview });

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

async function saveChanges(): Promise<void> {
  if (!trackData.value.track_path) return;
  isSaving.value = true; loadError.value = null;
  try {
    const result = await updateTrackCues(trackData.value.track_path, cueState.value.map((cue) => ({ hotcue: cue.id, start_ms: cue.position_ms })));
    if (!result.ok) throw new Error(result.error);
    trackData.value.cues = JSON.parse(JSON.stringify(cueState.value));
    hasUnsavedChanges.value = false;
  } catch (error) { loadError.value = `Failed to save changes: ${error instanceof Error ? error.message : String(error)}`; }
  finally { isSaving.value = false; }
}
function discardChanges(): void { setCueState(trackData.value.cues); hasUnsavedChanges.value = false; try { paintMarkers(); } catch (error) { console.error("[AudioPlayer] marker repaint failed:", error); } }

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
    previewCache.value.set(trimmed, metadata);
    currentPreview.value = metadata;
    const metadataCues = metadataCuesForTrack(metadata);
    const metadataDuration = Number((metadata as unknown as { duration_ms?: unknown }).duration_ms) || 0;
    trackData.value = { track_path: trimmed, bpm: metadata.bpm || 0, grid_anchor_ms: metadata.grid_anchor_ms || 0, duration_ms: metadataDuration, cues: metadataCues };
    setCueState(metadataCues); hasUnsavedChanges.value = false;
    await nextTick(); if (token !== loadToken) return;
    await initPeaks();
  } catch (error) {
    if (token !== loadToken) return;
    loadError.value = error instanceof Error ? error.message : String(error);
    trackData.value = { ...EMPTY_TRACK }; currentPreview.value = null; cueState.value = []; hasUnsavedChanges.value = false;
    await destroyPeaks();
  } finally { if (token === loadToken) setLoadingTrack(false); }
}

defineExpose({ loadTrack });
onMounted(() => { const initialPath = props.trackPath ?? selectedTrackPath.value; if (initialPath) void loadTrack(initialPath); });
onBeforeUnmount(() => { loadToken += 1; void destroyPeaks(); });

watch(status, async (nextStatus, previousStatus) => {
  if (nextStatus !== "success" || previousStatus !== "running" || !trackData.value.track_path) return;
  const currentPath = trackData.value.track_path;
  if (selectedTrackPath.value === currentPath && !selectedPlaylist.value) {
    const newCues = logs.value.flatMap((log) => log.msg?.type === "cue_written" ? [{ id: Number(log.msg.hotcue), position_ms: Number(log.msg.start_ms), is_valid: true }] : []);
    if (newCues.length) { const merged = mergeCues(cueState.value, newCues); trackData.value.cues = merged; setCueState(merged); hasUnsavedChanges.value = false; try { paintMarkers(); } catch { /* no marker layer during teardown */ } }
    return;
  }
  try {
    const metadataResult = await fetchTrackMetadata(currentPath);
    if (!metadataResult.ok || !metadataResult.metadata) return;
    const diskCues = metadataCuesForTrack(metadataResult.metadata);
    if (JSON.stringify(diskCues.map((cue) => cue.position_ms)) !== JSON.stringify(cueState.value.map((cue) => cue.position_ms))) {
      trackData.value.cues = diskCues; setCueState(diskCues); hasUnsavedChanges.value = false; try { paintMarkers(); } catch { /* no marker layer during teardown */ }
    }
  } catch (error) { console.warn("[AudioPlayer] Error comprobando NML:", error); }
});
watch(() => props.trackPath ?? selectedTrackPath.value, (next, previous) => {
  if (next === previous) return;
  if (next) void loadTrack(next);
  else { trackData.value = { ...EMPTY_TRACK }; cueState.value = []; hasUnsavedChanges.value = false; loadError.value = null; setLoadingTrack(false); void destroyPeaks(); }
});
</script>

<template>
  <section class="bg-zinc-900 border border-zinc-800 rounded-md p-2 flex flex-col gap-2 select-none overflow-y-auto">
    <PlayerHeader :has-track="Boolean(trackData.track_path)" :track-header-label="trackHeaderLabel" :bpm-label="bpmLabel" :remaining-label="remainingLabel" :cue-count="cueCount" :valid-cue-count="validCueCount" />
    <div v-if="loadError" class="text-xs font-mono text-red-500 bg-zinc-950 border border-zinc-800 rounded px-2 py-1">{{ loadError }}</div>
    <audio ref="audioRef" class="hidden" preload="none" aria-hidden="true" />
    <PlayerWaveform ref="waveformRef" :has-track="Boolean(trackData.track_path)" :is-loading="isLoadingTrack" :is-saving="isSaving" :track-css-gradient="trackCssGradient" @resize="handleWaveformResize" @wheel="handleZoomWheel" @zoom-in="zoomIn" @zoom-out="zoomOut" />
    <PlayerTransport :has-track="Boolean(trackData.track_path)" :is-playing="isPlaying" :pad-slots="padSlots" :active-pad="activePad" @play="togglePlay" @stop="stop" @jump="jumpToCue" @preview-start="startCuePreview" @preview-end="endCuePreview" @context-menu="openContextMenu" />
    <div class="flex items-center gap-2 px-1">
      <span class="text-xs font-mono" :class="hasUnsavedChanges ? 'text-warning' : 'text-zinc-500'">{{ hasUnsavedChanges ? "● Unsaved changes" : "Synced" }}</span>
      <div class="flex-1" />
      <button v-if="hasUnsavedChanges" type="button" class="text-xs font-semibold uppercase px-3 py-1 rounded bg-zinc-800 text-zinc-400 border border-zinc-700 hover:bg-zinc-700 cursor-pointer" @click="discardChanges">Discard</button>
      <button v-if="hasUnsavedChanges" type="button" class="text-xs font-semibold uppercase px-3 py-1 rounded bg-primary text-zinc-950 border border-primary hover:bg-accent cursor-pointer" @click="saveChanges">Save Changes</button>
    </div>
    <CueContextMenu :x="contextMenu.x" :y="contextMenu.y" :visible="contextMenu.visible" @close="closeContextMenu" @delete="deleteSelectedCue" />
  </section>
</template>
