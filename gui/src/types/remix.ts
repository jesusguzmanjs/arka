/** Playback behavior configured for an individual Remix Deck pad. */
export type PadPlayType = "loop" | "one-shot";
export type PadTriggerMode = "trigger" | "gate";

/** The 16 fixed pad colors used by Traktor's Remix Decks. */
export const TRAKTOR_COLORS = [
  "#FC4A47", "#FB7D4B", "#F89F2A", "#FCDE1C",
  "#FBFB1A", "#ABFC1C", "#5CFB5C", "#1AE8AA",
  "#1AE8E9", "#1CD0FB", "#579DFC", "#8D7BFB",
  "#BB77FB", "#DF68FB", "#FB5BEE", "#FC5189",
] as const;

/**
 * Pad behavior that can change without altering the source audio or its timing.
 */
export interface PadSettings {
  id: string;
  name: string;
  color: string;
  playType: PadPlayType;
  triggerMode: PadTriggerMode;
  sync: boolean;
  reverse: boolean;
  keylock: boolean;
  /** Normalized pad gain: 0 is neutral, -1 is -24 dB, and 1 is +12 dB. */
  volume: number;
  filter: number;
  /** Pitch offset in semitones applied by Tone.GrainPlayer detune. */
  transpose?: number;
  /** Whether the pad audio plays in reverse without modifying its source file. */
  isReversed?: boolean;
  /** Trimmed playback start, in seconds. */
  loopStart?: number;
  /** Trimmed playback end, in seconds. Null means the source duration. */
  loopEnd?: number | null;
}

/**
 * Source-audio facts and transformations used by the future audio engine.
 */
export interface PadAudioData {
  filePath: string;
  /** True when this WAV was rendered locally for the pad and can be safely removed. */
  isGenerated?: boolean;
  durationMs: number;
  originalBpm: number;
  /** Musical key of the source track, when available. */
  originalKey?: string;
  gridAnchorMs: number;
  startMs: number;
  endMs: number;
  pitchShift: number;
}

/** Result returned after extracting a selected loop for a Remix Deck pad. */
export interface PadExtractionResult {
  file_path: string;
  duration_ms: number;
}

/** Read-only Remix Set payload returned by CueGrid's ``--get-remix-set`` command. */
export interface RemixSetPayload {
  title: string;
  bpm: number;
  quantize_state: number;
  quantize_value: number;
  columns: Array<{
    keylock: number;
    punchmode: number;
    fxenable: number;
  }>;
  pads: Array<{
    id: string;
    name: string;
    path: string;
    color_id: number;
    sync: number;
    reverse: number;
    mode: number;
    type: number;
    transpose: number;
    gain: number;
    start_ms: number;
    end_ms: number;
    bpm: number;
    key?: string;
    duration_ms: number;
  }>;
}

/** A Remix Deck pad combines independently managed behavior and audio data. */
export interface RemixPadData {
  settings: PadSettings;
  audio: PadAudioData | null;
}
