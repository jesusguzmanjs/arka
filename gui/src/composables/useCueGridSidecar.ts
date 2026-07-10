// composables/useCueGridSidecar.ts
// See .openspec/3-gui-spec.md §6.4, §6.6, §6.7 and .openspec/4-library-spec.md §2.5.
//
// Spawns the packaged Python core as a Tauri sidecar process
// (binaries/cuegrid), streams its NDJSON stdout into useRunState in
// real-time, and exposes cancellation via the spawned Child handle.
//
// Process boundary: UI -> Command.sidecar -> OS process -> NDJSON on
// stdout -> parsed SidecarMessage -> useRunState -> UI (§6.1).

import { invoke } from "@tauri-apps/api/core";
import { save } from "@tauri-apps/plugin-dialog";
import { Command, type Child } from "@tauri-apps/plugin-shell";
import { useConfigState } from "./useConfigState";
import { useRunState, type RunSummary } from "./useRunState";
import { useAnalysisSession } from "./useAnalysisSession";
import { preparePlayerForAnalysis } from "./useTrackMetadata";
import type { CueGridConfig } from "../types/config";
import type { SidecarMessage } from "../types/sidecar";

const SIDECAR_NAME = "binaries/cuegrid";

/**
 * Builds the argv array for the sidecar directly from CueGridConfig.
 *
 * The target selector is intentionally explicit: a single-track run passes
 * one absolute path directly, while the main-panel run passes a playlist.
 */
function buildArgs(cfg: CueGridConfig, target: "track" | "playlist"): string[] {
  const args: string[] = [];
  if (target === "track") {
    args.push(cfg.selectedTrackPath!);
  } else {
    args.push("--playlist", cfg.selectedPlaylist!);
  }
  if (cfg.nmlPathOverride) args.push("--nml", cfg.nmlPathOverride);
  if (!cfg.includeStems) args.push("--no-stems");
  args.push("--mode", cfg.sensitivity);
  args.push("--max-cues", String(cfg.maxCues));
  if (cfg.clearExisting) args.push("--clear-existing");
  args.push("--json"); // §6.5 — switches core's main() to NDJSON stdout
  return args;
}

// Holds the in-flight sidecar's Child handle so cancel() can kill it.
// Module-scoped (not component-scoped) to match useRunState's singleton
// pattern — only one run is ever in flight at a time.
let currentChild: Child | null = null;

