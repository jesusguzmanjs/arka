/** Camelot/Open Key helpers used to classify compatible library tracks. */

type HarmonicMode = "A" | "B" | "m" | "d";

interface HarmonicKey {
  number: number;
  mode: HarmonicMode;
}

const HARMONIC_KEY_PATTERN = /^0?(\d{1,2})([ABmd])$/i;

function wrap(value: number): number {
  return value > 12 ? value - 12 : (value < 1 ? value + 12 : value);
}

function toggleMode(mode: HarmonicMode): HarmonicMode {
  if (mode === "A") return "B";
  if (mode === "B") return "A";
  return mode === "m" ? "d" : "m";
}

function parseHarmonicKey(value: string | null | undefined): HarmonicKey | null {
  const match = typeof value === "string" ? HARMONIC_KEY_PATTERN.exec(value.trim()) : null;
  if (!match) return null;

  const number = Number.parseInt(match[1], 10);
  if (number < 1 || number > 12) return null;

  const rawMode = match[2];
  const mode = rawMode.toLowerCase() === "a" || rawMode.toLowerCase() === "b"
    ? rawMode.toUpperCase() as "A" | "B"
    : rawMode.toLowerCase() as "m" | "d";
  return { number, mode };
}

function formatHarmonicKey({ number, mode }: HarmonicKey): string {
  return `${number}${mode}`;
}

function getDirect(number: number, mode: HarmonicMode): string[] {
  return [
    formatHarmonicKey({ number, mode }),
    formatHarmonicKey({ number: wrap(number + 1), mode }),
    formatHarmonicKey({ number: wrap(number - 1), mode }),
    formatHarmonicKey({ number, mode: toggleMode(mode) }),
  ];
}

/** Converts a valid key to its canonical form, removing a leading zero. */
export function normalizeHarmonicKey(value: string | null | undefined): string | null {
  const key = parseHarmonicKey(value);
  return key ? formatHarmonicKey(key) : null;
}

export interface HarmonicMatches {
  direct: string[];
  adjacent: string[];
}

/**
 * Returns direct harmonic matches and fuzzy matches reachable at exactly one
 * semitone of pitch adjustment. Invalid or absent keys intentionally match none.
 */
export function getHarmonicMatches(value: string | null | undefined): HarmonicMatches {
  const key = parseHarmonicKey(value);
  if (!key) return { direct: [], adjacent: [] };

  const direct = getDirect(key.number, key.mode);
  const directSet = new Set(direct);
  const adjacent = [
    ...getDirect(wrap(key.number + 7), key.mode),
    ...getDirect(wrap(key.number + 5), key.mode),
  ].filter((candidate, index, candidates) => !directSet.has(candidate) && candidates.indexOf(candidate) === index);

  return { direct, adjacent };
}
