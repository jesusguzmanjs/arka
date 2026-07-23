import { dirname, join } from "@tauri-apps/api/path";
import { readDir, readTextFile } from "@tauri-apps/plugin-fs";
import { computed, shallowRef, watch, type CSSProperties } from "vue";
import { useConfigState } from "./useConfigState";

export type DeckId = 0 | 1 | 2 | 3;
export type SessionHistoryStatus = "idle" | "discovering" | "loading" | "ready" | "empty" | "no-events" | "error";

export interface HistoryFileDescriptor {
  path: string;
  filename: string;
  startedAt: Date;
  displayLabel: string;
}

interface HistoryTrack {
  primaryKey: string;
  primaryKeyType: string;
  title: string;
  artist: string;
  bpm: number | null;
  key: string;
}

export interface HistoryEvent extends HistoryTrack {
  deck: DeckId;
  startTimeSeconds: number;
  durationSeconds: number;
  sourceOrder: number;
  playedPublic: boolean;
}

export interface TimelineGap {
  realStartOffsetSeconds: number;
  realEndOffsetSeconds: number;
  realDurationSeconds: number;
  renderedDurationSeconds: number;
  renderedStartOffsetSeconds: number;
  renderedEndOffsetSeconds: number;
}

export interface SessionTimeline {
  events: HistoryEvent[];
  originSeconds: number;
  endSeconds: number;
  durationSeconds: number;
  renderedDurationSeconds: number;
  gaps: TimelineGap[];
  warnings: string[];
}

export const INACTIVITY_THRESHOLD_SECONDS = 15 * 60;
export const COMPRESSED_GAP_SECONDS = 150;

interface CollectionTrack {
  title: string;
  artist: string;
  bpm: number | null;
  key: string;
}

const HISTORY_FILENAME = /^history_(\d{4})y(\d{2})m(\d{2})d_(\d{2})h(\d{2})m(\d{2})s\.nml$/i;
const FLOAT_ATTRIBUTE = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i;
const TRAKTOR_OPEN_KEY_MAP = [
  "8d", "8m", "3d", "3m", "10d", "10m", "5d", "5m", "12d", "12m", "7d", "7m",
  "2d", "2m", "9d", "9m", "4d", "4m", "11d", "11m", "6d", "6m", "1d", "1m",
] as const;

// Module-scoped singleton state. SessionHistoryView is mounted anew whenever
// the user changes tabs, so state must outlive an individual view instance.
const { nmlPathOverride } = useConfigState();
const files = shallowRef<HistoryFileDescriptor[]>([]);
const filterDate = shallowRef<string | null>(null);
const selectedFile = shallowRef<HistoryFileDescriptor | null>(null);
const timeline = shallowRef<SessionTimeline | null>(null);
const status = shallowRef<SessionHistoryStatus>("idle");
const errorMessage = shallowRef<string | null>(null);
const warningCount = computed(() => timeline.value?.warnings.length ?? 0);
const filteredFiles = computed(() => {
  const selectedDate = filterDate.value;
  if (!selectedDate) return files.value;
  return files.value.filter((file) => {
    const localDate = `${file.startedAt.getFullYear()}-${String(file.startedAt.getMonth() + 1).padStart(2, "0")}-${String(file.startedAt.getDate()).padStart(2, "0")}`;
    return localDate === selectedDate;
  });
});

let discoverySequence = 0;
let selectionSequence = 0;
let hasDiscoveredCollectionPath = false;
let discoveredCollectionPath: string | null | undefined;

function directChild(parent: Element, tagName: string): Element | null {
  return Array.from(parent.children).find((child) => child.tagName === tagName) ?? null;
}

function floatAttribute(element: Element, name: string): number | null {
  const value = element.getAttribute(name);
  const trimmedValue = value?.trim();
  if (!trimmedValue || !FLOAT_ATTRIBUTE.test(trimmedValue)) return null;
  const numberValue = Number.parseFloat(trimmedValue);
  return Number.isFinite(numberValue) ? numberValue : null;
}

function deckAttribute(element: Element): DeckId | null {
  const value = element.getAttribute("DECK");
  if (value === null || !/^\d+$/.test(value.trim())) return null;
  const deck = Number.parseInt(value, 10);
  return deck >= 0 && deck <= 3 ? deck as DeckId : null;
}

function collectionKey(location: Element): string | null {
  const volume = location.getAttribute("VOLUME");
  const directory = location.getAttribute("DIR");
  const filename = location.getAttribute("FILE");
  if (volume === null || directory === null || filename === null) return null;
  return `${volume}${directory}${filename}`;
}

function roundedTempo(entry: Element): number | null {
  const tempo = directChild(entry, "TEMPO");
  const bpm = tempo ? floatAttribute(tempo, "BPM") : null;
  return bpm === null || bpm < 0 ? null : Math.round(bpm * 100) / 100;
}

