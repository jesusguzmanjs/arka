<script setup lang="ts">
import { computed } from "vue";
import RemixPad from "./RemixPad.vue";
import type { PadSettings, RemixPadData } from "../../types/remix.ts";

const props = withDefaults(
  defineProps<{
    columnIndex: number;
    pads: RemixPadData[];
    activePadIndex: number | null;
    editingPadId: string | null;
    isBatchEditMode?: boolean;
    selectedPadIds?: Set<string>;
    padOffset?: number;
    volume?: number;
    filter?: number;
    keylockEnabled: boolean;
    punchModeEnabled: boolean;
  }>(),
  {
    isBatchEditMode: false,
    selectedPadIds: () => new Set<string>(),
    padOffset: 0,
    volume: 1,
    filter: 0,
  },
);

const activePad = computed(() => {
  if (props.activePadIndex === null) return null;
  return props.pads[props.activePadIndex - props.padOffset] ?? null;
});
const shouldShowStopButton = computed(() => (
  activePad.value !== null && activePad.value.settings.triggerMode !== "gate"
));

const emit = defineEmits<{
  (event: "pad-press", padIndex: number): void;
  (event: "pad-release", padIndex: number): void;
  (event: "context-menu", nativeEvent: MouseEvent, columnIndex: number, padIndex: number, padId: string): void;
  (event: "update:settings", columnIndex: number, padIndex: number, settings: PadSettings): void;
  (event: "toggle-selection", padId: string): void;
  (event: "update:volume", value: number): void;
  (event: "update:filter", value: number): void;
  (event: "toggle-keylock"): void;
  (event: "toggle-punch-mode"): void;
  (event: "stop"): void;
  (event: "end-rename"): void;
}>();

function emitVolume(event: Event): void {
  const input = event.target as HTMLInputElement;
  emit("update:volume", Number(input.value));
}

function emitFilter(event: Event): void {
  const input = event.target as HTMLInputElement;
  emit("update:filter", Number(input.value));
}

function resetFilter(): void {
  emit("update:filter", 0);
}
</script>

<template>
  <section class="remix-column" :aria-label="`Remix Deck slot ${columnIndex + 1}`">
    <div class="column-header">
      <div class="column-controls" aria-label="Column playback controls">
        <button
          type="button"
          class="column-mode-button"
          :class="{ 'is-enabled': keylockEnabled }"
          :aria-pressed="keylockEnabled"
          :aria-label="`Toggle Keylock for slot ${columnIndex + 1}`"
          title="Keylock"
          @click="emit('toggle-keylock')"
        >
          <span aria-hidden="true">🎵</span>
        </button>
        <button
          type="button"
          class="column-mode-button"
          :class="{ 'is-enabled': punchModeEnabled }"
          :aria-pressed="punchModeEnabled"
          :aria-label="`Toggle Punch Mode for slot ${columnIndex + 1}`"
          title="Punch Mode"
          @click="emit('toggle-punch-mode')"
        >
<!--          <span aria-hidden="true">👊</span>-->
        <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" aria-hidden="true">
          <path d="M17,10V6a2,2,0,0,0-2.109-2A2.118,2.118,0,0,0,13,6.17V18a4.017,4.017,0,0,1-1.246,2.9A3.968,3.968,0,0,1,9,22c-.071,0-.143,0-.215,0A4.089,4.089,0,0,1,5,17.83V14a1,1,0,0,0-1-1H3a1,1,0,0,1,0-2H4a3,3,0,0,1,3,3v3.83A2.118,2.118,0,0,0,8.891,20,2,2,0,0,0,11,18V6.17a4.089,4.089,0,0,1,3.787-4.165A4,4,0,0,1,19,6v4a1,1,0,0,0,1,1h1a1,1,0,0,1,0,2H20A3,3,0,0,1,17,10Z" />
        </svg>
        </button>
        <button
          v-if="shouldShowStopButton"
          type="button"
          class="column-stop-button"
          :aria-label="`Stop slot ${columnIndex + 1}`"
          title="Stop"
          @click="emit('stop')"
        >
          <span aria-hidden="true">■</span>
        </button>
      </div>
      <div class="control-row">
        <label class="control-label" :for="`slot-${columnIndex}-volume`">VOL</label>
        <input
          :id="`slot-${columnIndex}-volume`"
          class="channel-range volume-range"
          type="range"
          min="0"
          max="1"
          step="0.01"
          :name="`slot-${columnIndex}-volume`"
          :value="volume"
          :style="{ '--vol-percent': `${volume * 100}%` }"
          autocomplete="off"
          :aria-label="`Slot ${columnIndex + 1} volume`"
          @input="emitVolume"
        >
      </div>
      <div class="control-row">
        <label class="control-label" :for="`slot-${columnIndex}-filter`">FLT</label>
        <input
            :id="`slot-${columnIndex}-filter`"
            class="channel-range filter-range"
            type="range"
            min="-1"
            max="1"
            step="0.01"
            :name="`slot-${columnIndex}-filter`"
            :value="filter"
            title="Double-click to reset filter to center"
            autocomplete="off"
            :aria-label="`Slot ${columnIndex + 1} filter`"
            @input="emitFilter"
            @dblclick="resetFilter"
        >
      </div>
    </div>

    <div class="pad-stack">
      <RemixPad
        v-for="(pad, padIndex) in pads"
        :key="pad.settings.id"
        :pad="pad"
        :is-playing="activePadIndex === padOffset + padIndex"
        :is-batch-edit-mode="isBatchEditMode"
        :is-selected="selectedPadIds.has(pad.settings.id)"
        :editing-pad-id="editingPadId"
        @press="emit('pad-press', padOffset + padIndex)"
        @release="emit('pad-release', padOffset + padIndex)"
        @context-menu="emit('context-menu', $event, columnIndex, padOffset + padIndex, pad.settings.id)"
        @update:settings="emit('update:settings', columnIndex, padOffset + padIndex, $event)"
        @toggle-selection="emit('toggle-selection', $event)"
        @end-rename="emit('end-rename')"
      />
    </div>
  </section>
