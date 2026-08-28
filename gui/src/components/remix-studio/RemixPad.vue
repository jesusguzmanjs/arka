<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, useTemplateRef, watch } from "vue";
import type { PadSettings, RemixPadData } from "../../types/remix.ts";

const props = withDefaults(
  defineProps<{
    pad: RemixPadData;
    isPlaying?: boolean;
    isBatchEditMode?: boolean;
    isSelected?: boolean;
    editingPadId: string | null;
  }>(),
  { isPlaying: false, isBatchEditMode: false, isSelected: false },
);

const emit = defineEmits<{
  (event: "press", value: MouseEvent | TouchEvent): void;
  (event: "release", value: MouseEvent | TouchEvent): void;
  (event: "context-menu", value: MouseEvent): void;
  (event: "update:settings", value: PadSettings): void;
  (event: "toggle-selection", id: string): void;
  (event: "end-rename"): void;
}>();

const isLoaded = computed(() => props.pad.audio !== null);
const isRenaming = computed(() => props.editingPadId === props.pad.settings.id);
const tempName = shallowRef("");
const nameInput = useTemplateRef<HTMLInputElement>("nameInput");
const padElement = useTemplateRef<HTMLElement>("padElement");
const showGainPopover = ref(false);
const padClasses = computed(() => ({
  "is-empty": !isLoaded.value,
  "is-playing": props.isPlaying,
  "is-selected": props.isSelected,
  "batch-mode": props.isBatchEditMode,
}));
const padStyle = computed(() => (
  isLoaded.value ? { backgroundColor: props.pad.settings.color } : undefined
));
const padLabel = computed(() => (
  props.isBatchEditMode
    ? `${props.isSelected ? "Deselect" : "Select"} pad ${props.pad.settings.id}`
    : isLoaded.value
    ? `Play pad ${props.pad.settings.id}: ${props.pad.settings.name}`
    : `Empty pad ${props.pad.settings.id}`
));
const padPressed = computed(() => (
  props.isBatchEditMode ? props.isSelected : props.isPlaying
));
const padTooltip = computed(() => {
  const audio = props.pad.audio;
  if (!audio) return undefined;

  const key = audio.originalKey?.trim();
  return `BPM: ${audio.originalBpm.toFixed(2)}${key ? ` | Key: ${key}` : ""}`;
});
const gainLabel = computed(() => {
  const gain = props.pad.settings.volume;
  return `${gain > 0 ? "+" : ""}${gain.toFixed(2)}`;
});
const transpose = computed(() => Number(
  props.pad.settings.transpose || props.pad.audio?.pitchShift || 0,
));
const transposeLabel = computed(() => `${transpose.value > 0 ? "+" : ""}${transpose.value} st`);

watch(isRenaming, (renaming) => {
  if (!renaming) return;

  tempName.value = props.pad.settings.name;
  void nextTick(() => {
    nameInput.value?.focus();
    nameInput.value?.select();
  });
});

function emitPress(event: MouseEvent | TouchEvent): void {
  if (props.isBatchEditMode) {
    emit("toggle-selection", props.pad.settings.id);
    return;
  }
  emit("press", event);
}

function emitRelease(event: MouseEvent | TouchEvent): void {
  if (props.isBatchEditMode) return;
  emit("release", event);
}

function onContextMenu(event: MouseEvent): void {
  emit("context-menu", event);
}

function updateSettings(change: Partial<PadSettings>): void {
  emit("update:settings", { ...props.pad.settings, ...change });
}

function togglePlayType(): void {
  updateSettings({
    playType: props.pad.settings.playType === "loop" ? "one-shot" : "loop",
  });
}

function toggleTriggerMode(): void {
  updateSettings({
    triggerMode: props.pad.settings.triggerMode === "trigger" ? "gate" : "trigger",
  });
}

function toggleSync(): void {
  updateSettings({ sync: !props.pad.settings.sync });
}

function toggleReverse(): void {
  updateSettings({ isReversed: !props.pad.settings.isReversed });
}

function updateGain(event: Event): void {
  const input = event.target as HTMLInputElement;
  updateSettings({ volume: Number(input.value) });
}

function toggleGainPopover(): void {
  showGainPopover.value = !showGainPopover.value;
}

function closeGainPopoverOnOutsideClick(event: MouseEvent): void {
  if (!padElement.value?.contains(event.target as Node)) showGainPopover.value = false;
}

function commitRename(): void {
  const name = tempName.value.trim();
  if (name) updateSettings({ name });
  emit("end-rename");
}

function cancelRename(): void {
  tempName.value = props.pad.settings.name;
  emit("end-rename");
}

onMounted(() => window.addEventListener("click", closeGainPopoverOnOutsideClick));
onBeforeUnmount(() => window.removeEventListener("click", closeGainPopoverOnOutsideClick));
</script>

