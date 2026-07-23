import type { ExistingCue } from "./trackMetadata";

/** Metadata owned by the relational collection map. */
export interface CollectionTrack {
  artist: string;
  title: string;
  /** Traktor's release/album title. */
  album: string;
  remixer: string;
  producer: string;
  genre: string;
  label: string;
  comment: string;
  comment2: string;
  lyrics: string;
  mix: string;
  /** Normalized Traktor rating from 0 through 5. */
  rating: number;
  location_path: string;
  bpm: number | null;
  grid_anchor_ms: number | null;
  key: string | null;
  duration_ms: number | null;
  is_flex_grid: boolean;
  existing_cues: ExistingCue[];
  collection_index: number;
  /** Optional legacy NML flags field used only for the Stems badge. */
  flags?: number | null;
}

// Retained for source compatibility with existing library consumers.
export type TrackMetadata = CollectionTrack;

export interface PlaylistFolder {
  kind: "folder";
  name: string;
  children: PlaylistNode[];
}

export interface PlaylistLeaf {
  kind: "playlist";
  /** Stable NML PLAYLIST@UUID used for batch mutations. */
  uuid: string;
  name: string;
  track_paths: string[];
}

export type PlaylistNode = PlaylistFolder | PlaylistLeaf;

export interface LibraryPayload {
  collection: Record<string, CollectionTrack>;
  playlists: PlaylistNode[];
}

// Kept as a source-compatible name for existing table/player consumers.
export type LibraryTrack = CollectionTrack;

export type EditableMetadataField =
  | "title"
  | "release"
  | "artist"
  | "remixer"
  | "producer"
  | "genre"
  | "label"
  | "comment"
  | "comment2"
  | "lyrics"
  | "mix"
  | "rating";

export type MetadataPatch = Partial<Record<EditableMetadataField, string | number | null>>;

/** UI-level batch payload: one explicit partial patch for every target track. */
export interface MetadataUpdateItem {
  location_path: string;
  metadata: MetadataPatch;
}

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
