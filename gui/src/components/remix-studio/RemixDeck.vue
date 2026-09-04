<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, useTemplateRef, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { storeToRefs } from "pinia";
import MasterBpmControl from "./MasterBpmControl.vue";
import RemixColumn from "./RemixColumn.vue";
import { useConfigState } from "../../composables/core/useConfigState.ts";
import { useRemixAudio } from "../../composables/remix-studio/useRemixAudio.ts";
import { showAppToast } from "../../composables/core/useAppToast.ts";
import { useErrorReporter } from "../../composables/core/useErrorReporter.ts";
import { useWorkspaceStore } from "../../stores/useWorkspaceStore.ts";
import { TRAKTOR_COLORS, type PadExtractionResult, type PadFadeRenderRequest, type PadFadeRenderResult, type PadSettings, type RemixPadData, type RemixSetPayload } from "../../types/remix.ts";
import { isUnanalyzedTrack, MISSING_BPM_GRID_MESSAGE } from "../../utils/trackAnalysis.ts";

const setTitle = defineModel<string>("setTitle", { required: true });

const EMPTY_PAD_COLOR = "#333333";
const DEFAULT_REMIX_SET_TITLE = "New Remix Set";

const PADS_PER_PAGE = 4;
const PAGE_COUNT = 4;
const PAD_SHORTCUTS: Record<string, { columnIndex: number; padIndex: number }> = {
  "1": { columnIndex: 0, padIndex: 0 }, q: { columnIndex: 0, padIndex: 1 }, a: { columnIndex: 0, padIndex: 2 }, z: { columnIndex: 0, padIndex: 3 },
  "2": { columnIndex: 1, padIndex: 0 }, w: { columnIndex: 1, padIndex: 1 }, s: { columnIndex: 1, padIndex: 2 }, x: { columnIndex: 1, padIndex: 3 },
  "3": { columnIndex: 2, padIndex: 0 }, e: { columnIndex: 2, padIndex: 1 }, d: { columnIndex: 2, padIndex: 2 }, c: { columnIndex: 2, padIndex: 3 },
  "4": { columnIndex: 3, padIndex: 0 }, r: { columnIndex: 3, padIndex: 1 }, f: { columnIndex: 3, padIndex: 2 }, v: { columnIndex: 3, padIndex: 3 },
};
const currentPage = shallowRef(0);
const isBatchEditMode = shallowRef(false);
const selectedPadIds = ref<Set<string>>(new Set());
const clipboardData = ref<ClipboardData | null>(null);
const columnVolumes = ref<number[]>([1, 1, 1, 1]);
const {
  activePads,
  columnKeylock,
  columnPunchMode,
  globalQuantizeEnabled,
  globalQuantizeValue,
  isDeckPlaying,
  isRemixSetDirty,
  loadPadAudio,
  loadRemixSetPayload,
  markRemixSetDirty,
  masterBpm,
  pressPad,
  removePadAudio,
  releasePad,
  resetRemixSetToDefaults,
  setColumnVolume,
  setColumnFilter,
  setRemixSetDirty,
  stopColumn,
  toggleColumnKeylock,
  toggleColumnPunchMode,
  updatePlayerLoop,
  updatePlayerReverse,
  updatePlayerSync,
  updatePlayerVolume,
} = useRemixAudio();
const workspaceStore = useWorkspaceStore();
const { nmlPathOverride: currentNmlPath } = useConfigState();
const { activeLoopRange, activeStemTracks, activeStudioTrack, sourceTranspose, stemMuted, stemSoloed, remixPads } = storeToRefs(workspaceStore);
const isImportingLoop = shallowRef(false);
const isExportingRemixSet = shallowRef(false);
const isLoadRemixSetModalOpen = shallowRef(false);
const isLoadingRemixSetList = shallowRef(false);
const isLoadingSelectedRemixSet = shallowRef(false);
const remixSetLoadError = shallowRef<string | null>(null);
const availableRemixSetTitles = shallowRef<string[]>([]);
const pendingRemixSetTitle = shallowRef<string | null>(null);
const pendingRemixSetAction = shallowRef<"load" | "new" | null>(null);
const isDiscardChangesDialogOpen = shallowRef(false);
const isEditingSetTitle = shallowRef(false);
const editedSetTitle = shallowRef("");
const setTitleInput = useTemplateRef<HTMLInputElement>("setTitleInput");
const contextMenuEl = useTemplateRef<HTMLElement>("contextMenuEl");
let isApplyingLoadedRemixSet = false;
const renamingPadId = ref<string | null>(null);
const contextMenu = ref({
  visible: false,
  x: 0,
  y: 0,
  colIndex: -1,
  padIndex: -1,
  padId: "",
});
const pageOffset = computed(() => currentPage.value * PADS_PER_PAGE);
const visiblePadColumns = computed(() => remixPads.value.map((columnPads) => (
  columnPads.slice(pageOffset.value, pageOffset.value + PADS_PER_PAGE)
)));
const activePadIndexes = computed(() => remixPads.value.map((columnPads, columnIndex) => {
  const activePadId = activePads[columnIndex];
  return activePadId === null
    ? null
    : columnPads.findIndex((pad) => pad.settings.id === activePadId);
}));
const currentTrackPath = computed(() => activeStudioTrack.value?.location_path ?? null);
const activeSourcePaths = computed(() => {
  if (activeStemTracks.value.length !== 4) {
    return currentTrackPath.value === null ? [] : [currentTrackPath.value];
  }
  return activeStemTracks.value.filter((_, index) => (
    stemSoloed.value !== null ? stemSoloed.value === index : !stemMuted.value[index]
  ));
});
const canImportLoop = computed(() => {
  const loopRange = activeLoopRange.value;
  return loopRange !== null
    && Number.isFinite(loopRange.start)
    && Number.isFinite(loopRange.end)
    && loopRange.end > loopRange.start
    && currentTrackPath.value !== null;
});
const contextMenuPad = computed(() => (
  remixPads.value[contextMenu.value.colIndex]?.[contextMenu.value.padIndex] ?? null
));
const isDeckEmptyWithDefaultTitle = computed(() => (
  setTitle.value.trim() === DEFAULT_REMIX_SET_TITLE
  && remixPads.value.every((columnPads) => columnPads.every((pad) => pad.audio === null))
));
const shouldConfirmRemixSetReplacement = computed(() => (
  isRemixSetDirty.value && !isDeckEmptyWithDefaultTitle.value
));
const discardChangesPrompt = computed(() => (
  pendingRemixSetAction.value === "new"
    ? "You have unsaved changes. Discard and create a new set?"
    : "You have unsaved changes. Discard them and load the new set, or Cancel?"
));
const discardChangesActionLabel = computed(() => (
  pendingRemixSetAction.value === "new" ? "Discard and create" : "Discard and load"
));
// En el <script setup>:
const columnFilters = ref<number[]>([0, 0, 0, 0]);



