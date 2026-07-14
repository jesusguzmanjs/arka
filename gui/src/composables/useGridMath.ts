/** Pure beat-grid calculations. This module deliberately has no Vue or DOM dependency. */
export interface GridTrackData {
  bpm: number;
  grid_anchor_ms: number;
  duration_ms: number;
}

export interface GridPoint {
  timeMs: number;
  isBar: boolean;
  offset: number;
}

export function beatMs(bpm: number): number {
  return bpm > 0 && Number.isFinite(bpm) ? 60_000 / bpm : 0;
}

export function snapToGrid(positionMs: number, bpm: number, anchorMs: number): number {
  const beat = beatMs(bpm);
  if (beat <= 0) return positionMs;
  return anchorMs + Math.round((positionMs - anchorMs) / beat) * beat;
}

export function buildGrid(data: GridTrackData): GridPoint[] {
  const beat = beatMs(data.bpm);
  if (beat <= 0 || data.duration_ms <= 0) return [];

  const points: GridPoint[] = [];
  for (let index = 0; ; index += 1) {
    const time = data.grid_anchor_ms + index * beat;
    if (time > data.duration_ms + 0.5) break;
    points.push({ timeMs: time, isBar: index % 4 === 0, offset: index });
  }
  for (let index = 1; ; index += 1) {
    const time = data.grid_anchor_ms - index * beat;
    if (time < 0) break;
    points.push({ timeMs: time, isBar: index % 4 === 0, offset: -index });
  }

  return points.sort((a, b) => a.timeMs - b.timeMs);
}
