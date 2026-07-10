<script setup lang="ts">
// AudioPlayer.vue
// Isolated, lazily-loaded waveform + grid visualizer.
// See .openspec/3-player-spec.md §3 (Component Contract, incl. §3.7
// isLoadingTrack concurrency lock and §3.8 placeholder waveform), §4
// (Two-Stage Sync, incl. §4.3 session-scoped persistence), §5 (Visual
// Rules, revised v1.1: two fixed stage colors BLUE/GREEN, §5.4 two-row
// label stagger), revised by .openspec/4-library-spec.md §4.
//
// Data flow (§2.2, §4.1, revised §4.1/§4.3):
//   useConfigState().selectedTrackPath ──► fetchTrackMetadata() ──► usePlayerState
//          │                                    │
//          └──► convertFileSrc(path) ──────────┴──► wavesurfer.load(assetUrl)
//                                                       │
//                                                       ▼
//                                    "ready" ──► RegionsPlugin (markers)
//
//   useRunState().status "running" ──► resetPlayerState() + destroyWaveform()
//     ──► empty sandbox; successful completion only shows a reload notice
//
// The component is read-only (§3.5): every region is a point region with
// `drag: false` and `resize: false`, and no region-* event handlers are wired.
//
// Visual purity (§5 revised): the Wavesurfer TimelinePlugin is removed
// entirely. Marker labels are stripped to bare minimalism (raw pad number
// for bound cues, name for unbound cues, empty string for the grid anchor).
//
// v1.1 additions:
//  - §3.7 isLoadingTrack: shared concurrency lock in usePlayerState, set
//    true before teardown and false on the first terminal event.
//  - §3.8 placeholder waveform: the canvas region is a fixed-height slot
//    that never collapses; three mutually-exclusive content states.
//  - §4.3 session-scoped stage persistence: a track analyzed in the latest
//    run renders GREEN/post-analysis even when reloaded later.
//  - §5.1 revised: two fixed stage colors — player.pre (BLUE) for Stage 1,
//    player.post (GREEN) for Stage 2 — replacing the hotcue-cycled palette.
//  - §5.4 two-row label stagger keyed off marker index parity.

import {
  computed,
  onMounted,
  onUnmounted,
  ref,
  shallowRef,
  watch,
} from "vue";
import { convertFileSrc } from "@tauri-apps/api/core";
import WaveSurfer from "wavesurfer.js";
import RegionsPlugin from "wavesurfer.js/dist/plugins/regions.esm.js";

import { useConfigState } from "../composables/useConfigState";
import { useRunState } from "../composables/useRunState";
import {
  fetchTrackMetadata,
  usePlayerState,
  type ExistingCue,
  type PlayerMarker,
  type TrackMetadata,
  registerAnalysisTeardown,
} from "../composables/useTrackMetadata";
import { useAnalysisSession } from "../composables/useAnalysisSession";
import { useCueGridSidecar } from "../composables/useCueGridSidecar";
import CueContextMenu from "./CueContextMenu.vue";

// ---------------------------------------------------------------------------
// Props (§3.3) — single `disabled` flag, mirrors ConfigPanel's `locked`.
// ---------------------------------------------------------------------------

defineProps<{ disabled?: boolean }>();

// ---------------------------------------------------------------------------
// §5.1 (revised v1.1) — Marker color palette: two fixed stage colors.
// player.pre (BLUE) = Stage 1 / pre-existing cue.
// player.post (GREEN) = Stage 2 / newly injected cue (live or session match).
// player.grid = grid anchor line, drawn once, never re-colored.
// ---------------------------------------------------------------------------

const COLOR_PRE = "#60a5fa"; // player.pre  — blue-400
const COLOR_POST = "#4caf50"; // player.post — matches --success (green)
const COLOR_GRID = "#3a3a3e"; // player.grid — border-strong

// ---------------------------------------------------------------------------
// Shared state.
// ---------------------------------------------------------------------------

const { selectedTrackPath, update } = useConfigState();
const { logs } = useRunState();
const {
  loadedTrackPath,
  metadata,
  metadataError,
  markers,
  markerStage,
  isLoadingTrack,
  setLoadedMetadata,
  setMetadataError,
  setMarkers,
  setLoadingTrack,
  reset,
} = usePlayerState();
const { getTrackCues } = useAnalysisSession();
const { deleteCue: deleteCueFromSidecar } = useCueGridSidecar();

// ---------------------------------------------------------------------------
// Local refs.
// ---------------------------------------------------------------------------

// `shallowRef` — WaveSurfer is a complex mutable object; we never want Vue's
// reactivity to deep-proxy it.
const wavesurfer = shallowRef<WaveSurfer | null>(null);
const regionsPlugin = shallowRef<RegionsPlugin | null>(null);

const containerRef = ref<HTMLDivElement | null>(null);
const isPlaying = ref(false);
const isDecoding = ref(false);
const loadFailed = ref<string | null>(null);
const analysisNotice = ref<string | null>(null);
const deleteError = ref<string | null>(null);
const contextMenuVisible = ref(false);
const contextMenuX = ref(0);
const contextMenuY = ref(0);
const contextMenuCue = ref<ExistingCue | null>(null);
const deleteInFlight = ref(false);

