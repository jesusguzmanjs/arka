import { onBeforeUnmount, reactive, shallowRef } from "vue";

type ColumnWidths<TColumn extends string> = Record<TColumn, number>;

interface UseColumnResizeOptions<TColumn extends string> {
  columns: readonly TColumn[];
  initialWidths: ColumnWidths<TColumn>;
  minWidths: ColumnWidths<TColumn>;
  getContainer: () => HTMLElement | null;
}

/**
 * Keeps adjacent table columns within a fixed total width while a native mouse
 * drag changes their relative widths. Widths are stored as percentages so the
 * table remains responsive when its containing pane changes size.
 */
export function useColumnResize<TColumn extends string>(
  options: UseColumnResizeOptions<TColumn>,
) {
  const columnWidths = reactive({ ...options.initialWidths }) as ColumnWidths<TColumn>;
  const isResizing = shallowRef(false);
  let activeColumn: TColumn | null = null;
  let adjacentColumn: TColumn | null = null;
  let startX = 0;
  let initialActiveWidth = 0;
  let initialAdjacentWidth = 0;
  let previousUserSelect = "";
  let previousCursor = "";

  function stopResize(): void {
    if (!isResizing.value) return;

    document.removeEventListener("mousemove", resizeColumn);
    document.removeEventListener("mouseup", stopResize);
    document.body.style.userSelect = previousUserSelect;
    document.body.style.cursor = previousCursor;
    activeColumn = null;
    adjacentColumn = null;
    isResizing.value = false;
  }

  function resizeColumn(event: MouseEvent): void {
    const container = options.getContainer();
    if (!container || !activeColumn || !adjacentColumn) return;

    const containerWidth = container.getBoundingClientRect().width;
    if (containerWidth <= 0) return;

    const deltaPercent = ((event.clientX - startX) / containerWidth) * 100;
    const activeWidth = Math.max(
      options.minWidths[activeColumn],
      Math.min(
        initialActiveWidth + initialAdjacentWidth - options.minWidths[adjacentColumn],
        initialActiveWidth + deltaPercent,
      ),
    );

    columnWidths[activeColumn] = activeWidth;
    columnWidths[adjacentColumn] = initialActiveWidth + initialAdjacentWidth - activeWidth;
  }

  function startResize(column: TColumn, event: MouseEvent): void {
    const columnIndex = options.columns.indexOf(column);
    const nextColumn = options.columns[columnIndex + 1];
    if (columnIndex === -1 || nextColumn === undefined) return;

    event.preventDefault();
    event.stopPropagation();
    stopResize();

    activeColumn = column;
    adjacentColumn = nextColumn;
    startX = event.clientX;
    initialActiveWidth = columnWidths[column];
    initialAdjacentWidth = columnWidths[nextColumn];
    previousUserSelect = document.body.style.userSelect;
    previousCursor = document.body.style.cursor;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    isResizing.value = true;

    document.addEventListener("mousemove", resizeColumn);
    document.addEventListener("mouseup", stopResize);
  }

  onBeforeUnmount(stopResize);

  return { columnWidths, isResizing, startResize };
}