interface PadLocation {
  colIndex: number;
  padIndex: number;
  pad: RemixPadData;
}

interface PadImportTarget {
  colIndex: number;
  padIndex: number;
  padId: string;
}

interface ClipboardData {
  action: "copy" | "cut";
  padData: Pick<RemixPadData, "audio" | "settings">;
  sourceColIndex?: number;
  sourcePadId?: string;
}

function getTraktorQuantizeIndex(value: string): number {
  const mapping: Record<string, number> = {
    "16n": 0,
    "8n": 1,
    "4n": 2,
    "2n": 3,
    "1m": 4,
    "2m": 5,
    "4m": 6,
    "8m": 7,
  };
  return mapping[value] ?? 4;
}

function traktorColorIndex(color: string): number {
  const index = (TRAKTOR_COLORS as readonly string[]).indexOf(color.toUpperCase());
  return index >= 0 ? index + 1 : 1;
}

function updateColumnFilter(columnIndex: number, value: number): void {
  columnFilters.value[columnIndex] = value;
  setColumnFilter(columnIndex, value); // Llama al motor de audio (Tone.js / Rust)
}

async function exportRemixSet(): Promise<void> {
  if (isExportingRemixSet.value) return;

  const pads = remixPads.value.flatMap((columnPads) => columnPads.flatMap((pad) => {
    const audio = pad.audio;
    if (!audio?.filePath) return [];

    const padStartMs = pad.settings.loopStart !== null && pad.settings.loopStart !== undefined
        ? pad.settings.loopStart * 1000
        : (audio.startMs ?? 0);

    const padEndMs = pad.settings.loopEnd !== null && pad.settings.loopEnd !== undefined
        ? pad.settings.loopEnd * 1000
        : (audio.endMs ?? audio.durationMs ?? 0);

    return [{
      id: pad.settings.id,
      name: pad.settings.name,
      path: audio.filePath,
      type: pad.settings.playType === "loop" ? 0 : 1,
      mode: pad.settings.triggerMode === "gate" ? 0 : 1,
      sync: pad.settings.sync ? 1 : 0,
      reverse: pad.settings.isReversed ? 1 : 0,
      transpose: Number(pad.settings.transpose) || 0,
      gain: Math.max(0, Math.min(1, (pad.settings.volume + 1) / 2)),
      color_id: traktorColorIndex(pad.settings.color),
      start_ms: padStartMs,
      end_ms: padEndMs,
      duration_ms: audio.durationMs ?? 0,
      bpm: audio.originalBpm,
      key: audio.originalKey ?? "",
      fadeInMs: Math.max(0, Number(pad.settings.fadeInMs) || 0),
      fadeOutMs: Math.max(0, Number(pad.settings.fadeOutMs) || 0),
    }];
  }));
  const payload = {
    title: setTitle.value.trim() || DEFAULT_REMIX_SET_TITLE,
    bpm: Number(masterBpm.value),
    quantize_state: globalQuantizeEnabled.value ? 1 : 0,
    quantize_value: getTraktorQuantizeIndex(globalQuantizeValue.value),
    columns: columnKeylock.map((keylock, index) => ({
      keylock: keylock ? 1 : 0,
      punchmode: columnPunchMode[index] ? 1 : 0,
    })),
    pads,
  };

  isExportingRemixSet.value = true;
  try {
    const fadeRequests: PadFadeRenderRequest[] = pads.map((pad) => ({
      padId: pad.id,
      path: pad.path,
      fadeInMs: pad.fadeInMs,
      fadeOutMs: pad.fadeOutMs,
    }));
    const fadeResults = await invoke<PadFadeRenderResult[]>("render_remix_pad_fades", { pads: fadeRequests });
    const fadedPaths = new Map(fadeResults.map((result) => [result.pad_id, result.file_path]));
    const pythonPayload = {
      ...payload,
      pads: pads.map(({ fadeInMs: _fadeInMs, fadeOutMs: _fadeOutMs, ...pad }) => ({
        ...pad,
        path: fadedPaths.get(pad.id) ?? pad.path,
      })),
    };

    await invoke<string>("call_cuegrid_core", {
      args: ["--save-remix-set", JSON.stringify(pythonPayload)],
      nmlPath: currentNmlPath.value ?? null,
    });
    setRemixSetDirty(false);
    showAppToast("Remix Set saved successfully", "success");
  } catch (error) {
    console.error("Failed to save Remix Set:", error);
    useErrorReporter().triggerError(error);
    const detail = typeof error === "string"
      ? error
      : (typeof error === "object" && error !== null && "message" in error && typeof error.message === "string"
        ? error.message
        : "Unknown error");
    showAppToast(`Save failed: ${detail}`, "error");
  } finally {
    isExportingRemixSet.value = false;
  }
}

function errorMessage(error: unknown): string {
  if (typeof error === "string") return error;
  if (typeof error === "object" && error !== null && "message" in error && typeof error.message === "string") {
    return error.message;
  }
  return "Unknown error";
}

function beginSetTitleEdit(): void {
  editedSetTitle.value = setTitle.value;
  isEditingSetTitle.value = true;
  void nextTick(() => {
    setTitleInput.value?.focus();
    setTitleInput.value?.select();
  });
}

function finishSetTitleEdit(): void {
  const nextTitle = editedSetTitle.value.trim() || DEFAULT_REMIX_SET_TITLE;
  if (nextTitle !== setTitle.value) setTitle.value = nextTitle;
  isEditingSetTitle.value = false;
}

function cancelSetTitleEdit(): void {
  isEditingSetTitle.value = false;
}