// §1.3 / §3.9 — The eight pad bindings are kept as explicit reactive state.
// A null slot is the unmapped default and is what resetPlayerState() writes
// immediately during every teardown.
const padBindings = ref<Array<ExistingCue | null>>([]);

function emptyPadBindings(): Array<ExistingCue | null> {
  return Array.from({ length: 8 }, () => null);
}

function rebuildPadBindings(meta: TrackMetadata): void {
  padBindings.value = Array.from({ length: 8 }, (_, index) =>
    meta.existing_cues.find((cue) => cue.hotcue === index) ?? null,
  );
}

// §3.6 — Remaining Time indicator state. Local, transient playback UI state
// (not part of usePlayerState): resets naturally whenever the loaded track
// changes, exactly like isPlaying/isDecoding.
const currentTime = ref(0);
const duration = ref(0);

// Track the most recent Stage 1 load so a stale sidecar response (e.g. the
// user picked another track while the first was still decoding) can't write
// its metadata into the wrong waveform. Incremented on every Stage 1 kick.
let stage1Token = 0;

// ---------------------------------------------------------------------------
// §5.2 (revised) — Marker label helpers.
// Bound cue (hotcue >= 0): raw pad number string only (e.g. "8").
// Unbound cue (hotcue == -1): name only (no numeric substitute available).
// Grid anchor: empty string (purely visual line, no caption).
// ---------------------------------------------------------------------------

function hotcueLabel(hotcue: number): string | null {
  // Traktor's HOTCUE attribute is 0-indexed; on-screen pads are 1-indexed.
  // -1 = unbound → name-only label, no number.
  if (hotcue < 0) return null;
  return String(hotcue + 1);
}

/**
 * §5.1 (revised) — build a marker with a stage-keyed color.
 * `stage === "pre"` → BLUE (player.pre); `stage === "post"` → GREEN (player.post).
 * The hotcue-cycled palette is gone; color communicates stage, not pad slot.
 */
function buildMarker(cue: ExistingCue, stage: "pre" | "post"): PlayerMarker {
  const colorToken = stage === "pre" ? COLOR_PRE : COLOR_POST;
  return {
    hotcueLabel: hotcueLabel(cue.hotcue),
    name: cue.name,
    startMs: cue.start_ms,
    colorToken,
  };
}

// ---------------------------------------------------------------------------
// §3.4 / §4.1 — Lifecycle teardown and sanitization.
// ---------------------------------------------------------------------------

/**
 * Reset every player-owned projection before a track reload or NML mutation
 * synchronization. This intentionally does not destroy the decoded waveform;
 * track reload callers do that separately, while post-operation sync reuses it.
 */
function resetPlayerState(): void {
  // Invalidate all callbacks/listeners that are guarded by the current load
  // token before clearing the visual state.
  stage1Token += 1;
  regionsPlugin.value?.clearRegions();
  padBindings.value = emptyPadBindings();
  activePad.value = null;
  contextMenuVisible.value = false;
  contextMenuCue.value = null;
  reset();
}

function destroyWaveform(): void {
  if (wavesurfer.value) {
    try {
      wavesurfer.value.destroy();
    } catch {
      // destroy() can throw if the underlying media element was already
      // torn down (e.g. the webview navigated). Swallow — the goal is just
      // "no lingering AudioContext".
    }
  }
  wavesurfer.value = null;
  regionsPlugin.value = null;
  isPlaying.value = false;
  isDecoding.value = false;
  // §3.6 — reset transient playback UI state.
  currentTime.value = 0;
  duration.value = 0;
}

// ---------------------------------------------------------------------------
// §4.1 — Stage 1: On Selection.
// §3.7 — isLoadingTrack is set true at the very first line (before
// destroyWaveform) and false on the first terminal event (ready / metadata
// error / decode error).
// ---------------------------------------------------------------------------

