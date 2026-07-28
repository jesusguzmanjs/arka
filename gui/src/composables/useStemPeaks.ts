import { shallowRef } from "vue";
import { convertFileSrc } from "@tauri-apps/api/core";
import Peaks from "peaks.js";

export const STEM_LANES = [
  { name: "Drums", color: "#FF5722" },
  { name: "Bass", color: "#4CAF50" },
  { name: "Other", color: "#2196F3" },
  { name: "Vocals", color: "#FFC107" },
] as const;

interface StemPeaksOptions {
  getWaveformContainers: () => HTMLDivElement[];
  getAudioElements: () => HTMLAudioElement[];
  createPointMarker?: (markerOptions: any, laneIndex: number) => any; // <--- Suministra el generador de marcadores
  onPlayingChange: (isPlaying: boolean) => void;
  onDuration: (duration: number) => void;
  onViewportChanged?: () => void;
}

const ZOOM_LEVELS = [64, 256, 512, 1024, 2048, 4096];
const SUPPRESS_EVENT_MS = 750;

/** Owns opaque Peaks instances and all high-frequency synchronization imperatively. */
export function useStemPeaks(options: StemPeaksOptions) {
  const isLoading = shallowRef(false);
  const isReady = shallowRef(false);
  const muted = shallowRef<boolean[]>(STEM_LANES.map(() => false));
  const soloed = shallowRef<number | null>(null);
  let instances: any[] = [];
  let audioContext: AudioContext | null = null;
  let initToken = 0;
  let syncFrame: number | null = null;
  let resizeFrame: number | null = null;
  let isSyncing = false;
  const wheelHandlers = new Map<HTMLDivElement, (event: WheelEvent) => void>();
  let resizeObserver: ResizeObserver | null = null;
  const suppressedEvents = new WeakMap<object, Map<string, number>>();

  function suppress(instance: any, event: string): void {
    const events = suppressedEvents.get(instance) ?? new Map<string, number>();
    events.set(event, performance.now() + SUPPRESS_EVENT_MS);
    suppressedEvents.set(instance, events);
  }

  function isSuppressed(instance: any, event: string): boolean {
    const until = suppressedEvents.get(instance)?.get(event) ?? 0;
    return until > performance.now();
  }

  function syncViews(source: any): void {
    if (isSyncing || syncFrame !== null) return;
    syncFrame = requestAnimationFrame(() => {
      syncFrame = null;
      const sourceView = source?.views.getView("zoomview");
      if (!sourceView || isSyncing) return;

      const startTime = sourceView.getStartTime();
      const rawVisibleSeconds = sourceView.getEndTime() - startTime;

      // Guard de seguridad: Evitamos valores infinitesimales durante el resize
      const MIN_VISIBLE_SECONDS = 2;
      const visibleSeconds = Math.max(MIN_VISIBLE_SECONDS, rawVisibleSeconds);

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
    });
  }

  function syncPlayback(source: any, command: "play" | "pause" | "seek", time?: number): void {
    if (isSyncing) return;
    isSyncing = true;
    try {
      for (const target of instances) {
        if (target === source) continue;
        if (command === "play") {
          suppress(target, "playing");
          void target.player.play().catch(() => undefined);
        } else if (command === "pause") {
          suppress(target, "pause");
          target.player.pause();
        } else if (time !== undefined && Number.isFinite(time)) {
          suppress(target, "seeked");
          target.player.seek(time);
        }
      }
    } finally {
      isSyncing = false;
    }
  }

  function bindEvents(instance: any): void {
    const syncView = () => syncViews(instance);
    instance.on("zoomview.update", syncView);
    instance.on("zoomview.panned", syncView);
    instance.on("zoom.update", syncView);
    instance.on("player.playing", () => {
      if (isSuppressed(instance, "playing")) return;
      options.onPlayingChange(true);
      syncPlayback(instance, "play");
    });
    instance.on("player.pause", () => {
      if (isSuppressed(instance, "pause")) return;
      options.onPlayingChange(false);
      syncPlayback(instance, "pause");
    });
    instance.on("player.ended", () => options.onPlayingChange(false));
    instance.on("player.seeked", (time: number) => {
      if (!isSuppressed(instance, "seeked")) syncPlayback(instance, "seek", time);
    });
  }

  function handleWheel(instance: any, event: WheelEvent): void {
    if (isSyncing || event.deltaY === 0) return;
    event.preventDefault();
    const view = instance.views.getView("zoomview");
    if (!view) return;

    const currentSeconds = view.getEndTime() - view.getStartTime();

    // Cambiamos Math.max(1, ...) por Math.max(2, ...)
    const nextSeconds = Math.max(
        2,
        Math.min(instance.player.getDuration(), currentSeconds * (event.deltaY > 0 ? 1.2 : 0.8))
    );

    view.setZoom({ seconds: nextSeconds });
    syncViews(instance);
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

  function applyVolumes(): void {
    options.getAudioElements().forEach((audio, index) => {
      audio.volume = soloed.value === null
          ? (muted.value[index] ? 0 : 1)
          : (soloed.value === index ? 1 : 0);
    });
  }

  function createInstance(container: HTMLDivElement, audio: HTMLAudioElement, color: string, token: number, laneIndex: number): Promise<any> {
    return new Promise((resolve, reject) => {
      Peaks.init({
        zoomview: { container, waveformColor: color, playedWaveformColor: color, playheadColor: "#ffffff", showPlayheadTime: laneIndex === 0, showAxisLabels: false, axisGridlineColor: "transparent", axisLabelColor: "transparent", enablePoints: true, wheelMode: "none" },
        mediaElement: audio,
        webAudio: { audioContext: audioContext! },
        zoomLevels: ZOOM_LEVELS,
        keyboard: false,
        pointMarkerColor: "#facf25",
        // CONEXIÓN CLAVE: Pasamos la función del marcador Konva correspondiente a esta pista
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
    const audioElements = options.getAudioElements();
    if (stemPaths.length !== STEM_LANES.length || containers.length !== STEM_LANES.length || audioElements.length !== STEM_LANES.length) return null;

    const AudioContextConstructor = window.AudioContext ?? (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioContextConstructor) throw new Error("Web Audio is unavailable in this environment.");

    isLoading.value = true;
    isReady.value = false;
    muted.value = STEM_LANES.map(() => false);
    soloed.value = null;
    try {
      audioContext = new AudioContextConstructor();
      const createdInstances = await Promise.all(STEM_LANES.map((lane, index) => {
        const audio = audioElements[index];
        audio.src = convertFileSrc(stemPaths[index]);
        audio.preload = "auto";
        audio.load();
        return createInstance(containers[index], audio, lane.color, token, index);
      }));
      if (token !== initToken || createdInstances.some((instance) => !instance)) return null;

      instances = createdInstances;
      options.onDuration(instances[0].player.getDuration());
      instances.forEach((instance, index) => {
        bindEvents(instance);
        const handler = (event: WheelEvent) => handleWheel(instance, event);
        containers[index].addEventListener("wheel", handler, { passive: false });
        wheelHandlers.set(containers[index], handler);
      });
      bindResizeObserver(containers);
      applyVolumes();
      syncViews(instances[0]);
      isReady.value = true;
      return instances[0];
    } finally {
      if (token === initToken) isLoading.value = false;
    }
  }

  function getMasterPeaks(): any | null { return instances[0] ?? null; }
  function getStemPeaks(): readonly any[] { return instances; }
  function toggleMute(index: number): void {
    muted.value = muted.value.map((isMuted, audioIndex) => audioIndex === index ? !isMuted : isMuted);
    applyVolumes();
  }

  function toggleSolo(index: number): void {
    soloed.value = soloed.value === index ? null : index;
    applyVolumes();
  }

  function destroy(): void {
    initToken += 1;
    if (syncFrame !== null) cancelAnimationFrame(syncFrame);
    syncFrame = null;
    if (resizeFrame !== null) cancelAnimationFrame(resizeFrame);
    resizeFrame = null;
    window.removeEventListener("resize", fitViewsToContainers);
    resizeObserver?.disconnect();
    resizeObserver = null;
    isReady.value = false;
    for (const [container, handler] of wheelHandlers) container.removeEventListener("wheel", handler);
    wheelHandlers.clear();
    for (const instance of instances) { try { instance.destroy(); } catch (error) { console.warn("[StemPeaks] Peaks destroy failed:", error); } }
    instances = [];
    for (const audio of options.getAudioElements()) { try { audio.pause(); audio.removeAttribute("src"); audio.load(); } catch { /* best-effort media cleanup */ } }
    void audioContext?.close();
    audioContext = null;
    isLoading.value = false;
  }

  return { getMasterPeaks, getStemPeaks, isLoading, isReady, muted, soloed, initialize, toggleMute, toggleSolo, destroy };
}