<template>
  <section
      ref="padElement"
      class="remix-pad"
      :class="padClasses"
      :style="padStyle"
      :aria-label="`Remix pad ${pad.settings.id}`"
      :title="padTooltip"
      @contextmenu.stop.prevent="onContextMenu"
  >
    <button
        v-if="!isRenaming"
        type="button"
        class="pad-surface"
        :aria-label="padLabel"
        :aria-pressed="padPressed"
        @mousedown.left="emitPress"
        @mouseup.left="emitRelease"
        @mouseleave="emitRelease"
        @touchstart.prevent="emitPress"
        @touchend.prevent="emitRelease"
        @touchcancel.prevent="emitRelease"
    >
      <span v-if="isLoaded" class="pad-name">{{ pad.settings.name }}</span>
      <span v-else class="pad-id">{{ pad.settings.id }}</span>
    </button>
    <div v-else class="pad-surface">
      <input
        ref="nameInput"
        v-model="tempName"
        class="pad-name-input"
        type="text"
        :aria-label="`Rename pad ${pad.settings.id}`"
        @blur="commitRename"
        @keydown.enter.prevent="commitRename"
        @keydown.esc.prevent="cancelRename"
      >
    </div>

    <template v-if="isLoaded">
      <span v-if="transpose !== 0" class="transpose-badge" aria-label="Pad transpose">{{ transposeLabel }}</span>
      <button
        type="button"
        class="gain-toggle"
        :aria-expanded="showGainPopover"
        :aria-label="`Adjust gain for pad ${pad.settings.id}`"
        title="Pad gain"
        @click.stop="toggleGainPopover"
      >
        🔊
      </button>
      <div v-if="showGainPopover" class="pad-gain-popover" aria-label="Pad gain" @click.stop>
        <input
          :id="`pad-${pad.settings.id}-gain`"
          class="pad-gain-range"
          type="range"
          min="-1"
          max="1"
          step="0.01"
          :value="pad.settings.volume"
          :aria-label="`Pad ${pad.settings.id} gain`"
          @input="updateGain"
        >
        <span class="pad-gain-value">{{ gainLabel }}</span>
      </div>
    </template>

    <div v-if="isLoaded" class="pad-settings" aria-label="Pad settings">
      <button
        type="button"
        class="setting-button setting-icon"
        :aria-label="pad.settings.playType === 'loop' ? 'Loop playback' : 'One-shot playback'"
        :title="pad.settings.playType === 'loop' ? 'Loop playback' : 'One-shot playback'"
        @click.stop="togglePlayType"
      >
        <span aria-hidden="true">{{ pad.settings.playType === "loop" ? "↻" : "▶" }}</span>
      </button>
      <button
        type="button"
        class="setting-button"
        :class="{ 'is-enabled': pad.settings.triggerMode === 'gate' }"
        :aria-pressed="pad.settings.triggerMode === 'gate'"
        :title="`${pad.settings.triggerMode === 'trigger' ? 'Trigger' : 'Gate'} mode`"
        @click.stop="toggleTriggerMode"
      >
        {{ pad.settings.triggerMode === "trigger" ? "TRG" : "GATE" }}
      </button>
      <button
        type="button"
        class="setting-button"
        :class="{ 'is-enabled': pad.settings.sync }"
        :aria-pressed="pad.settings.sync"
        title="Tempo sync"
        @click.stop="toggleSync"
      >
        SYNC
      </button>
      <button
        type="button"
        class="setting-button"
        :class="{ 'is-enabled': pad.settings.isReversed }"
        :aria-pressed="pad.settings.isReversed"
        title="Reverse playback"
        @click.stop="toggleReverse"
      >
        REV
      </button>
    </div>
  </section>
</template>

<style scoped>
.remix-pad {
  position: relative;
  display: flex;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  box-sizing: border-box;
  flex-direction: column;
  justify-content: space-between;
  overflow: hidden;
  border: 1px solid #5a5a5e;
  border-radius: 0.25rem;
  background: #2a2a2e;
  box-shadow: inset 0 1px rgb(255 255 255 / 7%);
  transition: filter 100ms ease, box-shadow 100ms ease;
  user-select: none;
  -webkit-user-select: none;
  -webkit-tap-highlight-color: transparent;
}

.remix-pad.is-empty {
  border-style: dashed;
  background: #242427;
}

.remix-pad.is-playing {
  box-shadow: inset 0 0 0 2px #f7d15f, 0 0 15px rgb(237 180 11 / 45%);
  filter: brightness(1.16);
}

.remix-pad.is-selected {
  box-shadow: inset 0 0 0 2px #3b82f6;
}

.remix-pad.is-playing.is-selected {
  box-shadow: inset 0 0 0 2px #3b82f6, 0 0 15px rgb(237 180 11 / 45%);
}