async function runStage1(path: string): Promise<void> {
  // §3.7 — flip the shared lock on BEFORE any teardown begins. This is the
  // wider window that covers the --get-track-metadata sidecar round-trip too.
  setLoadingTrack(true);
  // §3.4 — sanitize regions, pads, metadata, and callback identity before
  // destroying the previous instance and recreating it.
  resetPlayerState();
  analysisNotice.value = null;
  // The reset invalidates old callbacks; this token belongs to this load.
  const myToken = ++stage1Token;
  destroyWaveform();
  loadFailed.value = null;

  // 1. Spawn --get-track-metadata and parse the one-shot JSON line (§4.1.1).
  const result = await fetchTrackMetadata(path);

  // Stale response — another track was selected while we were waiting.
  if (myToken !== stage1Token) return;

  if (!result.ok) {
    if (result.error) {
      // §4.1.2 — modeled error (not_found / ambiguous): non-fatal inline msg.
      analysisNotice.value = null;
      setMetadataError(path, result.error);
      loadFailed.value = result.error.message;
    } else {
      // I/O or parse fault — surface as a generic load failure.
      loadFailed.value = result.fault ?? "Failed to read track metadata.";
    }
    // §3.7 — terminal event: metadata error.
    setLoadingTrack(false);
    return;
  }

  const meta = result.metadata as TrackMetadata;
  setLoadedMetadata(path, meta);
  rebuildPadBindings(meta);

  // 2. §2.2 — Tauri Asset Bridge: convertFileSrc → asset:// URL → wavesurfer.
  const assetUrl = convertFileSrc(path);

  // 3. Instantiate wavesurfer + Regions plugin. The Regions plugin is created
  // up front so the "ready" handler can populate it without a race.
  // (TimelinePlugin is removed per §5 revised — no import, no usage.)
  if (!containerRef.value) {
    // component unmounted mid-flight — terminal event.
    setLoadingTrack(false);
    return;
  }

  const regions = RegionsPlugin.create();
  const ws = WaveSurfer.create({
    container: containerRef.value,
    height: 96,
    waveColor: "#3a3a3e",
    progressColor: "#4fd1c5",
    cursorColor: "#8a8a8e",
    cursorWidth: 1,
    barWidth: 2,
    barGap: 1,
    barRadius: 1,
    normalize: true,
    interact: true,
    plugins: [regions],
  });

  wavesurfer.value = ws;
  regionsPlugin.value = regions;

  // 4. §4.1.4 (revised) — On "ready" (audio decoded, duration known), paint
  // the Stage 1 markers (BLUE by default, or GREEN if a session match exists,
  // per §4.3). No Timeline plugin is instantiated.
  ws.once("ready", () => {
    if (myToken !== stage1Token) return; // stale
    onWaveformReady(meta);
    // §3.7 — terminal event: ready (success). The lock flips off here, the
    // widest possible window — covers metadata fetch + decode.
    setLoadingTrack(false);
  });

  ws.on("play", () => {
    if (myToken === stage1Token) isPlaying.value = true;
  });
  ws.on("pause", () => {
    if (myToken === stage1Token) isPlaying.value = false;
  });
  ws.on("finish", () => {
    if (myToken === stage1Token) isPlaying.value = false;
  });

  // §3.6 — track remaining time reactively against the known total duration.
  ws.on("timeupdate", (time: number) => {
    if (myToken !== stage1Token) return;
    currentTime.value = time;
  });
  ws.on("error", (err: Error) => {
    if (myToken !== stage1Token) return;
    loadFailed.value = `Audio decode failed: ${err.message}`;
    // §3.7 — terminal event: decode error.
    setLoadingTrack(false);
  });

  isDecoding.value = true;
  // 5. §2.2 — only the asset:// URL is ever passed to .load(), never the raw
  // filesystem path.
  try {
    await ws.load(assetUrl);
  } catch (err) {
    if (myToken !== stage1Token) return;
    loadFailed.value = `Failed to load audio: ${String(err)}`;
    // §3.7 — a load() rejection is a terminal failure too; the "ready" event
    // won't fire, so we must release the lock here.
    setLoadingTrack(false);
  } finally {
    if (myToken === stage1Token) isDecoding.value = false;
  }
}

// ---------------------------------------------------------------------------
// §4.1.4 (revised) + §4.3 — "ready" handler: Stage 1 markers, with
// session-aware stage resolution. No Timeline plugin is instantiated.
// ---------------------------------------------------------------------------

function onWaveformReady(meta: TrackMetadata): void {
  const ws = wavesurfer.value;
  if (!ws) return;

  // §3.6 — capture the decoded duration for the Remaining Time indicator.
  duration.value = ws.getDuration();

  // §4.3 step 4 — resolve the marker set:
  //   session match → paint the session's cues in GREEN, markerStage = post.
  //   no match      → paint existing_cues in BLUE, markerStage = pre.
  const sessionCues = getTrackCues(meta.artist, meta.title);
  if (sessionCues && sessionCues.length > 0) {
    paintSessionMatchedMarkers(meta, sessionCues);
  } else {
    paintStage1Markers(meta);
  }
}

// ---------------------------------------------------------------------------
// §3.5 + §5 (revised) — Region painting (read-only point regions).
// Labels are minimalist: bound cue → raw pad number, unbound cue → name,
// grid anchor → empty string.
//
// §5.4 — two-row label stagger: every point region's label element receives
// an ordinal CSS class (marker-even / marker-odd) cycling 0,1,0,1,... in the
// order markers are painted (ascending start_ms). A scoped <style> rule
// offsets the two classes vertically so adjacent labels never overlap.
// ---------------------------------------------------------------------------

function addPointRegion(
  regions: RegionsPlugin,
  startMs: number,
  color: string,
  label: string | null,
  name: string,
  index: number,
): void {
  // §3.5 — point region: start === end, drag/resize disabled.
  const startSec = startMs / 1000;
  // §5.2 (revised) — bound cue: bare number only. Unbound cue: name only.
  // No bracket, no `${label} ${name}` concatenation. The text content is a
  // pure string with absolutely no embedded HTML tag strings.
  const content = label !== null ? label : name;

  // §5.4 — two-row vertical stagger. wavesurfer.js v7's RegionsPlugin treats
  // a string `content` as plain text (textContent), NOT as HTML — passing
  // `<span class="marker-even">1</span>` as a string would escape it and
  // print the literal tag text on the canvas. To attach the ordinal CSS
  // class without polluting the visible content, we build a native
  // HTMLElement via document.createElement, set its textContent to the bare
  // number/name, add the marker-even/marker-odd class to that wrapper, and
  // pass the element itself to `content`. The plugin appends the element
  // as DOM, so the scoped :deep() rules in <style> can target it.
  const ordinalClass = index % 2 === 0 ? "marker-even" : "marker-odd";
  const labelEl = document.createElement("span");
  labelEl.className = ordinalClass;
  labelEl.textContent = content;

  regions.addRegion({
    start: startSec,
    end: startSec, // zero-width point
    drag: false,
    resize: false,
    color: hexWithAlpha(color, 0.85),
    content: labelEl,
  });
}

