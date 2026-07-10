// composables/useTrackMetadata.ts
// See .openspec/3-player-spec.md §1 (Core Extension), §4.1 (Stage 1), §6 (Data Structures).
//
// Spawns the packaged Python core as a Tauri sidecar (binaries/cuegrid-core) with
// the `--get-track-metadata <TRACK_PATH>` flag, buffers its one-shot JSON
// stdout line, parses it on process close, and updates a module-scoped
// singleton `usePlayerState` reactive store. Mirrors the spawn-buffer-parse
// pattern already used by TargetSelector.vue's `--list-playlists` call
// (3-gui-spec.md §3.3), NOT the NDJSON streaming pattern of
// useCueGridSidecar.ts.
//
// This file also owns the §6 `usePlayerState` singleton and the §1.3/§1.4
// TypeScript type definitions, since the spec's §3.2 file layout splits them
// into `usePlayerState.ts` and `types/trackMetadata.ts` but the player's
// internal state is private to this composable's consumers (§3.3) — keeping
// them co-located avoids a premature split that no other module reads.

import { reactive, toRefs } from "vue";
import { Command } from "@tauri-apps/plugin-shell";

// ---------------------------------------------------------------------------
// §1.3 / §1.4 / §6 — Data Structures (TypeScript)
// ---------------------------------------------------------------------------

/** §6 — mirrors §1.3's `type` serialization (name, not int value). */
export type CueTypeName = "CUE" | "FADE_IN" | "FADE_OUT" | "LOAD" | "LOOP";

/** §1.3 — a single pre-existing HotCue, GRID excluded upstream by the core. */
export interface ExistingCue {
  hotcue: number; // -1 = unbound (no pad)
  name: string;
  start_ms: number;
  type: CueTypeName;
}

/** §1.3 — success schema, single JSON line on stdout. */
export interface TrackMetadata {
  artist: string;
  title: string;
  bpm: number;
  grid_anchor_ms: number;
  existing_cues: ExistingCue[];
}

/** §1.4 — error schema, flat two-key object on the same stdout channel. */
export interface TrackMetadataError {
  error: "not_found" | "ambiguous";
  message: string;
}

export type TrackMetadataResult = TrackMetadata | TrackMetadataError;

/** §6 — single-key discriminator, exactly as specified. */
export function isTrackMetadataError(
  r: TrackMetadataResult,
): r is TrackMetadataError {
  return "error" in r;
}

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
  metadata: TrackMetadata | null;
  metadataError: TrackMetadataError | null;
  markers: PlayerMarker[];
  markerStage: MarkerStage | null;
  isLoadingTrack: boolean; // §3.7 — shared concurrency lock, read by LibraryBrowser.vue too
}

const playerState = reactive<PlayerState>({
  loadedTrackPath: null,
  metadata: null,
  metadataError: null,
  markers: [],
  markerStage: null,
  isLoadingTrack: false,
});

/**
 * Module-scoped singleton for the player's private state. No other component
 * reads this in Phase 3 (§3.3); it exists so AudioPlayer.vue's Stage 1 / Stage
 * 2 handlers share one source of truth and so a re-render never resurrects
 * stale Stage 1 muted markers underneath Stage 2's active ones (§4.2 step 4).
 */
let analysisTeardown: ((targetPath: string | null, force: boolean) => void) | null = null;

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
      playerState.metadata = null;
      playerState.metadataError = null;
      playerState.markers = [];
      playerState.markerStage = null;
    },
    /** Set the loaded-track path + metadata (Stage 1 success). */
    setLoadedMetadata(path: string, meta: TrackMetadata): void {
      playerState.loadedTrackPath = path;
      playerState.metadata = meta;
      playerState.metadataError = null;
    },
    /** Set a non-fatal metadata error (Stage 1 failure — no waveform). */
    setMetadataError(path: string, err: TrackMetadataError): void {
      playerState.loadedTrackPath = path;
      playerState.metadata = null;
      playerState.metadataError = err;
      playerState.markers = [];
      playerState.markerStage = null;
    },
    /** §3.7 — shared concurrency lock. Set true at the start of a track
     *  load, false on the first terminal event (ready / metadata error /
     *  decode error). Read by LibraryBrowser.vue to inert row clicks. */
    setLoadingTrack(value: boolean): void {
      playerState.isLoadingTrack = value;
    },
  };
}

