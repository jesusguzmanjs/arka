<script setup lang="ts">
import { computed, shallowRef } from "vue";
import { storeToRefs } from "pinia";
import { useLibraryState } from "../composables/useLibraryState";
import { useWorkspaceStore } from "../stores/useWorkspaceStore";
import type { CollectionTrack } from "../types/library";
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
  display: grid;
  flex: 1;
  min-height: 0;
  grid-template-rows: minmax(300px, 0.85fr) minmax(360px, 1.15fr);
  overflow-x: hidden;
  overflow-y: auto;
  background: #1c1c1e;
}

.top-workspace {
  display: grid;
  min-width: 0;
  min-height: 0;
  grid-template-columns: minmax(250px, 25%) minmax(0, 1fr);
}

.studio-zone {
  min-width: 0;
  min-height: 0;
  background: #232326;
}

.mini-library {
  display: flex;
  min-width: 250px;
  padding: 1.25rem 0.75rem 1.25rem 1.25rem;
  flex-direction: column;
  border-right: 1px solid #5a5a5e;
}

.pad-matrix {
  display: flex;
  min-height: 0;
  flex-direction: column;
  padding: 0.5rem 1.5rem;
}

.pad-matrix {
  border-top: 1px solid #5a5a5e;
}


@media (max-width: 720px) {
  .remix-studio {
    grid-template-rows: minmax(0, 1fr) minmax(180px, 1fr);
  }

  .top-workspace {
    grid-template-columns: 1fr;
    grid-template-rows: minmax(96px, auto) minmax(0, 1fr);
  }

  .mini-library {
    border-right: 0;
    border-bottom: 1px solid #5a5a5e;
  }
}
</style>
