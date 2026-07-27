<script setup lang="ts">
import { computed } from "vue";
import { storeToRefs } from "pinia";
import { useLibraryState } from "../composables/useLibraryState";
import { useWorkspaceStore } from "../stores/useWorkspaceStore";
import type { CollectionTrack } from "../types/library";
import MiniLibrary from "./MiniLibrary.vue";
import StemEditor from "./StemEditor.vue";

const workspaceStore = useWorkspaceStore();
const { activeStudioTrack } = storeToRefs(workspaceStore);
const { collection } = useLibraryState();

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

      <StemEditor :track="activeStudioTrack" />
    </section>

    <section class="studio-zone pad-matrix" aria-labelledby="pad-matrix-heading">
      <p id="pad-matrix-heading" class="zone-label">4×4 Pad Matrix</p>
      <p class="empty-state">Pad Matrix workspace</p>
    </section>
  </div>
</template>

<style scoped>
.remix-studio {
  display: grid;
  flex: 1;
  min-height: 0;
  grid-template-rows: minmax(50vh, 1fr) minmax(180px, 1fr);
  overflow: hidden;
  background: #1c1c1e;
}

.top-workspace {
  display: grid;
  min-width: 0;
  min-height: 0;
  grid-template-columns: minmax(240px, 25%) minmax(0, 1fr);
}

.studio-zone {
  min-width: 0;
  min-height: 0;
  background: #232326;
}

.mini-library {
  display: flex;
  padding: 1.25rem 0.75rem 1.25rem 1.25rem;
  border-right: 1px solid #5a5a5e;
}

.pad-matrix {
  display: flex;
  flex-direction: column;
  padding: 1.5rem;
}

.pad-matrix {
  border-top: 1px solid #5a5a5e;
}

.zone-label {
  margin: 0;
  color: #f7d15f;
  font-size: 0.6875rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  text-transform: uppercase;
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
