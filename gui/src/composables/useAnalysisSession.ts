// composables/useAnalysisSession.ts
// See .openspec/3-player-spec.md §4.3 (Session-Scoped Persistence Across
// Track Previews), §6 (Data Structures — AnalysisSessionState).
//
// Module-scoped singleton, structurally mirroring useRunState.ts. Tracks
// which artist/title pairs were touched by the *latest completed* run,
// decoupled from useRunState().logs (which the Telemetry Console's "Clear"
// action may wipe independently, §4.3 point 3).
//
// The map holds the cue_written entries grouped per track (keyed by
// `${artist}::${title}`) from the most recent *successful* run. It is wiped
// unconditionally at the very top of every new "Analyze & Inject" run
// (useCueGridSidecar.ts, §4.3 step 2), before startRun() — so every click
// discards the previous run's tracking regardless of what is previewed.

import { reactive, toRefs } from "vue";
import type { ExistingCue } from "./useTrackMetadata";
import type { SidecarMessage } from "../types/sidecar";

export interface AnalysisSessionState {
  tracks: Map<string, ExistingCue[]>; // key: `${artist}::${title}`
}

const state = reactive<AnalysisSessionState>({
  tracks: new Map(),
});

/** Build the lookup key used by the session map. */
function sessionKey(artist: string, title: string): string {
  return `${artist}::${title}`;
}

export function useAnalysisSession() {
  return {
    ...toRefs(state),

    /**
     * Empty the map. Called unconditionally at the very top of every new
     * run (useCueGridSidecar.ts), before startRun() — i.e. before any
     * NDJSON message from the new run can possibly arrive.
     */
    clearSession(): void {
      state.tracks = new Map();
    },

    /**
     * Scan the just-finished run's full NDJSON log in order and group every
     * `cue_written` message under its enclosing `track_start`/`track_complete`
     * pair's `artist`/`title` key, replacing the map wholesale. Called
     * exactly once, only on the `"running" → "success"` edge (never on
     * `"error"`/`"cancelled"`).
     *
     * A `track_start` with no matching `track_complete` (process killed
     * mid-track) is dropped — only completed tracks are captured, matching
     * the "nothing new was actually written to disk" gating of §4.2.
     */
    captureRun(logs: SidecarMessage[]): void {
      const map = new Map<string, ExistingCue[]>();
      let currentKey: string | null = null;
      let currentCues: ExistingCue[] = [];

      for (const m of logs) {
        if (m.type === "track_start") {
          currentKey = sessionKey(m.artist, m.title);
          currentCues = [];
          continue;
        }
        if (m.type === "track_complete") {
          if (currentKey !== null) {
            // Sort ascending by start_ms, matching Stage 1's ordering.
            currentCues.sort((a, b) => a.start_ms - b.start_ms);
            map.set(currentKey, currentCues);
          }
          currentKey = null;
          currentCues = [];
          continue;
        }
        if (m.type === "cue_written" && currentKey !== null) {
          // The core only ever writes CueType.CUE (2-core-spec.md §3.4).
          currentCues.push({
            hotcue: m.hotcue,
            name: m.name,
            start_ms: m.start_ms,
            type: "CUE",
          });
        }
      }

      state.tracks = map;
    },

    /** Look up a track's session cues by artist + title. */
    getTrackCues(artist: string, title: string): ExistingCue[] | undefined {
      return state.tracks.get(sessionKey(artist, title));
    },

    /** Whether a track was part of the latest completed run. */
    hasTrack(artist: string, title: string): boolean {
      return state.tracks.has(sessionKey(artist, title));
    },
  };
}
