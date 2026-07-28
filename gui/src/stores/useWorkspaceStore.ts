import { defineStore } from "pinia";
import { invoke } from "@tauri-apps/api/core";
import { useConfigState } from "../composables/useConfigState";
import type { CollectionTrack } from "../types/library";

export type WorkspaceTab = "collection" | "history" | "remix-studio";

export interface ActiveLoopRange {
  start: number;
  end: number;
  duration: number;
  beatCount: number;
}

let stemLoadToken = 0;

export const useWorkspaceStore = defineStore("workspace", {
  state: () => ({
    activeTab: "collection" as WorkspaceTab,
    /** The track currently loaded into Remix Studio's Stem Editor. */
    activeStudioTrack: null as CollectionTrack | null,
    /** Temporary WAV paths returned from the native Stem extractor, in lane order. */
    activeStemTracks: [] as string[],
    /** The current beat-quantized selection in Remix Studio, in seconds. */
    activeLoopRange: null as ActiveLoopRange | null,
    /** Stem lanes routed by the next Remix Deck pad drag. */
    selectedStems: [] as string[],
  }),

  actions: {
    selectTab(tab: WorkspaceTab): void {
      this.activeTab = tab;
    },

    sendToRemixStudio(track: CollectionTrack): void {
      this.activeStudioTrack = track;
      this.activeLoopRange = null;
      this.selectedStems = [];
      this.activeTab = "remix-studio";
      void this.loadStemTracks(track);
    },

    setActiveStudioTrack(track: CollectionTrack): void {
      this.activeStudioTrack = track;
      this.activeLoopRange = null;
      this.selectedStems = [];
      void this.loadStemTracks(track);
    },

    setActiveLoopRange(range: ActiveLoopRange | null): void {
      this.activeLoopRange = range;
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
