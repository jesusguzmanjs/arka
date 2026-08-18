import { convertFileSrc } from "@tauri-apps/api/core";
import * as Tone from "tone";
import { computed, onScopeDispose, reactive, ref, shallowRef, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { activeAudioEngine } from "./useGlobalAudio";
import { useAppToast } from "./useAppToast";
import { useWorkspaceStore } from "../stores/useWorkspaceStore";
import { TRAKTOR_COLORS, type PadAudioData, type PadSettings, type RemixSetPayload } from "../types/remix";

const COLUMN_COUNT = 4;
const EMPTY_PAD_COLOR = "#333333";
const QUANTIZE_VALUES = ["16n", "8n", "4n", "2n", "1m", "2m", "4m", "8m"] as const;

let remixAudioState: ReturnType<typeof createRemixAudioState> | null = null;

interface RemixChannel {
  filter: Tone.Filter;
  volume: Tone.Volume;
}

interface LoadedPadPlayer {
  player: Tone.GrainPlayer;
  originalBpm: number;
  sync: boolean;
  columnIndex: number;
  settings: PadSettings;
}

// Transformador seguro de volumen a decibelios (evita NaN)
const mapGainToDb = (value: number | undefined): number => {
  const val = typeof value === "number" && Number.isFinite(value) ? value : 0;
  return val >= 0 ? val * 12 : val * 24;
};

// Calculador seguro del final de bucle (evita bucles de 0 segundos)
// Calculador seguro del final de bucle (evita bucles de 0 segundos y errores de rango/ms)
function calculateValidLoopEnd(settings: PadSettings, bufferDuration: number): number {
  const start = settings.loopStart ?? 0;
  let end = settings.loopEnd;

  // 1. Si no hay end válido, o es <= start, devolvemos la duración total del buffer
  if (typeof end !== "number" || !Number.isFinite(end) || end <= start) {
    return bufferDuration > start ? bufferDuration : start + 0.1;
  }

  // 2. Si detectamos que viene en milisegundos (ej: 34926 en vez de 34.92), lo pasamos a segundos
  if (end > bufferDuration * 2 && end > 1000) {
    end = end / 1000;
  }

  // 3. BLINDAJE ANTI-CRASH: Math.min asegura que NUNCA supere la duración real del buffer
  return Math.min(Math.max(start + 0.1, end), bufferDuration);
}

function emptyPadSettings(id: string): PadSettings {
  return {
    id,
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
    isReversed: false,
    loopStart: 0,
    loopEnd: null,
  };
}

/** Owns the four independent Remix Deck audio channels and their pad players. */
function createRemixAudioState() {
  const workspaceStore = useWorkspaceStore();

  // Creamos la cadena de audio por canal: Filter -> Volume -> Output
  const channels: RemixChannel[] = Array.from({ length: COLUMN_COUNT }, () => {
    const filter = new Tone.Filter(20000, "lowpass");
    const volume = new Tone.Volume(0);
    filter.connect(volume);
    volume.toDestination();
    return { filter, volume };
  });

  const players = new Map<string, LoadedPadPlayer>();
  const activePads = reactive<(string | null)[]>(Array.from({ length: COLUMN_COUNT }, () => null));
  const columnFilters = reactive<number[]>(Array.from({ length: COLUMN_COUNT }, () => 0));
  const columnKeylock = reactive<boolean[]>(Array.from({ length: COLUMN_COUNT }, () => true));
  const columnPunchMode = reactive<boolean[]>(Array.from({ length: COLUMN_COUNT }, () => false));
  const columnStartTimes = reactive<number[]>(Array.from({ length: COLUMN_COUNT }, () => 0));
  const oneShotCompletionEvents = new Map<string, number>();
  const masterBpm = shallowRef(120);
  const globalQuantizeEnabled = shallowRef(true);
  const globalQuantizeValue = shallowRef("1m");
  const isRemixSetDirty = shallowRef(false);
  const originalRemixSetPayload = ref<RemixSetPayload | null>(null);
  const currentRemixSetTitle = ref("");
  const { showAppToast } = useAppToast();
  let isLoadingRemixSet = false;
  const isDeckPlaying = computed(() => activePads.some((id) => id !== null));

  function markRemixSetDirty(): void {
    if (!isLoadingRemixSet) isRemixSetDirty.value = true;
  }

  function setRemixSetDirty(value: boolean): void {
    isRemixSetDirty.value = value;
  }

  function channelFor(colIndex: number): RemixChannel | null {
    return channels[colIndex] ?? null;
  }

  function clearOneShotCompletion(padId: string): void {
    const eventId = oneShotCompletionEvents.get(padId);
    if (eventId !== undefined) Tone.Transport.clear(eventId);
    oneShotCompletionEvents.delete(padId);
  }

  function scheduleOneShotCompletion(
      padId: string,
      colIndex: number,
      player: Tone.GrainPlayer,
      startTime: number | string,
      offset: number,
  ): void {
    clearOneShotCompletion(padId);

    const playbackRate = Math.max(Math.abs(player.playbackRate), 0.01);
    const duration = Math.max(0, (player.buffer.duration - offset) / playbackRate);
    if (!Number.isFinite(duration) || duration <= 0) return;

    const completionTime = typeof startTime === "number"
        ? startTime + duration
        : `${startTime} + ${duration}`;
    const eventId = Tone.Transport.scheduleOnce(() => {
      oneShotCompletionEvents.delete(padId);
      if (activePads[colIndex] === padId) stopColumn(colIndex);
    }, completionTime);
    oneShotCompletionEvents.set(padId, eventId);
  }

  async function initAudio(): Promise<void> {
    await Tone.start();
    Tone.Transport.bpm.value = masterBpm.value;
    if (Tone.Transport.state !== "started") Tone.Transport.start();
  }

  function setPlaybackRate(loadedPlayer: LoadedPadPlayer, colIndex: number, padSettings: PadSettings): void {
    const origBpm = Number(loadedPlayer.originalBpm);
    const gBpm = Number(masterBpm.value);
    const baseDetune = (Number(padSettings.transpose) || 0) * 100;

    if (loadedPlayer.sync && Number.isFinite(origBpm) && origBpm > 0 && Number.isFinite(gBpm) && gBpm > 0) {
      const rate = gBpm / origBpm;
      loadedPlayer.player.playbackRate = Number.isFinite(rate) && rate > 0 ? rate : 1;
      if (!columnKeylock[colIndex]) {
        const pitchShiftCents = 1200 * Math.log2(loadedPlayer.player.playbackRate);
        loadedPlayer.player.detune = baseDetune + pitchShiftCents;
      } else {
        loadedPlayer.player.detune = baseDetune;
      }
    } else {
      loadedPlayer.player.playbackRate = 1;
      loadedPlayer.player.detune = baseDetune;
    }
  }

  function applyMasterBpm(newBpm: number): void {
    Tone.Transport.bpm.value = newBpm;
    for (const loadedPlayer of players.values()) {
      setPlaybackRate(loadedPlayer, loadedPlayer.columnIndex, loadedPlayer.settings);
    }
  }

  function setGlobalBpm(newBpm: number): void {
    if (!Number.isFinite(newBpm) || newBpm <= 0) return;
    masterBpm.value = newBpm;
  }

  function stopAllPads(): void {
    for (const padId of oneShotCompletionEvents.keys()) clearOneShotCompletion(padId);
    for (const { player } of players.values()) player.stop();
    for (let columnIndex = 0; columnIndex < COLUMN_COUNT; columnIndex += 1) {
      activePads[columnIndex] = null;
    }
  }

  function loadPadAudio(
      padId: string,
      colIndex: number,
      filePath: string,
      settings: PadSettings,
      audio: PadAudioData,
  ): void {
    const channel = channelFor(colIndex);
    if (!channel) return;

    markRemixSetDirty();

    const existingPlayer = players.get(padId);
    if (existingPlayer) {
      clearOneShotCompletion(padId);
      existingPlayer.player.dispose();
      players.delete(padId);
    }
    if (activePads[colIndex] === padId) activePads[colIndex] = null;

    const player = new Tone.GrainPlayer({
      url: convertFileSrc(filePath),
      loop: settings.playType === "loop",
      grainSize: 0.2,
      overlap: 0.1,
      onload: () => {
        const dur = player.buffer.duration;
        player.loopStart = settings.loopStart ?? 0;
        player.loopEnd = calculateValidLoopEnd(settings, dur);
      },
    });

    player.volume.value = mapGainToDb(settings.volume);
    player.detune = (Number(settings.transpose) || 0) * 100;
    player.reverse = Boolean(settings.isReversed);
    player.loopStart = settings.loopStart ?? 0;

    if (player.buffer?.loaded) {
      player.loopEnd = calculateValidLoopEnd(settings, player.buffer.duration);
    }

    player.onstop = () => {
      if (activePads[colIndex] === padId && player.state !== "started") activePads[colIndex] = null;
    };

    // Conectamos el reproductor al nodo de Filtro de la columna correspondiente
    player.connect(channel.filter);

    const loadedPlayer = {
      player,
      originalBpm: audio.originalBpm,
      sync: settings.sync,
      columnIndex: colIndex,
      settings,
    };

    setPlaybackRate(loadedPlayer, colIndex, settings);
    players.set(padId, loadedPlayer);
  }

  async function pressPad(padId: string, colIndex: number, settings: PadSettings): Promise<void> {
    const loadedPlayer = players.get(padId);
    if (!loadedPlayer || !channelFor(colIndex)) return;
    clearOneShotCompletion(padId);

    activeAudioEngine.value = "remix";
    await initAudio();

    loadedPlayer.sync = settings.sync;
    loadedPlayer.settings = settings;
    setPlaybackRate(loadedPlayer, colIndex, settings);

    const dur = loadedPlayer.player.buffer.duration;
    loadedPlayer.player.detune = (Number(settings.transpose) || 0) * 100;
    loadedPlayer.player.reverse = Boolean(settings.isReversed);
    loadedPlayer.player.loopStart = settings.loopStart ?? 0;
    loadedPlayer.player.loopEnd = calculateValidLoopEnd(settings, dur);

    if (!isDeckPlaying.value) Tone.Transport.position = 0;

    let startTime: any;
    if (!settings.sync || !isDeckPlaying.value || !globalQuantizeEnabled.value) {
      startTime = Tone.now();
    } else {
      startTime = `@${globalQuantizeValue.value}`;
    }

    const activePadId = activePads[colIndex];
    const isColumnPlaying = activePadId !== null;
    const isRetriggering = activePadId === padId;
    let offset = settings.loopStart ?? 0;
    if (isColumnPlaying && columnPunchMode[colIndex] && !isRetriggering) {
      const elapsedSeconds = Tone.Transport.seconds - columnStartTimes[colIndex];
      const loopLength = loadedPlayer.player.loopEnd - loadedPlayer.player.loopStart;
      if (elapsedSeconds > 0 && loopLength > 0) {
        offset = loadedPlayer.player.loopStart + (elapsedSeconds % loopLength);
      }
    } else if (!isColumnPlaying || isRetriggering) {
      columnStartTimes[colIndex] = Tone.Transport.seconds;
    }

    if (activePadId && activePadId !== padId) {
      clearOneShotCompletion(activePadId);
      players.get(activePadId)?.player.stop(startTime);
      activePads[colIndex] = null;
    }

    const isPlaying = loadedPlayer.player.state === "started";
    if (isPlaying) loadedPlayer.player.stop(startTime);
    loadedPlayer.player.start(startTime, offset);
    activePads[colIndex] = padId;
    if (settings.playType !== "loop") {
      scheduleOneShotCompletion(padId, colIndex, loadedPlayer.player, startTime, offset);
    }
  }

  function releasePad(padId: string, colIndex: number, settings: PadSettings): void {
    if (settings.triggerMode !== "gate") return;

    clearOneShotCompletion(padId);
    players.get(padId)?.player.stop();
    if (activePads[colIndex] === padId) activePads[colIndex] = null;
  }

  function stopColumn(colIndex: number): void {
    const activePadId = activePads[colIndex];
    if (!activePadId) return;

    clearOneShotCompletion(activePadId);
    players.get(activePadId)?.player.stop();
    activePads[colIndex] = null;
  }

  function removePadAudio(padId: string, colIndex: number): void {
    markRemixSetDirty();
    clearOneShotCompletion(padId);
    if (activePads[colIndex] === padId) activePads[colIndex] = null;

    const player = players.get(padId);
    if (!player) return;

    player.player.stop();
    player.player.dispose();
    players.delete(padId);
  }

  function updatePlayerLoop(padId: string, isLoop: boolean): void {
    markRemixSetDirty();
    const player = players.get(padId);
    if (player) player.player.loop = isLoop;
  }

  function updatePlayerLoopRange(padId: string, loopStart: number, loopEnd: number | null): void {
    markRemixSetDirty();
    const loadedPlayer = players.get(padId);
    if (!loadedPlayer) return;

    loadedPlayer.settings = { ...loadedPlayer.settings, loopStart, loopEnd };
    loadedPlayer.player.loopStart = loopStart;
    loadedPlayer.player.loopEnd = calculateValidLoopEnd(
        loadedPlayer.settings,
        loadedPlayer.player.buffer.duration,
    );
  }

  function updatePlayerTranspose(padId: string, transpose: number): void {
    markRemixSetDirty();
    const loadedPlayer = players.get(padId);
    if (!loadedPlayer) return;

    loadedPlayer.settings = { ...loadedPlayer.settings, transpose };
    loadedPlayer.player.detune = transpose * 100;
  }

  function updatePlayerReverse(padId: string, isReversed: boolean): void {
    markRemixSetDirty();
    const player = players.get(padId);
    if (player) player.player.reverse = isReversed;
  }

  function updatePlayerVolume(padId: string, volume: number): void {
    markRemixSetDirty();
    const player = players.get(padId);
    if (player) player.player.volume.value = mapGainToDb(volume);
  }

  function updatePlayerSync(padId: string, sync: boolean): void {
    markRemixSetDirty();
    const player = players.get(padId);
    if (!player) return;

    player.sync = sync;
    player.settings = { ...player.settings, sync };
    setPlaybackRate(player, player.columnIndex, player.settings);
  }

  function toggleColumnKeylock(colIndex: number): void {
    if (!channelFor(colIndex)) return;

    columnKeylock[colIndex] = !columnKeylock[colIndex];
    markRemixSetDirty();
    const activePadId = activePads[colIndex];
    if (!activePadId) return;

    const activePlayer = players.get(activePadId);
    if (activePlayer) setPlaybackRate(activePlayer, colIndex, activePlayer.settings);
  }

  function toggleColumnPunchMode(colIndex: number): void {
    if (!channelFor(colIndex)) return;

    columnPunchMode[colIndex] = !columnPunchMode[colIndex];
    markRemixSetDirty();
  }

  function setColumnVolume(colIndex: number, value: number): void {
    const channel = channelFor(colIndex);
    if (!channel) return;

    channel.volume.volume.value = Tone.gainToDb(Math.min(1, Math.max(0, value)));
  }

  function setColumnFilter(colIndex: number, value: number): void {
    const channel = channelFor(colIndex);
    if (!channel) return;

    columnFilters[colIndex] = value;
    markRemixSetDirty();

    const RAMP_TIME = 0.02;

    if (value < -0.01) {
      channel.filter.type = "lowpass";
      const absVal = Math.abs(value);
      const targetFreq = 20000 * Math.pow(180 / 20000, absVal);
      channel.filter.frequency.rampTo(targetFreq, RAMP_TIME);
    } else if (value > 0.01) {
      channel.filter.type = "highpass";
      const targetFreq = 20 * Math.pow(9000 / 20, value);
      channel.filter.frequency.rampTo(targetFreq, RAMP_TIME);
    } else {
      channel.filter.type = "lowpass";
      channel.filter.frequency.rampTo(20000, RAMP_TIME);
    }
  }

  function clearRemixPads(): void {
    for (const [colIndex, columnPads] of workspaceStore.remixPads.entries()) {
      for (const pad of columnPads) {
        removePadAudio(pad.settings.id, colIndex);
        pad.settings = emptyPadSettings(pad.settings.id);
        pad.audio = null;
      }
    }
  }

  function resetRemixSetToDefaults(): void {
    isLoadingRemixSet = true;
    try {
      masterBpm.value = 120;
      globalQuantizeEnabled.value = true;
      globalQuantizeValue.value = "4n";
      for (let colIndex = 0; colIndex < COLUMN_COUNT; colIndex += 1) {
        columnKeylock[colIndex] = true;
        columnPunchMode[colIndex] = false;
        setColumnFilter(colIndex, 0);
      }
      clearRemixPads();
    } finally {
      isLoadingRemixSet = false;
      isRemixSetDirty.value = false;
      originalRemixSetPayload.value = null;
      currentRemixSetTitle.value = "";
    }
  }

  function loadRemixSetPayload(payload: RemixSetPayload): void {
    isLoadingRemixSet = true;
    try {
      masterBpm.value = Number.isFinite(payload.bpm) && payload.bpm > 0 ? payload.bpm : 120;
      globalQuantizeEnabled.value = payload.quantize_state === 1;
      globalQuantizeValue.value = QUANTIZE_VALUES[payload.quantize_value] ?? "1m";

      for (let colIndex = 0; colIndex < COLUMN_COUNT; colIndex += 1) {
        const column = payload.columns[colIndex];
        columnKeylock[colIndex] = column?.keylock === 1;
        columnPunchMode[colIndex] = column?.punchmode === 1;
        setColumnFilter(colIndex, 0);
      }

      clearRemixPads();

      for (const incomingPad of payload.pads) {
        const location = workspaceStore.findRemixPad(incomingPad.id);
        if (!location) continue;

        const startMs = Number.isFinite(incomingPad.start_ms) ? incomingPad.start_ms : 0;
        const endMs = Number.isFinite(incomingPad.end_ms) ? incomingPad.end_ms : startMs;
        const color = TRAKTOR_COLORS[incomingPad.color_id - 1] ?? EMPTY_PAD_COLOR;
        const settings: PadSettings = {
          id: incomingPad.id,
          name: incomingPad.name,
          color,
          playType: incomingPad.type === 0 ? "loop" : "one-shot",
          triggerMode: incomingPad.mode === 1 ? "gate" : "trigger",
          sync: incomingPad.sync === 1,
          reverse: incomingPad.reverse === 1,
          keylock: columnKeylock[location.columnIndex],
          volume: Math.max(-1, Math.min(1, incomingPad.gain * 2 - 1)),
          filter: 0,
          transpose: incomingPad.transpose,
          isReversed: incomingPad.reverse === 1,
          loopStart: startMs / 1000,
          loopEnd: endMs / 1000,
        };
        const audio: PadAudioData = {
          filePath: incomingPad.path,
          durationMs: incomingPad.duration_ms || Math.max(0, endMs - startMs),
          originalBpm: incomingPad.bpm,
          originalKey: incomingPad.key || "",
          gridAnchorMs: 0,
          startMs,
          endMs,
          pitchShift: incomingPad.transpose,
        };

        location.pad.settings = settings;
        location.pad.audio = audio;
        loadPadAudio(settings.id, location.columnIndex, audio.filePath, settings, audio);
      }

      originalRemixSetPayload.value = JSON.parse(JSON.stringify(payload)) as RemixSetPayload;
      currentRemixSetTitle.value = payload.title;
    } finally {
      isLoadingRemixSet = false;
      isRemixSetDirty.value = false;
    }
  }

  async function syncActiveRemixSet(nmlPath: string | null): Promise<void> {
    if (!originalRemixSetPayload.value || !currentRemixSetTitle.value) return;

    try {
      const output = await invoke<string>("call_cuegrid_core", {
        args: ["--get-remix-set", currentRemixSetTitle.value],
        nmlPath,
      });
      const payload = JSON.parse(output) as RemixSetPayload;

      if (JSON.stringify(payload) === JSON.stringify(originalRemixSetPayload.value)) return;

      loadRemixSetPayload(payload);
      showAppToast("Remix Set updated from Traktor");
    } catch {
      resetRemixSetToDefaults();
      showAppToast("Remix Set was deleted in Traktor", "error");
    }
  }

  function disposeAudio(): void {
    stopAllPads();
    for (const { player } of players.values()) player.dispose();
    players.clear();
    for (const channel of channels) {
      channel.filter.dispose();
      channel.volume.dispose();
    }
  }

  watch(activeAudioEngine, (newEngine) => {
    if (newEngine !== "remix") {
      stopAllPads();
      Tone.Transport.pause();
    }
  }, { flush: "sync" });

  watch(masterBpm, (newBpm) => {
    if (!Number.isFinite(newBpm) || newBpm <= 0) return;
    applyMasterBpm(newBpm);
    markRemixSetDirty();
  }, { flush: "sync" });

  watch([globalQuantizeEnabled, globalQuantizeValue], markRemixSetDirty, { flush: "sync" });

  onScopeDispose(disposeAudio);

  return {
    activePads,
    columnFilters,
    columnKeylock,
    columnPunchMode,
    masterBpm,
    globalQuantizeEnabled,
    globalQuantizeValue,
    isRemixSetDirty,
    originalRemixSetPayload,
    currentRemixSetTitle,
    isDeckPlaying,
    initAudio,
    loadPadAudio,
    loadRemixSetPayload,
    markRemixSetDirty,
    pressPad,
    removePadAudio,
    releasePad,
    stopColumn,
    setColumnFilter,
    setColumnVolume,
    setGlobalBpm,
    setRemixSetDirty,
    syncActiveRemixSet,
    resetRemixSetToDefaults,
    toggleColumnKeylock,
    toggleColumnPunchMode,
    updatePlayerLoop,
    updatePlayerLoopRange,
    updatePlayerReverse,
    updatePlayerSync,
    updatePlayerTranspose,
    updatePlayerVolume,
  };
}

/** Returns the shared Remix Deck engine used by the deck and Pad Edit Mode. */
export function useRemixAudio() {
  remixAudioState ??= createRemixAudioState();
  return remixAudioState;
}

/** Reconciles the loaded Remix Set with the collection after Traktor closes. */
export async function syncActiveRemixSet(nmlPath: string | null): Promise<void> {
  await useRemixAudio().syncActiveRemixSet(nmlPath);
}