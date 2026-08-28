<script setup lang="ts">
// TelemetryConsole.vue
// Floating overlay / modal console. See .openspec/3-gui-spec.md §4
// (Telemetry overlay, revised).
//
// No longer a fixed rack block. Renders as a fixed-position premium floating
// window with a dark blurred backdrop, only visible when `open` is true.
// Closable via the close button, a backdrop click, and Escape.

import { nextTick, ref, watch } from "vue";
import { useRunState } from "../../composables/core/useRunState.ts";
import LogLine from "./LogLine.vue";

const props = defineProps<{
  open: boolean;
}>();

const emit = defineEmits<{
  (e: "close"): void;
}>();

const { logs, clearLogs } = useRunState();

const scrollEl = ref<HTMLDivElement | null>(null);
const copied = ref(false);

// Auto-scroll to bottom whenever a new log arrives while the console is open.
watch(
  () => logs.value.length,
  async () => {
    if (!props.open) return;
    await nextTick();
    const el = scrollEl.value;
    if (el) el.scrollTop = el.scrollHeight;
  },
);

// Also scroll to bottom when the console is opened, so it lands at the
// latest content rather than wherever it was last left.
watch(
  () => props.open,
  async (isOpen) => {
    if (!isOpen) return;
    await nextTick();
    const el = scrollEl.value;
    if (el) el.scrollTop = el.scrollHeight;
  },
);

function onBackdropClick(e: MouseEvent) {
  // Only close when the click lands on the backdrop itself, not the panel.
  if (e.target === e.currentTarget) emit("close");
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape") emit("close");
}

async function copyAll() {
  const text = logs.value
    .map((l) => {
      const d = new Date(l.ts);
      const p = (n: number) => n.toString().padStart(2, "0");
      return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}  ${JSON.stringify(l.msg)}`;
    })
    .join("\n");
  try {
    await navigator.clipboard.writeText(text);
    copied.value = true;
    setTimeout(() => (copied.value = false), 1200);
  } catch {
    /* clipboard unavailable */
  }
}
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-40 flex items-center justify-center bg-black/50 backdrop-blur-sm"
    @click="onBackdropClick"
    @keydown="onKeydown"
  >
    <!-- Floating console window -->
    <section
      class="flex flex-col min-h-0 bg-console border border-zinc-700/80 rounded-xl shadow-2xl w-[min(720px,92vw)] h-[min(420px,80vh)] overflow-hidden"
    >
      <!-- Header strip -->
      <div
        class="flex items-center justify-between px-4 py-2.5 border-b border-border bg-panel"
      >
        <span class="text-xs uppercase tracking-widest text-muted">Telemetry</span>
        <div class="flex items-center gap-2">
          <button
            type="button"
            class="px-2 py-1 text-xs rounded border border-border-strong text-muted hover:text-primary hover:border-border-strong transition-colors"
            :disabled="logs.length === 0"
            @click="clearLogs"
          >
            Clear
          </button>
          <button
            type="button"
            class="px-2 py-1 text-xs rounded border border-border-strong text-muted hover:text-primary transition-colors"
            :disabled="logs.length === 0"
            @click="copyAll"
          >
            {{ copied ? "Copied" : "⧉ Copy" }}
          </button>
          <button
            type="button"
            class="ml-1 w-7 h-7 inline-flex items-center justify-center rounded-md border border-border-strong text-muted hover:text-primary hover:border-border-strong transition-colors"
            title="Close (Esc)"
            @click="emit('close')"
          >
            ✕
          </button>
        </div>
      </div>

      <!-- Scrollable log body -->
      <div
        ref="scrollEl"
        class="flex-1 min-h-0 overflow-y-auto py-2 font-mono text-[12.5px] leading-relaxed"
      >
        <div v-if="logs.length === 0" class="px-4 py-6 text-dim text-sm">
          No telemetry yet. Configure a target and press “Analyze & Inject”.
        </div>
        <LogLine v-for="(entry, i) in logs" :key="i" :entry="entry" />
      </div>
    </section>
  </div>
</template>
