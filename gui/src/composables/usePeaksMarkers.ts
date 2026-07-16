import Konva from "konva";
import { buildGrid, type GridTrackData } from "./useGridMath";

export interface PlayerCue {
  id: number;
  position_ms: number;
  is_valid: boolean;
}

export interface GridLineReference {
  line: Konva.Line;
  offset: number;
}

const HEX_ACCENT = "#facf25";
const HEX_GRID_BEAT = "#777784";
const HEX_GRID_BAR = "#a1a1aa";
const HEX_GRAY = "#52525b";
const HEX_OVERVIEW_CUE = "#d27b00";
const HEX_GRID_ANCHOR = "#72ff58";

export interface GridAnchorDragEvent {
  deltaMs: number;
  startAnchorMs: number;
  isFinal: boolean;
}

/** Owns Konva marker construction and releases retained grid-line references on destruction. */
export function usePeaksMarkers(onGridAnchorDrag?: (event: GridAnchorDragEvent) => void) {
  let gridLines: GridLineReference[] = [];

  function markerHeight(options: any, group: Konva.Group): number {
    const layer = options.layer ?? group.getLayer();
    return layer?.getHeight?.() ?? 0;
  }

  function createPointMarker(options: any): any {
    const point = (options.point ?? {}) as Record<string, any>;
    const metadata = point.data ?? point;
    const kind: "grid" | "cue" | "grid-anchor" = metadata.kind ?? "cue";
    const isBar = Boolean(metadata.isBar);
    const cueId: number | undefined = metadata.cueId;
    const offset: number = metadata.offset ?? 0;
    const isValid = metadata.isValid !== false;
    const opacity = Number(metadata.opacity ?? 1);
    const editable = Boolean(options.editable ?? point.editable);
    const color = String(options.color ?? point.color ?? (isValid ? HEX_ACCENT : HEX_GRAY));

    if ((kind === "grid" || kind === "grid-anchor") && options.view === "overview") {
      const emptyMarker = new Konva.Group({ listening: false });
      return Object.assign(emptyMarker, { init() {}, fitToView() {}, update() {} });
    }

    if (options.view === "overview") {
      let line: Konva.Line | null = null;
      return {
        init(group: Konva.Group) {
          const height = markerHeight(options, group);
          line = new Konva.Line({ points: [0.5, 0, 0.5, height], stroke: HEX_OVERVIEW_CUE, strokeWidth: 1, opacity: 1, listening: false });
          group.add(line);
        },
        fitToView() {
          const height = markerHeight(options, line?.getParent() as Konva.Group);
          line?.points([0.5, 0, 0.5, height]);
        },
        update() {},
        destroy() {},
      };
    }

    let line: Konva.Line | null = null;
    let label: Konva.Rect | null = null;
    let labelText: Konva.Text | null = null;
    let parent: Konva.Group | null = null;
    return {
      init(group: Konva.Group) {
        parent = group;
        const height = markerHeight(options, group);
        const isGridAnchor = kind === "grid-anchor";
        const lineColor = isGridAnchor ? HEX_GRID_ANCHOR : kind === "grid" ? (isBar ? HEX_GRID_BAR : HEX_GRID_BEAT) : color;
        line = new Konva.Line({
          points: [0.5, 0, 0.5, height], stroke: lineColor,
          strokeWidth: isGridAnchor ? 3 : kind === "grid" && isBar ? 2 : kind === "grid" ? 1 : 2,
          opacity: kind === "grid" ? (isBar ? 1 : 0.8) : opacity, listening: isGridAnchor,
        });
        group.add(line);
        if (kind === "grid" && options.view === "zoomview") gridLines.push({ line, offset });
        if (kind === "cue" && cueId !== undefined && cueId !== null) {
          const width = 18;
          const heightPx = 14;
          const y = height - heightPx;
          label = new Konva.Rect({ x: -width / 2, y, width, height: heightPx, fill: isValid ? HEX_ACCENT : HEX_GRAY, cornerRadius: 2, opacity, listening: editable });
          labelText = new Konva.Text({ x: -width / 2, y: y + 1, width, height: heightPx, text: String(cueId + 1), fontSize: 10, fontFamily: "ui-monospace, monospace", fill: isValid ? "#241800" : "#a1a1aa", opacity, align: "center", listening: false });
          group.add(label);
          group.add(labelText);
        }
      },
      fitToView() {
        const height = markerHeight(options, parent as Konva.Group);
        line?.points([0.5, 0, 0.5, height]);
        if (label && labelText && cueId !== undefined && cueId !== null) {
          const y = height - 14;
          label.y(y);
          labelText.y(y + 1);
        }
      },
      update(next: any) { if (next?.color && line) line.stroke(next.color); },
      destroy() {
        if (kind === "grid" && options.view === "zoomview" && line) gridLines = gridLines.filter((item) => item.line !== line);
      },
    };
  }

  function paintAllMarkers(instance: any, track: GridTrackData, cues: readonly PlayerCue[], isGridEditMode = false): void {
    if (!instance) return;
    gridLines = [];
    instance.points.removeAll();
    instance.points.add(buildGrid(track).map((point, index) => ({
      id: `grid-${index}`, time: point.timeMs / 1000, editable: false,
      color: point.isBar ? HEX_GRID_BAR : HEX_GRID_BEAT,
      data: { kind: "grid", isBar: point.isBar, offset: point.offset },
    })));
    if (isGridEditMode) {
      instance.points.add([{
        id: "grid-anchor", time: track.grid_anchor_ms / 1000, editable: true, color: HEX_GRID_ANCHOR,
        data: { kind: "grid-anchor" },
      }]);
    }
    const orderedCues = cues.filter((cue) => cue.id !== undefined && cue.id !== null).slice().sort((a, b) => a.position_ms - b.position_ms);
    instance.points.add(orderedCues.map((cue) => ({
      id: `cue-${cue.id}`, time: cue.position_ms / 1000, editable: !isGridEditMode && cue.is_valid === true,
      color: cue.is_valid ? HEX_ACCENT : HEX_GRAY,
      data: { kind: "cue", cueId: cue.id, isValid: cue.is_valid, opacity: isGridEditMode ? 0.3 : 1 },
    })));
  }

  function notifyGridAnchorDrag(event: GridAnchorDragEvent): void { onGridAnchorDrag?.(event); }

  return { createPointMarker, markerHeight, paintAllMarkers, notifyGridAnchorDrag, getGridLines: () => gridLines };
}
