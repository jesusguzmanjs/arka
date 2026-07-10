<script setup lang="ts">
// App.vue — root layout shell only, no business logic.
// See .openspec/3-gui-spec.md §3.2, §4 (UI Layout, revised two-block rack).
//
// Layout (revised): a two-block resizable rack —
//   Block 1 (PlayerRack): <AudioPlayer> stacked directly above
//   <LibraryBrowser>, the latter as the block's flex-1 filler.
//   Block 2 (ConfigRack): <ConfigPanel> + <ActionBar>, protected by a
//   hard anti-clip CONFIG_MIN floor so the CTA is never clipped.
// A single horizontal cursor-ns-resize splitter sits between the two
// blocks. TelemetryConsole is no longer a rack block; it renders as a
// floating overlay toggled by TelemetryToggleButton (fixed bottom-right).

import AppHeader from "./components/AppHeader.vue";
import ConfigPanel from "./components/ConfigPanel.vue";
import ActionBar from "./components/ActionBar.vue";
import TelemetryConsole from "./components/TelemetryConsole.vue";
import TelemetryToggleButton from "./components/TelemetryToggleButton.vue";
import SummaryBadge from "./components/SummaryBadge.vue";
import LibraryBrowser from "./components/LibraryBrowser.vue";

import { defineAsyncComponent, onBeforeUnmount, ref } from "vue";
import { useRunState } from "./composables/useRunState";
import { useCueGridSidecar } from "./composables/useCueGridSidecar";

// Carga perezosa del reproductor: no pesa en el arranque inicial
const AudioPlayer = defineAsyncComponent(() => import("./components/AudioPlayer.vue"));

// Destructure `status` to a top-level ref so Vue's template auto-unwrap
// applies (accessing `runState.status` directly keeps the Ref<RunStatus>
// wrapper, which vue-tsc flags as having no overlap with a string).
const { status, analysisStatus } = useRunState();
const { exportTelemetry } = useCueGridSidecar();

// ─── Telemetry overlay toggle ─────────────────────────────────────────
// Local UI-chrome state, owned by App.vue per §4's convention. The console
// is a floating overlay, not a rack block.
const isTelemetryOpen = ref(false);

// ─── Vertical resizable panels (two blocks, one splitter) ────────────
// Block 1 (PlayerRack) is the flexible region — it absorbs whatever Block 2
// does not claim. Block 2 (ConfigRack) owns a reactive pixel height with a
// hard anti-clip minimum so ActionBar's CTA + status line are never clipped.
//
// §4 (revised): CONFIG_MIN is a hard, non-negotiable floor measured against
// ConfigPanel + ActionBar's actual rendered height. 320px comfortably fits
// the verify/sensitivity/clear rows + the full-width CTA + status line at
// the smallest supported window size.
const PLAYER_MIN = 160;
const CONFIG_MIN = 320;

const playerHeight = ref(220);
const configHeight = ref(360);

// Which splitter is currently being dragged (null = none). Only one splitter
// remains, but we keep the target-typed shape for clarity and future-proofing.
type SplitterTarget = "player-config" | null;
const dragging = ref<SplitterTarget>(null);
// Snapshot of cursor Y and the two affected panel heights at drag start
// so we can compute deltas smoothly without accumulating drift.
let dragStartY = 0;
let dragStartTop = 0;
let dragStartBottom = 0;

function onSplitterDown(target: Exclude<SplitterTarget, null>, e: MouseEvent) {
  // Prevent text selection while dragging the handle.
  e.preventDefault();
  dragging.value = target;
  dragStartY = e.clientY;
  dragStartTop = playerHeight.value;
  dragStartBottom = configHeight.value;
  window.addEventListener("mousemove", onWindowMouseMove);
  window.addEventListener("mouseup", onWindowMouseUp);
}

function onWindowMouseMove(e: MouseEvent) {
  if (!dragging.value) return;
  const delta = e.clientY - dragStartY;

  if (dragging.value === "player-config") {
    // Growing player shrinks config, and vice versa. Clamp both sides to
    // their minimums so neither block can collapse.
    const newPlayer = dragStartTop + delta;
    const newConfig = dragStartBottom - delta;
    if (newPlayer >= PLAYER_MIN && newConfig >= CONFIG_MIN) {
      playerHeight.value = newPlayer;
      configHeight.value = newConfig;
    }
  }
}

function onWindowMouseUp() {
  dragging.value = null;
  window.removeEventListener("mousemove", onWindowMouseMove);
  window.removeEventListener("mouseup", onWindowMouseUp);
}

onBeforeUnmount(() => {
  // Defensive: if the component unmounts mid-drag, drop the listeners.
  window.removeEventListener("mousemove", onWindowMouseMove);
  window.removeEventListener("mouseup", onWindowMouseUp);
});
</script>