function addGridAnchorLine(regions: RegionsPlugin, anchorMs: number): void {
  // §5.2 (revised) — the grid anchor is a distinct vertical line with no
  // text caption (content is empty), distinguishable from hotcue markers by
  // color and by having no text at all.
  const startSec = anchorMs / 1000;
  regions.addRegion({
    start: startSec,
    end: startSec,
    drag: false,
    resize: false,
    color: hexWithAlpha(COLOR_GRID, 0.9),
    content: "",
  });
}

/**
 * §4.3 step 4 (no match) — paint existing_cues from --get-track-metadata in
 * BLUE (player.pre), markerStage = "pre-analysis".
 */
function paintStage1Markers(meta: TrackMetadata): void {
  const regions = regionsPlugin.value;
  if (!regions) return;
  regions.clearRegions();

  // §5.2 — grid anchor line first (drawn once, never re-colored per §5.1).
  addGridAnchorLine(regions, meta.grid_anchor_ms);

  // §1.3 — existing_cues already excludes GRID and is sorted by start_ms.
  const stage1Markers: PlayerMarker[] = meta.existing_cues.map((c) =>
    buildMarker(c, "pre"),
  );
  stage1Markers.forEach((m, i) => {
    addPointRegion(regions, m.startMs, m.colorToken, m.hotcueLabel, m.name, i);
  });
  setMarkers(stage1Markers, "pre-analysis");
}

/**
 * §4.3 step 4 (match found) — paint the session's captured cues in GREEN
 * (player.post), markerStage = "post-analysis", exactly as if Stage 2 had
 * just run live.
 */
function paintSessionMatchedMarkers(meta: TrackMetadata, sessionCues: ExistingCue[]): void {
  const regions = regionsPlugin.value;
  if (!regions) return;
  regions.clearRegions();

  // §5.2 — grid anchor line first (drawn once, never re-colored per §5.1).
  addGridAnchorLine(regions, meta.grid_anchor_ms);

  // Session cues are already sorted ascending by start_ms (useAnalysisSession
  // sorts on capture), but sort defensively in case of a future change.
  const sorted = [...sessionCues].sort((a, b) => a.start_ms - b.start_ms);
  const sessionMarkers: PlayerMarker[] = sorted.map((c) => buildMarker(c, "post"));
  sessionMarkers.forEach((m, i) => {
    addPointRegion(regions, m.startMs, m.colorToken, m.hotcueLabel, m.name, i);
  });
  setMarkers(sessionMarkers, "post-analysis");
}

function repaintCurrentMetadataMarkers(): void {
  const meta = metadata.value;
  const regions = regionsPlugin.value;
  if (!meta || !regions) return;

  regions.clearRegions();
  addGridAnchorLine(regions, meta.grid_anchor_ms);
  const stage = markerStage.value === "post-analysis" ? "post" : "pre";
  const currentMarkers = meta.existing_cues.map((cue) => buildMarker(cue, stage));
  currentMarkers.forEach((marker, index) => {
    addPointRegion(
      regions,
      marker.startMs,
      marker.colorToken,
      marker.hotcueLabel,
      marker.name,
      index,
    );
  });
  setMarkers(currentMarkers, stage === "post" ? "post-analysis" : "pre-analysis");
}

async function deleteSelectedCue(): Promise<void> {
  const cue = contextMenuCue.value;
  const trackPath = loadedTrackPath.value;
  const meta = metadata.value;
  contextMenuVisible.value = false;
  if (!cue || !trackPath || !meta || deleteInFlight.value) return;

  analysisNotice.value = null;
  const backup = JSON.parse(JSON.stringify(meta.existing_cues)) as ExistingCue[];
  const originalCues = meta.existing_cues;
  deleteInFlight.value = true;
  deleteError.value = null;
  meta.existing_cues = originalCues.filter(
    (candidate) => candidate.hotcue !== cue.hotcue,
  );
  rebuildPadBindings(meta);
  repaintCurrentMetadataMarkers();

  const result = await deleteCueFromSidecar(
    trackPath,
    cue.hotcue,
    meta.title,
    meta.artist,
  );
  const stillShowingDeletedTrack =
    selectedTrackPath.value === trackPath && loadedTrackPath.value === trackPath;

  if (!result.ok) {
    if (stillShowingDeletedTrack) {
      // Restore the local snapshot if the sidecar did not persist the delete.
      meta.existing_cues = backup;
      rebuildPadBindings(meta);
      repaintCurrentMetadataMarkers();
      deleteError.value = `NML deletion failed: ${result.error ?? "unknown sidecar error"}`;
      logs.value.push({
        ts: Date.now(),
        msg: { type: "log", level: "error", message: deleteError.value },
      });
    }
  } else if (stillShowingDeletedTrack) {
    // Keep the successful deletion local and optimistic. Analysis uses the
    // Clean Sandbox path below; deletion must not trigger a metadata reload.
    rebuildPadBindings(meta);
    repaintCurrentMetadataMarkers();
  }
  deleteInFlight.value = false;
  contextMenuCue.value = null;
}

