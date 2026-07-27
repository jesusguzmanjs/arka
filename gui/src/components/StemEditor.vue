<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, shallowRef, useTemplateRef, watch } from "vue";
import { storeToRefs } from "pinia";
import { convertFileSrc } from "@tauri-apps/api/core";
import Peaks from "peaks.js";
import { fetchTrackMetadata } from "../composables/useTrackMetadata";
import { usePeaksMarkers, type PlayerCue } from "../composables/usePeaksMarkers";
import { usePlayerKeyboard } from "../composables/usePlayerKeyboard";
import { useStudioLoopSelection } from "../composables/useStudioLoopSelection";
import type { GridTrackData } from "../composables/useGridMath";
import { useWorkspaceStore } from "../stores/useWorkspaceStore";
import type { CollectionTrack } from "../types/library";

const props = defineProps<{ track: CollectionTrack | null }>();
const workspaceStore = useWorkspaceStore();
const { activeLoopRange } = storeToRefs(workspaceStore);
const waveformElement = useTemplateRef<HTMLDivElement>("waveform");
const audioElement = useTemplateRef<HTMLAudioElement>("audio");
const peaks = shallowRef<any>(null);
const audioContext = shallowRef<AudioContext | null>(null);
const isLoading = shallowRef(false);
const loadError = shallowRef<string | null>(null);
const isPlaying = shallowRef(false);
const currentTime = shallowRef(0);
const duration = shallowRef(0);
let initToken = 0;
let removeLoopSelection: (() => void) | null = null;
const ZOOM_LEVELS = [64, 256, 512, 1024, 2048, 4096];
const { createPointMarker, paintAllMarkers } = usePeaksMarkers();

const title = computed(() => props.track?.title || "Untitled track");
const artist = computed(() => props.track?.artist || "Unknown artist");
const bpm = computed(() => props.track?.bpm === null || props.track?.bpm === undefined ? "—" : props.track.bpm.toFixed(2));
const key = computed(() => props.track?.key?.trim() || "—");
const timeLabel = computed(() => `${formatTime(currentTime.value)} / ${formatTime(duration.value)}`);
const playLabel = computed(() => isPlaying.value ? "Pause" : "Play");
const loopLabel = computed(() => {
  const range = activeLoopRange.value;
  return range ? `Selected: ${range.beatCount} ${range.beatCount === 1 ? "Beat" : "Beats"} | ${formatDuration(range.duration)}` : null;
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

function destroyPeaks(): void {
  initToken += 1;
  removeLoopSelection?.();
  removeLoopSelection = null;
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
  instance.on("player.playing", () => { isPlaying.value = true; });
  instance.on("player.pause", () => { isPlaying.value = false; });
  instance.on("player.ended", () => { isPlaying.value = false; });
  instance.on("player.timeupdate", (time: number) => { currentTime.value = time; });
}

function paintBeatGrid(instance: any): void {
  paintAllMarkers(instance, gridTrack.value, gridCues.value);
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
      // Intentional ZoomView-only layout: no overview container is configured.
      zoomview: { container, waveformColor: "#edb40b", playedWaveformColor: "#f7d15f", playheadColor: "#ffffff", showPlayheadTime: true, showAxisLabels: false, axisGridlineColor: "transparent", axisLabelColor: "transparent", enablePoints: true },
      mediaElement: audio,
      ...(waveformData ? { waveformData } : { webAudio: { audioContext: audioContext.value! } }),
      zoomLevels: ZOOM_LEVELS,
      keyboard: false,
      pointMarkerColor: "#facf25",
      createPointMarker,
    } as any, (error: Error | null, instance: unknown) => {
      if (token !== initToken) { try { (instance as any)?.destroy?.(); } catch { /* stale callback */ } resolve(); return; }
      if (error || !instance) { reject(error ?? new Error("Peaks did not create an instance.")); return; }
      peaks.value = instance;
      wirePlayerEvents(instance);
      duration.value = (instance as any).player.getDuration() || duration.value;
      paintBeatGrid(instance);
      removeLoopSelection = useStudioLoopSelection({
        instance,
        waveformElement: container,
        grid: gridTrack.value,
        onChange: workspaceStore.setActiveLoopRange,
      });
      resolve();
    });
  });
}

