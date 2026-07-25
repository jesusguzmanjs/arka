<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, shallowRef, useTemplateRef } from "vue";
import { useCueGridSidecar } from "../composables/useCueGridSidecar";
import type {
  SmartPlaylistField,
  SmartPlaylistOperator,
  SmartPlaylistPayload,
  SmartPlaylistRule,
} from "../types/smartPlaylist";

type ValueKind = "number" | "whole-number" | "range" | "text" | "key" | "date" | "days" | "rating" | "format";

interface RuleDraft {
  id: number;
  field: SmartPlaylistField;
  operator: SmartPlaylistOperator;
  value: string | number;
  min: string | number;
  max: string | number;
}

interface FieldOption {
  value: SmartPlaylistField;
  label: string;
  valueKind: ValueKind;
  operators: readonly { value: SmartPlaylistOperator; label: string }[];
}

const props = defineProps<{
  existingPlaylists: string[];
}>();

const NUMERIC_OPERATORS = [
  { value: "equals", label: "Equals" },
  { value: "greater_than", label: "Greater than" },
  { value: "less_than", label: "Less than" },
  { value: "between", label: "Between" },
] as const;

const TEXT_OPERATORS = [
  { value: "contains", label: "Contains" },
  { value: "is_exactly", label: "Is exactly" },
  { value: "does_not_contain", label: "Does not contain" },
] as const;

const DATE_OPERATORS = [
  { value: "in_last_days", label: "In the last X days" },
  { value: "before", label: "Before date" },
  { value: "after", label: "After date" },
] as const;

const RATING_OPERATORS = [
  { value: "greater_than_or_equal", label: "At least" },
  { value: "less_than_or_equal", label: "At most" },
  { value: "equals", label: "Exactly" },
] as const;

const MATCH_OPTIONS = [
  { value: "all", label: "Match ALL" },
  { value: "any", label: "Match ANY" },
] as const;

const OPEN_KEY_OPTIONS = [
  "1d", "2d", "3d", "4d", "5d", "6d", "7d", "8d", "9d", "10d", "11d", "12d",
  "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m", "10m", "11m", "12m",
] as const;

const FIELD_OPTIONS: readonly FieldOption[] = [
  { value: "bpm", label: "BPM", valueKind: "number", operators: NUMERIC_OPERATORS },
  { value: "key", label: "Key", valueKind: "key", operators: [{ value: "is_exactly", label: "Exact match" }] },
  { value: "genre", label: "Genre", valueKind: "text", operators: TEXT_OPERATORS },
  { value: "rating", label: "Rating", valueKind: "rating", operators: RATING_OPERATORS },
  { value: "import_date", label: "Import Date", valueKind: "date", operators: DATE_OPERATORS },
  { value: "last_played", label: "Last Played", valueKind: "date", operators: DATE_OPERATORS },
  { value: "playcount", label: "Playcount", valueKind: "whole-number", operators: NUMERIC_OPERATORS },
  { value: "label", label: "Label", valueKind: "text", operators: TEXT_OPERATORS },
  { value: "comment", label: "Comment", valueKind: "text", operators: TEXT_OPERATORS },
  { value: "track_format", label: "Track Format", valueKind: "format", operators: [{ value: "is_exactly", label: "Is exactly" }] },
];

const emit = defineEmits<{
  close: [];
  saved: [name: string];
}>();

const dialogRef = useTemplateRef<HTMLElement>("dialogRef");
const playlistName = shallowRef("");
const match = shallowRef<"all" | "any">("all");
const isSaving = shallowRef(false);
const errorMessage = shallowRef<string | null>(null);
let nextRuleId = 1;
const rules = reactive<RuleDraft[]>([createRule()]);
const { compileSmartPlaylist } = useCueGridSidecar();

const isNameTaken = computed(() => {
  const normalizedName = playlistName.value.trim().toLowerCase();
  return normalizedName.length > 0 && props.existingPlaylists.some(
    (name) => name.trim().toLowerCase() === normalizedName,
  );
});

const isFormValid = computed(() =>
  playlistName.value.trim().length > 0 && !isNameTaken.value && rules.length > 0 && rules.every(isRuleValid),
);

function createRule(): RuleDraft {
  const field = FIELD_OPTIONS[0];
  return {
    id: nextRuleId++,
    field: field.value,
    operator: field.operators[0].value,
    value: "",
    min: "",
    max: "",
  };
}

