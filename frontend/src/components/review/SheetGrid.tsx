import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import type { ReviewOperation, ReviewState, SheetLayout } from "../../types";
import { cellKey } from "./useReview";
import { cellRef, cellState, fieldOf, groupActive, issuesByCell, sheetRows, STATE_LABEL } from "./cells";

export interface Selection {
  sheet: SheetLayout["key"];
  rowId: string | null;
  field: string;
}

interface Props {
  layout: SheetLayout;
  state: ReviewState;
  pending: Map<string, Extract<ReviewOperation, { op: "set" }>>;
  selection: Selection | null;
  onSelect: (selection: Selection) => void;
  onEdit: (selection: Selection, value: string | null) => void;
  onRun: (operations: ReviewOperation[]) => void;
}

export default function SheetGrid({ layout, state, pending, selection, onSelect, onEdit, onRun }: Props) {
  const [editing, setEditing] = useState<Selection | null>(null);
  const issues = issuesByCell(state);
  const rows = sheetRows(state.document.sheets, layout);

  const isSelected = (rowId: string | null, field: string) =>
    selection?.sheet === layout.key && selection.rowId === rowId && selection.field === field;

  const renderCell = (rowId: string | null, rowIndex: number, record: Record<string, unknown>, columnIndex: number) => {
    const column = layout.columns[columnIndex]!;
    if (column.field === null) {
      return (
        <td key={columnIndex} className="rv-cell na" title="Not extracted in this phase">
          —
        </td>
      );
    }
    const field = column.field;
    const key = cellKey(layout.key, rowId, field);
    const queued = pending.get(key);
    const extracted = fieldOf(record, field);
    const value = queued ? queued.value : (extracted?.value ?? null);
    const cellIssues = issues.get(key) ?? [];
    const stateClass = cellState(column, extracted, cellIssues, Boolean(queued));
    const here: Selection = { sheet: layout.key, rowId, field };
    const blocking = cellIssues.some((i) => i.severity === "blocking");
    const isEditing = editing?.rowId === rowId && editing.field === field && editing.sheet === layout.key;

    return (
      <td
        key={columnIndex}
        id={`cell-${layout.key}-${rowId ?? "study"}-${field}`}
        className={`rv-cell ${stateClass}${isSelected(rowId, field) ? " selected" : ""}${column.multiline ? " multiline" : ""}`}
        title={`${cellRef(layout, column, rowIndex)} · ${STATE_LABEL[stateClass]}${cellIssues.length ? ` · ${cellIssues.map((i) => i.message).join("; ")}` : ""}`}
        onClick={() => onSelect(here)}
        onDoubleClick={() => {
          onSelect(here);
          // Terminology, reference and fixed-choice cells are chosen in the side panel.
          if (!column.ct_klass && column.ref.length === 0 && column.choices.length === 0) setEditing(here);
        }}
      >
        {cellIssues.length > 0 && !queued && <span className={`rv-marker ${blocking ? "blocking" : "warning"}`} aria-hidden />}
        {isEditing ? (
          <InlineEditor
            initial={value ?? ""}
            multiline={column.multiline}
            onCommit={(next) => {
              setEditing(null);
              if (next !== (value ?? "")) onEdit(here, next === "" ? null : next);
            }}
            onCancel={() => setEditing(null)}
          />
        ) : (
          <span className="rv-value">{value ?? ""}</span>
        )}
      </td>
    );
  };

  if (layout.kind === "key_value") {
    const record = rows[0]?.record;
    if (!record) return <p className="muted">Nothing was extracted for this sheet.</p>;
    return (
      <div className="rv-grid-wrap">
        <table className="rv-grid">
          <thead>
            <tr>
              <th className="rv-rownum" />
              <th className="rv-letter">A</th>
              <th className="rv-letter">B</th>
            </tr>
          </thead>
          <tbody>
            {layout.columns.map((column, i) => (
              <tr key={column.header}>
                <td className="rv-rownum">{i + 1}</td>
                <th className="rv-key">
                  {column.header}
                  {column.required && <span className="req">*</span>}
                  {column.ct_klass && <span className="rv-ct">CT</span>}
                </th>
                {renderCell(null, 0, record, i)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  const lastRowId = rows.at(-1)?.rowId ?? null;
  return (
    <div className="rv-grid-wrap">
      <table className="rv-grid">
        <thead>
          <tr>
            <th className="rv-rownum" />
            <th className="rv-actions-h" />
            {layout.columns.map((c) => (
              <th key={c.letter} className="rv-letter">
                {c.letter}
              </th>
            ))}
          </tr>
          <tr>
            <th className="rv-rownum">{layout.first_row - 1}</th>
            <th className="rv-actions-h" />
            {layout.columns.map((c) => (
              <th key={c.letter} className="rv-header">
                {c.header}
                {c.required && <span className="req">*</span>}
                {c.ct_klass && <span className="rv-ct">CT</span>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ rowId, record }, index) => (
            <tr
              key={rowId}
              className={
                layout.leading_group && index > 0 && groupActive(layout, record, layout.leading_group) ? "group-start" : undefined
              }
            >
              <td className="rv-rownum">{layout.first_row + index}</td>
              <td className="rv-actions">
                <button title="Move up" disabled={index === 0} onClick={() => onRun([{ op: "move_row", sheet: layout.key, row_id: rowId!, to_index: index - 1 }])}>
                  ▲
                </button>
                <button
                  title="Move down"
                  disabled={index === rows.length - 1}
                  onClick={() => onRun([{ op: "move_row", sheet: layout.key, row_id: rowId!, to_index: index + 1 }])}
                >
                  ▼
                </button>
                <button title="Insert row below" onClick={() => onRun([{ op: "add_row", sheet: layout.key, after_row_id: rowId }])}>
                  +
                </button>
                <button
                  title="Delete row"
                  className="danger"
                  onClick={() => {
                    const name = fieldOf(record, "name")?.value ?? `row ${layout.first_row + index}`;
                    if (window.confirm(`Delete ${name} from ${layout.workbook_sheet}? This is recorded in the audit trail.`)) {
                      onRun([{ op: "delete_row", sheet: layout.key, row_id: rowId! }]);
                    }
                  }}
                >
                  ✕
                </button>
              </td>
              {layout.columns.map((_, columnIndex) => renderCell(rowId, index, record, columnIndex))}
            </tr>
          ))}
        </tbody>
      </table>
      <button className="btn small" onClick={() => onRun([{ op: "add_row", sheet: layout.key, after_row_id: lastRowId }])}>
        + Add row
      </button>
    </div>
  );
}

function InlineEditor(props: { initial: string; multiline: boolean; onCommit: (value: string) => void; onCancel: () => void }) {
  const { initial, multiline, onCommit, onCancel } = props;
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLTextAreaElement & HTMLInputElement>(null);
  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);

  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onCancel();
    } else if (e.key === "Enter" && (!multiline || e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      onCommit(value);
    }
  };

  return multiline ? (
    <textarea
      ref={ref}
      className="rv-editor"
      rows={Math.min(10, Math.max(3, value.split("\n").length + 1))}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={onKey}
      onBlur={() => onCommit(value)}
      onClick={(e) => e.stopPropagation()}
    />
  ) : (
    <input
      ref={ref}
      className="rv-editor"
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={onKey}
      onBlur={() => onCommit(value)}
      onClick={(e) => e.stopPropagation()}
    />
  );
}
