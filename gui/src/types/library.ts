import type { ExistingCue } from "./trackMetadata";

/** Metadata owned by the relational collection map. */
export interface TrackMetadata {
  artist: string;
  title: string;
  location_path: string;
  bpm: number | null;
  grid_anchor_ms: number | null;
  duration_ms: number | null;
  is_flex_grid: boolean;
  existing_cues: ExistingCue[];
  collection_index: number;
  /** Optional legacy NML flags field used only for the Stems badge. */
  flags?: number | null;
}

export interface PlaylistFolder {
  kind: "folder";
  name: string;
  children: PlaylistNode[];
}

export interface PlaylistLeaf {
  kind: "playlist";
  name: string;
  track_paths: string[];
}

export type PlaylistNode = PlaylistFolder | PlaylistLeaf;

export interface LibraryPayload {
  collection: Record<string, TrackMetadata>;
  playlists: PlaylistNode[];
}

// Kept as a source-compatible name for existing table/player consumers.
export type LibraryTrack = TrackMetadata;

export interface PlaylistTracksError {
  error: "not_found" | "ambiguous";
  message: string;
}

export type PlaylistTracksResult = LibraryTrack[] | PlaylistTracksError;

export function isPlaylistTracksError(
  r: PlaylistTracksResult,
): r is PlaylistTracksError {
  return !Array.isArray(r);
}