// ---------------------------------------------------------------------------
// Clean Sandbox analysis lifecycle.
// Analysis deliberately does not hot-reload metadata or repaint the existing
// waveform. The player is emptied before the sidecar starts and the user must
// manually select the track again after analysis completes.
// ---------------------------------------------------------------------------

function unloadForAnalysis(clearSelection = true): void {
  resetPlayerState();
  destroyWaveform();
  // Clear the shared selection as well as the player-local state. This makes
  // selecting the same path after analysis a real state transition instead of
  // a no-op assignment that Vue's watcher correctly ignores.
  if (clearSelection && selectedTrackPath.value !== null) {
    update("selectedTrackPath", null);
  }
  setLoadingTrack(false);
  analysisNotice.value = null;
  loadFailed.value = null;
  deleteError.value = null;
}

const currentTrack = computed(() =>
  loadedTrackPath.value ? { path: loadedTrackPath.value } : undefined,
);

function prepareForAnalysis(targetPath: string | null, force: boolean): void {
  const targetTrack = targetPath ? { path: targetPath } : null;
  if (!force && targetTrack?.path !== currentTrack.value?.path) {
    // Background analysis must not interrupt the currently playing song.
    return;
  }
  unloadForAnalysis(true);
}

const unregisterAnalysisTeardown = registerAnalysisTeardown(
  (targetPath, force) => prepareForAnalysis(targetPath, force),
);

// ---------------------------------------------------------------------------
// Color helper — wavesurfer region `color` is a CSS color string; we add an
// alpha channel so the waveform stays visible underneath the marker.
// ---------------------------------------------------------------------------

