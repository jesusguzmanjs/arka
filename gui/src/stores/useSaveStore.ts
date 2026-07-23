import { defineStore } from "pinia";
import { useCueGridSidecar } from "../composables/useCueGridSidecar";
import { useLibraryState } from "../composables/useLibraryState";
import { syncPlayerAfterMetadataMutation } from "../composables/useTrackMetadata";
import type { CollectionTrack, MetadataPatch } from "../types/library";

let activeSave: Promise<void> | null = null;

export const useSaveStore = defineStore("save", {
  state: () => ({
    isSaving: false,
    modifiedTracks: new Set<string>(),
    modifiedPlaylists: new Set<string>(),
    writeMetadataToFiles: false,
  }),

  getters: {
    isDirty: (state) => state.modifiedTracks.size > 0 || state.modifiedPlaylists.size > 0,
  },

  actions: {
    markTrackDirty(path: string): void {
      this.modifiedTracks.add(path);
    },

    markPlaylistDirty(id: string): void {
      this.modifiedPlaylists.add(id);
    },

    clearDirtyState(): void {
      this.modifiedTracks.clear();
      this.modifiedPlaylists.clear();
      this.writeMetadataToFiles = false;
    },

    setWriteMetadataToFiles(value: boolean): void {
      this.writeMetadataToFiles = value;
    },

    async saveAll(): Promise<void> {
      if (activeSave) return activeSave;

      activeSave = (async () => {
        this.isSaving = true;
        try {
          const { collection, playlistLeaves, loadLibrary } = useLibraryState();
          const paths = [...this.modifiedTracks];
          const tracks = paths.map((path) => {
            const track = collection.value[path];
            if (!track) throw new Error(`Cannot save track missing from the collection: ${path}`);
            return serializeTrack(track);
          });
          const playlists = [...this.modifiedPlaylists].map((uuid) => {
            const playlist = playlistLeaves.value.find((candidate) => candidate.uuid === uuid);
            return playlist
              ? { uuid, action: "update" as const, name: playlist.name, entries: playlist.track_paths }
              : { uuid, action: "delete" as const };
          });
          const result = await useCueGridSidecar().batchSave(
            { tracks, playlists },
            this.writeMetadataToFiles,
          );
          if (!result.ok) throw new Error(result.error ?? "Batch save failed.");
          await loadLibrary();
          await syncPlayerAfterMetadataMutation(paths);
          this.clearDirtyState();
        } finally {
          this.isSaving = false;
          activeSave = null;
        }
      })();

      return activeSave;
    },
  },
});

function serializeTrack(track: CollectionTrack): {
  path: string;
  cues: { hotcue: number; start_ms: number }[];
  grid_anchor_ms?: number;
  bpm?: number;
  metadata: MetadataPatch;
} {
  return {
    path: track.location_path,
    cues: track.existing_cues
      .filter((cue) => cue.type === "CUE" && Number.isInteger(cue.hotcue))
      .map((cue) => ({ hotcue: Number(cue.hotcue), start_ms: Number(cue.start_ms) })),
    ...(track.grid_anchor_ms === null ? {} : { grid_anchor_ms: track.grid_anchor_ms }),
    ...(track.bpm === null ? {} : { bpm: track.bpm }),
    metadata: {
      title: track.title,
      release: track.album,
      artist: track.artist,
      remixer: track.remixer,
      producer: track.producer,
      genre: track.genre,
      label: track.label,
      comment: track.comment,
      comment2: track.comment2,
      lyrics: track.lyrics,
      mix: track.mix,
      rating: track.rating,
    },
  };
}
