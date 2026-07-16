// types/config.ts
// See .openspec/4-library-spec.md §2.2 (revises 3-gui-spec.md §5.2).
//
// Phase 4 deprecates the manual TargetSelector target-type model entirely.
// The old `targetType` / `trackPath` / `trackTitle` / `playlistName` /
// `artist` / `title` fields are removed; the GUI now drives the sidecar
// exclusively in playlist-batch mode, with an optional single-track
// preview driven by `selectedTrackPath` (never sent to the sidecar).

export type Sensitivity = "soft" | "medium" | "hard";

export interface CueGridConfig {
  selectedPlaylist: string | null; // the playlist currently active in LibraryBrowser's left column (batch target)
  selectedTrackPath: string | null; // location_path of the track double-clicked in the right column (preview target only)
  sensitivity: Sensitivity;
  maxCues: number;
  clearExisting: boolean;
  nmlPathOverride: string | null; // advanced/optional; null = auto-discover — unchanged from §5.2
}

export const defaultConfig: CueGridConfig = {
  selectedPlaylist: null,
  selectedTrackPath: null,
  sensitivity: "medium",
  maxCues: 8,
  clearExisting: false,
  nmlPathOverride: null,
};
