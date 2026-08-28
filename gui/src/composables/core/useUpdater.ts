import { getVersion } from "@tauri-apps/api/app";
import { relaunch } from "@tauri-apps/plugin-process";
import { check, type Update } from "@tauri-apps/plugin-updater";
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

async function executeUpdate(update: Update): Promise<void> {
  if (isUpdating.value) return;

  isUpdating.value = true;
  try {
    await update.downloadAndInstall();
    await relaunch();
  } catch (error) {
    console.error("Unable to install the available update:", error);
    window.alert(`Unable to install the update. ${errorMessage(error)}`);
  } finally {
    isUpdating.value = false;
  }
}

async function checkOnStartup(): Promise<void> {
  try {
    const update = await checkForUpdate();
    if (update === null) return;

    updateInfo.value = update;
    const shouldInstall = window.confirm(
      `Arka ${update.version} is available. Download, install, and restart now?`,
    );
    if (shouldInstall) await executeUpdate(update);
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
    if (update === null) window.alert("Arka is up to date.");
  } catch (error) {
    console.error("Unable to check for updates:", error);
    window.alert(`Unable to check for updates. ${errorMessage(error)}`);
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
