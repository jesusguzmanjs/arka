// composables/useTrackMetadata.ts
// See .openspec/3-player-spec.md §1 (Core Extension), §4.1 (Stage 1), §6 (Data Structures).
//
// Spawns the packaged Python core via Rust Resource architecture with
// the `--get-track-metadata <TRACK_PATH>` flag, fetches its JSON stdout,
// parses it, and updates a module-scoped singleton `usePlayerState` reactive store.
//
// This file also owns the §6 `usePlayerState` singleton and the §1.3/§1.4
// TypeScript type definitions, since the spec's §3.2 file layout splits them
// into `usePlayerState.ts` and `types/trackMetadata.ts` but the player's
// internal state is private to this composable's consumers (§3.3) — keeping
// them co-located avoids a premature split that no other module reads.

import { markRaw, reactive, toRefs } from "vue";
import { useCueGridSidecar } from "../core/useCueGridSidecar.ts";
import { getHarmonicMatches } from "../../utils/harmonicKeys.ts";
import type {
  ExistingCue,
  SuperJSON,
  TrackMetadata,
  TrackMetadataError,
  TrackMetadataResult,
} from "../../types/trackMetadata.ts";
import { isTrackMetadataError } from "../../types/trackMetadata.ts";

// ---------------------------------------------------------------------------
// §1.3 / §1.4 / §6 — Data Structures (TypeScript)
// ---------------------------------------------------------------------------

export type { ExistingCue, SuperJSON, TrackMetadata, TrackMetadataError, TrackMetadataResult };
export { isTrackMetadataError } from "../../types/trackMetadata.ts";

// ---------------------------------------------------------------------------
// §6 — usePlayerState singleton (mirrors useRunState.ts's shape)
// ---------------------------------------------------------------------------

export type MarkerStage = "pre-analysis" | "post-analysis";

export interface PlayerMarker {
  hotcueLabel: string | null; // "[N]" or null (unbound cue, name-only label)
  name: string;
  startMs: number;
  colorToken: string; // one of §5.1's tailwind color tokens
}

interface PlayerState {
  loadedTrackPath: string | null;
  activeDirectKeys: string[];
  activeAdjacentKeys: string[];
  metadata: TrackMetadata | null;
  metadataError: TrackMetadataError | null;
  markers: PlayerMarker[];
  markerStage: MarkerStage | null;
  isLoadingTrack: boolean; // §3.7 — shared concurrency lock, read by LibraryBrowser.vue too
  previewCache: Map<string, SuperJSON>;
}

const playerState = reactive<PlayerState>({
  loadedTrackPath: null,
  activeDirectKeys: [],
  activeAdjacentKeys: [],
  metadata: null,
  metadataError: null,
  markers: [],
  markerStage: null,
  isLoadingTrack: false,
  previewCache: markRaw(new Map<string, SuperJSON>()),
});

/**
 * Module-scoped singleton for the player's private state. No other component
 * reads this in Phase 3 (§3.3); it exists so AudioPlayer.vue's Stage 1 / Stage
 * 2 handlers share one source of truth and so a re-render never resurrects
 * stale Stage 1 muted markers underneath Stage 2's active ones (§4.2 step 4).
 */
let analysisTeardown: ((targetPath: string | null, force: boolean) => void) | null = null;
let metadataRefresh: ((editedPaths: readonly string[]) => Promise<void>) | null = null;

/** Register the mounted AudioPlayer's teardown routine for sidecar runs. */
export function registerAnalysisTeardown(
  teardown: (targetPath: string | null, force: boolean) => void,
): () => void {
  analysisTeardown = teardown;
  return () => {
    if (analysisTeardown === teardown) analysisTeardown = null;
  };
}

/** Apply the v1.7 conditional teardown before an analysis sidecar starts. */
export function preparePlayerForAnalysis(
  targetPath: string | null,
  force: boolean,
): void {
  analysisTeardown?.(targetPath, force);
}

/** Register the mounted player's reset → force-read → rebuild metadata chain. */
export function registerMetadataRefresh(
  refresh: (editedPaths: readonly string[]) => Promise<void>,
): () => void {
  metadataRefresh = refresh;
  return () => {
    if (metadataRefresh === refresh) metadataRefresh = null;
  };
}

/** Refresh only when the currently loaded preview was changed by a metadata batch. */
export async function syncPlayerAfterMetadataMutation(
  editedPaths: readonly string[],
): Promise<void> {
  if (!playerState.loadedTrackPath || !editedPaths.includes(playerState.loadedTrackPath)) return;
  await metadataRefresh?.(editedPaths);
}