async function openRemixSetLoadModal(): Promise<void> {
  isLoadRemixSetModalOpen.value = true;
  isLoadingRemixSetList.value = true;
  remixSetLoadError.value = null;
  availableRemixSetTitles.value = [];

  try {
    const output = await invoke<string>("call_cuegrid_core", {
      args: ["--list-remix-sets"],
      nmlPath: currentNmlPath.value ?? null,
    });
    const titles: unknown = JSON.parse(output);
    if (!Array.isArray(titles) || !titles.every((title) => typeof title === "string")) {
      throw new Error("CueGrid returned an invalid Remix Set list.");
    }
    availableRemixSetTitles.value = titles;
  } catch (error) {
    remixSetLoadError.value = `Unable to load Remix Sets: ${errorMessage(error)}`;
  } finally {
    isLoadingRemixSetList.value = false;
  }
}

function closeRemixSetLoadModal(): void {
  if (isLoadingSelectedRemixSet.value) return;
  isLoadRemixSetModalOpen.value = false;
  remixSetLoadError.value = null;
}

function requestRemixSetLoad(title: string): void {
  if (shouldConfirmRemixSetReplacement.value) {
    pendingRemixSetTitle.value = title;
    pendingRemixSetAction.value = "load";
    isDiscardChangesDialogOpen.value = true;
    return;
  }
  void loadSelectedRemixSet(title);
}

function cancelDiscardRemixSetChanges(): void {
  pendingRemixSetTitle.value = null;
  pendingRemixSetAction.value = null;
  isDiscardChangesDialogOpen.value = false;
}

function discardChangesAndLoadRemixSet(): void {
  const title = pendingRemixSetTitle.value;
  const action = pendingRemixSetAction.value;
  pendingRemixSetTitle.value = null;
  pendingRemixSetAction.value = null;
  isDiscardChangesDialogOpen.value = false;
  if (action === "new") {
    void resetToNewRemixSet();
  } else if (title) {
    void loadSelectedRemixSet(title);
  }
}

function createNewRemixSet(): void {
  if (shouldConfirmRemixSetReplacement.value) {
    pendingRemixSetAction.value = "new";
    isDiscardChangesDialogOpen.value = true;
    return;
  }
  void resetToNewRemixSet();
}

async function resetToNewRemixSet(): Promise<void> {
  isApplyingLoadedRemixSet = true;
  try {
    resetRemixSetToDefaults();
    setTitle.value = DEFAULT_REMIX_SET_TITLE;
    await nextTick();
    setRemixSetDirty(false);
    showAppToast("New Remix Set ready", "success");
  } finally {
    isApplyingLoadedRemixSet = false;
  }
}

async function loadSelectedRemixSet(title: string): Promise<void> {
  isLoadingSelectedRemixSet.value = true;
  remixSetLoadError.value = null;

  try {
    const output = await invoke<string>("call_cuegrid_core", {
      args: ["--get-remix-set", title],
      nmlPath: currentNmlPath.value ?? null,
    });
    const payload = JSON.parse(output) as RemixSetPayload;
    if (!payload || typeof payload.title !== "string" || !Array.isArray(payload.columns) || !Array.isArray(payload.pads)) {
      throw new Error("CueGrid returned an invalid Remix Set payload.");
    }

    isApplyingLoadedRemixSet = true;
    loadRemixSetPayload(payload);
    setTitle.value = payload.title || DEFAULT_REMIX_SET_TITLE;
    await nextTick();
    setRemixSetDirty(false);
    isLoadRemixSetModalOpen.value = false;
    showAppToast(`Loaded Remix Set: ${setTitle.value}`, "success");
  } catch (error) {
    remixSetLoadError.value = `Unable to load Remix Set: ${errorMessage(error)}`;
  } finally {
    isApplyingLoadedRemixSet = false;
    isLoadingSelectedRemixSet.value = false;
  }
}

function toggleBatchEditMode(): void {
  isBatchEditMode.value = !isBatchEditMode.value;
  if (!isBatchEditMode.value) selectedPadIds.value.clear();
}

function togglePadSelection(padId: string): void {
  if (selectedPadIds.value.has(padId)) {
    selectedPadIds.value.delete(padId);
  } else {
    selectedPadIds.value.add(padId);
  }
}

function selectAllPads(): void {
  for (const columnPads of remixPads.value) {
    for (const pad of columnPads) {
      if (pad.audio !== null) selectedPadIds.value.add(pad.settings.id);
    }
  }
}

function clonePadData(pad: RemixPadData): Pick<RemixPadData, "audio" | "settings"> {
  return {
    audio: pad.audio === null ? null : { ...pad.audio },
    settings: { ...pad.settings },
  };
}

function emptyPadSettings(padId: string): PadSettings {
  return {
    id: padId,
    name: "",
    color: EMPTY_PAD_COLOR,
    playType: "loop",
    triggerMode: "trigger",
    sync: true,
    reverse: false,
    keylock: false,
    volume: 0,
    filter: 0,
    transpose: 0,
    loopStart: 0,
  };
}

function findPadById(padId: string): PadLocation | null {
  for (const [colIndex, columnPads] of remixPads.value.entries()) {
    const padIndex = columnPads.findIndex((pad) => pad.settings.id === padId);
    if (padIndex !== -1) return { colIndex, padIndex, pad: columnPads[padIndex] };
  }

  return null;
}

function applySettingsToPad(padId: string, changes: Partial<PadSettings>): void {
  const location = findPadById(padId);
  if (!location) return;

  const settings = { ...location.pad.settings, ...changes };
  location.pad.settings = settings;
  updatePlayerLoop(settings.id, settings.playType === "loop");
  updatePlayerReverse(settings.id, Boolean(settings.isReversed));
  updatePlayerSync(settings.id, settings.sync);
  updatePlayerVolume(settings.id, settings.volume);
  markRemixSetDirty();
}

function getChangedSettings(current: PadSettings, next: PadSettings): Partial<PadSettings> {
  return Object.fromEntries(
    Object.entries(next).filter(([key, value]) => value !== current[key as keyof PadSettings]),
  ) as Partial<PadSettings>;
}

async function handlePadPress(columnIndex: number, padIndex: number): Promise<void> {
  const pad = remixPads.value[columnIndex][padIndex];
  if (pad.audio === null) {
    if (canImportLoop.value) {
      await importLoopToPad({
        colIndex: columnIndex,
        padIndex,
        padId: pad.settings.id,
      });
    }
    return;
  }

  await pressPad(pad.settings.id, columnIndex, pad.settings);
}

function handlePadRelease(columnIndex: number, padIndex: number): void {
  const pad = remixPads.value[columnIndex][padIndex];
  releasePad(pad.settings.id, columnIndex, pad.settings);
}

