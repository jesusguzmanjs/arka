import type { CollectionTrack } from "../types/library.ts";

export const MISSING_BPM_GRID_MESSAGE =
  "This track lacks BPM/Grid data in the collection. Please analyze or run Check Consistency in Traktor.";

/** True when the collection cannot provide the BPM/Grid data required by grid-based tools. */
export function isUnanalyzedTrack(
  track: Pick<CollectionTrack, "bpm"> | null | undefined,
): boolean {
  return !track || typeof track.bpm !== "number" || !Number.isFinite(track.bpm) || track.bpm <= 0;
}
