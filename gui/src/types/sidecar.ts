// types/sidecar.ts
// See .openspec/3-gui-spec.md §6.5
// Discriminated union of messages emitted by the Python core's --json mode.

export type SidecarMessage =
  | { type: "log"; level: "info" | "warning" | "error"; message: string }
  | { type: "nml_resolved"; path: string }
  | { type: "track_start"; index: number; total: number; artist: string; title: string }
  | {
      type: "event_detected";
      label: string;
      time_ms: number;
      confidence: number;
      is_major_phrase: boolean;
    }
  | { type: "cue_written"; hotcue: number; name: string; start_ms: number }
  | {
      type: "track_complete";
      artist: string;
      title: string;
      event_count: number;
      cue_count: number;
      error: string | null;
    }
  | { type: "summary"; total: number; succeeded: number; skipped: number }
  | { type: "fatal_error"; message: string };

// A log entry as stored in useRunState: the original message plus a
// client-side timestamp for the console gutter (the core does not emit
// timestamps; the spec's example console shows HH:MM:SS prefixed lines).
export interface LogEntry {
  ts: number; // epoch ms
  msg: SidecarMessage;
}
