// composables/useCueGridSidecar.ts
// See .openspec/3-gui-spec.md §6.4, §6.6, §6.7 and .openspec/4-library-spec.md §2.5.
//
// Spawns the packaged Python core via Rust Resource architecture,
// streams its NDJSON stdout into useRunState in real-time via Tauri Events,
// and exposes cancellation via Rust process tracking.

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { save } from "@tauri-apps/plugin-dialog";
import { ref, watch } from "vue";
import { useConfigState } from "./useConfigState";
import { useRunState, type RunSummary } from "./useRunState";
import { useAnalysisSession } from "./useAnalysisSession";
import { preparePlayerForAnalysis } from "./useTrackMetadata";
import type { CueGridConfig } from "../types/config";
import type { SidecarMessage } from "../types/sidecar";
import type {
  SmartPlaylistCompileResult,
  SmartPlaylistPayload,
} from "../types/smartPlaylist";

/**
 * Builds the argv array for the sidecar directly from CueGridConfig.
 */
function buildArgs(
  cfg: CueGridConfig,
  target: "track" | "playlist",
  selectedLibraryPaths: readonly string[] = [],
): string[] {
  const args: string[] = [];
  if (target === "track") {
    args.push(...(selectedLibraryPaths.length > 0 ? selectedLibraryPaths : [cfg.selectedTrackPath!]));
  } else {
    args.push("--playlist", cfg.selectedPlaylist!);
  }
  args.push("--mode", cfg.sensitivity);
  args.push("--max-cues", String(cfg.maxCues));
  if (cfg.clearExisting) args.push("--clear-existing");
  args.push("--verbose");
  args.push("--json"); // §6.5 — switches core's main() to NDJSON stdout
  return args;
}

// Almacena los des-registradores de eventos para limpiarlos al cerrar el proceso
let unlistens: UnlistenFn[] = [];
let resolveActiveRun: ((succeeded: boolean) => void) | null = null;
const analyzedTrackPaths = ref<readonly string[]>([]);

const { selectedTrackPath } = useConfigState();
const { status, awaitPlayerLoad, releaseSystemBusy } = useRunState();

