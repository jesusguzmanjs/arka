// composables/useRunState.ts
// See .openspec/3-gui-spec.md §5.4
//
// Module-scoped singleton for run/telemetry state. `logs` is cleared at
// the start of each run (not across runs) so the console always reflects
// the most recent invocation.

import { reactive, readonly, toRefs } from "vue";
import type { LogEntry, SidecarMessage } from "../types/sidecar";

export type RunStatus = "idle" | "running" | "success" | "error" | "cancelled";

export interface RunSummary {
  total: number;
  succeeded: number;
  skipped: number;
}

interface RunState {
  status: RunStatus;
  analysisStatus: string | null;
  logs: LogEntry[]; // append-only for the current run
  startedAt: number | null;
  summary: RunSummary | null; // set once a "summary" message arrives
  currentPid: number | null; // for cancellation (§6.6)
}

const state = reactive<RunState>({
  status: "idle",
  analysisStatus: null,
  logs: [],
  startedAt: null,
  summary: null,
  currentPid: null,
});

export function useRunState() {
  return {
    ...toRefs(state),
    /** Clear logs and mark a run as started. */
    startRun: () => {
      state.logs = [];
      state.analysisStatus = null;
      state.summary = null;
      state.startedAt = Date.now();
      state.status = "running";
    },
    /** Update the user-facing, reactive analysis status text. */
    setAnalysisStatus: (message: string | null) => {
      state.analysisStatus = message;
    },
    /** Append a sidecar message to the console with a client timestamp. */
    pushLog: (msg: SidecarMessage) => {
      state.logs.push({ ts: Date.now(), msg });
    },
    /** Mark the run complete with an optional summary. */
    finishRun: (status: "success" | "error" | "cancelled", summary?: RunSummary) => {
      state.status = status;
      state.summary = summary ?? state.summary;
      state.currentPid = null;
    },
    /** Empty the console without changing status. */
    clearLogs: () => {
      state.logs = [];
    },
    /** Reset back to idle (used by ActionBar's "Reset" affordance). */
    reset: () => {
      state.status = "idle";
      state.analysisStatus = null;
      state.logs = [];
      state.summary = null;
      state.startedAt = null;
      state.currentPid = null;
    },
    /** Read-only view of the whole slice, for components that only read. */
    readonly: () => readonly(state),
  };
}
