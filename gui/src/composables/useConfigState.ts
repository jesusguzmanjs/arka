// composables/useConfigState.ts
// See .openspec/4-library-spec.md §2 (revises 3-gui-spec.md §5.1/§5.2/§5.3/§5.5).
//
// Module-scoped singleton state. Every importer gets the same reactive
// proxy — behaves like a minimal store without a dependency. If we later
// add multi-window / routed views, swap this for Pinia (per spec §5.1).

import { computed, reactive, toRefs } from "vue";
import { type CueGridConfig, defaultConfig } from "../types/config";

const STORAGE_KEY = "cuegrid.config.v1";

function loadPersisted(): Partial<CueGridConfig> | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<CueGridConfig>;
    return {
      sensitivity: parsed.sensitivity,
      maxCues: parsed.maxCues,
      clearExisting: parsed.clearExisting,
      nmlPathOverride: parsed.nmlPathOverride,
    };
  } catch {
    return null;
  }
}

function persist(state: CueGridConfig) {
  try {
    // §2.4: selectedPlaylist and selectedTrackPath are per-session only
    // (never restored on restart), so the left column always boots fresh
    // and no stale preview pointer can survive a relaunch.
    const { selectedPlaylist, selectedTrackPath, ...rest } = state;
    void selectedPlaylist;
    void selectedTrackPath;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(rest));
  } catch {
    /* ignore quota / private-mode errors */
  }
}

const persisted = loadPersisted();

const state = reactive<CueGridConfig>({
  ...defaultConfig,
  ...(persisted ?? {}),
});

// §2.3 validation rule (revised): isValid is true IF AND ONLY IF
// selectedPlaylist is non-null. selectedTrackPath is never part of
// validity — a track preview is entirely optional.
function validate(s: CueGridConfig): boolean {
  return s.selectedPlaylist != null && s.selectedPlaylist.trim().length > 0;
}

export function useConfigState() {
  const setCustomNmlPath = (path: string | null) => {
    state.nmlPathOverride = path?.trim() || null;
    persist(state);
  };

  return {
    ...toRefs(state),
    customNmlPath: computed<string | null>({
      get: () => state.nmlPathOverride,
      set: setCustomNmlPath,
    }),
    isValid: computed(() => validate(state)),
    reset: () => Object.assign(state, defaultConfig),
    setCustomNmlPath,
    update: <K extends keyof CueGridConfig>(key: K, value: CueGridConfig[K]) => {
      state[key] = value;
      persist(state);
    },
  };
}