function handleColumnStop(columnIndex: number): void {
  stopColumn(columnIndex);
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable;
}

function getPadShortcut(event: KeyboardEvent): { columnIndex: number; padIndex: number } | null {
  return PAD_SHORTCUTS[event.key.toLowerCase()] ?? null;
}

const pressedPadShortcuts = new Map<string, { columnIndex: number; padIndex: number }>();

function handlePadShortcutKeyDown(event: KeyboardEvent): void {
  if (isTypingTarget(event.target)) return;

  if (event.key === "ArrowDown") {
    event.preventDefault();
    currentPage.value = Math.min(currentPage.value + 1, PAGE_COUNT - 1);
    return;
  }

  if (event.key === "ArrowUp") {
    event.preventDefault();
    currentPage.value = Math.max(currentPage.value - 1, 0);
    return;
  }

  if (event.repeat) return;
  const shortcut = getPadShortcut(event);
  if (!shortcut) return;

  event.preventDefault();
  const pad = { ...shortcut, padIndex: pageOffset.value + shortcut.padIndex };
  pressedPadShortcuts.set(event.code, pad);
  if (event.shiftKey) {
    handleColumnStop(pad.columnIndex);
    return;
  }
  void handlePadPress(pad.columnIndex, pad.padIndex);
}

function handlePadShortcutKeyUp(event: KeyboardEvent): void {
  const pad = pressedPadShortcuts.get(event.code);
  if (!pad) return;

  event.preventDefault();
  pressedPadShortcuts.delete(event.code);
  handlePadRelease(pad.columnIndex, pad.padIndex);
}

function updateColumnVolume(columnIndex: number, value: number): void {
  columnVolumes.value[columnIndex] = value;
  setColumnVolume(columnIndex, value);
}

function onPadSettingsUpdate(colIndex: number, padIndex: number, newSettings: PadSettings): void {
  const pad = remixPads.value[colIndex]?.[padIndex];
  if (!pad) return;

  if (isBatchEditMode.value && selectedPadIds.value.has(pad.settings.id)) {
    const changes = getChangedSettings(pad.settings, newSettings);
    for (const selectedPadId of selectedPadIds.value) applySettingsToPad(selectedPadId, changes);
    return;
  }

  applySettingsToPad(pad.settings.id, newSettings);
}

function openContextMenu(event: MouseEvent, colIndex: number, padIndex: number, padId: string): void {
  // Primero le damos la posición inicial
  contextMenu.value = {
    visible: true,
    x: event.clientX,
    y: event.clientY,
    colIndex,
    padIndex,
    padId,
  };

  // Inmediatamente después, medimos y corregimos si se sale
  nextTick(() => {
    const el = contextMenuEl.value;
    if (!el) return;

    const rect = el.getBoundingClientRect();
    let newX = contextMenu.value.x;
    let newY = contextMenu.value.y;

    // Si se sale por la derecha, lo empujamos a la izquierda (dejando 8px de margen)
    if (newX + rect.width > window.innerWidth) {
      newX = Math.max(8, window.innerWidth - rect.width - 8);
    }
    // Si se sale por abajo, lo empujamos hacia arriba (dejando 8px de margen)
    if (newY + rect.height > window.innerHeight) {
      newY = Math.max(8, window.innerHeight - rect.height - 8);
    }

    contextMenu.value.x = newX;
    contextMenu.value.y = newY;
  });
}

function closeContextMenu(): void {
  contextMenu.value.visible = false;
}

function copyPad(): void {
  const pad = contextMenuPad.value;
  if (!pad?.audio) return;

  clipboardData.value = { action: "copy", padData: clonePadData(pad) };
  closeContextMenu();
}

function cutPad(): void {
  const pad = contextMenuPad.value;
  if (!pad?.audio) return;

  clipboardData.value = {
    action: "cut",
    padData: clonePadData(pad),
    sourceColIndex: contextMenu.value.colIndex,
    sourcePadId: pad.settings.id,
  };
  closeContextMenu();
}

function pastePad(): void {
  const target = contextMenuPad.value;
  const clipboard = clipboardData.value;
  if (!target || target.audio !== null || !clipboard?.padData.audio) return;

  const source = clipboard.action === "cut" && clipboard.sourcePadId !== undefined && clipboard.sourceColIndex !== undefined
      ? findPadById(clipboard.sourcePadId)
      : null;
  if (clipboard.action === "cut" && (!source || source.colIndex !== clipboard.sourceColIndex)) return;

  const copiedData = clonePadData(clipboard.padData);
  if (!copiedData.audio) return;

  // Guardamos el ID del destino para no perderlo, pero conservamos el nombre y ajustes del origen
  const targetId = target.settings.id;
  target.audio = copiedData.audio;
  target.settings = {
    ...copiedData.settings,
    id: targetId, // Mantiene el ID del hueco nuevo (ej. A2)
    name: copiedData.settings.name || targetId // Mantiene el nombre del pad copiado
  };

  loadPadAudio(target.settings.id, contextMenu.value.colIndex, target.audio.filePath, target.settings, target.audio);

  if (clipboard.action === "cut" && source) {
    removePadAudio(source.pad.settings.id, source.colIndex);
    source.pad.audio = null;
    source.pad.settings = emptyPadSettings(source.pad.settings.id);
    selectedPadIds.value.delete(source.pad.settings.id);
    clipboardData.value = null;
  }

  markRemixSetDirty();
  closeContextMenu();
}

function sourceBpmForImportedPad(): number {
  const trackBpm = activeStudioTrack.value?.bpm;
  if (typeof trackBpm === "number" && Number.isFinite(trackBpm) && trackBpm > 0) {
    return trackBpm;
  }

  return Number.isFinite(masterBpm.value) && masterBpm.value > 0 ? masterBpm.value : 120;
}