<template>
  <div
    class="h-screen w-screen flex flex-col bg-zinc-950 text-primary font-ui overflow-hidden"
    @contextmenu.prevent
  >
    <!-- Chassis top bar: branding + NML indicator (kept outside the
         resizable stack so it never collapses). -->
    <AppHeader />

    <!-- Resizable modular rack: exactly two deck blocks separated by a
         single thin high-tech splitter handle. -->
    <div class="flex-1 min-h-0 flex flex-col">
      <!-- ── Block 1: PlayerRack ─────────────────────────────────── -->
      <section
        class="bg-zinc-900 border border-zinc-800/80 rounded-lg shadow-inner m-1.5 overflow-hidden flex flex-col flex-1 min-h-0"
        :style="{ height: playerHeight + 'px' }"
      >
        <!-- AudioPlayer keeps a fixed-height sub-region at the top of
             Block 1; no independent splitter against LibraryBrowser. -->
        <div class="shrink-0">
          <AudioPlayer :disabled="status === 'running'" />
        </div>
        <!-- LibraryBrowser is Block 1's flex-1 filler, occupying all
             remaining height underneath the player. -->
        <div class="flex-1 min-h-0 overflow-hidden">
          <LibraryBrowser :disabled="status === 'running'" />
        </div>
      </section>

      <!-- Splitter: PlayerRack ↔ ConfigRack (the only splitter left) -->
      <div
        class="h-1 cursor-ns-resize bg-transparent hover:bg-teal-500/60 transition-all duration-200 select-none shrink-0"
        @mousedown="onSplitterDown('player-config', $event)"
      />

      <!-- ── Block 2: ConfigRack (anti-clip protected) ──────────── -->
      <section
        class="bg-zinc-900 border border-zinc-800/80 rounded-lg shadow-inner m-1.5 overflow-hidden flex flex-col"
        :style="{ height: configHeight + 'px' }"
      >
        <div
          class="flex items-center gap-2 px-4 py-2 border-b border-zinc-800/80 border-l-2 border-l-teal-500/30"
        >
          <span class="text-xs uppercase tracking-widest text-muted">Config & Run</span>
        </div>
        <div class="flex-1 min-h-0 overflow-y-auto flex flex-col">
          <ConfigPanel :locked="status === 'running'" />
          <ActionBar />
        </div>
      </section>
    </div>

    <!-- Summary badge sits outside the resizable stack so it's always
         visible when a run finishes. -->
    <SummaryBadge />

    <!-- Bottom-right status controls remain grouped and ordered: export first,
         telemetry launcher second. -->
    <div class="fixed bottom-3 right-3 z-50 flex items-center gap-2">
      <button
        type="button"
        :disabled="!analysisStatus"
        class="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border border-zinc-700/80 bg-zinc-900/80 text-muted text-xs font-mono backdrop-blur-sm transition-all duration-200 enabled:hover:text-primary enabled:hover:border-teal-500/60 enabled:hover:bg-zinc-800/90 disabled:cursor-not-allowed disabled:opacity-50"
        @click="exportTelemetry"
      >
        Export
      </button>
      <TelemetryToggleButton :open="isTelemetryOpen" @toggle="isTelemetryOpen = !isTelemetryOpen" />
    </div>

    <!-- Floating telemetry console overlay. Rendered outside the rack;
         only visible when isTelemetryOpen is true. -->
    <TelemetryConsole :open="isTelemetryOpen" @close="isTelemetryOpen = false" />
  </div>
</template>

<style>
@tailwind base;
@tailwind components;
@tailwind utilities;

/* The app is always dark (spec §4) — force the color scheme so native
 * form controls / scrollbars render dark too. */
:root {
  color-scheme: dark;
}

html,
body,
#app {
  height: 100%;
  margin: 0;
  /* Strict viewport boundary: without overflow:hidden here, any sub-pixel
   * rounding between #app's height:100% and the root div's h-screen (100vh)
   * lets the body scroll, which breaks the flex min-h-0 height chain all the
   * way down to LibraryBrowser's inner overflow-y-auto columns. The root
   * <div> in <template> already carries h-screen overflow-hidden, but this
   * guards the ancestor chain too. */
  overflow: hidden;
}

/* Slim dark scrollbars for the telemetry console. */
*::-webkit-scrollbar {
  width: 10px;
  height: 10px;
}
*::-webkit-scrollbar-track {
  background: transparent;
}
*::-webkit-scrollbar-thumb {
  background: #2a2a2e;
  border-radius: 6px;
}
*::-webkit-scrollbar-thumb:hover {
  background: #3a3a3e;
}
</style>
