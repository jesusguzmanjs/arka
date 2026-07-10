// types/library.ts
// See .openspec/4-library-spec.md §1.3/§1.4 — mirrors the core's
// `--get-playlist-tracks` one-shot JSON schema exactly, including the
// source NML flags used to render native Stem availability badges.

export interface LibraryTrack {
  artist: string;
  title: string;
  location_path: string;
  flags: number | null;
}

export interface PlaylistTracksError {
  error: "not_found" | "ambiguous";
  message: string;
}

export type PlaylistTracksResult = LibraryTrack[] | PlaylistTracksError;

/**
 * §1.4's discriminator: success is always a bare array, error is always
 * a flat object — Array.isArray() alone is sufficient and preferred
 * over an "error" in obj check (which would also be correct, but the
 * array/object distinction is the more direct fit for this flag's
 * schema, unlike --get-track-metadata's object/object schema).
 */
export function isPlaylistTracksError(
  r: PlaylistTracksResult,
): r is PlaylistTracksError {
  return !Array.isArray(r);
}
