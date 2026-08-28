import { onBeforeUnmount, onMounted } from "vue";

export interface PlayerKeyboardHandlers {
  togglePlay: () => void | Promise<void>;
  stop?: () => void;
  onPadTrigger?: (padNumber: number) => void | Promise<void>;
  onPadRelease?: (padNumber: number) => void;
  onSeekBeats?: (beats: number) => void;
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
    if (event.key === " ") {
      event.preventDefault();
      void handlers.togglePlay();
      return;
    }
    if (event.key === "Enter" && handlers.stop) {
      event.preventDefault();
      handlers.stop();
      return;
    }
    if (event.key === "+" || event.key === "=") { event.preventDefault(); handlers.zoomIn?.(); return; }
    if (event.key === "-" || event.key === "_") { event.preventDefault(); handlers.zoomOut?.(); return; }
    const padNumber = getPadNumber(event);
    if (padNumber !== null && handlers.onPadTrigger) {
      event.preventDefault();
      previewingPads.add(padNumber);
      void handlers.onPadTrigger(padNumber);
      return;
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      handlers.onSeekBeats?.(4);
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      handlers.onSeekBeats?.(-4);
    }
  };

  const onKeyUp = (event: KeyboardEvent) => {
    const padNumber = getPadNumber(event);
    if (padNumber === null || !previewingPads.delete(padNumber)) return;
    event.preventDefault();
    handlers.onPadRelease?.(padNumber);
  };

  onMounted(() => {
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
  });
  onBeforeUnmount(() => {
    window.removeEventListener("keydown", onKeyDown);
    window.removeEventListener("keyup", onKeyUp);
  });
}