export function usePlayerState() {
  return {
    ...toRefs(playerState),
    /** Replace the whole marker list + stage in one write (§4.2 step 4). */
    setMarkers(markers: PlayerMarker[], stage: MarkerStage): void {
      playerState.markers = markers;
      playerState.markerStage = stage;
    },
    /** Reset to the empty pre-load state — used on track change / unmount. */
    reset(): void {
      playerState.loadedTrackPath = null;
      playerState.activeDirectKeys = [];
      playerState.activeAdjacentKeys = [];
      playerState.metadata = null;
      playerState.metadataError = null;
      playerState.markers = [];
      playerState.markerStage = null;
    },
    /** Set the loaded-track path + metadata (Stage 1 success). */
    setLoadedMetadata(path: string, meta: TrackMetadata, key: string | null = null): void {
      playerState.loadedTrackPath = path;
      const matches = getHarmonicMatches(key);
      playerState.activeDirectKeys = matches.direct;
      playerState.activeAdjacentKeys = matches.adjacent;
      playerState.metadata = meta;
      playerState.metadataError = null;
    },
    /** Set a non-fatal metadata error (Stage 1 failure — no waveform). */
    setMetadataError(path: string, err: TrackMetadataError): void {
      playerState.loadedTrackPath = path;
      playerState.activeDirectKeys = [];
      playerState.activeAdjacentKeys = [];
      playerState.metadata = null;
      playerState.metadataError = err;
      playerState.markers = [];
      playerState.markerStage = null;
    },
    /** §3.7 — shared concurrency lock. Set true at the start of a track
     * load, false on the first terminal event (ready / metadata error /
     * decode error). Read by LibraryBrowser.vue to inert row clicks. */
    setLoadingTrack(value: boolean): void {
      playerState.isLoadingTrack = value;
    },
  };
}

// ---------------------------------------------------------------------------
// §1.1 / §4.1 — core execution via Rust resources bridge
// ---------------------------------------------------------------------------

export interface FetchTrackMetadataResult {
  ok: boolean;
  /** Present when `ok === true`. */
  metadata?: TrackMetadata;
  /** Present when `ok === false` and the failure was a modeled §1.4 error. */
  error?: TrackMetadataError;
  /** Present when `ok === false` and the failure was an I/O or parse fault. */
  fault?: string;
}

/**
 * Executes `cuegrid-core --get-track-metadata <trackPath>` via Rust resources bridge.
 *
 * The exit code and output follow the final source of truth (2-core-spec.md §11.6):
 * success schema (§1.3) or error schema (§1.4). Both shapes travel on stdout.
 */
 export async function fetchTrackMetadata(
   trackPath: string,
 ): Promise<FetchTrackMetadataResult> {
   const cached = playerState.previewCache.get(trackPath);
   if (cached) return { ok: true, metadata: cached };
 
   const { callCueGridCore } = useCueGridSidecar();
   const args = ["--get-track-metadata", trackPath];
 
   try {
     // Invocamos el puente genérico. Pedimos 'any' porque Rust puede devolver string o el objeto.
     const rawStdout = await callCueGridCore(args);
     
     let parsed: any;
 
     if (typeof rawStdout === "string") {
       // 1. Limpiamos cualquier warning de consola que haya antes/después del JSON
       const firstBrace = rawStdout.indexOf('{');
       const lastBrace = rawStdout.lastIndexOf('}');
 
       if (firstBrace === -1 || lastBrace === -1) {
         return {
           ok: false,
           fault: "Core resource exited and returned no JSON output.",
         };
       }
 
       const cleanStr = rawStdout.substring(firstBrace, lastBrace + 1);
 
       try {
         parsed = JSON.parse(cleanStr);
         // 2. Si Rust devolvió un JSON doblemente serializado ("{\"artist..."), 
         // el primer parse devuelve un string literal. Hacemos un segundo parse automático.
         if (typeof parsed === "string") {
           parsed = JSON.parse(parsed);
         }
       } catch (err) {
         return {
           ok: false,
           fault: `Failed to parse core stdout as JSON: ${String(err)}`,
         };
       }
     } else {
       // Si el puente de Tauri ya lo ha parseado nativamente en Rust
       parsed = rawStdout; 
     }
 
     if (isTrackMetadataError(parsed)) {
       return { ok: false, error: parsed };
     }
 
     // 3. ACTUALIZACIÓN: Verificamos el nuevo esquema de 3 Bandas (l, m, h) en el color_map
     if (
       typeof parsed.artist !== "string" ||
       typeof parsed.title !== "string" ||
       typeof parsed.bpm !== "number" ||
       typeof parsed.grid_anchor_ms !== "number" ||
       !Array.isArray(parsed.existing_cues) ||
       !Array.isArray(parsed.waveform_peaks) ||
       !parsed.waveform_peaks.every((value: any) => typeof value === "number" && Number.isFinite(value)) ||
       !Array.isArray(parsed.color_map) ||
       !parsed.color_map.every(
         (bucket: any) =>
           bucket !== null &&
           typeof bucket === "object" &&
           typeof bucket.l === "number" && Number.isFinite(bucket.l) &&
           typeof bucket.m === "number" && Number.isFinite(bucket.m) &&
           typeof bucket.h === "number" && Number.isFinite(bucket.h)
       )
     ) {
       return {
         ok: false,
         fault: "Core returned a success-shaped object missing required fields (check l, m, h).",
       };
     }
 
     return { ok: true, metadata: parsed as TrackMetadata };
 
   } catch (err) {
     return {
       ok: false,
       fault: `Core execution bridge error: ${typeof err === "string" ? err : String(err)}`,
     };
   }
 }
