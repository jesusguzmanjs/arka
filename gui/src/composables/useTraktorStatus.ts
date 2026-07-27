import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { onMounted, onUnmounted, shallowRef } from "vue";

export const isTraktorRunning = shallowRef(false);

export function useTraktorStatus() {
  let unlisten: UnlistenFn | undefined;

  onMounted(async () => {
    unlisten = await listen<boolean>("traktor-status", (event) => {
      isTraktorRunning.value = event.payload;
    });

    isTraktorRunning.value = await invoke<boolean>("get_traktor_status");
  });

  onUnmounted(() => {
    unlisten?.();
  });

  return { isTraktorRunning };
}
