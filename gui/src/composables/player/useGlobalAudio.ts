import { ref } from "vue";

/** The single audio engine currently permitted to produce audible playback. */
export const activeAudioEngine = ref<"stems" | "remix" | null>(null);
