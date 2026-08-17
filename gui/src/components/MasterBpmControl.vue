<script setup lang="ts">
const masterBpm = defineModel<number>({ required: true });

function clampBpm(value: number): number {
  return Math.min(220, Math.max(40, value));
}

function adjustBpm(delta: number): void {
  masterBpm.value = clampBpm(Number((masterBpm.value + delta).toFixed(2)));
}

function normalizeBpm(): void {
  if (!Number.isFinite(masterBpm.value)) {
    masterBpm.value = 120;
    return;
  }

  masterBpm.value = clampBpm(masterBpm.value);
}
</script>

<template>
  <div class="master-bpm-control">
    <label class="master-bpm-label" for="master-bpm-input">Master BPM</label>
    <input
      id="master-bpm-input"
      v-model.number="masterBpm"
      type="number"
      class="master-bpm-input"
      step="0.01"
      min="40"
      max="220"
      name="master-bpm"
      autocomplete="off"
      inputmode="decimal"
      @change="normalizeBpm"
    />
    <div class="master-bpm-adjustments" aria-label="Master BPM adjustments">
      <button
        type="button"
        class="master-bpm-button"
        title="Decrease BPM by 1; Shift-click for 0.1"
        aria-label="Decrease Master BPM"
        @click="adjustBpm($event.shiftKey ? -0.1 : -1)"
      >
        −
      </button>
      <button
        type="button"
        class="master-bpm-button"
        title="Increase BPM by 1; Shift-click for 0.1"
        aria-label="Increase Master BPM"
        @click="adjustBpm($event.shiftKey ? 0.1 : 1)"
      >
        +
      </button>
    </div>
  </div>
</template>

<style scoped>
.master-bpm-control,
.master-bpm-adjustments {
  display: flex;
  align-items: center;
}

.master-bpm-control {
  gap: 0.5rem;
}

.master-bpm-label {
  color: #f7d15f;
  font-size: 0.6875rem;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.master-bpm-input {
  width: 5.25rem;
  padding: 0.1875rem 0.375rem;
  border: 1px solid #64646b;
  border-radius: 0.125rem;
  background: #111114;
  color: #f7d15f;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.875rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  text-align: center;
}

.master-bpm-input:focus-visible,
.master-bpm-button:focus-visible {
  outline: 2px solid #fff;
  outline-offset: 2px;
}

.master-bpm-adjustments {
  gap: 0.25rem;
}

.master-bpm-button {
  width: 1.5rem;
  height: 1.5rem;
  padding: 0;
  border: 1px solid #64646b;
  border-radius: 0.125rem;
  background: #2a2a2e;
  color: #f2f2f2;
  cursor: pointer;
  font-size: 1rem;
  font-weight: 700;
  line-height: 1;
}

.master-bpm-button:hover {
  border-color: #f7d15f;
  background: #4b3d17;
  color: #f7d15f;
}
</style>