async function loadFileToPad(): Promise<void> {
  const { colIndex, padIndex } = contextMenu.value;
  const pad = remixPads.value[colIndex]?.[padIndex];
  if (!pad) return;

  try {
    const selected = await open({
      multiple: false,
      directory: false,
      filters: [{ name: "Audio files", extensions: ["wav", "mp3", "aif", "aiff", "flac", "m4a", "ogg"] }],
    });
    const selectedPath = typeof selected === "string" ? selected : null;
    if (!selectedPath) return;

    removePadAudio(pad.settings.id, colIndex);
    pad.audio = {
      filePath: selectedPath,
      durationMs: 0,
      originalBpm: 120,
      originalKey: "",
      gridAnchorMs: 0,
      startMs: 0,
      endMs: 0,
      pitchShift: 0,
    };
    pad.settings = {
      ...pad.settings,
      name: pad.settings.name || pad.settings.id,
      transpose: 0,
      loopStart: 0,
      loopEnd: null,
    };
    loadPadAudio(pad.settings.id, colIndex, selectedPath, pad.settings, pad.audio);
  } catch (error) {
    console.error("Unable to load file into Remix Pad:", error);
    showAppToast("Unable to load audio file", "error");
  } finally {
    closeContextMenu();
  }
}

async function importLoopToPad({ colIndex, padIndex, padId }: PadImportTarget): Promise<void> {
  if (isUnanalyzedTrack(activeStudioTrack.value)) {
    showAppToast(MISSING_BPM_GRID_MESSAGE, "warning");
    return;
  }

  const loopRange = activeLoopRange.value;
  const pad = remixPads.value[colIndex]?.[padIndex];

  if (!loopRange || !canImportLoop.value || !pad || isImportingLoop.value) return;

  const sourcePaths = activeSourcePaths.value;
  if (sourcePaths.length === 0) {
    showAppToast("No audio active to extract", "error");
    return;
  }

  isImportingLoop.value = true;
  try {
    const sourceBpm = sourceBpmForImportedPad();
    const sourceKey = activeStudioTrack.value?.key ?? "";
    const result = await invoke<PadExtractionResult>("extract_pad_audio", {
      sourcePaths,
      startSec: loopRange.start,
      endSec: loopRange.end,
      padId,
    });

    pad.audio = {
      filePath: result.file_path,
      durationMs: result.duration_ms,
      originalBpm: sourceBpm,
      originalKey: sourceKey,
      gridAnchorMs: 0,
      startMs: 0,
      endMs: result.duration_ms,
      pitchShift: 0,
    };
    pad.settings = {
      ...pad.settings,
      color: TRAKTOR_COLORS[Math.floor(Math.random() * TRAKTOR_COLORS.length)],
      name: pad.settings.id,
      transpose: sourceTranspose.value,
      fadeInMs: 0,
      fadeOutMs: 0,
      loopStart: 0,
      loopEnd: result.duration_ms / 1000.0,
    };
    loadPadAudio(pad.settings.id, colIndex, result.file_path, pad.settings, pad.audio);
  } catch (error) {
    console.error("Unable to import loop into Remix Pad:", error);
  } finally {
    isImportingLoop.value = false;
  }
}

async function importLoopFromContextMenu(): Promise<void> {
  try {
    await importLoopToPad(contextMenu.value);
  } finally {
    closeContextMenu();
  }
}

async function clearPad(colIndex: number, padIndex: number): Promise<void> {
  const pad = remixPads.value[colIndex]?.[padIndex];
  if (!pad) {
    closeContextMenu();
    return;
  }

  if (pad.audio?.isGenerated) {
    try {
      await invoke("delete_generated_audio", { path: pad.audio.filePath });
    } catch (error) {
      console.error("Failed to clean up generated audio file:", error);
    }
  }

  removePadAudio(pad.settings.id, colIndex);
  pad.audio = null;
  pad.settings = emptyPadSettings(pad.settings.id);
  markRemixSetDirty();
  closeContextMenu();
}

function renamePad(colIndex: number, padIndex: number): void {
  const padId = remixPads.value[colIndex]?.[padIndex]?.settings.id;
  if (!padId) {
    closeContextMenu();
    return;
  }

  renamingPadId.value = padId;
  closeContextMenu();
}

function setPadColor(colIndex: number, padIndex: number, color: string): void {
  const pad = remixPads.value[colIndex]?.[padIndex];
  if (!pad) {
    closeContextMenu();
    return;
  }

  onPadSettingsUpdate(colIndex, padIndex, { ...pad.settings, color });
  closeContextMenu();
}

onMounted(() => {
  window.addEventListener("click", closeContextMenu);
  window.addEventListener("keydown", handlePadShortcutKeyDown);
  window.addEventListener("keyup", handlePadShortcutKeyUp);
});
onBeforeUnmount(() => {
  window.removeEventListener("click", closeContextMenu);
  window.removeEventListener("keydown", handlePadShortcutKeyDown);
  window.removeEventListener("keyup", handlePadShortcutKeyUp);
});

watch(setTitle, () => {
  if (!isApplyingLoadedRemixSet) markRemixSetDirty();
});
</script>

