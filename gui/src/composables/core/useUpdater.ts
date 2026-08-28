import { getVersion } from "@tauri-apps/api/app";
import { relaunch } from "@tauri-apps/plugin-process";
import { check, type Update } from "@tauri-apps/plugin-updater";
import { ask, message } from "@tauri-apps/plugin-dialog";
import { shallowRef } from "vue";

const currentAppVersion = shallowRef("");
const isChecking = shallowRef(false);
const isUpdating = shallowRef(false);
const updateInfo = shallowRef<Update | null>(null);
let activeCheck: Promise<Update | null> | null = null;

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function checkForUpdate(): Promise<Update | null> {
  if (activeCheck === null) {
    activeCheck = check().finally(() => {
      activeCheck = null;
    });
  }

  return activeCheck;
}

async function loadCurrentVersion(): Promise<void> {
  try {
    currentAppVersion.value = await getVersion();
  } catch (error) {
    console.error("Unable to load the current app version:", error);
  }
}

async function executeUpdate(update: Update, silent = false): Promise<void> {
  if (isUpdating.value) return;

  isUpdating.value = true;
  try {
    await update.downloadAndInstall();
    await relaunch();
  } catch (error) {
    console.error("Unable to install the available update:", error);
    if (!silent) {
      await message(`Unable to install the update. ${errorMessage(error)}`, {
        title: "Update Error",
        kind: "error"
      });
    }
  } finally {
    isUpdating.value = false;
  }
}

async function checkOnStartup(): Promise<void> {
  try {
    const update = await checkForUpdate();
    if (update === null) return;

    updateInfo.value = update;

    // ask() lanza una ventana a nivel de OS, imposible de saltar por el navegador
    const shouldInstall = await ask(
        `Arka v${update.version} is available. Download, install, and restart now?`,
        {
          title: "Update Available",
          kind: "info",
          okLabel: "Install and Restart",
          cancelLabel: "Later",
        }
    );

    if (shouldInstall) await executeUpdate(update, true);
  } catch (error) {
    console.error("Unable to check for updates on startup:", error);
  }
}

async function checkManually(): Promise<void> {
  if (isChecking.value || isUpdating.value) return;

  isChecking.value = true;
  try {
    const update = await checkForUpdate();
    updateInfo.value = update;
    if (update === null) {
      await message("Arka is up to date.", { title: "Updater", kind: "info" });
    }
  } catch (error) {
    console.error("Unable to check for updates:", error);
    await message(`Unable to check for updates. ${errorMessage(error)}`, {
      title: "Update Error",
      kind: "error"
    });
  } finally {
    isChecking.value = false;
  }
}

export function useUpdater() {
  return {
    currentAppVersion,
    isChecking,
    isUpdating,
    updateInfo,
    loadCurrentVersion,
    checkOnStartup,
    checkManually,
    executeUpdate,
  };
}