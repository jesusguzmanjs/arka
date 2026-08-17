import { defineStore } from "pinia";
import { invoke } from "@tauri-apps/api/core";
import { useConfigState } from "../composables/useConfigState";
import type { CollectionTrack } from "../types/library";
import type { RemixPadData } from "../types/remix";

export type WorkspaceTab = "collection" | "history" | "remix-studio";

export interface ActiveLoopRange {
  start: number;
  end: number;
  duration: number;
  beatCount: number;
}

export type EditorMode = "library" | "pad";

const REMIX_COLUMN_IDS = ["A", "B", "C", "D"] as const;
const PADS_PER_COLUMN = 16;
const EMPTY_PAD_COLOR = "#333333";

function createRemixPad(columnIndex: number, padIndex: number): RemixPadData {
  return {
    settings: {
      id: `${REMIX_COLUMN_IDS[columnIndex]}${padIndex + 1}`,
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
    },
    audio: null,
  };
}

function createRemixPadMatrix(): RemixPadData[][] {
  return REMIX_COLUMN_IDS.map((_, columnIndex) => (
    Array.from({ length: PADS_PER_COLUMN }, (_, padIndex) => createRemixPad(columnIndex, padIndex))
  ));
}

let stemLoadToken = 0;

export const useWorkspaceStore = defineStore("workspace", {
  state: () => ({
    activeTab: "collection" as WorkspaceTab,
    /** The track currently loaded into Remix Studio's Stem Editor. */
    activeStudioTrack: null as CollectionTrack | null,
    /** Temporary WAV paths returned from the native Stem extractor, in lane order. */
    activeStemTracks: [] as string[],
    /** Current Stem Editor mix state, in the same order as `activeStemTracks`. */
    stemMuted: [false, false, false, false] as boolean[],
    stemSoloed: null as number | null,
    /** The current beat-quantized selection in Remix Studio, in seconds. */
    activeLoopRange: null as ActiveLoopRange | null,
    /** Stem lanes routed by the next Remix Deck pad drag. */
    selectedStems: [] as string[],
    /** Shared Remix Deck data, so Pad Edit Mode can save its target directly. */
    remixPads: createRemixPadMatrix(),
    editorMode: "library" as EditorMode,
    editingPadId: null as string | null,
  }),

  actions: {
    selectTab(tab: WorkspaceTab): void {
      this.activeTab = tab;
    },

    sendToRemixStudio(track: CollectionTrack): void {
      this.activeStudioTrack = track;
      this.activeLoopRange = null;
      this.selectedStems = [];
      this.resetStemMixState();
      this.activeTab = "remix-studio";
      void this.loadStemTracks(track);
    },

    setActiveStudioTrack(track: CollectionTrack | null): void {
      this.activeStudioTrack = track;
      this.activeLoopRange = null;
      this.selectedStems = [];
      this.resetStemMixState();
      this.activeStemTracks = [];
      if (track) {
        void this.loadStemTracks(track);
      } else {
        stemLoadToken += 1;
      }
    },

    setActiveLoopRange(range: ActiveLoopRange | null): void {
      this.activeLoopRange = range;
    },

    setEditorMode(mode: EditorMode, padId: string | null = null): void {
      this.editorMode = mode;
      this.editingPadId = mode === "pad" ? padId : null;
    },

    exitPadEditMode(): void {
      this.editorMode = "library";
      this.editingPadId = null;
      this.activeLoopRange = null;
    },

    findRemixPad(padId: string): { pad: RemixPadData; columnIndex: number; padIndex: number } | null {
      for (let columnIndex = 0; columnIndex < this.remixPads.length; columnIndex += 1) {
        const padIndex = this.remixPads[columnIndex].findIndex((pad) => pad.settings.id === padId);
        if (padIndex >= 0) return { pad: this.remixPads[columnIndex][padIndex], columnIndex, padIndex };
      }
      return null;
    },

    selectStem(stem: string, additive: boolean): void {
      if (additive) {
        this.selectedStems = this.selectedStems.includes(stem)
          ? this.selectedStems.filter((selected) => selected !== stem)
          : [...this.selectedStems, stem];
        return;
      }
      this.selectedStems = [stem];
    },

    resetStemMixState(): void {
      this.stemMuted = [false, false, false, false];
      this.stemSoloed = null;
    },

    setStemMixState(muted: readonly boolean[], soloed: number | null): void {
      this.stemMuted = [0, 1, 2, 3].map((index) => muted[index] === true);
      this.stemSoloed = soloed !== null && soloed >= 0 && soloed < 4 ? soloed : null;
    },

    async loadStemTracks(track: CollectionTrack): Promise<void> {
      const token = ++stemLoadToken;
      this.activeStemTracks = [];

      const nmlPath = useConfigState().nmlPathOverride.value;
      if (!track.audio_id || !nmlPath) return;

      try {
        const stemPath = await invoke<string | null>("check_stem_exists", {
          audioId: track.audio_id,
          nmlPath,
          stemsDirOverride: null,
        });
        if (!stemPath || token !== stemLoadToken) return;

        const stemTracks = await invoke<string[]>("extract_stems", { stemFilePath: stemPath });
        if (token === stemLoadToken && stemTracks.length === 4) this.activeStemTracks = stemTracks;
      } catch (error) {
        if (token === stemLoadToken) console.warn("[RemixStudio] Native Stem extraction unavailable:", error);
      }
    },
  },
});
