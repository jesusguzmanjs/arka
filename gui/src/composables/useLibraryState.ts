import { computed, nextTick, reactive, toRefs } from "vue";
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
  selectedLibraryPaths: string[];
  libraryLoading: boolean;
  libraryError: string | null;
}

const state = reactive<LibraryState>({
  collection: {},
  playlists: [],
  selectedContext: ALL_TRACKS_CONTEXT,
  selectedLibraryPaths: [],
  libraryLoading: false,
  libraryError: null,
});

let loadToken = 0;
let lastSelectedLibraryPath: string | null = null;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function patchTrackInCollection(path: string, updates: Partial<TrackMetadata>) {
  if (state.collection[path]) {
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
      typeof value.album === "string" &&
      typeof value.remixer === "string" &&
      typeof value.producer === "string" &&
      typeof value.genre === "string" &&
      typeof value.label === "string" &&
      typeof value.comment === "string" &&
      typeof value.comment2 === "string" &&
      typeof value.lyrics === "string" &&
      typeof value.mix === "string" &&
      typeof value.rating === "number" &&
      Number.isInteger(value.rating) && value.rating >= 0 && value.rating <= 5 &&
      typeof value.location_path === "string" &&
      isNullableFiniteNumber(value.bpm) &&
      isNullableFiniteNumber(value.grid_anchor_ms) &&
      (value.key === null || typeof value.key === "string") &&
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
      typeof value.uuid === "string" && value.uuid.length > 0 &&
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

function findPlaylistByUuid(nodes: readonly PlaylistNode[], uuid: string): PlaylistLeaf | undefined {
  for (const node of nodes) {
    if (node.kind === "playlist" && node.uuid === uuid) return node;
    if (node.kind === "folder") {
      const match = findPlaylistByUuid(node.children, uuid);
      if (match) return match;
    }
  }
  return undefined;
}

function removePlaylistByUuid(nodes: PlaylistNode[], uuid: string): PlaylistLeaf | undefined {
  for (let index = 0; index < nodes.length; index += 1) {
    const node = nodes[index];
    if (node.kind === "playlist" && node.uuid === uuid) {
      return nodes.splice(index, 1)[0] as PlaylistLeaf;
    }
    if (node.kind === "folder") {
      const removed = removePlaylistByUuid(node.children, uuid);
      if (removed) return removed;
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
  const { callCueGridCore } = useCueGridSidecar();

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

    try {
      const raw = await callCueGridCore(args);
      const payload = parseLibraryPayload(raw);

      if (myToken !== loadToken) return;

      const selectedPlaylist = findPlaylist(payload.playlists, state.selectedContext);
      const selectedPath = selectedTrackPath.value;
      const canKeepSelectedTrack = selectedPath !== null &&
          Object.prototype.hasOwnProperty.call(payload.collection, selectedPath);

      state.collection = payload.collection;
      state.playlists = payload.playlists;
      state.selectedLibraryPaths = state.selectedLibraryPaths.filter((path) =>
          Object.prototype.hasOwnProperty.call(payload.collection, path),
      );

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
      state.selectedLibraryPaths = [];
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
    state.selectedLibraryPaths = [];
    lastSelectedLibraryPath = null;
    update("selectedPlaylist", context === ALL_TRACKS_CONTEXT ? null : context);
  }

  function selectPlaylist(name: string): void {
    selectContext(name);
  }

  function updatePlaylist(uuid: string, updates: Pick<PlaylistLeaf, "name" | "track_paths">): boolean {
    const playlist = findPlaylistByUuid(state.playlists, uuid);
    if (!playlist) return false;
    const previousName = playlist.name;
    playlist.name = updates.name;
    playlist.track_paths = updates.track_paths;
    if (state.selectedContext === previousName) {
      state.selectedContext = updates.name;
      update("selectedPlaylist", updates.name);
    }
    return true;
  }

  function deletePlaylist(uuid: string): PlaylistLeaf | undefined {
    const removed = removePlaylistByUuid(state.playlists, uuid);
    if (removed && state.selectedContext === removed.name) {
      selectContext(ALL_TRACKS_CONTEXT);
    }
    return removed;
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

  function selectLibraryTrack(
      track: LibraryTrack,
      event: Pick<MouseEvent, "ctrlKey" | "metaKey" | "shiftKey">,
      visibleTracks: readonly LibraryTrack[],
  ): void {
    const path = track.location_path;
    const isMultiSelectModifier = event.ctrlKey || event.metaKey;

    if (event.shiftKey && lastSelectedLibraryPath) {
      // 1. SHIFT + CLIC: Selección de rango
      const anchorIndex = visibleTracks.findIndex(
          (candidate) => candidate.location_path === lastSelectedLibraryPath
      );
      const targetIndex = visibleTracks.findIndex((candidate) => candidate.location_path === path);

      if (anchorIndex !== -1 && targetIndex !== -1) {
        const [start, end] = [anchorIndex, targetIndex].sort((a, b) => a - b);
        const rangePaths = visibleTracks
            .slice(start, end + 1)
            .map((candidate) => candidate.location_path);

        if (isMultiSelectModifier) {
          // Mantener lo anterior y añadir el rango (Ctrl + Shift + Clic)
          const combined = new Set([...state.selectedLibraryPaths, ...rangePaths]);
          state.selectedLibraryPaths = Array.from(combined);
        } else {
          // Reemplazar la selección solo con el rango (Shift + Clic normal)
          state.selectedLibraryPaths = rangePaths;
        }
        return; // No actualizamos lastSelectedLibraryPath para que sirva de pivote si sigues haciendo shift+clic
      }
    } else if (isMultiSelectModifier) {
      // 2. CTRL/CMD + CLIC: Alternar (Toggle) selección individual
      const pathIndex = state.selectedLibraryPaths.indexOf(path);
      if (pathIndex === -1) {
        state.selectedLibraryPaths = [...state.selectedLibraryPaths, path];
      } else {
        const newSelected = [...state.selectedLibraryPaths];
        newSelected.splice(pathIndex, 1);
        state.selectedLibraryPaths = newSelected;
      }
      lastSelectedLibraryPath = path;
    } else {
      // 3. CLIC NORMAL: Limpia el resto y selecciona solo este
      state.selectedLibraryPaths = [path];
      lastSelectedLibraryPath = path;
    }
  }

  function selectOnlyLibraryTrack(track: LibraryTrack): void {
    state.selectedLibraryPaths = [track.location_path];
    lastSelectedLibraryPath = track.location_path;
  }

  function selectAllLibraryTracks(tracks: readonly LibraryTrack[]): void {
    state.selectedLibraryPaths = tracks.map((track) => track.location_path);
    lastSelectedLibraryPath = state.selectedLibraryPaths[state.selectedLibraryPaths.length - 1] ?? null;
  }

  return {
    ...toRefs(state),
    currentViewTracks,
    playlistLeaves,
    patchTrackInCollection,
    loadLibrary,
    selectContext,
    selectPlaylist,
    updatePlaylist,
    deletePlaylist,
    selectLibraryTrack,
    selectOnlyLibraryTrack,
    selectAllLibraryTracks,
    selectTrackForPreview,
  };
}