<template>
  <section class="remix-deck" aria-label="Remix Deck">
    <nav class="page-selector" aria-label="Remix Deck page selector">
      <button
        v-for="pageNumber in PAGE_COUNT"
        :key="pageNumber"
        type="button"
        class="page-button"
        :class="{ 'is-active': currentPage === pageNumber - 1 }"
        :aria-label="`Show Remix Deck page ${pageNumber}`"
        :aria-pressed="currentPage === pageNumber - 1"
        :title="`Page ${pageNumber}`"
        @click="currentPage = pageNumber - 1"
      />
    </nav>

    <div class="remix-deck-main">
      <h2 class="remix-set-title">
        <button
          v-if="!isEditingSetTitle"
          type="button"
          class="remix-set-title-button"
          title="Double-click to rename Remix Set"
          @dblclick="beginSetTitleEdit"
          @keydown.enter.prevent="beginSetTitleEdit"
          @keydown.space.prevent="beginSetTitleEdit"
        >
          <span>{{ setTitle }}</span>
          <span class="remix-set-title-edit-hint" aria-hidden="true">✎</span>
        </button>
        <input
          v-else
          ref="setTitleInput"
          v-model="editedSetTitle"
          class="remix-set-title-input"
          type="text"
          name="remix-set-title"
          autocomplete="off"
          aria-label="Remix Set title"
          @blur="finishSetTitleEdit"
          @keydown.enter.prevent="finishSetTitleEdit"
          @keydown.esc.prevent="cancelSetTitleEdit"
        >
      </h2>
      <header class="master-header">
        <div class="master-settings">
          <MasterBpmControl v-model="masterBpm" />
          <div class="quantize-controls">
            <button
              type="button"
              class="quantize-toggle"
              :class="{ 'is-active': globalQuantizeEnabled }"
              :aria-pressed="globalQuantizeEnabled"
              aria-label="Toggle Quantize"
              title="Toggle Quantize"
              @click="globalQuantizeEnabled = !globalQuantizeEnabled"
            >
              Q
            </button>
            <select
              v-model="globalQuantizeValue"
              :disabled="isDeckPlaying"
              class="quantize-select"
              aria-label="Quantize Value"
              title="Quantize value"
            >
              <option value="16n">1/4</option>
              <option value="8n">1/2</option>
              <option value="4n">1</option>
              <option value="2n">2</option>
              <option value="1m">4</option>
              <option value="2m">8</option>
              <option value="4m">16</option>
              <option value="8m">32</option>
            </select>
          </div>
        </div>
        <div class="master-actions" aria-label="Remix Set actions">
          <button
            type="button"
            class="master-action-button batch-edit-toggle"
            :class="{ 'is-active': isBatchEditMode }"
            :aria-pressed="isBatchEditMode"
            aria-label="Toggle Batch Edit"
            title="Toggle Batch Edit"
            @click="toggleBatchEditMode"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M4 6.5 12 3l8 3.5-8 3.5-8-3.5Zm0 5 8 3.5 8-3.5M4 16.5 12 20l8-3.5" />
            </svg>
          </button>
          <span v-if="isBatchEditMode" class="batch-selection-counter" aria-live="polite">
            {{ selectedPadIds.size }} selected
          </span>
          <button
            type="button"
            class="master-action-button new-remix-set-button"
            :disabled="isLoadingSelectedRemixSet"
            aria-label="New Remix Set"
            title="New Remix Set"
            @click="createNewRemixSet"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6 3.5h8l4 4V20.5H6zM14 3.5v4h4M12 11v6m-3-3h6" />
            </svg>
          </button>
          <button
            type="button"
            class="master-action-button load-remix-set-button"
            :disabled="isLoadingRemixSetList || isLoadingSelectedRemixSet"
            aria-label="Load Remix Set"
            title="Load Remix Set"
            @click="openRemixSetLoadModal"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M3.5 7.5h6l2 2H20.5v10h-17zM12 12v5m-2.5-2.5H12m0 0h2.5" />
            </svg>
          </button>
          <button
            type="button"
            class="master-action-button save-remix-set-button"
            :disabled="isExportingRemixSet || !isRemixSetDirty"
            :aria-label="isExportingRemixSet ? 'Saving Remix Set' : 'Save Remix Set'"
            title="Save Remix Set"
            @click="exportRemixSet"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M5 3.5h12l2 2v15H5zM8 3.5v6h7v-6m-7 16v-6h8v6" />
            </svg>
          </button>
        </div>
      </header>

      <div class="remix-grid">
        <RemixColumn
          v-for="(pads, columnIndex) in visiblePadColumns"
          :key="`column-${columnIndex}`"
          :column-index="columnIndex"
          :pads="pads"
          :pad-offset="pageOffset"
          :active-pad-index="activePadIndexes[columnIndex]"
          :editing-pad-id="renamingPadId"
          :is-batch-edit-mode="isBatchEditMode"
          :selected-pad-ids="selectedPadIds"
          :volume="columnVolumes[columnIndex]"
          :filter="columnFilters[columnIndex]"
          :keylock-enabled="columnKeylock[columnIndex]"
          :punch-mode-enabled="columnPunchMode[columnIndex]"
          @pad-press="handlePadPress(columnIndex, $event)"
          @pad-release="handlePadRelease(columnIndex, $event)"
          @context-menu="openContextMenu"
          @update:settings="onPadSettingsUpdate"
          @toggle-selection="togglePadSelection"
          @update:volume="updateColumnVolume(columnIndex, $event)"
          @update:filter="updateColumnFilter(columnIndex, $event)"
          @toggle-keylock="toggleColumnKeylock(columnIndex)"
          @toggle-punch-mode="toggleColumnPunchMode(columnIndex)"
          @stop="handleColumnStop(columnIndex)"
          @end-rename="renamingPadId = null"
        />
      </div>
    </div>

    <div
      v-if="contextMenu.visible"
      ref="contextMenuEl"
      class="context-menu"
      :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }"
      aria-label="Remix Pad actions"
      @click.stop
    >
      <button
        v-if="isBatchEditMode"
        type="button"
        class="context-menu-button"
        @click="selectAllPads"
      >
        Select All
      </button>
      <button
        v-if="contextMenuPad?.audio === null"
        type="button"
        class="context-menu-button"
        :disabled="!canImportLoop || isImportingLoop"
        @click="importLoopFromContextMenu"
      >
        {{ isImportingLoop ? "Importing…" : "Import Loop" }}
      </button>
      <button
        v-if="contextMenuPad?.audio === null"
        type="button"
        class="context-menu-button"
        @click="loadFileToPad"
      >
        Load File…
      </button>
      <button
        v-if="contextMenuPad?.audio"
        type="button"
        class="context-menu-button"
        @click="copyPad"
      >
        Copy Pad
      </button>
      <button
        v-if="contextMenuPad?.audio"
        type="button"
        class="context-menu-button"
        @click="cutPad"
      >
        Cut Pad
      </button>
      <button
        v-if="clipboardData !== null && contextMenuPad?.audio === null"
        type="button"
        class="context-menu-button"
        @click="pastePad"
      >
        Paste Pad
      </button>
      <button
        v-if="contextMenuPad?.audio"
        type="button"
        class="context-menu-button"
        @click="workspaceStore.setEditorMode('pad', contextMenu.padId); closeContextMenu()"
      >
        Edit Audio
      </button>
      <button
        type="button"
        class="context-menu-button"
        @click="renamePad(contextMenu.colIndex, contextMenu.padIndex)"
      >
        Rename Pad
      </button>
      <button
        v-if="contextMenuPad?.audio"
        type="button"
        class="context-menu-button context-menu-button-danger"
        @click="clearPad(contextMenu.colIndex, contextMenu.padIndex)"
      >
        Clear Pad
      </button>
      <div class="color-picker-container" aria-label="Pad color palette">
        <button
          v-for="color in TRAKTOR_COLORS"
          :key="color"
          type="button"
          class="color-swatch"
          :style="{ backgroundColor: color }"
          :aria-label="`Set pad color to ${color}`"
          :aria-pressed="contextMenuPad?.settings.color === color"
          :title="color"
          @click.stop="setPadColor(contextMenu.colIndex, contextMenu.padIndex, color)"
        />
      </div>
    </div>

    <div
      v-if="isLoadRemixSetModalOpen"
      class="remix-set-modal-overlay"
      @click.self="closeRemixSetLoadModal"
    >
      <section
        class="remix-set-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="load-remix-set-title"
      >
        <header class="remix-set-modal-header">
          <h3 id="load-remix-set-title" class="remix-set-modal-title">Load Remix Set</h3>
          <button
            type="button"
            class="remix-set-modal-close"
            :disabled="isLoadingSelectedRemixSet"
            aria-label="Close Load Remix Set"
            @click="closeRemixSetLoadModal"
          >
            ×
          </button>
        </header>

        <p v-if="isLoadingRemixSetList" class="remix-set-modal-status">Reading your Traktor collection…</p>
        <p v-else-if="remixSetLoadError" class="remix-set-modal-error" role="alert">{{ remixSetLoadError }}</p>
        <p v-else-if="availableRemixSetTitles.length === 0" class="remix-set-modal-status">No Remix Sets found.</p>
        <div v-else class="remix-set-list" aria-label="Available Remix Sets">
          <button
            v-for="(title, index) in availableRemixSetTitles"
            :key="`${title}-${index}`"
            type="button"
            class="remix-set-list-button"
            :disabled="isLoadingSelectedRemixSet"
            @click="requestRemixSetLoad(title)"
          >
            {{ isLoadingSelectedRemixSet ? "Loading…" : title }}
          </button>
        </div>

        <footer class="remix-set-modal-footer">
          <button
            type="button"
            class="remix-set-modal-cancel"
            :disabled="isLoadingSelectedRemixSet"
            @click="closeRemixSetLoadModal"
          >
            Cancel
          </button>
        </footer>
      </section>
    </div>

    <div
      v-if="isDiscardChangesDialogOpen"
      class="remix-set-modal-overlay"
      @click.self="cancelDiscardRemixSetChanges"
    >
      <section
        class="remix-set-modal remix-set-discard-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="discard-remix-set-title"
      >
        <h3 id="discard-remix-set-title" class="remix-set-modal-title">Discard unsaved changes?</h3>
        <p class="remix-set-modal-status">
          {{ discardChangesPrompt }}
        </p>
        <footer class="remix-set-modal-footer">
          <button type="button" class="remix-set-modal-cancel" @click="cancelDiscardRemixSetChanges">Cancel</button>
          <button type="button" class="remix-set-modal-discard" @click="discardChangesAndLoadRemixSet">{{ discardChangesActionLabel }}</button>
        </footer>
      </section>
    </div>
  </section>