// ---------------------------------------------------------------------------
// §1.1 / §4.1 — sidecar spawn + buffer + parse-on-close
// ---------------------------------------------------------------------------

const SIDECAR_NAME = "binaries/cuegrid-core";

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
 * Spawn `binaries/cuegrid-core --get-track-metadata <trackPath>`, buffer stdout,
 * and parse the single JSON line emitted on process close (§1.2, §4.1).
 *
 * The exit code is the final source of truth (2-core-spec.md §11.6): `0` →
 * success schema (§1.3), `1` → error schema (§1.4). Both shapes travel on
 * stdout, so this function only needs the close event — no stderr code path.
 *
 * Does NOT touch `usePlayerState` itself; the caller (AudioPlayer.vue) owns
 * the reactive update so it can interleave the `convertFileSrc` + wavesurfer
 * load with the metadata write. Returns a discriminated result object so the
 * caller can branch without try/catch.
 */
export async function fetchTrackMetadata(
  trackPath: string,
): Promise<FetchTrackMetadataResult> {
  const command = Command.sidecar(SIDECAR_NAME, [
    "--get-track-metadata",
    trackPath,
  ]);

  // §1.2 — exactly one JSON line on stdout; buffer the whole stream and parse
  // on close, exactly like TargetSelector.vue's --list-playlists consumption.
  let buffer = "";

  command.stdout.on("data", (chunk: string) => {
    buffer += chunk;
  });

  // §1.2 routes the modeled error case through stdout, not stderr — so stderr
  // only ever carries non-JSON noise (a stray traceback that bypassed the
  // modeled path, a third-party warning). Capture it for diagnostics but do
  // not let it drive the result.
  let stderrText = "";
  command.stderr.on("data", (chunk: string) => {
    stderrText += chunk;
  });

  return new Promise<FetchTrackMetadataResult>((resolve) => {
    command.on("close", (data: { code: number | null }) => {
      const raw = buffer.trim();
      const code = data.code;

      // No stdout at all — process failed before printing the modeled line.
      if (raw.length === 0) {
        resolve({
          ok: false,
          fault:
            stderrText.trim() ||
            `Sidecar exited with code ${code ?? "null"} and no stdout.`,
        });
        return;
      }

      let parsed: TrackMetadataResult;
      try {
        parsed = JSON.parse(raw) as TrackMetadataResult;
      } catch (err) {
        resolve({
          ok: false,
          fault: `Failed to parse sidecar stdout as JSON: ${String(err)}`,
        });
        return;
      }

      // §1.4 — error schema is a flat two-key object; the exit code is `1`
      // but we trust the JSON shape over the code (the spec's `"error" in obj`
      // check is the documented discriminator).
      if (isTrackMetadataError(parsed)) {
        resolve({ ok: false, error: parsed });
        return;
      }

      // §1.3 — success schema. Sanity-check the required fields so a malformed
      // core build doesn't silently feed `undefined` into the waveform code.
      if (
        typeof parsed.artist !== "string" ||
        typeof parsed.title !== "string" ||
        typeof parsed.bpm !== "number" ||
        typeof parsed.grid_anchor_ms !== "number" ||
        !Array.isArray(parsed.existing_cues)
      ) {
        resolve({
          ok: false,
          fault: "Sidecar returned a success-shaped object missing required fields.",
        });
        return;
      }

      resolve({ ok: true, metadata: parsed });
    });

    command.on("error", (err: string) => {
      resolve({ ok: false, fault: `Sidecar spawn error: ${err}` });
    });

    // Spawn the process. If spawn() itself rejects (e.g. sidecar binary
    // missing), resolve with a fault — the close handler won't fire.
    command.spawn().catch((err: unknown) => {
      resolve({
        ok: false,
        fault: `Failed to spawn sidecar: ${String(err)}`,
      });
    });
  });
}