.remix-pad.batch-mode .pad-surface {
  cursor: cell;
}

.pad-surface {
  display: flex;
  min-width: 0;
  min-height: 0;
  width: 100%;
  flex: 1;
  align-items: center;
  justify-content: center;
  border: 0;
  background: transparent;
  color: #fff;
  cursor: pointer;
  touch-action: manipulation;
  user-select: none;
  -webkit-user-select: none;
  -webkit-user-drag: none;
}

.pad-surface:hover {
  background: rgb(255 255 255 / 8%);
}

.pad-surface:active {
  background: rgb(0 0 0 / 18%);
}

.pad-surface:focus-visible,
.setting-button:focus-visible {
  z-index: 1;
  outline: 2px solid #fff;
  outline-offset: -3px;
}

.pad-name {
  overflow: hidden;
  max-width: 100%;
  padding: 0.75rem;
  font-size: 0.875rem;
  font-weight: 800;
  letter-spacing: 0.02em;
  text-align: center;
  text-overflow: ellipsis;
  text-shadow: 0 1px 2px rgb(0 0 0 / 55%);
  white-space: nowrap;
}

.pad-name-input {
  width: 100%;
  padding: 0.75rem;
  border: 0;
  border-bottom: 1px solid rgb(255 255 255 / 50%);
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 0.875rem;
  font-weight: 800;
  letter-spacing: 0.02em;
  outline: none;
  text-align: center;
}

.transpose-badge {
  position: absolute;
  z-index: 2;
  top: 0.25rem;
  left: 0.25rem;
  padding: 0.125rem 0.3rem;
  border: 1px solid rgb(255 255 255 / 28%);
  border-radius: 0.125rem;
  background: rgb(18 18 20 / 58%);
  color: #f7d15f;
  font-family: ui-monospace, "Cascadia Code", monospace;
  font-size: 0.5625rem;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.03em;
  pointer-events: none;
}

.pad-id {
  color: #8b8b91;
  font-family: ui-monospace, "Cascadia Code", monospace;
  font-size: 0.875rem;
  font-weight: 700;
  letter-spacing: 0.08em;
}

.gain-toggle {
  position: absolute;
  z-index: 2;
  top: 0.25rem;
  right: 0.25rem;
  width: 1.25rem;
  height: 1.25rem;
  padding: 0;
  border: 1px solid rgb(255 255 255 / 35%);
  border-radius: 50%;
  background: rgb(18 18 20 / 55%);
  color: #fff;
  cursor: pointer;
  font-size: 0.625rem;
  line-height: 1;
}

.gain-toggle:hover,
.gain-toggle[aria-expanded="true"] {
  border-color: #f7d15f;
  background: rgb(18 18 20 / 82%);
}

.gain-toggle:focus-visible {
  outline: 2px solid #fff;
  outline-offset: 2px;
}

.pad-gain-popover {
  position: absolute;
  z-index: 3;
  top: 1.75rem;
  right: 0.25rem;
  display: flex;
  width: 8.5rem;
  box-sizing: border-box;
  align-items: center;
  gap: 0.375rem;
  padding: 0.1875rem 0.375rem;
  border: 1px solid rgb(247 209 95 / 55%);
  border-radius: 0.1875rem;
  background: #202024;
  box-shadow: 0 0.35rem 0.8rem rgb(0 0 0 / 40%);
}

.pad-gain-range {
  min-width: 0;
  height: 0.625rem;
  flex: 1;
  accent-color: #f7d15f;
  cursor: pointer;
}

.pad-gain-value {
  min-width: 2.9rem;
  color: rgb(255 255 255 / 82%);
  font-family: ui-monospace, "Cascadia Code", monospace;
  font-size: 0.6rem;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.pad-settings {
  display: flex;
  width: 100%;
  min-height: 1.5rem;
  margin-top: auto;
  border-top: 1px solid rgb(0 0 0 / 28%);
  background: rgb(18 18 20 / 46%);
  font-size: 0.65rem;
  opacity: 0.8;
}

.setting-button {
  min-width: 0;
  min-height: 1.5rem;
  flex: 1;
  border: 0;
  border-right: 1px solid rgb(255 255 255 / 12%);
  background: transparent;
  color: rgb(255 255 255 / 72%);
  cursor: pointer;
  font-family: ui-monospace, "Cascadia Code", monospace;
  font-size: inherit;
  font-weight: 800;
  letter-spacing: 0.04em;
}

.setting-button:last-child {
  border-right: 0;
}

.setting-button:hover {
  background: rgb(255 255 255 / 14%);
  color: #fff;
}

.setting-button.is-enabled {
  background: rgb(247 209 95 / 20%);
  color: #f7d15f;
}

.setting-icon {
  font-size: 0.875rem;
}

@media (prefers-reduced-motion: reduce) {
  .remix-pad {
    transition: none;
  }

}
</style>
