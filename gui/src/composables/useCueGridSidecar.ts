// composables/useCueGridSidecar.ts
// See .openspec/3-gui-spec.md §6.4, §6.6, §6.7 and .openspec/4-library-spec.md §2.5.
//
// Spawns the packaged Python core via Rust Resource architecture,
// streams its NDJSON stdout into useRunState in real-time via Tauri Events,
// and exposes cancellation via Rust process tracking.

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { save } from "@tauri-apps/plugin-dialog";
import { useConfigState } from "./useConfigState";
import { useRunState, type RunSummary } from "./useRunState";
import { useAnalysisSession } from "./useAnalysisSession";
import { preparePlayerForAnalysis } from "./useTrackMetadata";
import type { CueGridConfig } from "../types/config";
import type { SidecarMessage } from "../types/sidecar";

export interface CuePointPayload {
  name: string;
  type: 0;
  start_ms: number;
  len_ms: 0.0;
  repeats: -1;
  hotcue: number;
  displ_order: 0;
}

/**
 * Builds the argv array for the sidecar directly from CueGridConfig.
 */
function buildArgs(cfg: CueGridConfig, target: "track" | "playlist"): string[] {
  const args: string[] = [];
  if (target === "track") {
    args.push(cfg.selectedTrackPath!);
  } else {
    args.push("--playlist", cfg.selectedPlaylist!);
  }
  if (cfg.nmlPathOverride) args.push("--nml", cfg.nmlPathOverride);
  args.push("--mode", cfg.sensitivity);
  args.push("--max-cues", String(cfg.maxCues));
  if (cfg.clearExisting) args.push("--clear-existing");
  args.push("--verbose");
  args.push("--json"); // §6.5 — switches core's main() to NDJSON stdout
  return args;
}

// Almacena los des-registradores de eventos para limpiarlos al cerrar el proceso
let unlistens: UnlistenFn[] = [];

function cleanupListeners() {
  unlistens.forEach((u) => u());
  unlistens = [];
}

export function useCueGridSidecar() {
  const {
    selectedPlaylist,
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
      sensitivity: sensitivity.value,
      maxCues: maxCues.value,
      clearExisting: clearExisting.value,
      nmlPathOverride: nmlPathOverride.value,
    };

    cleanupListeners();
    let lastSummary: RunSummary | null = null;

    // 1. Escuchar el STDOUT de Rust en tiempo real
    const unlistenStdout = await listen<string>("analysis-stdout", (event) => {
      const line = event.payload.trim();
      if (!line) return;
      try {
        const msg = JSON.parse(line) as SidecarMessage;
        if (msg.type === "summary") {
          lastSummary = { total: msg.total, succeeded: msg.succeeded, skipped: msg.skipped };
        }
        pushLog(msg);
      } catch {
        pushLog({ type: "log", level: "error", message: line });
      }
    });
    unlistens.push(unlistenStdout);

    // 2. Escuchar el STDERR de Rust
    const unlistenStderr = await listen<string>("analysis-stderr", (event) => {
      const text = event.payload.trim();
      if (!text) return;
      pushLog({ type: "log", level: "error", message: text });
    });
    unlistens.push(unlistenStderr);

    // 3. Escuchar el evento de cierre del proceso
    const unlistenClose = await listen<number | null>("analysis-close", (event) => {
      cleanupListeners();
      const code = event.payload;
      const succeeded = code === 0;

      if (succeeded) {
        captureRun(logSnapshot());
        setAnalysisStatus(`${successLabel} analyzed successfully`);
      }
      finishRun(succeeded ? "success" : "error", lastSummary ?? undefined);
    });
    unlistens.push(unlistenClose);

    // 4. Arrancar el motor en Rust
    try {
      startRun();
      currentPid.value = 99999; // Mock PID (la cancelación ahora va por backend)
      await invoke("start_analysis_stream", { args: buildArgs(cfg, target) });
    } catch (err) {
      cleanupListeners();
      pushLog({ type: "log", level: "error", message: `Failed to start stream: ${String(err)}` });
      finishRun("error");
    }
  }

  /** Snapshot of the current run's full NDJSON log, for captureRun(). */
  function logSnapshot(): SidecarMessage[] {
    return useRunState().logs.value.map((l) => l.msg);
  }

  /**
   * Actualiza los cues en lote usando el puente genérico call_cuegrid_core (one-shot).
   */
  async function updateTrackCues(
    trackPath: string,
    cues: CuePointPayload[],
    newGridAnchorMs?: number,
    newBpm?: number,
  ): Promise<{ ok: boolean; error?: string }> {
    const args = [trackPath, "--update-cues", JSON.stringify(cues)];
    if (newGridAnchorMs !== undefined) args.push("--grid-anchor", String(newGridAnchorMs));
    if (newBpm !== undefined) args.push("--bpm", String(newBpm));
    if (nmlPathOverride.value) args.push("--nml", nmlPathOverride.value);

    try {
      await invoke("call_cuegrid_core", { args });
      return { ok: true };
    } catch (error) {
      return { ok: false, error: String(error) };
    }
  }

  /**
   * Elimina un cue específico usando el puente genérico call_cuegrid_core (one-shot).
   */
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

    try {
      await invoke("call_cuegrid_core", { args });
      return { ok: true };
    } catch (error) {
      return { ok: false, error: String(error) };
    }
  }

  /**
   * Auto-descubre el NML por defecto usando el puente genérico call_cuegrid_core (one-shot).
   */
  async function discoverAndSetDefaultNml(): Promise<void> {
    if (nmlPathOverride.value) return;

    try {
      const stdoutText = await invoke<string>("call_cuegrid_core", { args: ["--discover-nml"] });
      if (stdoutText.trim()) {
        const result = JSON.parse(stdoutText);
        if (result.path) {
          nmlPathOverride.value = result.path;
          console.log("[Boot] Auto-descubierto NML por defecto:", result.path);
        }
      }
    } catch (e) {
      console.error("Error parseando el path del NML:", e);
    }
  }

  /**
   * Cancela el análisis matando de raíz el proceso en Rust.
   */
  async function cancel(): Promise<void> {
    try {
      await invoke("cancel_analysis");
    } catch {
      // Ignorar fallos si el proceso ya había muerto solo
    } finally {
      cleanupListeners();
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
    useRunState().reset();
  }

  return { run, runSingleTrack, deleteCue, cancel, exportTelemetry, resetRun, updateTrackCues, discoverAndSetDefaultNml, nmlPathOverride };
}