</template>

<style scoped>
.remix-deck {
  display: flex;
  flex-direction: row;
  width: 100%;
  min-width: 0;
  min-height: 0;
  flex: 1;
  gap: 8px;
  overflow: hidden;
  padding: 0.25rem;
}

.remix-deck-main {
  display: flex;
  min-width: 0;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  gap: 0.25rem;
}

.remix-set-title {
  margin: 0;
  color: #f2f2f2;
  font-size: 1.25rem;
  font-weight: 800;
  line-height: 1.25;
}

.remix-set-title-button,
.remix-set-title-input {
  box-sizing: border-box;
  min-width: 0;
  border: 1px solid transparent;
  border-radius: 0.25rem;
  background: transparent;
  color: inherit;
  font: inherit;
  line-height: inherit;
}

.remix-set-title-button {
  display: inline-flex;
  max-width: 100%;
  align-items: center;
  gap: 0.375rem;
  padding: 0 0.5rem;
  cursor: text;
}

.remix-set-title-edit-hint {
  color: #f7d15f;
  font-size: 0.875rem;
  opacity: 0;
  transition: opacity 0.15s ease;
}

.remix-set-title-button:hover,
.remix-set-title-button:focus-visible {
  border-color: #4d4d52;
  background: rgb(247 209 95 / 8%);
}

.remix-set-title-button:hover .remix-set-title-edit-hint,
.remix-set-title-button:focus-visible .remix-set-title-edit-hint {
  opacity: 1;
}

.remix-set-title-button:focus-visible,
.remix-set-title-input:focus-visible {
  outline: 2px solid #fff;
  outline-offset: 2px;
}

.remix-set-title-input {
  width: min(100%, 28rem);
  padding: 0 0.5rem;
  border-color: #f7d15f;
  outline: none;
}

