import { onBeforeUnmount, onMounted } from "vue";

export interface PlayerKeyboardHandlers {
  togglePlay: () => void | Promise<void>;
  stop: () => void;
  hasPad: (padIndex: number) => boolean;
  previewStart: (padIndex: number) => void | Promise<void>;
  previewEnd: (padIndex: number) => void;
}

export function isFocusedOnInput(): boolean {
  const element = document.activeElement as HTMLElement | null;
  if (!element) return false;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(element.tagName) || element.isContentEditable;
}

/** Registers keyboard transport shortcuts exactly for the component lifecycle. */
export function usePlayerKeyboard(handlers: PlayerKeyboardHandlers): void {
  const onKeyDown = (event: KeyboardEvent) => {
    if (isFocusedOnInput() || event.repeat) return;
    if (event.key === " ") { event.preventDefault(); void handlers.togglePlay(); return; }
    if (event.key === "Enter") { event.preventDefault(); handlers.stop(); return; }
    if (/^[1-8]$/.test(event.key)) {
      event.preventDefault();
      const padIndex = Number(event.key);
      if (handlers.hasPad(padIndex)) void handlers.previewStart(padIndex);
    }
  };
  const onKeyUp = (event: KeyboardEvent) => {
    if (isFocusedOnInput() || !/^[1-8]$/.test(event.key)) return;
    event.preventDefault();
    handlers.previewEnd(Number(event.key));
  };
  onMounted(() => { window.addEventListener("keydown", onKeyDown); window.addEventListener("keyup", onKeyUp); });
  onBeforeUnmount(() => { window.removeEventListener("keydown", onKeyDown); window.removeEventListener("keyup", onKeyUp); });
}
