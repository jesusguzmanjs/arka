import { message } from "@tauri-apps/plugin-dialog";
import { useSaveStore } from "../stores/useSaveStore";

let activeConfirmation: Promise<boolean> | null = null;

/**
 * Resolves unsaved work through the application's single native confirmation
 * flow. A true result means it is safe to continue with the protected action.
 */
export function useUnsavedChangesGuard() {
  const saveStore = useSaveStore();

  async function resolveUnsavedChanges(): Promise<boolean> {
    if (!saveStore.isDirty) return true;
    if (activeConfirmation) return activeConfirmation;

    activeConfirmation = (async () => {
      try {
        const result = await message(
          "You have unsaved changes. Do you want to save them before exiting?",
          {
            title: "Unsaved changes",
            kind: "warning",
            buttons: {
              yes: "Save Changes",
              no: "Discard",
              cancel: "Cancel",
            },
          },
        );

        if (result === "Discard") {
          saveStore.clearDirtyState();
          return true;
        }

        if (result !== "Save Changes") return false;

        try {
          await saveStore.saveAll();
          return !saveStore.isDirty;
        } catch {
          return false;
        }
      } finally {
        activeConfirmation = null;
      }
    })();

    return activeConfirmation;
  }

  return { resolveUnsavedChanges };
}
