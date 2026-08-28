<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, shallowRef, useTemplateRef } from "vue";
import type { CSSProperties } from "vue";

const props = defineProps<{
  modelValue: string | number | readonly string[];
  options: readonly string[];
  ariaLabel: string;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: string[]];
}>();

const pickerRef = useTemplateRef<HTMLElement>("pickerRef");
const triggerRef = useTemplateRef<HTMLButtonElement>("triggerRef");
const menuRef = useTemplateRef<HTMLElement>("menuRef");
const isOpen = shallowRef(false);
const menuStyle = shallowRef<CSSProperties>({});

const selectedKeys = computed<string[]>({
  get: () => {
    const values: readonly string[] = typeof props.modelValue === "string"
      ? props.modelValue.split(",")
      : (Array.isArray(props.modelValue) ? props.modelValue : []);
    return [...new Set(values.map((value: string) => value.trim()).filter((value: string) => props.options.includes(value)))];
  },
  set: (value) => emit("update:modelValue", value),
});

function addKey(key: string): void {
  if (key && !selectedKeys.value.includes(key)) {
    selectedKeys.value = [...selectedKeys.value, key];
  }
}

function removeKey(key: string): void {
  selectedKeys.value = selectedKeys.value.filter((selected) => selected !== key);
}

function closeOnOutsidePointer(event: PointerEvent): void {
  const target = event.target as Node;
  if (
    pickerRef.value
    && !pickerRef.value.contains(target)
    && !menuRef.value?.contains(target)
  ) {
    isOpen.value = false;
  }
}

function positionMenu(): void {
  const trigger = triggerRef.value;
  const app = document.getElementById("app");
  if (!trigger || !app) return;

  const triggerBounds = trigger.getBoundingClientRect();
  const appBounds = app.getBoundingClientRect();
  const gap = 4;
  const availableBelow = appBounds.bottom - triggerBounds.bottom - gap;
  const availableAbove = triggerBounds.top - appBounds.top - gap;
  const openBelow = availableBelow >= availableAbove;

  menuStyle.value = {
    left: `${triggerBounds.left}px`,
    width: `${triggerBounds.width}px`,
    maxHeight: `${Math.max(0, openBelow ? availableBelow : availableAbove)}px`,
    ...(openBelow
      ? { top: `${triggerBounds.bottom + gap}px` }
      : { bottom: `${window.innerHeight - triggerBounds.top + gap}px` }),
  };
}

async function toggleMenu(): Promise<void> {
  isOpen.value = !isOpen.value;
  if (isOpen.value) {
    await nextTick();
    positionMenu();
  }
}

function repositionOpenMenu(): void {
  if (isOpen.value) positionMenu();
}

onMounted(() => {
  document.addEventListener("pointerdown", closeOnOutsidePointer);
  document.addEventListener("scroll", repositionOpenMenu, true);
  window.addEventListener("resize", repositionOpenMenu);
});

onUnmounted(() => {
  document.removeEventListener("pointerdown", closeOnOutsidePointer);
  document.removeEventListener("scroll", repositionOpenMenu, true);
  window.removeEventListener("resize", repositionOpenMenu);
});
</script>

<template>
  <div
    ref="pickerRef"
    class="relative min-w-0 max-w-full rounded border border-zinc-700 bg-zinc-950 focus-within:border-primary focus-within:ring-1 focus-within:ring-primary"
    @keydown.esc.stop="isOpen = false"
  >
    <div v-if="selectedKeys.length" class="flex flex-wrap gap-1.5 p-2" aria-label="Selected Open Keys">
      <span
        v-for="key in selectedKeys"
        :key="key"
        class="inline-flex items-center gap-1 rounded bg-primary/15 py-1 pl-2 pr-1 text-xs font-medium text-secondary"
      >
        {{ key }}
        <button
          type="button"
          class="rounded p-0.5 text-secondary hover:bg-primary/20 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary"
          :aria-label="`Remove ${key}`"
          @click="removeKey(key)"
        >
          <span aria-hidden="true">×</span>
        </button>
      </span>
    </div>
    <button
      ref="triggerRef"
      type="button"
      class="flex w-full items-center justify-between gap-2 bg-zinc-950 px-2.5 py-2 text-left text-sm text-zinc-100 hover:bg-elevated focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary"
      :aria-label="ariaLabel"
      aria-haspopup="listbox"
      :aria-expanded="isOpen"
      @click="toggleMenu"
    >
      <span>{{ selectedKeys.length ? "Add another Open Key" : "Select Open Key" }}</span>
      <span class="text-secondary" aria-hidden="true">⌄</span>
    </button>
  </div>
  <Teleport to="body">
    <div
      v-if="isOpen"
      ref="menuRef"
      class="fixed z-[80] overflow-y-auto rounded border border-zinc-700 bg-zinc-950 py-1 shadow-lg shadow-black/40"
      :style="menuStyle"
      role="listbox"
      aria-multiselectable="true"
      @keydown.esc.stop="isOpen = false"
    >
      <button
        v-for="key in options"
        :key="key"
        type="button"
        role="option"
        class="flex w-full items-center justify-between px-2.5 py-2 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-primary"
        :class="selectedKeys.includes(key) ? 'bg-primary/15 text-secondary' : 'text-zinc-100 hover:bg-elevated'"
        :aria-selected="selectedKeys.includes(key)"
        @click="addKey(key)"
      >
        <span>{{ key }}</span>
        <span v-if="selectedKeys.includes(key)" class="text-primary" aria-hidden="true">✓</span>
      </button>
    </div>
  </Teleport>
</template>
