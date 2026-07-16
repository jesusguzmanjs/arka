import { computed, nextTick, reactive, toRefs } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { useConfigState } from "./useConfigState";
import { useCueGridSidecar } from "./useCueGridSidecar";
import type {
  LibraryPayload,
  LibraryTrack,
  PlaylistLeaf,
  PlaylistNode,
  TrackMetadata,
} from "../types/library";

export const ALL_TRACKS_CONTEXT = "ALL_TRACKS";

interface LibraryState {
  collection: Record<string, TrackMetadata>;
  playlists: PlaylistNode[];
  selectedContext: string;
  libraryLoading: boolean;
  libraryError: string | null;
}

const state = reactive<LibraryState>({
  collection: {},
  playlists: [],
  selectedContext: ALL_TRACKS_CONTEXT,
  libraryLoading: false,
  libraryError: null,
});

let loadToken = 0;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

// En useLibraryState.ts, añade esta función por encima del return:

function patchTrackInCollection(path: string, updates: Partial<TrackMetadata>) {
  if (state.collection[path]) {
    // Actualizamos solo las propiedades que nos pasen (ej: bpm)
    Object.assign(state.collection[path], updates);
  }
}

function isNullableFiniteNumber(value: unknown): value is number | null {
  return value === null || (typeof value === "number" && Number.isFinite(value));
}

function isTrackMetadata(value: unknown): value is TrackMetadata {
  if (!isRecord(value)) return false;

  return (
    typeof value.artist === "string" &&
    typeof value.title === "string" &&
    typeof value.location_path === "string" &&
    isNullableFiniteNumber(value.bpm) &&
    isNullableFiniteNumber(value.grid_anchor_ms) &&
    isNullableFiniteNumber(value.duration_ms) &&
    typeof value.is_flex_grid === "boolean" &&
    Array.isArray(value.existing_cues) &&
    typeof value.collection_index === "number" &&
    Number.isInteger(value.collection_index)
  );
}

function isPlaylistNode(value: unknown): value is PlaylistNode {
  if (!isRecord(value) || typeof value.kind !== "string" || typeof value.name !== "string") {
    return false;
  }

  if (value.kind === "folder") {
    return Array.isArray(value.children) && value.children.every(isPlaylistNode);
  }

  return value.kind === "playlist" &&
    Array.isArray(value.track_paths) &&
    value.track_paths.every((path) => typeof path === "string");
}

function isLibraryPayload(value: unknown): value is LibraryPayload {
  if (!isRecord(value) || !isRecord(value.collection) || !Array.isArray(value.playlists)) {
    return false;
  }

  return (
    Object.entries(value.collection).every(([path, track]) =>
      typeof path === "string" && isTrackMetadata(track),
    ) && value.playlists.every(isPlaylistNode)
  );
}

function findPlaylist(nodes: readonly PlaylistNode[], name: string): PlaylistLeaf | undefined {
  for (const node of nodes) {
    if (node.kind === "playlist" && node.name === name) return node;
    if (node.kind === "folder") {
      const match = findPlaylist(node.children, name);
      if (match) return match;
    }
  }
  return undefined;
}

function flattenPlaylists(nodes: readonly PlaylistNode[]): PlaylistLeaf[] {
  return nodes.flatMap((node) =>
    node.kind === "playlist" ? [node] : flattenPlaylists(node.children),
  );
}

function parseLibraryPayload(raw: unknown): LibraryPayload {
  const parsed = typeof raw === "string" ? JSON.parse(raw) as unknown : raw;
  if (!isLibraryPayload(parsed)) {
    throw new Error("Sidecar returned an invalid --get-library payload.");
  }

  return parsed;
}

export function useLibraryState() {
  const { update, selectedTrackPath } = useConfigState();
  const { nmlPathOverride } = useCueGridSidecar();

  const currentViewTracks = computed<TrackMetadata[]>(() => {
    if (state.selectedContext === ALL_TRACKS_CONTEXT) {
      return Object.values(state.collection).sort(
        (a, b) => a.collection_index - b.collection_index,
      );
    }

    const playlist = findPlaylist(state.playlists, state.selectedContext);
    if (!playlist) return [];

    return playlist.track_paths
      .map((path) => state.collection[path])
      .filter((track): track is TrackMetadata => track !== undefined);
  });

  const playlistLeaves = computed(() => flattenPlaylists(state.playlists));

  async function loadLibrary(): Promise<void> {
    const myToken = ++loadToken;
    state.libraryLoading = true;
    state.libraryError = null;

    const args = ["--get-library"];
    if (nmlPathOverride.value) args.push("--nml", nmlPathOverride.value);

    try {
      const raw = await invoke<string>("call_cuegrid_core", { args });
      const payload = parseLibraryPayload(raw);

      if (myToken !== loadToken) return;

      const selectedPlaylist = findPlaylist(payload.playlists, state.selectedContext);
      const selectedPath = selectedTrackPath.value;
      const canKeepSelectedTrack = selectedPath !== null &&
        Object.prototype.hasOwnProperty.call(payload.collection, selectedPath);

      state.collection = payload.collection;
      state.playlists = payload.playlists;

      if (state.selectedContext !== ALL_TRACKS_CONTEXT && !selectedPlaylist) {
        state.selectedContext = ALL_TRACKS_CONTEXT;
        update("selectedPlaylist", null);
      }

      if (!canKeepSelectedTrack) update("selectedTrackPath", null);
    } catch (error) {
      if (myToken !== loadToken) return;
      state.collection = {};
      state.playlists = [];
      state.selectedContext = ALL_TRACKS_CONTEXT;
      update("selectedPlaylist", null);
      update("selectedTrackPath", null);
      state.libraryError = error instanceof Error ? error.message : String(error);
    } finally {
      if (myToken === loadToken) state.libraryLoading = false;
    }
  }

  function selectContext(context: string): void {
    if (context !== ALL_TRACKS_CONTEXT && !findPlaylist(state.playlists, context)) return;

    state.selectedContext = context;
    update("selectedPlaylist", context === ALL_TRACKS_CONTEXT ? null : context);
    update("selectedTrackPath", null);
  }

  function selectPlaylist(name: string): void {
    selectContext(name);
  }

  function selectTrackForPreview(track: LibraryTrack): void {
    const path = track.location_path;

    if (selectedTrackPath.value === path) {
      update("selectedTrackPath", null);
      void nextTick(() => {
        if (selectedTrackPath.value === null) update("selectedTrackPath", path);
      });
      return;
    }

    update("selectedTrackPath", path);
  }

  return {
    ...toRefs(state),
    currentViewTracks,
    playlistLeaves,
    patchTrackInCollection,
    loadLibrary,
    selectContext,
    selectPlaylist,
    selectTrackForPreview,
  };
}
