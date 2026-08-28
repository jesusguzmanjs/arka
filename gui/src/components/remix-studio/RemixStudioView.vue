<script setup lang="ts">
import { computed, shallowRef } from "vue";
import { storeToRefs } from "pinia";
import { useLibraryState } from "../../composables/collection/useLibraryState.ts";
import { useWorkspaceStore } from "../../stores/useWorkspaceStore.ts";
import { showAppToast } from "../../composables/core/useAppToast.ts";
import type { CollectionTrack } from "../../types/library.ts";
import { isUnanalyzedTrack, MISSING_BPM_GRID_MESSAGE } from "../../utils/trackAnalysis.ts";
import MiniLibrary from "./MiniLibrary.vue";
import RemixDeck from "./RemixDeck.vue";
import StemEditor from "./StemEditor.vue";

const workspaceStore = useWorkspaceStore();
const { activeStudioTrack, activeStemTracks } = storeToRefs(workspaceStore);
const { collection } = useLibraryState();
const setTitle = shallowRef("New Remix Set");

const miniLibraryTracks = computed(() => Object.values(collection.value)
  .sort((left, right) => left.collection_index - right.collection_index));

function loadTrackInStemEditor(track: CollectionTrack): void {
  if (isUnanalyzedTrack(track)) {
    showAppToast(MISSING_BPM_GRID_MESSAGE, "warning");
    return;
  }
  workspaceStore.setActiveStudioTrack(track);
}

</script>

<template>
  <div class="remix-studio">
    <section class="top-workspace" aria-label="Remix Studio top workspace">
      <aside class="studio-zone mini-library" aria-label="Mini library and filters">
        <MiniLibrary :tracks="miniLibraryTracks" :loaded-track="activeStudioTrack" @select="loadTrackInStemEditor" />
      </aside>

      <StemEditor :track="activeStudioTrack" :stem-tracks="activeStemTracks" />
    </section>

    <section class="studio-zone pad-matrix" aria-label="Remix pad matrix">
      <RemixDeck v-model:set-title="setTitle" />
    </section>
  </div>
</template>

<style scoped>
.remix-studio {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 970px;
  min-height: 780px;
  background: #1c1c1e;
}

.top-workspace {
  display: flex;
  min-width: 0;
  min-height: 420px;
  flex: 1 1 50%;
  flex-direction: row;
}

.studio-zone {
  min-width: 0;
  min-height: 0;
  background: #232326;
}

.mini-library {
  display: flex;
  width: 320px;
  padding: 1.25rem 0.75rem 1.25rem 1.25rem;
  flex: 0 0 320px;
  flex-direction: column;
  border-right: 1px solid #5a5a5e;
}

.pad-matrix {
  display: flex;
  min-height: 360px;
  flex: 1 1 50%;
  flex-direction: column;
  border-top: 1px solid #5a5a5e;
  padding: 0.5rem 1.5rem;
}
</style>
