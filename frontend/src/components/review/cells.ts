import type {
  ColumnLayout,
  ExtractedField,
  ExtractionSheets,
  ReviewIssue,
  ReviewState,
  SheetLayout,
  SheetRecord,
} from "../../types";

export interface SheetRow {
  rowId: string | null; // null for key/value sheets
  record: SheetRecord;
}

/** Records shown on a sheet, found by the layout's dotted source path (mirrors workbook/sources.py). */
export function sheetRows(sheets: ExtractionSheets, layout: SheetLayout): SheetRow[] {
  let obj: unknown = sheets;
  for (const part of layout.source.split(".")) {
    if (!obj || typeof obj !== "object") return [];
    obj = (obj as Record<string, unknown>)[part];
  }
  if (obj === null || obj === undefined) return [];
  if (layout.kind === "key_value") return [{ rowId: null, record: obj as SheetRecord }];
  return (obj as SheetRecord[]).map((record) => ({ rowId: record.row_id ? String(record.row_id) : null, record }));
}

export function groupActive(layout: SheetLayout, record: SheetRecord, group: string): boolean {
  return layout.columns.some((c) => c.group === group && c.field && !isEmpty(fieldOf(record, c.field)));
}

export function isEmpty(field: ExtractedField | undefined): boolean {
  return !field || field.value === null || field.value === "";
}

/**
 * Names of entities of the given kinds, in sheet order, for reference pickers. When `scope` is given
 * (e.g. { timeline: "Main Timeline" }), only entities whose record has those field values are listed:
 * a schedule row can only be scheduled at timepoints of its own timeline.
 */
export function entityNames(
  sheets: ExtractionSheets,
  layouts: SheetLayout[],
  kinds: string[],
  scope: Record<string, string | null> = {},
): string[] {
  const names = new Set<string>();
  for (const layout of layouts) {
    for (const column of layout.columns) {
      if (!column.entity || !column.field || !kinds.includes(column.entity)) continue;
      for (const { record } of sheetRows(sheets, layout)) {
        if (column.group && !groupActive(layout, record, column.group)) continue;
        const inScope = Object.entries(scope).every(
          ([field, wanted]) => !layout.columns.some((c) => c.field === field) || fieldOf(record, field)?.value === wanted,
        );
        const value = fieldOf(record, column.field)?.value;
        if (value && inScope) names.add(value);
      }
    }
  }
  return [...names];
}

export function fieldOf(record: SheetRecord, field: string): ExtractedField | undefined {
  const value = record[field];
  return value && typeof value === "object" && "value" in value ? (value as ExtractedField) : undefined;
}

/** Workbook reference for a cell, e.g. studyDesignArms!D3 or study!B2. */
export function cellRef(layout: SheetLayout, column: ColumnLayout, rowIndex: number): string {
  if (layout.kind === "key_value") {
    return `${layout.workbook_sheet}!B${layout.columns.indexOf(column) + 1}`;
  }
  return `${layout.workbook_sheet}!${column.letter}${layout.first_row + rowIndex}`;
}

export type CellState = "na" | "empty-required" | "term" | "edited" | "low" | "derived" | "ok" | "empty";

export function cellState(
  column: ColumnLayout,
  field: ExtractedField | undefined,
  issues: ReviewIssue[],
  pending: boolean,
): CellState {
  if (column.field === null) return "na";
  // Issues describe the last saved value, so an unsaved edit shows as edited until it is saved.
  if (pending) return "edited";
  if (issues.some((i) => i.kind === "missing_required")) return "empty-required";
  if (issues.some((i) => i.kind === "terminology_not_exact" && i.severity === "blocking")) return "term";
  if (field?.provenance?.origin === "human") return "edited";
  if (issues.some((i) => i.severity === "warning")) return "low";
  if (!field || field.value === null || field.value === "") return "empty";
  if (field.provenance?.origin === "derived") return "derived";
  return "ok";
}

export const STATE_LABEL: Record<CellState, string> = {
  ok: "high confidence",
  derived: "generated",
  low: "low confidence / unverified",
  term: "terminology not resolved",
  edited: "edited by reviewer",
  "empty-required": "required but empty",
  empty: "empty",
  na: "not extracted in this phase",
};

export function issuesByCell(state: ReviewState): Map<string, ReviewIssue[]> {
  const map = new Map<string, ReviewIssue[]>();
  for (const issue of state.validation.issues) {
    if (!issue.field) continue;
    const key = `${issue.sheet}|${issue.row_id ?? ""}|${issue.field}`;
    map.set(key, [...(map.get(key) ?? []), issue]);
  }
  return map;
}
