import { beatMs, snapToGrid, type GridTrackData } from "./useGridMath";
import type { ActiveLoopRange } from "../stores/useWorkspaceStore";

const LOOP_ID = "studio-loop";
const CLICK_TOLERANCE_SECONDS = 0.01;

interface SegmentLike {
  id?: string;
  startTime: number;
  endTime: number;
  update: (options: Record<string, unknown>) => void;
}

interface LoopSelectionOptions {
  instance: any;
  waveformElement: HTMLElement;
  grid: GridTrackData;
  onChange: (range: ActiveLoopRange | null) => void;
}

function snapSeconds(seconds: number, grid: GridTrackData): number {
  const snappedMs = snapToGrid(seconds * 1000, grid.bpm, grid.grid_anchor_ms);
  return Math.max(0, Math.min(grid.duration_ms / 1000, snappedMs / 1000));
}

function normaliseSegment(segment: SegmentLike, grid: GridTrackData): ActiveLoopRange | null {
  const start = snapSeconds(Math.min(segment.startTime, segment.endTime), grid);
  const end = snapSeconds(Math.max(segment.startTime, segment.endTime), grid);
  if (end <= start) return null;
  const beatDuration = beatMs(grid.bpm) / 1000;
  if (beatDuration <= 0) return null;
  return { start, end, duration: end - start, beatCount: Math.max(1, Math.round((end - start) / beatDuration)) };
}

/** Owns the Studio-only beat-snapped Peaks segment and its mouse gesture lifecycle. */
export function useStudioLoopSelection(options: LoopSelectionOptions): () => void {
  const { instance, waveformElement, grid, onChange } = options;
  const view = instance.views.getView("zoomview");
  if (!view) return () => {};
  view.setWaveformDragMode("insert-segment");
  view.enableSegmentDragging(true);
  view.setSegmentDragMode("overlap");
  view.setMinSegmentDragWidth(0);

  function applySegment(segment: SegmentLike): void {
    const range = normaliseSegment(segment, grid);
    if (!range) return;
    segment.update({ id: LOOP_ID, startTime: range.start, endTime: range.end, editable: true, overlay: true, color: "#edb40b", borderColor: "#f7d15f" });
    onChange(range);
  }

  function createSingleActiveSegment(range: ActiveLoopRange): void {
    // Peaks may assign transient IDs to insert-drag segments. Removing every
    // segment before adding the canonical loop guarantees one region only.
    instance.segments.removeAll();
    instance.segments.add({
      id: LOOP_ID,
      startTime: range.start,
      endTime: range.end,
      editable: true,
      overlay: true,
      color: "#edb40b",
      borderColor: "#f7d15f",
    });
    onChange(range);
  }

  function handleSegmentInsert(event: { segment: SegmentLike }): void {
    const segment = event.segment;
    if (Math.abs(segment.endTime - segment.startTime) <= CLICK_TOLERANCE_SECONDS) {
      if (segment.id) instance.segments.removeById(segment.id);
      instance.player.seek(snapSeconds(segment.startTime, grid));
      return;
    }
    const range = normaliseSegment(segment, grid);
    if (range) createSingleActiveSegment(range);
  }

  function handleSegmentDragged(event: { segment: SegmentLike }): void {
    if (event.segment.id === LOOP_ID) applySegment(event.segment);
  }

  let panStartX: number | null = null;
  function handleShiftMouseDown(event: MouseEvent): void {
    if (!event.shiftKey) return;
    panStartX = event.clientX;
    event.preventDefault();
    event.stopPropagation();
  }
  function handleShiftMouseMove(event: MouseEvent): void {
    if (panStartX === null) return;
    const delta = event.clientX - panStartX;
    panStartX = event.clientX;
    view.scrollWaveform({ pixels: -delta });
    event.preventDefault();
  }
  function endShiftPan(): void { panStartX = null; }

  instance.on("segments.insert", handleSegmentInsert);
  instance.on("segments.dragged", handleSegmentDragged);
  instance.on("segments.dragend", handleSegmentDragged);
  waveformElement.addEventListener("mousedown", handleShiftMouseDown, true);
  window.addEventListener("mousemove", handleShiftMouseMove, true);
  window.addEventListener("mouseup", endShiftPan, true);

  return () => {
    instance.off("segments.insert", handleSegmentInsert);
    instance.off("segments.dragged", handleSegmentDragged);
    instance.off("segments.dragend", handleSegmentDragged);
    waveformElement.removeEventListener("mousedown", handleShiftMouseDown, true);
    window.removeEventListener("mousemove", handleShiftMouseMove, true);
    window.removeEventListener("mouseup", endShiftPan, true);
    instance.segments.removeAll();
  };
}
