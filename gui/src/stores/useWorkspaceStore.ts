import { defineStore } from "pinia";
import type { CollectionTrack } from "../types/library";

export type WorkspaceTab = "collection" | "history" | "remix-studio";

export interface ActiveLoopRange {
  start: number;
  end: number;
  duration: number;
  beatCount: number;
}

export const useWorkspaceStore = defineStore("workspace", {
  state: () => ({
    activeTab: "collection" as WorkspaceTab,
    /** The track currently loaded into Remix Studio's Stem Editor. */
    activeStudioTrack: null as CollectionTrack | null,
    /** The current beat-quantized selection in Remix Studio, in seconds. */
    activeLoopRange: null as ActiveLoopRange | null,
  }),

  actions: {
    selectTab(tab: WorkspaceTab): void {
      this.activeTab = tab;
    },

    sendToRemixStudio(track: CollectionTrack): void {
      this.activeStudioTrack = track;
      this.activeLoopRange = null;
      this.activeTab = "remix-studio";
    },

    setActiveStudioTrack(track: CollectionTrack): void {
      this.activeStudioTrack = track;
      this.activeLoopRange = null;
    },

    setActiveLoopRange(range: ActiveLoopRange | null): void {
      this.activeLoopRange = range;
    },
  },
});