async function togglePlayback(): Promise<void> {
  const instance = peaks.value;
  if (!instance) return;
  if (isPlaying.value) { instance.player.pause(); return; }
  try { await instance.player.play(); } catch (error) { loadError.value = `Playback failed: ${String(error)}`; }
}

function stop(): void {
  const instance = peaks.value;
  if (!instance) return;
  instance.player.pause();
  instance.player.seek(0);
  isPlaying.value = false;
}

function zoomIn(): void {
  const view = peaks.value?.views.getView("zoomview");
  if (view) view.setZoom({ seconds: Math.max(1, (view.getEndTime() - view.getStartTime()) * 0.8) });
}

function zoomOut(): void {
  const view = peaks.value?.views.getView("zoomview");
  if (view) view.setZoom({ seconds: Math.min(Math.max(1, duration.value), (view.getEndTime() - view.getStartTime()) * 1.2) });
}

usePlayerKeyboard({
  togglePlay: togglePlayback,
  stop,
  zoomIn,
  zoomOut,
  enablePadShortcuts: false,
});

watch(() => props.track, async (track) => {
  if (!track) { destroyPeaks(); duration.value = 0; isLoading.value = false; loadError.value = null; return; }
  try { await loadTrack(track); }
  catch (error) { if (props.track?.location_path === track.location_path) loadError.value = `Waveform unavailable: ${String(error)}`; }
  finally { if (props.track?.location_path === track.location_path) isLoading.value = false; }
}, { immediate: true });

watch([gridTrack, gridCues], () => {
  if (peaks.value) paintBeatGrid(peaks.value);
}, { deep: true });

onBeforeUnmount(destroyPeaks);
</script>

<template>
  <section class="studio-zone stem-editor" aria-labelledby="stem-editor-heading">
    <p class="zone-label">Stem Editor</p>
    <template v-if="track">
      <header class="track-header">
        <div class="track-identification"><h2 id="stem-editor-heading" class="track-title">{{ title }}</h2><p class="track-artist">{{ artist }}</p></div>
        <dl class="track-details" aria-label="Track details"><div><dt>BPM</dt><dd>{{ bpm }}</dd></div><div><dt>Key</dt><dd>{{ key }}</dd></div></dl>
      </header>
      <div class="waveform-shell" :class="{ 'is-loading': isLoading }"><div ref="waveform" class="waveform" aria-label="Audio waveform. Click or drag to seek." /><p v-if="isLoading" class="waveform-status">Loading waveform…</p></div>
      <p v-if="loopLabel" class="loop-feedback">{{ loopLabel }}</p>
      <p v-if="loadError" class="waveform-error">{{ loadError }}</p>
      <div class="transport"><button type="button" class="play-button" :disabled="!peaks" :aria-label="playLabel" @click="togglePlayback">{{ playLabel }}</button><output class="time-display" aria-label="Elapsed time and total duration">{{ timeLabel }}</output></div>
      <audio ref="audio" class="audio-element" preload="none" />
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
.transport { display: flex; margin-top: .75rem; align-items: center; gap: .75rem; }.play-button { min-width: 4.5rem; padding: .45rem .75rem; border: 1px solid #edb40b; border-radius: .1875rem; background: #edb40b; color: #17171a; cursor: pointer; font-size: .75rem; font-weight: 800; text-transform: uppercase; }.play-button:hover:not(:disabled) { background: #f7d15f; }.play-button:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }.play-button:disabled { cursor: not-allowed; opacity: .5; }.time-display { color: #f2f2f2; font-family: ui-monospace, "Cascadia Code", monospace; font-size: .75rem; font-variant-numeric: tabular-nums; }.waveform-error { margin: .5rem 0 0; color: #f87171; font-size: .75rem; }.audio-element { display: none; }
:deep(.peaks-view-container) { overflow: hidden !important; } @media (max-width: 520px) { .track-header { align-items: flex-start; flex-direction: column; } }
.loop-feedback { position: absolute; right: 1.5rem; bottom: 3.75rem; z-index: 2; margin: 0; padding: .3rem .45rem; border: 1px solid rgb(247 209 95 / 55%); background: rgb(23 23 26 / 88%); color: #f7d15f; font-family: ui-monospace, "Cascadia Code", monospace; font-size: .6875rem; font-variant-numeric: tabular-nums; pointer-events: none; }
</style>
