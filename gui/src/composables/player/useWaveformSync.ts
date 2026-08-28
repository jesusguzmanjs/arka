import { computed, type Ref } from "vue";
import type { SuperJSON } from "../../types/trackMetadata.ts";
import type { GridLineReference } from "./usePeaksMarkers.ts";

interface SyncTrackData { bpm: number; duration_ms: number; }
interface WaveformSyncOptions {
  peaks: Ref<any>;
  trackData: Ref<SyncTrackData>;
  preview: Ref<SuperJSON | null>;
  getZoomviewElement: () => HTMLDivElement | null;
  getGradientElement: () => HTMLDivElement | null;
  getGradientMaskElement: () => HTMLDivElement | null;
  getGridLines: () => readonly GridLineReference[];
}

function hexToRgb(hex: string) {
  const bigint = Number.parseInt(hex.replace("#", ""), 16);
  return { r: (bigint >> 16) & 255, g: (bigint >> 8) & 255, b: bigint & 255 };
}

/** Owns CSS spectral masking and the single high-frequency viewport/LOD RAF loop. */
export function useWaveformSync(options: WaveformSyncOptions) {
  let syncRaf: number | null = null;
  const trackCssGradient = computed(() => {
    const colorMap = options.preview.value?.color_map;
    if (!options.trackData.value.duration_ms) return "none";

    // V1.0 Fallback: Si el backend envía el array de espectro vacío,
    // rellenamos la onda entera con el amarillo corporativo de CueGrid.
    if (!colorMap || colorMap.length === 0) {
      return "linear-gradient(to right, #ada17b, #ada17b)";
    }

    // 1. Corregido el # en los Hex
    const low = hexToRgb("#E01622"); // Rojo/Rosa Neón (Kicks)
    const mid = hexToRgb("#84588A"); // Púrpura brillante (Sintes/Voces)
    const high = hexToRgb("#279AF1"); // Cyan eléctrico (Hi-hats)

    const lastIndex = Math.max(1, colorMap.length - 1);
    const stops = colorMap.map((bucket, index) => {
      // 2. Asegúrate de que tu JSON manda l, m, h. Si no, esto será 0.
// Extraemos los valores puros
      const l_raw = bucket.l || 0;
      const m_raw = bucket.m || 0;
      const h_raw = bucket.h || 0;

      // ---------------------------------------------------------
      // TRUCO PRO 2: Visual EQ (Ecualización Visual)
      // Ajustamos los pesos artificialmente para equilibrar la vista
      // ---------------------------------------------------------
      const l = l_raw * 1.1; // Los graves (kicks) los subimos un pelín
      const m = m_raw * 0.6; // ATENUAMOS los medios casi a la mitad
      const h = h_raw * 2.5; // MULTIPLICAMOS los agudos para que destaquen

      const total = l + m + h;

      let color = "#18181b"; // Zinc-900 (Ruido de fondo)

      if (total > 0.005) {
        // Ahora aplicamos el cuadrado sobre los valores ya ecualizados
        const weightL = Math.pow(l / total, 2);
        const weightM = Math.pow(m / total, 2);
        const weightH = Math.pow(h / total, 2);
        const weightTotal = weightL + weightM + weightH;

        // Normalizamos los nuevos pesos y aplicamos el boost de brillo (* 1.2)
        const r = Math.min(255, Math.round(((low.r * weightL + mid.r * weightM + high.r * weightH) / weightTotal) * 1.2));
        const g = Math.min(255, Math.round(((low.g * weightL + mid.g * weightM + high.g * weightH) / weightTotal) * 1.2));
        const b = Math.min(255, Math.round(((low.b * weightL + mid.b * weightM + high.b * weightH) / weightTotal) * 1.2));

        color = `rgb(${r}, ${g}, ${b})`;
      }
      return `${color} ${((index / lastIndex) * 100).toFixed(2)}%`;
    });
    return `linear-gradient(to right, ${stops.join(", ")})`;
  });
// ...
  function syncZoomGradientLoop(): void {
    const view = options.peaks.value?.views?.getView("zoomview");
    const gradientEl = options.getGradientElement();
    const maskEl = options.getGradientMaskElement();
    const durationMs = options.trackData.value.duration_ms;
    if (view && gradientEl && maskEl && durationMs) {
      const start = view.getStartTime();
      const end = view.getEndTime();
      const viewDuration = end - start;
      if (viewDuration > 0) {
        const zoomview = options.getZoomviewElement();
        const canvas = zoomview?.querySelector("canvas");
        const baseWidth = canvas?.clientWidth || zoomview?.clientWidth || 0;
        if (baseWidth > 0) {
          maskEl.style.width = `${baseWidth}px`;
          gradientEl.style.width = `${(durationMs / 1000 / viewDuration) * baseWidth}px`;
          gradientEl.style.left = `${-(start / viewDuration) * baseWidth}px`;
        }
        const bpm = options.trackData.value.bpm;
        if (bpm > 0) {
          const visibleBeats = viewDuration / (60 / bpm);
          const fade = (startAt: number, range: number) => Math.max(0.2, Math.min(1, 1 - (visibleBeats - startAt) / range));
          let fadeBeats = fade(60, 40);
          let fadeBars = fade(150, 100);
          let fade16 = fade(300, 200);
          let fade32 = fade(600, 300);
          if (visibleBeats > 300) fadeBeats = 0;
          if (visibleBeats > 600) fadeBars = 0;
          if (visibleBeats > 1000) fade16 = 0;
          if (visibleBeats > 1000) fade32 = 0;
          let needsRedraw = false;
          const gridLines = options.getGridLines();
          gridLines.forEach(({ line, offset }) => {
            const absOffset = Math.abs(offset);
            const isBar = absOffset % 4 === 0;
            const baseOpacity = isBar ? 0.9 : 0.45;
            let opacity = absOffset % 64 === 0 ? baseOpacity : absOffset % 32 === 0 ? baseOpacity * fade32 : absOffset % 16 === 0 ? baseOpacity * fade16 : isBar ? baseOpacity * fadeBars : baseOpacity * fadeBeats;
            opacity = Math.round(opacity * 1000) / 1000;
            if (line.opacity() !== opacity) {
              line.opacity(opacity);
              line.visible(opacity > 0);
              needsRedraw = true;
            }
          });
          if (needsRedraw) gridLines[0]?.line.getLayer()?.batchDraw();
        }
      }
    }
    syncRaf = requestAnimationFrame(syncZoomGradientLoop);
  }

  function startSyncLoop(): void { if (syncRaf === null) syncZoomGradientLoop(); }
  function stopSyncLoop(): void { if (syncRaf !== null) cancelAnimationFrame(syncRaf); syncRaf = null; }
  return { trackCssGradient, syncZoomGradientLoop, startSyncLoop, stopSyncLoop };
}
