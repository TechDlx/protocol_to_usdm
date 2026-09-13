import type { ColumnLayout, ExtractedField, ReviewIssue, ReviewState, ReviewSheetKey, SheetLayout, SheetRecord } from "../../types";

export interface SheetRow {
  rowId: string | null; // null for the study key/value sheet
  record: SheetRecord;
}

/** Records shown on a sheet, mirroring backend/pipeline/review/validation.py `sheet_rows`. */
export function sheetRows(state: ReviewState, sheet: ReviewSheetKey): SheetRow[] {
  const s = state.document.sheets;
  if (sheet === "study") return s.study ? [{ rowId: null, record: s.study }] : [];
  if (sheet === "dates") {
    const dates = (s.study?.governance_dates as SheetRecord[] | undefined) ?? [];
    return dates.map((record) => ({ rowId: String(record.row_id), record }));
  }
  const rows = (s[sheet] as SheetRecord[] | null) ?? [];
  return rows.map((record) => ({ rowId: String(record.row_id), record }));
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
  if (issues.some((i) => i.kind === "terminology_not_exact")) return "term";
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
