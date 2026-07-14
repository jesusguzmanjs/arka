// composables/useLibraryState.ts
// See .openspec/4-library-spec.md §3.3.
//
// Module-scoped singleton state for the Library Browser: the playlists
// list (populated on boot via --list-playlists), the tracklist of the
// currently-selected playlist (populated on click via
// --get-playlist-tracks), and the loading/error flags each fetch owns.
//
// Mirrors useTrackMetadata.ts's spawn-buffer-parse-on-close pattern for
// both one-shot sidecar calls (NOT the NDJSON streaming pattern of
// useCueGridSidecar.ts). State mutations write through useConfigState()
// for `selectedPlaylist` / `selectedTrackPath` so the rest of the app
// (ActionBar, AudioPlayer) reacts to the same singleton source of truth.

import { nextTick, reactive, toRefs } from "vue";
import { invoke } from "@tauri-apps/api/core"; // Swapeado por la API nativa de invocación
import { useConfigState } from "./useConfigState";
import { useCueGridSidecar } from "./useCueGridSidecar";
import {
  type LibraryTrack,
  type PlaylistTracksResult,
  isPlaylistTracksError,
} from "../types/library";

interface LibraryState {
  playlists: string[];
  playlistsLoading: boolean;
  tracks: LibraryTrack[];
  tracksLoading: boolean;
  tracksError: string | null; // human-readable, already unwrapped from PlaylistTracksError
}

const state = reactive<LibraryState>({
  playlists: [],
  playlistsLoading: false,
  tracks: [],
  tracksLoading: false,
  tracksError: null,
});

// Stale-response guard, matching AudioPlayer.vue's `stage1Token` pattern
// (3-player-spec.md's runStage1 implementation) — a rapid second click
// on a different playlist must not let the first click's late-arriving
// response clobber the second's once-correct result.
let selectionToken = 0;

export function useLibraryState() {
  const { update, selectedTrackPath } = useConfigState();
  const { nmlPathOverride } = useCueGridSidecar();

  /**
   * Invokes `call_cuegrid_core` with `["--list-playlists"]` via Rust resource resolving.
   * Populates `state.playlists`. Relocated verbatim from TargetSelector.vue's former
   * onMounted hook (4-library-spec.md §3.1).
   */
  async function loadPlaylists(): Promise<void> {
    state.playlistsLoading = true;

    const args = ["--list-playlists"];
    if (nmlPathOverride.value) {
      args.push("--nml", nmlPathOverride.value);
    }

    try {
      // Llamamos al puente nativo de Rust pasando los argumentos estructurados
      const raw = await invoke<string>("call_cuegrid_core", { args });
      const trimmed = raw.trim();

      if (!trimmed) {
        state.playlists = [];
        state.playlistsLoading = false;
        return;
      }

      const parsed = JSON.parse(trimmed);
      if (Array.isArray(parsed) && parsed.every((p) => typeof p === "string")) {
        state.playlists = parsed;
      } else {
        console.error("[--list-playlists] unexpected JSON shape:", parsed);
        state.playlists = [];
      }
    } catch (err) {
      console.error("[--list-playlists] run or parse error:", err);
      state.playlists = [];
    } finally {
      state.playlistsLoading = false;
    }
  }

  /**
   * Invokes `call_cuegrid_core` with `["--get-playlist-tracks", name]` via Rust resource
   * resolving. On a modeled error (§1.4) the message is surfaced via `state.tracksError`;
   * on success `state.tracks` is replaced wholesale.
   *
   * §2.4 clearing rule: also clears `selectedTrackPath` unconditionally
   * so a stale preview from a previous playlist can't survive the switch.
   */
  async function selectPlaylist(name: string): Promise<void> {
    const myToken = ++selectionToken;
    update("selectedPlaylist", name);
    update("selectedTrackPath", null); // §2.4 clearing rule — unconditional
    state.tracksLoading = true;
    state.tracksError = null;

    const args = ["--get-playlist-tracks", name];
    if (nmlPathOverride.value) {
      args.push("--nml", nmlPathOverride.value);
    }

    try {
      const raw = await invoke<string>("call_cuegrid_core", { args });

      // Stale response — another playlist was clicked while we were waiting.
      if (myToken !== selectionToken) {
        return;
      }

      const trimmed = raw.trim();
      if (!trimmed) {
        state.tracks = [];
        state.tracksError = "Sidecar returned no output for this playlist.";
        state.tracksLoading = false;
        return;
      }

      const parsed = JSON.parse(trimmed) as PlaylistTracksResult;
      if (isPlaylistTracksError(parsed)) {
        state.tracks = [];
        state.tracksError = parsed.message;
      } else {
        state.tracks = parsed;
        state.tracksError = null;
      }
    } catch (err) {
      if (myToken !== selectionToken) {
        return;
      }
      console.error("[--get-playlist-tracks] execution error:", err);
      state.tracks = [];
      state.tracksError = typeof err === "string" ? err : String(err);
    } finally {
      if (myToken === selectionToken) {
        state.tracksLoading = false;
      }
    }
  }

  /**
   * §4.1 — double-click bridge: writes the track's location_path into
   * `useConfigState().selectedTrackPath`, which AudioPlayer.vue watches
   * to fire Stage 1 sync. Does NOT touch the batch target.
   */
  function selectTrackForPreview(track: LibraryTrack): void {
    const path = track.location_path;

    // Re-selecting the same path must still reload the player. A direct
    // same-value assignment does not trigger AudioPlayer's watcher, so force
    // a null -> path transition. Guard the next-tick write so a newer click
    // cannot be overwritten by this older reload request.
    if (selectedTrackPath.value === path) {
      update("selectedTrackPath", null);
      void nextTick(() => {
        if (selectedTrackPath.value === null) {
          update("selectedTrackPath", path);
        }
      });
      return;
    }

    update("selectedTrackPath", path);
  }

  return {
    ...toRefs(state),
    loadPlaylists,
    selectPlaylist,
    selectTrackForPreview,
  };
}