.master-header {
  display: flex;
  min-height: 2rem;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0 0.5rem;
  border: 1px solid #4d4d52;
  border-radius: 0.25rem;
  background: linear-gradient(90deg, #1e1e22, #2a2924, #1e1e22);
}

.master-settings,
.master-actions {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 0.5rem;
}

.master-actions {
  flex: 0 0 auto;
}

.quantize-controls {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding-left: 0.75rem;
  border-left: 1px solid #4d4d52;
}

.quantize-toggle {
  width: 1.5rem;
  height: 1.5rem;
  padding: 0;
  border: 1px solid #64646b;
  border-radius: 0.125rem;
  background: #2a2a2e;
  color: #f2f2f2;
  cursor: pointer;
  font-size: 0.75rem;
  font-weight: 800;
  line-height: 1;
}

.quantize-toggle:hover {
  border-color: #f7d15f;
  background: #4b3d17;
  color: #f7d15f;
}

.quantize-toggle.is-active {
  border-color: #f7d15f;
  background: #f7d15f;
  color: #17171a;
}

.quantize-select {
  min-width: 3.25rem;
  padding: 0.1875rem 0.375rem;
  border: 1px solid #64646b;
  border-radius: 0.125rem;
  background: #17171a;
  color: #f7d15f;
  cursor: pointer;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.75rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.quantize-select:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.quantize-toggle:focus-visible,
.quantize-select:focus-visible,
.master-action-button:focus-visible,
.batch-edit-toggle:focus-visible,
.load-remix-set-button:focus-visible,
.save-remix-set-button:focus-visible {
  outline: 2px solid #fff;
  outline-offset: 2px;
}

.master-action-button {
  display: inline-flex;
  width: 2rem;
  height: 2rem;
  align-items: center;
  justify-content: center;
  gap: 0.25rem;
  padding: 0;
  border: 1px solid #64646b;
  border-radius: 0.125rem;
  background: #2a2a2e;
  color: #f2f2f2;
  cursor: pointer;
}

.master-action-button svg {
  width: 1rem;
  height: 1rem;
  fill: none;
  stroke: currentColor;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.8;
}

.master-action-button:hover:not(:disabled) {
  border-color: #f7d15f;
  background: #4b3d17;
  color: #f7d15f;
}

.batch-edit-toggle {
  color: #bfdbfe;
}

.batch-edit-toggle.is-active {
  border-color: #3b82f6;
  background: #2563eb;
  color: #fff;
}

.batch-selection-counter {
  color: #bfdbfe;
  font-family: ui-monospace, "Cascadia Code", monospace;
  font-size: 0.625rem;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.save-remix-set-button {
  border: 1px solid #edb40b;
  background: transparent;
  color: #edb40b;
}

.save-remix-set-button:hover:not(:disabled) {
  background: #edb40b;
  color: #17171a;
}

.master-action-button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.remix-set-modal-overlay {
  position: fixed;
  z-index: 1100;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 1rem;
  background: rgb(0 0 0 / 66%);
}

.remix-set-modal {
  display: flex;
  width: min(100%, 25rem);
  max-height: min(30rem, calc(100vh - 2rem));
  flex-direction: column;
  gap: 0.75rem;
  padding: 1rem;
  border: 1px solid #64646b;
  border-radius: 0.25rem;
  background: #202024;
  box-shadow: 0 0.75rem 2rem rgb(0 0 0 / 55%);
}

.remix-set-modal-header,
.remix-set-modal-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.remix-set-modal-title {
  margin: 0;
  color: #f2f2f2;
  font-size: 0.8125rem;
  font-weight: 800;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.remix-set-modal-close,
.remix-set-modal-cancel,
.remix-set-modal-discard,
.remix-set-list-button {
  border: 1px solid #64646b;
  border-radius: 0.125rem;
  background: #2a2a2e;
  color: #f2f2f2;
  cursor: pointer;
}

.remix-set-modal-close {
  width: 1.5rem;
  height: 1.5rem;
  padding: 0;
  font-size: 1.125rem;
  line-height: 1;
}

.remix-set-modal-status,
.remix-set-modal-error {
  margin: 0;
  color: #b8b8bd;
  font-size: 0.75rem;
  line-height: 1.45;
}

.remix-set-modal-error {
  color: #fca5a5;
}

.remix-set-list {
  display: grid;
  min-height: 0;
  gap: 0.375rem;
  overflow-y: auto;
}

.remix-set-list-button {
  width: 100%;
  padding: 0.5rem 0.625rem;
  font-size: 0.75rem;
  font-weight: 700;
  text-align: left;
}

.remix-set-list-button:hover:not(:disabled),
.remix-set-modal-close:hover:not(:disabled),
.remix-set-modal-cancel:hover:not(:disabled) {
  border-color: #f7d15f;
  background: #4b3d17;
  color: #f7d15f;
}

.remix-set-modal-cancel,
.remix-set-modal-discard {
  padding: 0.375rem 0.625rem;
  font-size: 0.6875rem;
  font-weight: 700;
}

.remix-set-discard-dialog {
  width: min(100%, 22rem);
}

.remix-set-modal-discard {
  border-color: #c75050;
  background: #7f2f33;
}

.remix-set-modal-discard:hover {
  background: #9f393f;
}

.remix-set-modal-close:focus-visible,
.remix-set-modal-cancel:focus-visible,
.remix-set-modal-discard:focus-visible,
.remix-set-list-button:focus-visible {
  outline: 2px solid #fff;
  outline-offset: 2px;
}

.remix-set-modal-close:disabled,
.remix-set-modal-cancel:disabled,
.remix-set-list-button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.page-selector {
  display: flex;
  box-sizing: border-box;
  width: 24px;
  min-height: 0;
  flex: 0 0 24px;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
  padding-top: 50px;
}

.page-button {
  height: 32px;
  padding: 0;
  border: 1px solid #555;
  border-radius: 2px;
  background: transparent;
  cursor: pointer;
}

.page-button:hover {
  border-color: #f7d15f;
  background: rgb(247 209 95 / 15%);
}

.page-button.is-active {
  border-color: #f7d15f;
  background: #edb40b;
  box-shadow: inset 0 0 0 1px rgb(255 255 255 / 20%);
}

.page-button:focus-visible {
  outline: 2px solid #fff;
  outline-offset: 2px;
}

.remix-grid {
  display: flex;
  min-width: 0;
  min-height: 0;
  flex: 1;
  gap: 0.5rem;
  overflow-x: hidden;
  overflow-y: auto;
}

.context-menu {
  position: fixed;
  z-index: 1000;
  width: 14.5rem;
  min-width: 0;
  box-sizing: border-box;
  padding: 0.25rem;
  border: 1px solid #5a5a5e;
  border-radius: 0.25rem;
  background: #202024;
  box-shadow: 0 0.5rem 1.25rem rgb(0 0 0 / 45%);
}

.context-menu-button {
  width: 100%;
  padding: 0.5rem 0.625rem;
  border: 0;
  border-radius: 0.125rem;
  background: transparent;
  color: #f2f2f2;
  cursor: pointer;
  font-size: 0.75rem;
  font-weight: 700;
  text-align: left;
}

.context-menu-button:hover:not(:disabled) {
  background: rgb(247 209 95 / 18%);
  color: #f7d15f;
}

.context-menu-button:focus-visible {
  outline: 2px solid #fff;
  outline-offset: 2px;
}

.context-menu-button:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.context-menu-button-danger {
  color: #f87171;
}

.context-menu-button-danger:hover:not(:disabled) {
  background: rgb(248 113 113 / 18%);
  color: #fecaca;
}

.color-picker-container {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 0.5rem;
  justify-items: center;
  margin-top: 0.5rem;
  padding: 0.25rem 0;
  border-top: 1px solid #444;
}

.color-swatch {
  width: 1.25rem;
  height: 1.25rem;
  padding: 0;
  border: 1px solid rgb(255 255 255 / 20%);
  border-radius: 50%;
  cursor: pointer;
  touch-action: manipulation;
  transition: transform 0.1s ease;
}

.color-swatch:hover {
  transform: scale(1.1);
}

.color-swatch:focus-visible {
  outline: 2px solid #f7d15f;
  outline-offset: 2px;
}

@media (prefers-reduced-motion: reduce) {
  .color-swatch,
  .remix-set-title-edit-hint {
    transition: none;
  }
}
</style>
