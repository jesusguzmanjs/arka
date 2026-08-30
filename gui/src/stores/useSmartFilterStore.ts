import { defineStore } from "pinia";

/** The minimum collection metadata needed for related-track matching. */
export interface Track {
  id: string;
  bpm: number;
  key: string;
  title?: string;
}

type HarmonicMode = "minor" | "major";

interface ParsedHarmonicKey {
  number: number;
  mode: HarmonicMode;
}

const HARMONIC_KEY_PATTERN = /^(\d{1,2})([mdab])$/i;
const INVALID_KEY_JUMPS = 99;

function parseHarmonicKey(key: string): ParsedHarmonicKey | null {
  const match = HARMONIC_KEY_PATTERN.exec(key.trim());
  if (!match) return null;

  const number = Number.parseInt(match[1], 10);
  if (number < 1 || number > 12) return null;

  const mode = match[2].toLowerCase();
  return {
    number,
    mode: mode === "m" || mode === "a" ? "minor" : "major",
  };
}

function calculateKeyJumps(key1: string, key2: string): number {
  const firstKey = parseHarmonicKey(key1);
  const secondKey = parseHarmonicKey(key2);
  if (!firstKey || !secondKey) return INVALID_KEY_JUMPS;

  const directDistance = Math.abs(firstKey.number - secondKey.number);
  const circularDistance = Math.min(directDistance, 12 - directDistance);
  const modeJump = firstKey.mode === secondKey.mode ? 0 : 1;

  return circularDistance + modeJump;
}

export const useSmartFilterStore = defineStore("smartFilter", {
  state: () => ({
    targetTrack: null as Track | null,
    bpmTolerance: 6,
    keyJumps: 2,
  }),

  getters: {
    isActive: (state): boolean => state.targetTrack !== null,

    currentBpmRange: (state): { min: number; max: number } => {
      if (!state.targetTrack) return { min: 0, max: 0 };

      const tolerance = state.targetTrack.bpm * (state.bpmTolerance / 100);
      return {
        min: state.targetTrack.bpm - tolerance,
        max: state.targetTrack.bpm + tolerance,
      };
    },
  },

  actions: {
    applyFilter(track: Track, bpmTol: number, jumps: number): void {
      this.targetTrack = track;
      this.bpmTolerance = bpmTol;
      this.keyJumps = jumps;
    },

    clearFilter(): void {
      this.targetTrack = null;
    },

    filterTracks<T extends Track>(allTracks: T[]): T[] {
      const targetTrack = this.targetTrack;
      if (!this.isActive || !targetTrack) return allTracks;

      const { min, max } = this.currentBpmRange;
      return allTracks.filter((track) => (
        track.id !== targetTrack.id
        && track.bpm >= min
        && track.bpm <= max
        && calculateKeyJumps(targetTrack.key, track.key) <= this.keyJumps
      ));
    },
  },
});