export function traktorKeyToOpenKey(value: string | null): string {
  if (value === null || !/^\d+$/.test(value.trim())) return "";
  return TRAKTOR_OPEN_KEY_MAP[Number.parseInt(value, 10)] ?? "";
}

function parseHistoryFilename(filename: string): HistoryFileDescriptor | null {
  const match = HISTORY_FILENAME.exec(filename);
  if (!match) return null;

  const [, yearText, monthText, dayText, hourText, minuteText, secondText] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = Number(secondText);
  const startedAt = new Date(year, month - 1, day, hour, minute, second);

  if (
    startedAt.getFullYear() !== year ||
    startedAt.getMonth() !== month - 1 ||
    startedAt.getDate() !== day ||
    startedAt.getHours() !== hour ||
    startedAt.getMinutes() !== minute ||
    startedAt.getSeconds() !== second
  ) {
    return null;
  }

  return {
    path: "",
    filename,
    startedAt,
    displayLabel: new Intl.DateTimeFormat("en-US", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(startedAt),
  };
}

export function parseSessionHistory(xml: string): SessionTimeline | null {
  const doc = new DOMParser().parseFromString(xml, "application/xml");
  if (doc.querySelector("parsererror")) {
    throw new Error("The selected history file is not valid XML.");
  }

  const root = doc.documentElement;
  const collection = directChild(root, "COLLECTION");
  const collectionIndex = new Map<string, CollectionTrack | null>();
  const warnings: string[] = [];

  if (collection) {
    for (const entry of Array.from(collection.children).filter((child) => child.tagName === "ENTRY")) {
      const location = directChild(entry, "LOCATION");
      if (!location) continue;
      const key = collectionKey(location);
      if (!key) continue;

      if (collectionIndex.has(key)) {
        collectionIndex.set(key, null);
        continue;
      }

      collectionIndex.set(key, {
        title: entry.getAttribute("TITLE") ?? "",
        artist: entry.getAttribute("ARTIST") ?? "",
        bpm: roundedTempo(entry),
        key: traktorKeyToOpenKey(directChild(entry, "MUSICAL_KEY")?.getAttribute("VALUE") ?? null),
      });
    }
  }

  const events: HistoryEvent[] = [];
  let sourceOrder = 0;
  const historyEntries = Array.from(doc.querySelectorAll('NODE[NAME="HISTORY"] > PLAYLIST > ENTRY')).filter(
    (entry) => entry.parentElement?.parentElement?.getAttribute("TYPE") === "PLAYLIST",
  );

  for (const entry of historyEntries) {
    const eventOrder = sourceOrder++;
    const primaryKey = directChild(entry, "PRIMARYKEY");
    const extendedData = directChild(entry, "EXTENDEDDATA");
    if (!primaryKey || !extendedData) {
      warnings.push("Skipped a history entry with missing playback metadata.");
      continue;
    }

    const key = primaryKey.getAttribute("KEY");
    const track = key ? collectionIndex.get(key) : undefined;
    if (!key || track === undefined) {
      warnings.push("Skipped a history entry whose track was not found in the embedded collection.");
      continue;
    }
    if (track === null) {
      warnings.push("Skipped a history entry with an ambiguous collection location.");
      continue;
    }

    const deck = deckAttribute(extendedData);
    const startTimeSeconds = floatAttribute(extendedData, "STARTTIME");
    const durationSeconds = floatAttribute(extendedData, "DURATION");
    if (
      deck === null ||
      startTimeSeconds === null ||
      startTimeSeconds < 0 ||
      durationSeconds === null ||
      durationSeconds <= 0 ||
      !Number.isFinite(startTimeSeconds + durationSeconds)
    ) {
      warnings.push("Skipped a history entry with invalid deck or timing data.");
      continue;
    }

    events.push({
      primaryKey: key,
      primaryKeyType: primaryKey.getAttribute("TYPE") ?? "",
      title: track.title,
      artist: track.artist,
      bpm: track.bpm,
      key: track.key,
      deck,
      startTimeSeconds,
      durationSeconds,
      sourceOrder: eventOrder,
      playedPublic: extendedData.getAttribute("PLAYEDPUBLIC") === "1",
    });
  }

  if (events.length === 0) return null;

  events.sort((left, right) => left.startTimeSeconds - right.startTimeSeconds || left.sourceOrder - right.sourceOrder);
  const originSeconds = events[0].startTimeSeconds;
  const endSeconds = Math.max(...events.map((event) => event.startTimeSeconds + event.durationSeconds));
  const durationSeconds = endSeconds - originSeconds;

  if (!Number.isFinite(durationSeconds) || durationSeconds <= 0) return null;

  const gaps: TimelineGap[] = [];
  let activityEndSeconds = events[0].startTimeSeconds + events[0].durationSeconds;
  let removedSeconds = 0;
  for (const event of events.slice(1)) {
    const gapDuration = event.startTimeSeconds - activityEndSeconds;
    if (gapDuration > INACTIVITY_THRESHOLD_SECONDS) {
      const realStartOffsetSeconds = activityEndSeconds - originSeconds;
      const realEndOffsetSeconds = event.startTimeSeconds - originSeconds;
      const renderedStartOffsetSeconds = realStartOffsetSeconds - removedSeconds;
      gaps.push({
        realStartOffsetSeconds,
        realEndOffsetSeconds,
        realDurationSeconds: gapDuration,
        renderedDurationSeconds: COMPRESSED_GAP_SECONDS,
        renderedStartOffsetSeconds,
        renderedEndOffsetSeconds: renderedStartOffsetSeconds + COMPRESSED_GAP_SECONDS,
      });
      removedSeconds += gapDuration - COMPRESSED_GAP_SECONDS;
    }
    activityEndSeconds = Math.max(activityEndSeconds, event.startTimeSeconds + event.durationSeconds);
  }

  return { events, originSeconds, endSeconds, durationSeconds, renderedDurationSeconds: durationSeconds - removedSeconds, gaps, warnings };
}

export function renderedOffsetSeconds(realOffsetSeconds: number, timeline: SessionTimeline): number {
  return realOffsetSeconds - timeline.gaps
    .filter((gap) => gap.realEndOffsetSeconds <= realOffsetSeconds)
    .reduce((removed, gap) => removed + gap.realDurationSeconds - gap.renderedDurationSeconds, 0);
}

export function timelineBlockStyle(
  event: HistoryEvent,
  timeline: SessionTimeline,
  pixelsPerSecond: number,
): CSSProperties {
  const offsetSeconds = renderedOffsetSeconds(event.startTimeSeconds - timeline.originSeconds, timeline);

  return {
    left: `${offsetSeconds * pixelsPerSecond}px`,
    width: `${event.durationSeconds * pixelsPerSecond}px`,
  };
}

async function discoverHistoryFiles(collectionNmlPath = nmlPathOverride.value): Promise<void> {
  const sequence = ++discoverySequence;
  const collectionPathChanged = hasDiscoveredCollectionPath && collectionNmlPath !== discoveredCollectionPath;
  hasDiscoveredCollectionPath = true;
  discoveredCollectionPath = collectionNmlPath;

  if (collectionPathChanged) {
    ++selectionSequence;
    files.value = [];
    selectedFile.value = null;
    timeline.value = null;
    errorMessage.value = null;
  }

  if (!collectionNmlPath) {
    status.value = "error";
    errorMessage.value = "A collection.nml path is required before session history can be read.";
    return;
  }

  status.value = "discovering";
  try {
    const historyDirectory = await join(await dirname(collectionNmlPath), "History");
    const entries = await readDir(historyDirectory);
    const discovered = entries
      .filter((entry) => entry.isFile && entry.name !== undefined)
      .map((entry) => parseHistoryFilename(entry.name!))
      .filter((descriptor): descriptor is HistoryFileDescriptor => descriptor !== null)
      .map(async (descriptor) => ({ ...descriptor, path: await join(historyDirectory, descriptor.filename) }));
    const resolved = await Promise.all(discovered);
    if (sequence !== discoverySequence) return;

    files.value = resolved.sort((left, right) => right.startedAt.getTime() - left.startedAt.getTime());
    status.value = selectedFile.value && timeline.value ? "ready" : resolved.length === 0 ? "empty" : "idle";
  } catch (error) {
    if (sequence !== discoverySequence) return;
    status.value = "error";
    errorMessage.value = `Unable to read the History directory: ${String(error)}`;
  }
}

async function selectHistoryFile(file: HistoryFileDescriptor): Promise<void> {
  const sequence = ++selectionSequence;
  selectedFile.value = file;
  timeline.value = null;
  errorMessage.value = null;
  status.value = "loading";

  try {
    const xml = await readTextFile(file.path);
    const parsed = parseSessionHistory(xml);
    if (sequence !== selectionSequence) return;

    timeline.value = parsed;
    status.value = parsed ? "ready" : "no-events";
  } catch (error) {
    if (sequence !== selectionSequence) return;
    status.value = "error";
    errorMessage.value = `Unable to parse ${file.filename}: ${String(error)}`;
  }
}

watch(
  nmlPathOverride,
  (collectionNmlPath) => {
    void discoverHistoryFiles(collectionNmlPath);
  },
  { immediate: true },
);

export function useSessionHistory() {
  return {
    files,
    filterDate,
    filteredFiles,
    selectedFile,
    timeline,
    status,
    errorMessage,
    warningCount,
    discoverHistoryFiles,
    selectHistoryFile,
  };
}
