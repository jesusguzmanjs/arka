import { onBeforeUnmount, onMounted } from "vue";
import type { PlayerCue } from "./usePeaksMarkers";

export interface PlayerKeyboardHandlers {
  togglePlay: () => void | Promise<void>;
  stop: () => void;
  /** Defaults to true for the Collection player; Studio needs transport only. */
  enablePadShortcuts?: boolean;
  getPad?: (padNumber: number) => PlayerCue | null;
  jump?: (padNumber: number) => void | Promise<void>;
  previewEnd?: (padNumber: number) => void;
  addCue?: (padIndex: number) => void | Promise<void>;
  deleteCue?: (padIndex: number) => void | Promise<void>;
  skipBeats?: (beats: number) => void;
  zoomIn?: () => void;
  zoomOut?: () => void;
}

export function isFocusedOnInput(): boolean {
  const element = document.activeElement as HTMLElement | null;
  if (!element) return false;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(element.tagName) || element.isContentEditable;
}

function getPadNumber(event: KeyboardEvent): number | null {
  const match = /^(?:Digit|Numpad)([1-8])$/.exec(event.code) ?? /^[1-8]$/.exec(event.key);
  return match ? Number(match[1]) : null;
}

/** Registers keyboard transport shortcuts exactly for the component lifecycle. */
export function usePlayerKeyboard(handlers: PlayerKeyboardHandlers): void {
  const previewingPads = new Set<number>();
  const onKeyDown = (event: KeyboardEvent) => {
    if (isFocusedOnInput() || event.repeat) return;
    if (event.key === " ") { event.preventDefault(); void handlers.togglePlay(); return; }
    if (event.key === "Enter") { event.preventDefault(); handlers.stop(); return; }
    if (event.key === "+" || event.key === "=") { event.preventDefault(); handlers.zoomIn?.(); return; }
    if (event.key === "-" || event.key === "_") { event.preventDefault(); handlers.zoomOut?.(); return; }
    const padNumber = getPadNumber(event);
    if (handlers.enablePadShortcuts !== false && padNumber !== null) {
      event.preventDefault();
      const cue = handlers.getPad?.(padNumber) ?? null;
      if (event.shiftKey) {
        if (cue) void handlers.deleteCue?.(padNumber - 1);
        return;
      }
      if (cue) {
        previewingPads.add(padNumber);
        void handlers.jump?.(padNumber);
      } else {
        void handlers.addCue?.(padNumber - 1);
      }
      return;
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      handlers.skipBeats?.(8);
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      handlers.skipBeats?.(-8);
      return;
    }
  };
  const onKeyUp = (event: KeyboardEvent) => {
    if (isFocusedOnInput()) return;
    const padNumber = getPadNumber(event);
    if (handlers.enablePadShortcuts === false || padNumber === null || !previewingPads.delete(padNumber)) return;
    event.preventDefault();
    handlers.previewEnd?.(padNumber);
  };
  onMounted(() => { window.addEventListener("keydown", onKeyDown); window.addEventListener("keyup", onKeyUp); });
  onBeforeUnmount(() => { window.removeEventListener("keydown", onKeyDown); window.removeEventListener("keyup", onKeyUp); });
}
