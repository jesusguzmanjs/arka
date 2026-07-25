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

export interface RunProgress {
  current: number;
  total: number;
}

interface RunState {
  status: RunStatus;
  isSystemBusy: boolean;
  awaitingPlayerLoad: boolean;
  analysisStatus: string | null;
  logs: LogEntry[]; // append-only for the current run
  startedAt: number | null;
  summary: RunSummary | null; // set once a "summary" message arrives
  progress: RunProgress | null;
  currentPid: number | null; // for cancellation (§6.6)
}

const state = reactive<RunState>({
  status: "idle",
  isSystemBusy: false,
  awaitingPlayerLoad: false,
  analysisStatus: null,
  logs: [],
  startedAt: null,
  summary: null,
  progress: null,
  currentPid: null,
});

let playerLoadTimeout: ReturnType<typeof setTimeout> | null = null;

function clearPlayerLoadTimeout(): void {
  if (playerLoadTimeout === null) return;
  clearTimeout(playerLoadTimeout);
  playerLoadTimeout = null;
}

function releaseSystemBusy(): void {
  clearPlayerLoadTimeout();
  state.awaitingPlayerLoad = false;
  state.isSystemBusy = false;
}

function handleMessage(msg: SidecarMessage): void {
  if (msg.type === "track_start") {
    state.progress = { current: msg.index, total: msg.total };
  }
  state.logs.push({ ts: Date.now(), msg });
}

export function clearSummary(): void {
  state.summary = null;
  state.analysisStatus = null;
}

export function useRunState() {
  return {
    ...toRefs(state),
    /** Clear logs and mark a run as started. */
    startRun: () => {
      releaseSystemBusy();
      state.logs = [];
      state.analysisStatus = null;
      state.summary = null;
      state.progress = null;
      state.startedAt = Date.now();
      state.status = "running";
      state.isSystemBusy = true;
    },
    /** Update the user-facing, reactive analysis status text. */
    setAnalysisStatus: (message: string | null) => {
      state.analysisStatus = message;
    },
    clearSummary,
    /** Update run state and append a sidecar message with a client timestamp. */
    handleMessage,
    pushLog: handleMessage,
    /** Mark the run complete with an optional summary. */
    finishRun: (status: "success" | "error" | "cancelled", summary?: RunSummary) => {
      state.status = status;
      state.summary = summary ?? state.summary;
      state.progress = null;
      state.currentPid = null;
      if (status !== "success") releaseSystemBusy();
    },
    /** Hold the global interaction lock while an auto-loaded player mounts. */
    awaitPlayerLoad: () => {
      clearPlayerLoadTimeout();
      state.awaitingPlayerLoad = true;
      state.isSystemBusy = true;
      playerLoadTimeout = setTimeout(releaseSystemBusy, 5_000);
    },
    /** Release a single-track auto-load lock after the player reaches a terminal state. */
    completePlayerLoad: () => {
      if (state.awaitingPlayerLoad) releaseSystemBusy();
    },
    releaseSystemBusy,
    /** Empty the console without changing status. */
    clearLogs: () => {
      state.logs = [];
    },
    /** Reset back to idle (used by AutoCuePanel's reset flow). */
    reset: () => {
      releaseSystemBusy();
      state.status = "idle";
      state.analysisStatus = null;
      state.logs = [];
      state.summary = null;
      state.progress = null;
      state.startedAt = null;
      state.currentPid = null;
    },
    /** Read-only view of the whole slice, for components that only read. */
    readonly: () => readonly(state),
  };
}