function hexWithAlpha(hex: string, alpha: number): string {
  // Accepts #rrggbb, returns rgba(...). Falls back to the input on odd shapes.
  const m = /^#([0-9a-fA-F]{6})$/.exec(hex);
  if (!m) return hex;
  const r = parseInt(m[1].slice(0, 2), 16);
  const g = parseInt(m[1].slice(2, 4), 16);
  const b = parseInt(m[1].slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

// ---------------------------------------------------------------------------
// Transport.
// ---------------------------------------------------------------------------

async function togglePlay(): Promise<void> {
  const ws = wavesurfer.value;
  if (!ws) return;
  try {
    await ws.playPause();
  } catch {
    // play() can reject if the media element isn't ready yet — non-fatal.
  }
}

function stop(): void {
  wavesurfer.value?.stop();
}

// ---------------------------------------------------------------------------
// §3.9 / §3.10 — Virtual HotCue pads + momentary cue behavior.
// Pads are 1-indexed in the UI; the NML HOTCUE attribute is 0-indexed, so
// pad N maps to hotcue === N - 1 (same convention as §5.2's hotcueLabel).
// A pad is enabled iff !isLoadingTrack AND a bound cue exists for its index.
// Momentary behavior: press → seek + play; release → pause + return to cue.
// ---------------------------------------------------------------------------

// §3.10 — tracks which pad is currently being held down (mouse or keyboard),
// so the template can render a hardware-press visual (brightness/scale).
// null when no pad is active. Set in pressPad, cleared in releasePad.
const activePad = ref<number | null>(null);

/**
 * §5.1 / §3.9 — stage-keyed pad background. Enabled pads mirror the marker
 * stage color: BLUE (player.pre) for Stage 1, GREEN (player.post) for Stage 2.
 * Returns a Tailwind class string for the enabled pad's resting background.
 */
const padStageBg = computed(() => {
  return markerStage.value === "post-analysis"
    ? "bg-green-600"
    : "bg-blue-600";
});

/**
 * §3.9 — look up the bound cue for a 1-indexed pad. Returns null if no
 * track is loaded, no cue is bound to that pad index, or a load is in flight.
 */
function cueForPad(padIndex: number): ExistingCue | null {
  if (isLoadingTrack.value) return null;
  return padBindings.value[padIndex - 1] ?? null;
}

/**
 * §3.9 — pad enabled-state for the template. Strict contract: enabled iff
 * !isLoadingTrack AND a bound cue exists for this pad index.
 */
function isPadEnabled(padIndex: number): boolean {
  return cueForPad(padIndex) !== null;
}

function openPadContextMenu(event: MouseEvent, padIndex: number): void {
  const cue = cueForPad(padIndex);
  if (!cue) return;
  contextMenuCue.value = cue;
  contextMenuX.value = event.clientX;
  contextMenuY.value = event.clientY;
  contextMenuVisible.value = true;
  deleteError.value = null;
}

/**
 * §3.10 — momentary press (mousedown / keydown). Seeks to the cue's exact
 * start_ms (converted to seconds) and triggers immediate playback.
 * No-op if the pad is disabled or no wavesurfer instance exists.
 */
function pressPad(padIndex: number): void {
  const cue = cueForPad(padIndex);
  if (!cue) return;
  const ws = wavesurfer.value;
  if (!ws) return;
  activePad.value = padIndex;
  ws.setTime(cue.start_ms / 1000);
  void ws.play();
}

/**
 * §3.10 — momentary release (mouseup / mouseleave / keyup). Pauses playback
 * AND snaps the playhead back to the cue's exact start position (stutter /
 * return-on-release, matching standard DJ hardware behavior). No-op if no
 * wavesurfer instance exists; safe to call even if press never fired.
 */
function releasePad(padIndex: number): void {
  // A context-menu opening can move the pointer out of the pad and fire
  // mouseleave without a matching left-button press. Never touch Wavesurfer
  // for that synthetic release.
  if (activePad.value !== padIndex) return;

  const ws = wavesurfer.value;
  if (ws) {
    ws.pause();
    // Stutter return: seek back to the cue's start so the next press replays
    // from the same point. No-op safe if the cue is no longer bound.
    const cue = cueForPad(padIndex);
    if (cue) ws.setTime(cue.start_ms / 1000);
  }
  activePad.value = null;
}

// ---------------------------------------------------------------------------
// §3.11 / §3.12 — Global keyboard shortcuts layer.
// Registered on window in onMounted, removed in onUnmounted. Active only
// when the user is not focused on an input element (input/textarea/select/
// contentEditable). Keys 1-8 → pads (momentary), Space → toggle, Enter →
// stop, ArrowLeft/ArrowRight → ±8-beat jump.
// ---------------------------------------------------------------------------

/**
 * §3.11 — focus guard. Returns true (and the handler should bail) when the
 * user is focused on an input element, so typing into TargetSelector /
 * ConfigPanel fields doesn't misfire as pad/transport input.
 */
function isFocusedOnInput(): boolean {
  const el = document.activeElement as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (el.isContentEditable) return true;
  return false;
}

function onGlobalKeyDown(event: KeyboardEvent): void {
  // §3.11 — focus guard runs first, before repeat-guard and pad-state lookup.
  if (isFocusedOnInput()) return;

  const ws = wavesurfer.value;
  // No instance at all (no track ever loaded) — nothing to do for any mapping.
  if (!ws) return;

  // Keys 1-8 → momentary pad press (§3.10 / §3.11).
  if (event.key >= "1" && event.key <= "8") {
    // §3.10 — strict repeat guard: holding a pad key must not spam restart.
    if (event.repeat) return;
    const padIndex = Number(event.key);
    pressPad(padIndex);
    return;
  }

  // Space → toggle Play/Pause (§3.11).
  if (event.key === " " || event.code === "Space") {
    event.preventDefault(); // suppress browser's space-scrolls-page behavior
    if (event.repeat) return; // avoid double-toggling on held-key auto-repeat
    void togglePlay();
    return;
  }

  // Enter → Stop: pause + return to 0.0 (§3.11).
  if (event.key === "Enter") {
    ws.pause();
    ws.setTime(0);
    return;
  }

  // ArrowLeft / ArrowRight → ±8-beat relative jump (§3.12).
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    // §3.12 — no-op safety: no track, or missing/invalid BPM.
    if (!hasTrack.value) return;
    const bpm = metadata.value?.bpm;
    if (typeof bpm !== "number" || !isFinite(bpm) || bpm <= 0) return;

    const jump = 8.0 * (60.0 / bpm);
    const current = ws.getCurrentTime();
    const total = ws.getDuration();
    if (event.key === "ArrowLeft") {
      ws.setTime(Math.max(0, current - jump));
    } else {
      ws.setTime(Math.min(total, current + jump));
    }
    return;
  }
}

function onGlobalKeyUp(event: KeyboardEvent): void {
  // §3.11 — focus guard.
  if (isFocusedOnInput()) return;

  // Keys 1-8 → momentary pad release (§3.10 / §3.11).
  if (event.key >= "1" && event.key <= "8") {
    const padIndex = Number(event.key);
    releasePad(padIndex);
    return;
  }
  // Space / Enter / Arrows: keyup is a no-op (toggle/stop/jump fire on keydown).
}

// ---------------------------------------------------------------------------
// §3.4 / §4.1 (4-library-spec.md) — Lifecycle wiring.
// ---------------------------------------------------------------------------

// On mount: if a selectedTrackPath is somehow already set (defensive —
// §2.4 excludes it from persistence so this is effectively always a no-op
// on a fresh launch), kick off Stage 1 immediately.
onMounted(() => {
  if (selectedTrackPath.value) {
    void runStage1(selectedTrackPath.value);
  }
  // §3.11 — register global keyboard shortcuts on window. Registered here
  // (not at module scope) because the player is lazily loaded (§3.1); a
  // module-scope listener would outlive the component and fire on pages
  // where no player exists.
  window.addEventListener("keydown", onGlobalKeyDown);
  window.addEventListener("keyup", onGlobalKeyUp);
});

// On unmount: destroy unconditionally, null-check on the ref only (§3.4).
onUnmounted(() => {
  // §3.11 — remove global keyboard listeners (paired with onMounted).
  window.removeEventListener("keydown", onGlobalKeyDown);
  window.removeEventListener("keyup", onGlobalKeyUp);
  unregisterAnalysisTeardown();
  resetPlayerState();
  destroyWaveform();
  // §3.7 — defensive: ensure the lock is released if we unmount mid-load.
  setLoadingTrack(false);
});

// §4.1 — watch selectedTrackPath (the double-click / Load-icon bridge from
// LibraryBrowser.vue). A short 50ms coalescing window guards against two
// rapid double-clicks on different rows spawning overlapping
// --get-track-metadata processes.
let trackPathDebounce: ReturnType<typeof setTimeout> | null = null;
watch(
  () => selectedTrackPath.value,
  (newPath, oldPath) => {
    if (newPath === oldPath) return;
    if (trackPathDebounce) clearTimeout(trackPathDebounce);
    // Sanitize immediately, before the debounce window or the next metadata
    // process can leave old regions/pad bindings visible.
    resetPlayerState();
    if (!newPath) {
      // Cleared — tear down without re-spawning.
      destroyWaveform();
      loadFailed.value = null;
      // §3.7 — no load in flight anymore.
      setLoadingTrack(false);
      return;
    }
    trackPathDebounce = setTimeout(() => {
      void runStage1(newPath);
    }, 50);
  },
);

// Analysis teardown is requested explicitly by the sidecar entry point so
// single-track background analysis can leave the active player untouched.

// ---------------------------------------------------------------------------
// Derived view state for the template.
// ---------------------------------------------------------------------------

const hasTrack = computed(
  () => !!loadedTrackPath.value && !!metadata.value && !metadataError.value,
);
const trackHeader = computed(() => {
  const m = metadata.value;
  if (!m) return "";
  return `${m.artist} — ${m.title}`;
});
const bpmLabel = computed(() => {
  const m = metadata.value;
  return m ? `${m.bpm.toFixed(1)} BPM` : "";
});
// §3.6 — Remaining Time indicator. Format: -MM:SS, always prefixed with a
// literal "-". Hidden entirely when !hasTrack (not rendered as -00:00).
const remainingLabel = computed(() => {
  if (!hasTrack.value) return "";
  const total = duration.value;
  if (!total || !isFinite(total)) return "";
  const remaining = Math.max(0, total - currentTime.value);
  const m = Math.floor(remaining / 60);
  const s = Math.floor(remaining % 60);
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `-${pad(m)}:${pad(s)}`;
});
const stageLabel = computed(() => {
  switch (markerStage.value) {
    case "pre-analysis":
      return "pre-analysis";
    case "post-analysis":
      return "post-analysis";
    default:
      return "—";
  }
});
const cueCount = computed(() => markers.value.length);
</script>

<template>
  <section
    class="bg-panel border-b border-border px-6 py-4 flex flex-col gap-3"
    :class="{ 'opacity-60 pointer-events-none': disabled }"
  >
    <!-- Header row: track identity + remaining-time + BPM + stage badge -->
    <div class="flex items-center gap-4">
      <div class="flex items-center gap-2 min-w-0">
        <span class="text-xs uppercase tracking-widest text-muted">Player</span>
        <span
          v-if="hasTrack"
          class="truncate text-sm text-primary font-mono"
          :title="trackHeader"
        >
          {{ trackHeader }}
        </span>
        <span v-else-if="metadataError" class="text-sm text-warn font-mono">
          {{ metadataError.message }}
        </span>
        <span v-else-if="loadFailed" class="text-sm text-error font-mono">
          {{ loadFailed }}
        </span>
        <span v-else-if="analysisNotice" class="text-sm text-success font-mono">
          {{ analysisNotice }}
        </span>
        <span v-else class="text-sm text-dim">
          No track selected.
        </span>
      </div>

      <div class="flex-1" />

      <div class="flex items-center gap-3 text-xs text-muted font-mono">
        <!-- §3.6 — Remaining Time indicator, immediately left of BPM -->
        <span v-if="remainingLabel">{{ remainingLabel }}</span>
        <span v-if="bpmLabel">{{ bpmLabel }}</span>
        <span v-if="hasTrack">
          <span class="text-dim">stage:</span>
          <span class="text-primary">{{ stageLabel }}</span>
        </span>
        <span v-if="hasTrack">
          <span class="text-dim">cues:</span>
          <span class="text-primary">{{ cueCount }}</span>
        </span>
      </div>

    </div>

    <!-- §3.8 — Waveform canvas region: a fixed-height slot, ALWAYS rendered.
         The box itself never resizes or disappears; only its contents switch
         between three mutually-exclusive states (real waveform / loading
         overlay / placeholder pattern). This guarantees no layout shift when
         a track is loaded or cleared. -->
    <div class="relative">
      <div v-if="deleteError" class="mb-2 text-xs text-error font-mono">
        {{ deleteError }}
      </div>
      <!-- The container div wavesurfer writes its canvas into. Always present
           at the same fixed footprint (height: 96px matches wavesurfer.create). -->
      <div
        ref="containerRef"
        class="w-full rounded-md border border-border bg-zinc-800 overflow-hidden"
        style="height: 96px"
      />

      <!-- State 3: Placeholder pattern — !hasTrack && !isLoadingTrack.
           A static, muted repeating-bar pattern echoing wavesurfer's own
           bar-style rendering, occupying the identical box. -->
      <div
        v-if="!hasTrack && !isLoadingTrack"
        class="absolute inset-0 flex items-center justify-center pointer-events-none"
        aria-hidden="true"
      >
        <div class="wf-placeholder-bars w-full h-full rounded-md"></div>
      </div>

      <!-- State 2: Loading overlay — isLoadingTrack (§3.7). Drawn on top of
           whichever of states 1/3 is underneath. A single, unmistakable
           pulsing spinner centered over the entire waveform area. -->
      <div
        v-if="isLoadingTrack"
        class="absolute inset-0 flex items-center justify-center bg-zinc-800/70 text-sm text-muted font-mono"
      >
        <span class="wf-spinner mr-2" aria-hidden="true"></span>
        Loading…
      </div>
    </div>

    <CueContextMenu
      :x="contextMenuX"
      :y="contextMenuY"
      :visible="contextMenuVisible && !deleteInFlight"
      @close="contextMenuVisible = false"
      @delete="void deleteSelectedCue()"
    />

    <div class="flex items-center gap-4">
      <div class="flex items-center gap-2 min-w-0">
          <div v-if="hasTrack" class="flex items-center gap-2">
            <button
              type="button"
              class="px-3 py-1.5 rounded-md text-sm border border-border-strong text-primary hover:bg-elevated transition-colors"
              @click="togglePlay"
            >
              {{ isPlaying ? "❚❚ Pause" : "▶ Play" }}
            </button>
            <button
              type="button"
              class="px-3 py-1.5 rounded-md text-sm border border-border-strong text-muted hover:text-primary transition-colors"
              @click="stop"
            >
              ■ Stop
            </button>
            <!-- §3.9 — Virtual HotCue pads: horizontal row of 8 numbered pads
                 (1-8), placed immediately right of the Play/Stop buttons.
                 Pad N is enabled iff !isLoadingTrack AND a bound cue exists for
                 hotcue === N - 1 (0-indexed NML). Unmapped/loading pads render
                 disabled, translucent, non-clickable. §3.10 wires momentary
                 cue behavior: mousedown → seek+play; mouseup/mouseleave → pause.

                 Color sync (§5.1): enabled pads mirror the marker stage color —
                 BLUE (bg-blue-600) for Stage 1, GREEN (bg-green-600) for Stage 2 —
                 via the padStageBg computed. Active/pressed pads brighten
                 (bg-blue-400 / bg-green-400) and scale down (scale-95) to
                 simulate a hardware button press, driven by activePad ref. -->
            <div class="flex items-center gap-1 ml-2" role="group" aria-label="HotCue pads">
              <button
                v-for="padIndex in 8"
                :key="padIndex"
                type="button"
                :disabled="!isPadEnabled(padIndex)"
                :aria-label="`HotCue pad ${padIndex}`"
                :aria-pressed="activePad === padIndex"
                class="wf-pad w-8 h-8 rounded-md text-xs font-mono border border-border-strong transition-all duration-75"
                :class="
                  isPadEnabled(padIndex)
                    ? activePad === padIndex
                      ? (markerStage === 'post-analysis' ? 'bg-green-400 text-white scale-95 brightness-125 cursor-pointer' : 'bg-blue-400 text-white scale-95 brightness-125 cursor-pointer')
                      : (padStageBg + ' text-white hover:brightness-110 cursor-pointer')
                    : 'bg-elevated text-dim opacity-60 pointer-events-none cursor-not-allowed'
                "
                @mousedown.left.prevent="pressPad(padIndex)"
                @mouseup.left.prevent="releasePad(padIndex)"
                @mouseleave.prevent="releasePad(padIndex)"
                @contextmenu.stop.prevent="openPadContextMenu($event, padIndex)"
              >
                {{ padIndex }}
              </button>
            </div>
          </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
/* §3.9 — Virtual HotCue pad base. Keeps the pad's box uniform regardless of
 * enabled/disabled state; the enabled/disabled visual distinction is driven
 * entirely by the template's :class binding (opacity-50 / pointer-events-none
 * / cursor-not-allowed when disabled, hover:bg-elevated / cursor-pointer when
 * enabled). Matches the existing transport-button chrome tokens. */
.wf-pad {
  line-height: 1;
  user-select: none;
}

/* §3.8 — Placeholder waveform pattern. A static, muted repeating bar
 * pattern that echoes wavesurfer's own bar-style rendering, occupying the
 * identical box as a real waveform so the player's height never collapses
 * when no track is loaded. Uses the existing bg-zinc-800/border-border
 * tones already used for the container's own chrome. */
.wf-placeholder-bars {
  background-image:
    linear-gradient(
      to right,
      transparent 0,
      transparent 2px,
      #3a3a3e 2px,
      #3a3a3e 4px,
      transparent 4px,
      transparent 7px
    );
  background-size: 7px 100%;
  background-repeat: repeat-x;
  background-position: center;
  opacity: 0.5;
}

/* §3.7 — Loading spinner. A clear, unmistakable, pulsing/spinning affordance
 * centered over the entire waveform area while isLoadingTrack is true. */
.wf-spinner {
  display: inline-block;
  width: 14px;
  height: 14px;
  border: 2px solid #8a8a8e;
  border-top-color: transparent;
  border-radius: 50%;
  animation: wf-spin 0.8s linear infinite;
}

@keyframes wf-spin {
  to {
    transform: rotate(360deg);
  }
}

/* §5.4 — Marker label two-row stagger. Every point region's label element
 * receives an ordinal class (marker-even / marker-odd) set by addPointRegion
 * at creation time, cycling 0,1,0,1,... in the order markers are painted
 * (ascending start_ms). The two classes are offset vertically so adjacent
 * labels never overlap each other's text — only their vertical connector
 * lines may sit close together.
 *
 * These rules use :deep() because wavesurfer.js's Regions plugin renders the
 * `content` HTML into elements outside this component's scoped style
 * boundary (inside the containerRef div). The label <span> is added by
 * addPointRegion via the `content` option. */
:deep(.marker-even) {
  top: 2px;
}
:deep(.marker-odd) {
  top: 16px;
}
</style>
