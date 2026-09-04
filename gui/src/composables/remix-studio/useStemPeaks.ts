import { shallowRef } from "vue";
import { convertFileSrc } from "@tauri-apps/api/core";
import Peaks from "peaks.js";
import * as Tone from "tone";
import { activeAudioEngine } from "../player/useGlobalAudio.ts";

export const STEM_LANES = [
  { name: "Drums", color: "#FF5722" },
  { name: "Bass", color: "#4CAF50" },
  { name: "Other", color: "#2196F3" },
  { name: "Vocals", color: "#FFC107" },
] as const;

interface StemPeaksOptions {
  getWaveformContainers: () => HTMLDivElement[];
  createPointMarker?: (markerOptions: any, laneIndex: number) => any;
  onDuration: (duration: number) => void;
  onMixStateChanged?: (muted: readonly boolean[], soloed: number | null) => void;
  onViewportChanged?: () => void;
}

const ZOOM_LEVELS = [64, 256, 512, 1024, 2048, 4096, 8192, 16384];
let syncTimeout: ReturnType<typeof setTimeout> | null = null;
let savedTransportTime = 0;

/** Owns opaque Peaks instances and Tone's shared, sample-accurate Stem transport. */
export function useStemPeaks(options: StemPeaksOptions) {
  const isLoading = shallowRef(false);
  const isReady = shallowRef(false);
  const muted = shallowRef<boolean[]>(STEM_LANES.map(() => false));
  const soloed = shallowRef<number | null>(null);
  let instances: any[] = [];
  let tonePlayers: Tone.Player[] = [];
  let tonePitchShifts: Tone.PitchShift[] = [];
  let sourceTranspose = 0;
  let initToken = 0;
  let syncFrame: number | null = null;
  let resizeFrame: number | null = null;
  let isSyncing = false;
  let resizeObserver: ResizeObserver | null = null;
  let isPlaying = false;
  let animationFrame: number | null = null;
  let activePlayerAdapters = 0;
  const eventEmitters = new Set<any>();
  let mutedByAudioMutex = false;
  let stoppedByAudioMutex = false;

  function emit(event: string, time?: number): void {
    eventEmitters.forEach((eventEmitter) => eventEmitter.emit(event, time));
  }

  function stopTimeUpdates(): void {
    if (animationFrame !== null) cancelAnimationFrame(animationFrame);
    animationFrame = null;
  }

  function stopTransport(): void {
    isPlaying = false;
    stopTimeUpdates();
    Tone.Transport.stop();
    Tone.Transport.cancel(0);
  }

  function createCustomPlayer(): any {
    let eventEmitter: any = null;
    const VISUAL_OFFSET = 0.04;

    const getVisualTime = () => {
      const time = activeAudioEngine.value === "stems" ? Tone.Transport.seconds : savedTransportTime;
      return Math.max(0, time - VISUAL_OFFSET);
    };

    const emitTimeUpdate = () => {
      if (activeAudioEngine.value !== "stems" || !isPlaying) {
        animationFrame = null;
        return;
      }
      eventEmitter?.emit("player.timeupdate", getVisualTime());
      animationFrame = requestAnimationFrame(emitTimeUpdate);
    };

    return {
      init: async (emitter: any) => {
        eventEmitter = emitter;
        eventEmitters.add(emitter);
        activePlayerAdapters += 1;
      },
      destroy: () => {
        if (eventEmitter) eventEmitters.delete(eventEmitter);
        eventEmitter = null;
        activePlayerAdapters = Math.max(0, activePlayerAdapters - 1);
        if (activePlayerAdapters === 0) stopTransport();
      },
      play: async () => {
        await Tone.start();
        if (activeAudioEngine.value !== "stems") return;
        // Inyectamos el tiempo congelado a Tone justo antes de arrancar
        Tone.Transport.seconds = savedTransportTime;
        Tone.Transport.start();
        isPlaying = true;
        emit("player.playing", Tone.Transport.seconds);
        stopTimeUpdates();
        emitTimeUpdate();
      },
      pause: () => {
        if (activeAudioEngine.value === "stems") Tone.Transport.pause();
        isPlaying = false;
        emit("player.pause", Tone.Transport.seconds);
        stopTimeUpdates();
      },
      isPlaying: () => isPlaying,
      isSeeking: () => false,
      getCurrentTime: () => getVisualTime(),
      getDuration: () => tonePlayers[0]?.buffer.duration ?? 0,
      seek: (time: number) => {
        savedTransportTime = time;
        // Solo manipulamos Tone.js si está reproduciendo
        if (activeAudioEngine.value === "stems" && isPlaying) {
          Tone.Transport.seconds = time;
        }
        emit("player.seeked", time);
        emit("player.timeupdate", time);
      },
    };
  }

  function syncViews(source: any): void {
    if (syncTimeout !== null) clearTimeout(syncTimeout);

    const applySync = () => {
      const sourceView = source?.views.getView("zoomview");
      if (!sourceView || isSyncing) return;

      const startTime = sourceView.getStartTime();
      const visibleSeconds = Math.max(2, sourceView.getEndTime() - startTime);
      isSyncing = true;
      try {
        for (const target of instances) {
          if (target === source) continue;
          const targetView = target.views.getView("zoomview");
          targetView?.setZoom({ seconds: visibleSeconds });
          targetView?.setStartTime(startTime);
        }
      } finally {
        isSyncing = false;
      }
      options.onViewportChanged?.();
    };

    if (!isSyncing && syncFrame === null) {
      syncFrame = requestAnimationFrame(() => {
        syncFrame = null;
        applySync();
      });
    }
    syncTimeout = setTimeout(applySync, 50);
  }

  function bindEvents(instance: any): void {
    const syncView = () => syncViews(instance);
    instance.on("zoomview.update", syncView);
    instance.on("zoomview.panned", syncView);
    instance.on("zoom.update", syncView);
    instance.on("player.seeked", (time: number) => {
      if (Number.isFinite(time)) syncViews(instance);
    });
  }

  function fitViewsToContainers(): void {
    if (resizeFrame !== null) return;
    resizeFrame = requestAnimationFrame(() => {
      resizeFrame = null;
      for (const instance of instances) instance.views.getView("zoomview")?.fitToContainer();
    });
  }

  function bindResizeObserver(containers: HTMLDivElement[]): void {
    resizeObserver?.disconnect();
    resizeObserver = new ResizeObserver(fitViewsToContainers);
    containers.forEach((container) => resizeObserver?.observe(container));
    window.addEventListener("resize", fitViewsToContainers);
  }

  function applyMuteState(): void {
    tonePlayers.forEach((player, index) => {
      const shouldMute = mutedByAudioMutex || (soloed.value === null
          ? muted.value[index]
          : soloed.value !== index);

      // -100 dB es silencio absoluto garantizado en Tone.js sin errores de rango
      player.volume.value = shouldMute ? -100 : 0;
    });
  }

  function pauseForAudioMutex(): void {
    savedTransportTime = Tone.Transport.seconds;
    mutedByAudioMutex = true;
    stoppedByAudioMutex = true;
    isPlaying = false;
    stopTimeUpdates();
    for (const player of tonePlayers) {
      player.unsync();
      player.stop();
    }
    applyMuteState();
    emit("player.pause", savedTransportTime);
  }

  function resumeFromAudioMutex(): void {
    if (stoppedByAudioMutex) {
      Tone.Transport.seconds = savedTransportTime;
      for (const player of tonePlayers) player.sync().start(0);
      stoppedByAudioMutex = false;
    }
    mutedByAudioMutex = false;
    applyMuteState();
  }

  function notifyMixStateChanged(): void {
    options.onMixStateChanged?.([...muted.value], soloed.value);
  }

  function createInstance(container: HTMLDivElement, color: string, token: number, laneIndex: number): Promise<any> {
    return new Promise((resolve, reject) => {
      Peaks.init({
        zoomview: { container, waveformColor: color, playedWaveformColor: color, playheadColor: "#ffffff", showPlayheadTime: laneIndex === 0, showAxisLabels: false, axisGridlineColor: "transparent", axisLabelColor: "transparent", enablePoints: true, wheelMode: "none" },
        player: createCustomPlayer(),
        webAudio: { audioBuffer: tonePlayers[laneIndex]?.buffer.get() },
        zoomLevels: ZOOM_LEVELS,
        keyboard: false,
        pointMarkerColor: "#facf25",
        createPointMarker: (markerOptions: any) => options.createPointMarker?.(markerOptions, laneIndex),
      } as any, (error: Error | null, instance: unknown) => {
        if (token !== initToken) {
          try { (instance as any)?.destroy?.(); } catch { /* stale callback */ }
          resolve(null);
        } else if (error || !instance) {
          reject(error ?? new Error("Peaks did not create a Stem instance."));
        } else {
          resolve(instance);
        }
      });
    });
  }

  async function initialize(stemPaths: readonly string[]): Promise<any | null> {
    destroy();
    const token = ++initToken;
    const containers = options.getWaveformContainers();
    if (stemPaths.length !== STEM_LANES.length || containers.length !== STEM_LANES.length) return null;

    isLoading.value = true;
    isReady.value = false;
    muted.value = STEM_LANES.map(() => false);
    soloed.value = null;
    mutedByAudioMutex = false;
    stoppedByAudioMutex = false;
    notifyMixStateChanged();
    try {
      await Tone.start();
      tonePlayers = stemPaths.map((path) => {
        const player = new Tone.Player({ url: convertFileSrc(path), autostart: false, fadeIn: 0.004, fadeOut: 0.004, });
        const pitchShift = new Tone.PitchShift(sourceTranspose).toDestination();
        player.connect(pitchShift);
        tonePitchShifts.push(pitchShift);
        player.sync().start(0);
        return player;
      });
      await Tone.loaded();
      if (token !== initToken) return null;

      const createdInstances = await Promise.all(STEM_LANES.map((lane, index) =>
          createInstance(containers[index], lane.color, token, index),
      ));
      if (token !== initToken || createdInstances.some((instance) => !instance)) return null;

      instances = createdInstances;
      options.onDuration(tonePlayers[0].buffer.duration);
      instances.forEach(bindEvents);
      bindResizeObserver(containers);
      applyMuteState();
      syncViews(instances[0]);
      if (activeAudioEngine.value !== "stems") pauseForAudioMutex();
      isReady.value = true;
      return instances[0];
    } finally {
      if (token === initToken) isLoading.value = false;
    }
  }

  function getMasterPeaks(): any | null { return instances[0] ?? null; }
  function getStemPeaks(): readonly any[] { return instances; }

  function toggleMute(index: number): void {
    const nextMuted = [...muted.value];
    nextMuted[index] = !nextMuted[index];
    muted.value = nextMuted;

    // UX: Si silenciamos la pista que estaba en Solo, quitamos el Solo
    if (soloed.value === index) {
      soloed.value = null;
    }

    applyMuteState();
    notifyMixStateChanged();
  }

  function toggleSolo(index: number): void {
    if (soloed.value === index) {
      soloed.value = null;
    } else {
      soloed.value = index;

      // UX: Si ponemos una pista en Solo, nos aseguramos de quitarle el Mute
      const nextMuted = [...muted.value];
      nextMuted[index] = false;
      muted.value = nextMuted;
    }

    applyMuteState();
    notifyMixStateChanged();
  }

  function destroy(): void {
    initToken += 1;
    if (syncTimeout !== null) clearTimeout(syncTimeout);
    syncTimeout = null;
    if (syncFrame !== null) cancelAnimationFrame(syncFrame);
    syncFrame = null;
    if (resizeFrame !== null) cancelAnimationFrame(resizeFrame);
    resizeFrame = null;
    stopTransport();
    window.removeEventListener("resize", fitViewsToContainers);
    resizeObserver?.disconnect();
    resizeObserver = null;
    isReady.value = false;
    mutedByAudioMutex = false;
    stoppedByAudioMutex = false;
    activePlayerAdapters = 0;
    for (const instance of instances) { try { instance.destroy(); } catch (error) { console.warn("[StemPeaks] Peaks destroy failed:", error); } }
    instances = [];
    eventEmitters.clear();
    for (const player of tonePlayers) player.dispose();
    tonePlayers = [];
    for (const pitchShift of tonePitchShifts) pitchShift.dispose();
    tonePitchShifts = [];
    isLoading.value = false;
  }

  function getTonePlayers(): Tone.Player[] {
    return tonePlayers;
  }

  /** Applies source pitch without changing the shared transport timing. */
  function setSourceTranspose(value: number): void {
    sourceTranspose = Number.isFinite(value) ? Math.max(-12, Math.min(12, Math.round(value))) : 0;
    for (const pitchShift of tonePitchShifts) pitchShift.pitch = sourceTranspose;
  }

  return {
    getMasterPeaks,
    getStemPeaks,
    getTonePlayers,
    setSourceTranspose,
    isLoading,
    isReady,
    muted,
    soloed,
    initialize,
    pauseForAudioMutex,
    resumeFromAudioMutex,
    toggleMute,
    toggleSolo,
    destroy,
  };
}
