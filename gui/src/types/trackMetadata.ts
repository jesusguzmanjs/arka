export type CueTypeName = "CUE" | "FADE_IN" | "FADE_OUT" | "LOAD" | "LOOP";

export interface ExistingCue {
  hotcue: number;
  name: string;
  start_ms: number;
  type: CueTypeName;
}

export interface ColorMapBucket {
  l: number;
  m: number;
  h: number;
}

export interface SuperJSON {
  artist: string;
  title: string;
  bpm: number;
  grid_anchor_ms: number;
  existing_cues: ExistingCue[];
  waveform_peaks: number[];
  color_map: ColorMapBucket[];
}

export type TrackMetadata = SuperJSON;

export interface TrackMetadataError {
  error: "not_found" | "ambiguous" | "preview_failed";
  message: string;
}

export type TrackMetadataResult = TrackMetadata | TrackMetadataError;

export function isTrackMetadataError(
  result: TrackMetadataResult,
): result is TrackMetadataError {
  return "error" in result;
}