function definitionFor(field: SmartPlaylistField): FieldOption {
  return FIELD_OPTIONS.find((option) => option.value === field) ?? FIELD_OPTIONS[0];
}

function valueKind(rule: RuleDraft): ValueKind {
  return rule.operator === "between" ? "range" : (
    rule.operator === "in_last_days" ? "days" : definitionFor(rule.field).valueKind
  );
}

function inputType(rule: RuleDraft): "date" | "number" | "text" {
  const kind = valueKind(rule);
  if (kind === "date") return "date";
  if (kind === "number" || kind === "whole-number" || kind === "days") return "number";
  return "text";
}

function changeField(rule: RuleDraft): void {
  const definition = definitionFor(rule.field);
  rule.operator = definition.operators[0].value;
  rule.value = "";
  rule.min = "";
  rule.max = "";
  errorMessage.value = null;
}

function changeOperator(rule: RuleDraft): void {
  rule.value = "";
  rule.min = "";
  rule.max = "";
  errorMessage.value = null;
}

function addRule(): void {
  rules.push(createRule());
}

function removeRule(index: number): void {
  if (rules.length === 1) return;
  rules.splice(index, 1);
}

function finiteNumber(value: string | number): number | null {
  if (value === null || value === undefined || String(value).trim() === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function isPositiveInteger(value: string | number): boolean {
  const number = finiteNumber(value);
  return number !== null && Number.isInteger(number) && number > 0;
}

function isRuleValid(rule: RuleDraft): boolean {
  const kind = valueKind(rule);
  if (kind === "text" || kind === "key" || kind === "date" || kind === "format") return String(rule.value).trim().length > 0;
  if (kind === "rating") {
    const rating = finiteNumber(rule.value);
    return rating !== null && Number.isInteger(rating) && rating >= 1 && rating <= 5;
  }
  if (kind === "days") return isPositiveInteger(rule.value);
  if (kind === "whole-number") {
    if (rule.operator === "between") {
      const minimum = finiteNumber(rule.min);
      const maximum = finiteNumber(rule.max);
      return minimum !== null && maximum !== null && Number.isInteger(minimum) && Number.isInteger(maximum) && minimum >= 0 && minimum <= maximum;
    }
    const value = finiteNumber(rule.value);
    return value !== null && Number.isInteger(value) && value >= 0;
  }
  if (kind === "range") {
    const minimum = finiteNumber(rule.min);
    const maximum = finiteNumber(rule.max);
    return minimum !== null && maximum !== null && minimum <= maximum;
  }
  return finiteNumber(rule.value) !== null;
}

function dateForCore(value: string | number): string {
  return String(value).replace(/-/g, "/");
}

function ruleForCore(rule: RuleDraft): SmartPlaylistRule {
  const kind = valueKind(rule);
  let value: SmartPlaylistRule["value"];
  if (kind === "range") {
    value = { min: Number(rule.min), max: Number(rule.max) };
  } else if (kind === "number" || kind === "whole-number" || kind === "days" || kind === "rating") {
    value = Number(rule.value);
  } else if (kind === "date") {
    value = dateForCore(rule.value);
  } else {
    value = String(rule.value).trim();
  }
  return { field: rule.field, operator: rule.operator, value };
}

async function savePlaylist(): Promise<void> {
  if (!isFormValid.value || isSaving.value) return;
  isSaving.value = true;
  errorMessage.value = null;

  const payload: SmartPlaylistPayload = {
    name: playlistName.value.trim(),
    match: match.value,
    rules: rules.map(ruleForCore),
  };
  const result = await compileSmartPlaylist(payload);
  isSaving.value = false;

  if (!result.ok) {
    errorMessage.value = result.error;
    return;
  }
  emit("saved", result.result.name);
}

function requestClose(): void {
  if (!isSaving.value) emit("close");
}

function onKeyDown(event: KeyboardEvent): void {
  if (event.key === "Escape") requestClose();
}

onMounted(() => {
  document.addEventListener("keydown", onKeyDown);
  dialogRef.value?.focus();
});

onUnmounted(() => document.removeEventListener("keydown", onKeyDown));
</script>

<template>
  <Teleport to="body">
    <div
      class="fixed inset-0 z-[70] flex items-center justify-center bg-zinc-950/85 p-4 backdrop-blur-sm"
      role="presentation"
      @click.self="requestClose"
    >
      <section
        ref="dialogRef"
        class="flex max-h-full w-full max-w-4xl flex-col overflow-hidden rounded-lg border border-primary/30 bg-zinc-900 shadow-2xl shadow-black/60 outline-none"
        role="dialog"
        aria-modal="true"
        aria-labelledby="smart-playlist-title"
        tabindex="-1"
      >
        <header class="flex items-start justify-between gap-4 border-b border-zinc-700 px-5 py-4">
          <div>
            <p class="text-[10px] font-semibold uppercase tracking-[0.18em] text-secondary">Collection tools</p>
            <h2 id="smart-playlist-title" class="mt-1 text-lg font-semibold text-zinc-100">Create Smart Playlist</h2>
            <p class="mt-1 text-xs text-muted">Build a rule set; Arka compiles the current matches into Traktor.</p>
          </div>
          <button
            type="button"
            class="rounded p-2 text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-50"
            aria-label="Close Smart Playlist creator"
            :disabled="isSaving"
            @click="requestClose"
          >
            <span aria-hidden="true">×</span>
          </button>
        </header>

        <form class="min-h-0 overflow-y-auto overscroll-contain p-5" @submit.prevent="savePlaylist">
          <div class="grid gap-4 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
            <div>
              <label class="block text-xs font-medium text-zinc-300">
                <span class="mb-1.5 block">Playlist name</span>
                <input
                  v-model="playlistName"
                  type="text"
                  name="smart-playlist-name"
                  autocomplete="off"
                  placeholder="e.g. Recent 8A"
                  class="block w-full rounded border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                  :aria-invalid="isNameTaken"
                  :aria-describedby="isNameTaken ? 'smart-playlist-name-warning' : undefined"
                >
              </label>
              <p
                v-if="isNameTaken"
                id="smart-playlist-name-warning"
                class="mt-1.5 text-xs text-warning"
                role="alert"
              >
                A playlist with this name already exists.
              </p>
            </div>

            <fieldset class="flex rounded border border-zinc-700 bg-zinc-950/60 p-1" aria-label="Rule matching condition">
              <legend class="sr-only">Rule matching condition</legend>
              <label
                v-for="option in MATCH_OPTIONS"
                :key="option.value"
                class="cursor-pointer rounded px-3 py-2 text-xs font-semibold transition-colors"
                :class="match === option.value ? 'bg-primary text-zinc-950' : 'text-muted hover:text-zinc-100'"
              >
                <input v-model="match" class="sr-only" type="radio" name="smart-playlist-match" :value="option.value">
                {{ option.label }}
              </label>
            </fieldset>
          </div>

          <div class="mt-6 border-y border-zinc-800/90">
            <div class="flex items-center justify-between bg-zinc-950/45 px-3 py-2">
              <div>
                <h3 class="text-[11px] font-semibold uppercase tracking-[0.16em] text-dim">Rules</h3>
                <p class="mt-0.5 text-xs text-muted">{{ match === 'all' ? 'Every rule must match.' : 'At least one rule must match.' }}</p>
              </div>
              <button
                type="button"
                class="inline-flex items-center gap-1.5 rounded border border-primary/50 px-2.5 py-1.5 text-xs font-semibold text-primary transition-colors hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                @click="addRule"
              >
                <span aria-hidden="true">+</span> Add rule
              </button>
            </div>

            <div class="hidden grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.2fr)_32px] gap-3 border-t border-zinc-800 px-3 py-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-dim md:grid">
              <span>Field</span><span>Operator</span><span>Value</span><span class="sr-only">Remove</span>
            </div>

            <div class="divide-y divide-zinc-800/80">
              <div
                v-for="(rule, index) in rules"
                :key="rule.id"
                class="grid gap-2 px-3 py-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.2fr)_32px] md:items-center md:gap-3"
              >
                <label class="block min-w-0 text-[10px] font-semibold uppercase tracking-[0.12em] text-dim md:hidden">Field</label>
                <select
                  v-model="rule.field"
                  class="min-w-0 rounded border border-zinc-700 bg-zinc-950 px-2.5 py-2 text-sm text-zinc-100 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                  :aria-label="`Field for rule ${index + 1}`"
                  @change="changeField(rule)"
                >
                  <option v-for="field in FIELD_OPTIONS" :key="field.value" :value="field.value">{{ field.label }}</option>
                </select>

                <label class="block min-w-0 text-[10px] font-semibold uppercase tracking-[0.12em] text-dim md:hidden">Operator</label>
                <select
                  v-model="rule.operator"
                  class="min-w-0 rounded border border-zinc-700 bg-zinc-950 px-2.5 py-2 text-sm text-zinc-100 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                  :aria-label="`Operator for rule ${index + 1}`"
                  @change="changeOperator(rule)"
                >
                  <option v-for="operator in definitionFor(rule.field).operators" :key="operator.value" :value="operator.value">{{ operator.label }}</option>
                </select>

                <label class="block min-w-0 text-[10px] font-semibold uppercase tracking-[0.12em] text-dim md:hidden">Value</label>
                <div class="min-w-0">
                  <div v-if="valueKind(rule) === 'range'" class="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
                    <input v-model="rule.min" type="number" inputmode="decimal" placeholder="Min" class="min-w-0 rounded border border-zinc-700 bg-zinc-950 px-2.5 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary" :aria-label="`Minimum value for rule ${index + 1}`">
                    <span class="text-xs text-dim" aria-hidden="true">to</span>
                    <input v-model="rule.max" type="number" inputmode="decimal" placeholder="Max" class="min-w-0 rounded border border-zinc-700 bg-zinc-950 px-2.5 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary" :aria-label="`Maximum value for rule ${index + 1}`">
                  </div>
                  <select v-else-if="valueKind(rule) === 'key'" v-model="rule.value" class="w-full rounded border border-zinc-700 bg-zinc-950 px-2.5 py-2 text-sm text-zinc-100 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary" :aria-label="`Open Key value for rule ${index + 1}`">
                    <option value="" disabled>Select an Open Key</option>
                    <option v-for="key in OPEN_KEY_OPTIONS" :key="key" :value="key">{{ key }}</option>
                  </select>
                  <select v-else-if="valueKind(rule) === 'rating'" v-model="rule.value" class="w-full rounded border border-zinc-700 bg-zinc-950 px-2.5 py-2 text-sm text-zinc-100 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary" :aria-label="`Rating value for rule ${index + 1}`">
                    <option value="" disabled>Select a rating</option>
                    <option v-for="rating in 5" :key="rating" :value="String(rating)">{{ '★'.repeat(rating) }}{{ '☆'.repeat(5 - rating) }} · {{ rating }}</option>
                  </select>
                  <select v-else-if="valueKind(rule) === 'format'" v-model="rule.value" class="w-full rounded border border-zinc-700 bg-zinc-950 px-2.5 py-2 text-sm text-zinc-100 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary" :aria-label="`Track format value for rule ${index + 1}`">
                    <option value="" disabled>Select a track format</option>
                    <option value="Stem">Stem</option>
                  </select>
                  <input
                      v-else
                      :key="rule.field"
                      v-model="rule.value"
                      :type="inputType(rule)"
                      :step="['number', 'whole-number', 'days'].includes(valueKind(rule)) ? (valueKind(rule) === 'number' ? 'any' : '1') : undefined"
                      :min="valueKind(rule) === 'whole-number' ? '0' : (valueKind(rule) === 'days' ? '1' : undefined)"
                      :inputmode="['number', 'whole-number', 'days'].includes(valueKind(rule)) ? 'decimal' : undefined"
                      :placeholder="valueKind(rule) === 'days' ? 'Number of days' : 'Enter a value'"
                      class="w-full rounded border border-zinc-700 bg-zinc-950 px-2.5 py-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
                      :aria-label="`Value for rule ${index + 1}`"
                  >
                </div>

                <button
                  type="button"
                  class="h-8 w-8 justify-self-end rounded text-zinc-500 transition-colors hover:bg-warning/10 hover:text-warning focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-30"
                  :disabled="rules.length === 1"
                  :aria-label="`Remove rule ${index + 1}`"
                  title="Remove rule"
                  @click="removeRule(index)"
                >
                  <span aria-hidden="true">−</span>
                </button>
              </div>
            </div>
          </div>

          <p v-if="errorMessage" class="mt-3 text-sm text-warning" role="alert" aria-live="polite">{{ errorMessage }}</p>

          <footer class="mt-5 flex items-center justify-end gap-3 border-t border-zinc-800 pt-4">
            <button type="button" class="rounded px-3 py-2 text-sm text-zinc-300 transition-colors hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-50" :disabled="isSaving" @click="requestClose">Cancel</button>
            <button type="submit" class="rounded bg-primary px-4 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-secondary disabled:cursor-not-allowed disabled:opacity-50" :disabled="!isFormValid || isSaving">
              {{ isSaving ? 'Creating…' : 'Create Smart Playlist' }}
            </button>
          </footer>
        </form>
      </section>
    </div>
  </Teleport>
</template>