watch(status, (nextStatus, previousStatus) => {
  if (
    previousStatus === "running"
    && nextStatus === "success"
    && analyzedTrackPaths.value.length === 1
  ) {
    const analyzedTrackPath = analyzedTrackPaths.value[0];
    if (selectedTrackPath.value === analyzedTrackPath) {
      releaseSystemBusy();
    } else {
      awaitPlayerLoad();
      selectedTrackPath.value = analyzedTrackPath;
    }
  } else if (nextStatus === "success") {
    releaseSystemBusy();
  }

  if (nextStatus !== "running") analyzedTrackPaths.value = [];
});

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
    handleMessage,
    finishRun,
    currentPid,
    setAnalysisStatus,
  } = useRunState();
  const { clearSession, captureRun } = useAnalysisSession();

  function callCueGridCore(args: string[]): Promise<string> {
    return invoke<string>("call_cuegrid_core", {
      args,
      nmlPath: nmlPathOverride.value,
    });
  }

  async function run(): Promise<void> {
    if (!isValid.value) return;
    await runAnalysis("playlist", selectedPlaylist.value!, selectedPlaylist.value!, []);
  }

  async function runSingleTrack(trackPath: string, trackTitle: string): Promise<void> {
    if (!trackPath) return;
    await runAnalysis("track", trackPath, trackTitle, [trackPath]);
  }

  async function runSelectedTracks(
    tracks: readonly Pick<{ location_path: string; title: string }, "location_path" | "title">[],
  ): Promise<void> {
    if (tracks.length === 0) return;
    const trackPaths = tracks.map((track) => track.location_path);
    await runAnalysis("track", trackPaths[0], `${tracks.length} selected tracks`, trackPaths);
  }

  async function runAnalysis(
    target: "track" | "playlist",
    targetValue: string,
    successLabel: string,
    selectedLibraryPaths: readonly string[] = [],
  ): Promise<boolean> {
    analyzedTrackPaths.value = [...selectedLibraryPaths];
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
    const completion = new Promise<boolean>((resolve) => {
      resolveActiveRun = resolve;
    });

    // 1. Escuchar el STDOUT de Rust en tiempo real
    const unlistenStdout = await listen<string>("analysis-stdout", (event) => {
      const line = event.payload.trim();
      if (!line) return;
      try {
        const msg = JSON.parse(line) as SidecarMessage;
        if (msg.type === "summary") {
          lastSummary = { total: msg.total, succeeded: msg.succeeded, skipped: msg.skipped };
        }
        handleMessage(msg);
      } catch {
        handleMessage({ type: "log", level: "error", message: line });
      }
    });
    unlistens.push(unlistenStdout);

    // 2. Escuchar el STDERR de Rust
    const unlistenStderr = await listen<string>("analysis-stderr", (event) => {
      const text = event.payload.trim();
      if (!text) return;
      handleMessage({ type: "log", level: "error", message: text });
    });
    unlistens.push(unlistenStderr);

    // 3. Escuchar el evento de cierre del proceso
    const unlistenClose = await listen<number | null>("analysis-close", (event) => {
      cleanupListeners();
      const code = event.payload;
      const succeeded = code === 0;

      if (succeeded) {
        captureRun(logSnapshot());
        setAnalysisStatus(`${successLabel} Auto Cue complete`);
      }
      finishRun(succeeded ? "success" : "error", lastSummary ?? undefined);
      resolveActiveRun?.(succeeded);
      resolveActiveRun = null;
    });
    unlistens.push(unlistenClose);

    // 4. Arrancar el motor en Rust
    try {
      startRun();
      currentPid.value = 99999; // Mock PID (la cancelación ahora va por backend)
      await invoke("start_analysis_stream", {
        args: buildArgs(cfg, target, selectedLibraryPaths),
        nmlPath: nmlPathOverride.value,
      });
    } catch (err) {
      cleanupListeners();
      handleMessage({ type: "log", level: "error", message: `Failed to start stream: ${String(err)}` });
      finishRun("error");
      resolveActiveRun?.(false);
      resolveActiveRun = null;
    }
    return completion;
  }

  /** Snapshot of the current run's full NDJSON log, for captureRun(). */
  function logSnapshot(): SidecarMessage[] {
    return useRunState().logs.value.map((l) => l.msg);
  }

  async function batchSave(
    payload: {
      tracks: readonly Record<string, unknown>[];
      playlists: readonly Record<string, unknown>[];
    },
    writeToFiles: boolean,
  ): Promise<{ ok: boolean; error?: string }> {
    const args = ["--batch-save", JSON.stringify(payload)];
    if (writeToFiles) args.push("--write-to-files");
    args.push("--json");
    try {
      await callCueGridCore(args);
      return { ok: true };
    } catch (error) {
      return { ok: false, error: String(error) };
    }
  }

  async function compileSmartPlaylist(
    payload: SmartPlaylistPayload,
  ): Promise<{ ok: true; result: SmartPlaylistCompileResult } | { ok: false; error: string }> {
    const args = ["--compile-smart-playlist", JSON.stringify(payload), "--json"];
    try {
      const stdout = await callCueGridCore(args);
      const lines = stdout.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
      const response = JSON.parse(lines[lines.length - 1] ?? "") as {
        ok?: boolean;
        error?: unknown;
        result?: Partial<SmartPlaylistCompileResult>;
      } & Partial<SmartPlaylistCompileResult>;
      if (response.ok === false) {
        return {
          ok: false,
          error: typeof response.error === "string"
            ? response.error
            : "Core could not compile the Smart Playlist.",
        };
      }
      const compiled = response.result ?? response;
      if (
        compiled.type !== "smart_playlist_compiled" ||
        typeof compiled.name !== "string" ||
        typeof compiled.matched !== "number" ||
        typeof compiled.uuid !== "string"
      ) {
        return { ok: false, error: "Core returned an invalid Smart Playlist response." };
      }
      return { ok: true, result: compiled as SmartPlaylistCompileResult };
    } catch (error) {
      return { ok: false, error: String(error) };
    }
  }

  async function createStaticPlaylist(
    payload: { name: string; entries: string[] },
  ): Promise<{ ok: true; result: { type: "static_playlist_created"; name: string; entries: number; uuid: string } } | { ok: false; error: string }> {
    const args = ["--create-static-playlist", JSON.stringify(payload), "--json"];
    try {
      const stdout = await callCueGridCore(args);
      const lines = stdout.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
      const response = JSON.parse(lines[lines.length - 1] ?? "") as unknown;
      if (typeof response !== "object" || response === null) throw new Error("Core returned an invalid playlist response.");
      const result = response as { type?: unknown; name?: unknown; entries?: unknown; uuid?: unknown; error?: unknown };
      if (result.type !== "static_playlist_created" || typeof result.name !== "string" || typeof result.entries !== "number" || typeof result.uuid !== "string") {
        return { ok: false, error: typeof result.error === "string" ? result.error : "Core could not create the playlist." };
      }
      return { ok: true, result: result as { type: "static_playlist_created"; name: string; entries: number; uuid: string } };
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
      const stdoutText = await callCueGridCore(["--discover-nml"]);
      if (stdoutText.trim()) {
        const result = JSON.parse(stdoutText);
        if (result.path) {
          useConfigState().setCustomNmlPath(result.path);
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
      resolveActiveRun?.(false);
      resolveActiveRun = null;
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

  return { run, runSingleTrack, runSelectedTracks, batchSave, compileSmartPlaylist, createStaticPlaylist, cancel, exportTelemetry, resetRun, discoverAndSetDefaultNml, nmlPathOverride, callCueGridCore };
}