</template>

<style scoped>
.remix-column {
  display: flex;
  min-width: 0;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  gap: 4px;
}

.column-header {
  display: flex;
  box-sizing: border-box;
  min-height: 0;
  flex: 0 0 auto;
  flex-direction: column;
  gap: 2px;
  padding: 4px 6px;
  background: #1b1b1e;
  border-bottom: 1px solid #45454a;
}

.column-controls {
  display: flex;
  gap: 0.25rem;
  margin-bottom: 0;
}

.column-mode-button {
  display: inline-flex;
  width: 1.35rem;
  height: 1.15rem;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 1px solid #64646b;
  border-radius: 0.125rem;
  background: #2a2a2e;
  color: #f2f2f2;
  cursor: pointer;
  font-size: 0.625rem;
  line-height: 1;
}

.column-mode-button:hover {
  border-color: #f7d15f;
  background: #4b3d17;
}

.column-mode-button.is-enabled {
  border-color: #f7d15f;
  background: #edb40b;
}

.column-mode-button:focus-visible {
  outline: 2px solid #fff;
  outline-offset: 2px;
}

.column-stop-button {
  display: inline-flex;
  width: 1.35rem;
  height: 1.15rem;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 1px solid #8f3d3d;
  border-radius: 0.125rem;
  background: #2a2a2e;
  color: #f87171;
  cursor: pointer;
  font-size: 0.625rem;
  line-height: 1;
}

.column-stop-button:hover {
  border-color: #f87171;
  background: #4a2528;
  color: #fecaca;
}

.column-stop-button:focus-visible {
  outline: 2px solid #fff;
  outline-offset: 2px;
}

.control-row {
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
}

.control-label {
  width: 1.5rem;
  flex: 0 0 1.5rem;
  color: #8a8a8e;
  font-family: ui-monospace, "Cascadia Code", monospace;
  font-size: 0.6rem;
  font-weight: 800;
  letter-spacing: 0.08em;
}

.channel-range {
  width: 100%;
  min-width: 0;
  height: 0.75rem;
  margin: 0;
  cursor: pointer;
  appearance: none;
  background: transparent;
  -webkit-appearance: none;
}

.channel-range::-webkit-slider-runnable-track {
  height: 3px;
  border-radius: 999px;
  background: linear-gradient(to right, #f7d15f 0 var(--vol-percent), #505057 var(--vol-percent) 100%);
}

.channel-range::-webkit-slider-thumb {
  width: 0.625rem;
  height: 0.625rem;
  margin-top: -0.21875rem;
  border: 1px solid #f7d15f;
  border-radius: 50%;
  background: #edb40b;
  -webkit-appearance: none;
}

.channel-range::-moz-range-track {
  height: 3px;
  border-radius: 999px;
  background: linear-gradient(to right, #f7d15f 0 var(--vol-percent), #505057 var(--vol-percent) 100%);
}

.channel-range::-moz-range-thumb {
  width: 0.5625rem;
  height: 0.5625rem;
  border: 1px solid #f7d15f;
  border-radius: 50%;
  background: #edb40b;
}

.channel-range:focus-visible {
  outline: 2px solid #fff;
  outline-offset: 2px;
}

.volume-range::-webkit-slider-thumb {
  background: #edb40b;
}

.volume-range::-moz-range-thumb {
  background: #edb40b;
}

.pad-stack {
  display: grid;
  min-width: 0;
  min-height: 0;
  flex: 1;
  grid-template-rows: repeat(4, minmax(0, 1fr));
  gap: 4px;
}
/* Estilo para la pista del slider de filtro */
.filter-range::-webkit-slider-runnable-track {
  height: 3px;
  border-radius: 999px;
  background: #505057;
}

.filter-range::-webkit-slider-thumb {
  width: 0.625rem;
  height: 0.625rem;
  margin-top: -0.21875rem;
  border: 1px solid #3b82f6;
  border-radius: 50%;
  background: #60a5fa;
  -webkit-appearance: none;
}

.filter-range::-moz-range-thumb {
  width: 0.5625rem;
  height: 0.5625rem;
  border: 1px solid #3b82f6;
  border-radius: 50%;
  background: #60a5fa;
}
</style>
