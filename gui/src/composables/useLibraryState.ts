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
import { Command } from "@tauri-apps/plugin-shell";
import { useConfigState } from "./useConfigState";
import {
  type LibraryTrack,
  type PlaylistTracksResult,
  isPlaylistTracksError,
} from "../types/library";

const SIDECAR_NAME = "binaries/cuegrid";

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

  /**
   * Spawn `binaries/cuegrid --list-playlists`, buffer stdout, and parse
   * the single JSON array line emitted on process close (2-core-spec.md
   * §12.3). Populates `state.playlists`. Relocated verbatim from
   * TargetSelector.vue's former onMounted hook (4-library-spec.md §3.1).
   */
  async function loadPlaylists(): Promise<void> {
    state.playlistsLoading = true;
    const command = Command.sidecar(SIDECAR_NAME, ["--list-playlists"]);
    let buffer = "";
    let stderrText = "";

    command.stdout.on("data", (chunk: string) => {
      buffer += chunk;
    });
    command.stderr.on("data", (chunk: string) => {
      stderrText += chunk;
    });

    return new Promise<void>((resolve) => {
      command.on("close", () => {
        const raw = buffer.trim();
        if (!raw) {
          if (stderrText.trim()) {
            console.error("[--list-playlists] stderr:", stderrText.trim());
          }
          state.playlists = [];
          state.playlistsLoading = false;
          resolve();
          return;
        }
        try {
          const parsed = JSON.parse(raw);
          if (Array.isArray(parsed) && parsed.every((p) => typeof p === "string")) {
            state.playlists = parsed;
          } else {
            console.error("[--list-playlists] unexpected JSON shape:", parsed);
            state.playlists = [];
          }
        } catch (err) {
          console.error("[--list-playlists] failed to parse stdout:", err, raw);
          state.playlists = [];
        }
        state.playlistsLoading = false;
        resolve();
      });

      command.on("error", (err: string) => {
        console.error("[--list-playlists] spawn error:", err);
        state.playlists = [];
        state.playlistsLoading = false;
        resolve();
      });

      command.spawn().catch((err: unknown) => {
        console.error("[--list-playlists] failed to spawn sidecar:", err);
        state.playlists = [];
        state.playlistsLoading = false;
        resolve();
      });
    });
  }

  /**
   * Spawn `binaries/cuegrid --get-playlist-tracks <name>`, buffer stdout,
   * and parse the single JSON line emitted on process close
   * (4-library-spec.md §1.2). On a modeled error (§1.4) the message is
   * surfaced via `state.tracksError`; on success `state.tracks` is
   * replaced wholesale.
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

    const command = Command.sidecar(SIDECAR_NAME, [
      "--get-playlist-tracks",
      name,
    ]);
    let buffer = "";
    let stderrText = "";

    command.stdout.on("data", (chunk: string) => {
      buffer += chunk;
    });
    command.stderr.on("data", (chunk: string) => {
      stderrText += chunk;
    });

    return new Promise<void>((resolve) => {
      command.on("close", () => {
        // Stale response — another playlist was clicked while we were waiting.
        if (myToken !== selectionToken) {
          resolve();
          return;
        }

        const raw = buffer.trim();
        if (!raw) {
          state.tracks = [];
          state.tracksError =
            stderrText.trim() ||
            "Sidecar returned no output for this playlist.";
          state.tracksLoading = false;
          resolve();
          return;
        }

        let parsed: PlaylistTracksResult;
        try {
          parsed = JSON.parse(raw) as PlaylistTracksResult;
        } catch (err) {
          state.tracks = [];
          state.tracksError = `Failed to parse sidecar output: ${String(err)}`;
          state.tracksLoading = false;
          resolve();
          return;
        }

        if (isPlaylistTracksError(parsed)) {
          state.tracks = [];
          state.tracksError = parsed.message;
        } else {
          state.tracks = parsed;
          state.tracksError = null;
        }
        state.tracksLoading = false;
        resolve();
      });

      command.on("error", (err: string) => {
        if (myToken !== selectionToken) {
          resolve();
          return;
        }
        state.tracks = [];
        state.tracksError = `Sidecar spawn error: ${err}`;
        state.tracksLoading = false;
        resolve();
      });

      command.spawn().catch((err: unknown) => {
        if (myToken !== selectionToken) {
          resolve();
          return;
        }
        state.tracks = [];
        state.tracksError = `Failed to spawn sidecar: ${String(err)}`;
        state.tracksLoading = false;
        resolve();
      });
    });
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
