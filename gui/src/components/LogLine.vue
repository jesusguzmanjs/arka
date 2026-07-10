<script setup lang="ts">
// LogLine.vue
// Renders one SidecarMessage, color-coded by level/type.
// See .openspec/3-gui-spec.md §3.3, §4 (example console).
import { computed } from "vue";
import type { LogEntry } from "../types/sidecar";

const props = defineProps<{
  entry: LogEntry;
}>();

// Short HH:MM:SS timestamp for the console gutter.
const tsLabel = computed(() => {
  const d = new Date(props.entry.ts);
  const p = (n: number) => n.toString().padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
});

// A short type tag shown in the gutter (mirrors the spec's example:
// info / event / cue / done / etc.).
const tag = computed(() => {
  const m = props.entry.msg;
  switch (m.type) {
    case "log":
      return m.level;
    case "nml_resolved":
      return "nml";
    case "track_start":
      return "track";
    case "event_detected":
      return "event";
    case "cue_written":
      return "cue";
    case "track_complete":
      return "done";
    case "summary":
      return "summary";
    case "fatal_error":
      return "fatal";
  }
});

// Tailwind color class for the tag, by severity / type.
const tagColor = computed(() => {
  const m = props.entry.msg;
  switch (m.type) {
    case "log":
      if (m.level === "error") return "text-error";
      if (m.level === "warning") return "text-warn";
      return "text-muted";
    case "nml_resolved":
      return "text-accent";
    case "track_start":
      return "text-accent";
    case "event_detected":
      return "text-accent";
    case "cue_written":
      return "text-success";
    case "track_complete":
      return "text-success";
    case "summary":
      return "text-success";
    case "fatal_error":
      return "text-error";
  }
});

// Human-readable body of the line.
const body = computed(() => {
  const m = props.entry.msg;
  switch (m.type) {
    case "log":
      return m.message;
    case "nml_resolved":
      return `Resolved collection.nml → ${m.path}`;
    case "track_start":
      return `Track ${m.index}/${m.total}: ${m.artist} - ${m.title}`;
    case "event_detected":
      return `${m.label} @ ${(m.time_ms / 1000).toFixed(3)}s  conf=${m.confidence.toFixed(2)}${m.is_major_phrase ? "  [major]" : ""}`;
    case "cue_written":
      return `[${m.hotcue}] "${m.name}" @ ${m.start_ms}ms written`;
    case "track_complete":
      return `Processed ${m.artist} - ${m.title}: ${m.event_count} events, ${m.cue_count} cues${m.error ? `  error: ${m.error}` : ""}`;
    case "summary":
      return `Processed ${m.succeeded}/${m.total} tracks (${m.skipped} skipped)`;
    case "fatal_error":
      return `FATAL: ${m.message}`;
  }
});
</script>

<template>
  <div class="flex gap-3 px-3 py-0.5 hover:bg-elevated/40">
    <span class="text-dim select-none">{{ tsLabel }}</span>
    <span class="w-16 shrink-0 select-none" :class="tagColor">{{ tag }}</span>
    <span class="flex-1 whitespace-pre-wrap break-all">{{ body }}</span>
  </div>
</template>