export function useCueGridSidecar() {
  const {
    selectedPlaylist,
    includeStems,
    sensitivity,
    maxCues,
    clearExisting,
    nmlPathOverride,
    isValid,
  } = useConfigState();
  const {
    startRun,
    pushLog,
    finishRun,
    reset,
    currentPid,
    setAnalysisStatus,
  } = useRunState();
  const { clearSession, captureRun } = useAnalysisSession();

  async function run(): Promise<void> {
    if (!isValid.value) return;
    await runAnalysis("playlist", selectedPlaylist.value!, selectedPlaylist.value!);
  }

  async function runSingleTrack(trackPath: string, trackTitle: string): Promise<void> {
    if (!trackPath) return;
    await runAnalysis("track", trackPath, trackTitle);
  }

  async function runAnalysis(
    target: "track" | "playlist",
    targetValue: string,
    successLabel: string,
  ): Promise<void> {
    clearSession();
    preparePlayerForAnalysis(target === "track" ? targetValue : null, target === "playlist");

    const cfg: CueGridConfig = {
      selectedPlaylist: target === "playlist" ? targetValue : null,
      selectedTrackPath: target === "track" ? targetValue : null,
      includeStems: includeStems.value,
      sensitivity: sensitivity.value,
      maxCues: maxCues.value,
      clearExisting: clearExisting.value,
      nmlPathOverride: nmlPathOverride.value,
    };

    startRun();

    const command = Command.sidecar(SIDECAR_NAME, buildArgs(cfg, target));

    // NDJSON line buffering (§6.6): `data` events deliver arbitrary
    // chunks, not lines, so we must buffer and split on "\n", carrying
    // over any partial trailing line to the next chunk.
    let buffer = "";
    let lastSummary: RunSummary | null = null;

    command.stdout.on("data", (chunk: string) => {
      buffer += chunk;
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        handleLine(line);
      }
    });

    command.stderr.on("data", (chunk: string) => {
      // stderr is not NDJSON — Python tracebacks and low-level warnings
      // land here regardless of --json mode, so it is always routed as
      // an error log rather than parsed.
      const text = chunk.trim();
      if (!text) return;
      pushLog({ type: "log", level: "error", message: text });
    });

    command.on("error", (error: string) => {
      pushLog({ type: "log", level: "error", message: String(error) });
    });

    command.on("close", (data: { code: number | null }) => {
      currentChild = null;
      const succeeded = data.code === 0;
      // §4.3 — capture the run's cue_written entries into the session map
      // only on success (never on error/cancelled, matching §4.2's "nothing
      // new was actually written to disk" gating). captureRun reads the full
      // log snapshot from useRunState, so it must run before any reset.
      if (succeeded) {
        captureRun(logSnapshot());
      }
      if (succeeded) {
        setAnalysisStatus(`${successLabel} analyzed successfully`);
      }
      finishRun(succeeded ? "success" : "error", lastSummary ?? undefined);
    });

    function handleLine(rawLine: string) {
      const line = rawLine.trim();
      if (!line) return;
      try {
        const msg = JSON.parse(line) as SidecarMessage;
        if (msg.type === "summary") {
          lastSummary = { total: msg.total, succeeded: msg.succeeded, skipped: msg.skipped };
        }
        pushLog(msg);
      } catch {
        // Non-JSON stdout (stray print(), a traceback that bypassed
        // --json mode, a third-party warning) is not fatal — surface it
        // as an error-level log rather than crashing the parser or
        // silently dropping it (§6.6).
        pushLog({ type: "log", level: "error", message: line });
      }
    }

    try {
      currentChild = await command.spawn();
      currentPid.value = currentChild.pid;
    } catch (err) {
      currentChild = null;
      pushLog({ type: "log", level: "error", message: `Failed to spawn sidecar: ${String(err)}` });
      finishRun("error");
    }
  }

  /** Snapshot of the current run's full NDJSON log, for captureRun(). */
  function logSnapshot(): SidecarMessage[] {
    // Read from useRunState's reactive logs array. We re-invoke useRunState
    // here (it's a module singleton, so this returns the same state) to
    // avoid threading the logs ref through the closure above.
    return useRunState().logs.value.map((l) => l.msg);
  }

  async function deleteCue(
    trackPath: string,
    hotcueIndex: number,
    title?: string,
    artist?: string,
  ): Promise<{ ok: boolean; error?: string }> {
    const args = [trackPath, "--delete-cue", String(hotcueIndex)];
    if (nmlPathOverride.value) args.push("--nml", nmlPathOverride.value);
    if (title) args.push("--title", title);
    if (artist) args.push("--artist", artist);

    const command = Command.sidecar(SIDECAR_NAME, args);
    let stderrText = "";
    let settled = false;

    return new Promise((resolve) => {
      const finish = (result: { ok: boolean; error?: string }) => {
        if (settled) return;
        settled = true;
        resolve(result);
      };

      command.stderr.on("data", (chunk: string) => {
        stderrText += chunk;
      });
      command.on("error", (error: string) => {
        finish({ ok: false, error: `Failed to run delete sidecar: ${String(error)}` });
      });
      command.on("close", (data: { code: number | null }) => {
        if (data.code === 0) {
          finish({ ok: true });
        } else {
          finish({
            ok: false,
            error:
              stderrText.trim() ||
              `Delete sidecar exited with code ${data.code ?? "null"}.`,
          });
        }
      });

      command.spawn().catch((error: unknown) => {
        finish({ ok: false, error: `Failed to spawn delete sidecar: ${String(error)}` });
      });
    });
  }

  async function cancel(): Promise<void> {
    if (!currentChild) return;
    try {
      await currentChild.kill();
    } catch {
      // Process may have already exited; nothing further to do.
    } finally {
      currentChild = null;
      finishRun("cancelled");
    }
  }

  async function exportTelemetry(): Promise<void> {
    const destination = await save({
      defaultPath: "cuegrid_telemetry.csv",
      filters: [{ name: "CSV files", extensions: ["csv"] }],
    });
    if (destination === null) return;

    await invoke("export_last_run_telemetry", { destination });
  }

  function resetRun(): void {
    reset();
  }

  return { run, runSingleTrack, deleteCue, cancel, exportTelemetry, resetRun };
}
