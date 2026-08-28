import { shallowRef } from "vue";

export type AppToastKind = "success" | "warning" | "error";

const message = shallowRef<string | null>(null);
const kind = shallowRef<AppToastKind>("success");
let dismissTimer: number | undefined;

export function showAppToast(nextMessage: string, nextKind: AppToastKind = "success"): void {
  message.value = nextMessage;
  kind.value = nextKind;

  if (dismissTimer !== undefined) window.clearTimeout(dismissTimer);
  dismissTimer = window.setTimeout(() => {
    message.value = null;
    dismissTimer = undefined;
  }, 4000);
}

export function useAppToast() {
  return { message, kind, showAppToast };
}
