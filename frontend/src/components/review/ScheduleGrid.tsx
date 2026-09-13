import type { ReviewOperation, ReviewState, SheetLayout } from "../../types";
import { fieldOf, issuesByCell, sheetRows } from "./cells";
import type { Selection } from "./SheetGrid";

interface Props {
  state: ReviewState;
  selection: Selection | null;
  onSelect: (selection: Selection) => void;
  onRun: (operations: ReviewOperation[]) => void;
}

const split = (value: string | null | undefined) =>
  (value ?? "")
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);

/**
 * The schedule of activities as a matrix per timeline: epochs and timepoints across, activities down.
 * It is a view over the timepoints and schedule sheets: clicking a cell adds or removes that timepoint
 * in the row's scheduledAt list, so every change goes through the same audited operations.
 */
export default function ScheduleGrid({ state, selection, onSelect, onRun }: Props) {
  const sheets = state.document.sheets;
  const layout = (key: string) => state.layouts.find((l) => l.key === key) as SheetLayout;
  const timelines = sheetRows(sheets, layout("timelines"));
  const timepoints = sheetRows(sheets, layout("timepoints"));
  const rows = sheetRows(sheets, layout("schedule"));
  const activities = new Map(
    sheetRows(sheets, layout("activities")).map(({ record }) => [
      fieldOf(record, "name")?.value ?? "",
      fieldOf(record, "label")?.value ?? fieldOf(record, "name")?.value ?? "",
    ]),
  );
  const issues = issuesByCell(state);
  const threshold = state.confidence_threshold;

  if (timelines.length === 0) {
    return <p className="muted">No schedule of activities was extracted.</p>;
  }

  return (
    <div className="soa">
      <p className="muted small">
        Click a cell to add or remove an activity at a timepoint. Click an activity or timepoint name for its source and
        details. Every change is audited like any other edit.
      </p>
      {timelines.map(({ record: timeline, rowId: timelineRowId }) => {
        const name = fieldOf(timeline, "name")?.value ?? "";
        const columns = timepoints.filter(({ record }) => fieldOf(record, "timeline")?.value === name);
        const columnNames = columns.map(({ record }) => fieldOf(record, "name")?.value ?? "");
        const own = rows.filter(({ record }) => fieldOf(record, "timeline")?.value === name);
        // Epoch header: consecutive timepoints in the same epoch share one spanning cell.
        const epochs: { label: string; span: number }[] = [];
        for (const { record } of columns) {
          const epoch = fieldOf(record, "epoch")?.value ?? "";
          const last = epochs.at(-1);
          if (last && last.label === epoch) last.span += 1;
          else epochs.push({ label: epoch, span: 1 });
        }
        return (
          <section key={timelineRowId ?? name} className="soa-timeline">
            <h3>
              <button className="link" onClick={() => onSelect({ sheet: "timelines", rowId: timelineRowId, field: "name" })}>
                {name}
              </button>
              <span className="muted small">
                {" "}
                · sheet {fieldOf(timeline, "sheet_name")?.value} · {columns.length} timepoints · {own.length} activities
              </span>
            </h3>
            <div className="soa-wrap">
              <table className="soa-grid">
                <thead>
                  <tr>
                    <th className="soa-corner">Epoch</th>
                    {epochs.map((e, i) => (
                      <th key={i} colSpan={e.span} className="soa-epoch">
                        {e.label || "—"}
                      </th>
                    ))}
                  </tr>
                  <tr>
                    <th className="soa-corner">Activity</th>
                    {columns.map(({ record, rowId }) => {
                      const selected = selection?.sheet === "timepoints" && selection.rowId === rowId;
                      return (
                        <th key={rowId} className={`soa-tp${selected ? " selected" : ""}`}>
                          <button className="link" onClick={() => onSelect({ sheet: "timepoints", rowId, field: "label" })}>
                            {fieldOf(record, "label")?.value ?? fieldOf(record, "name")?.value}
                          </button>
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody>
                  {own.map(({ record, rowId }) => {
                    const scheduled = fieldOf(record, "scheduled_at");
                    const marks = new Set(split(scheduled?.value));
                    const unknown = [...marks].filter((m) => !columnNames.includes(m));
                    const activity = fieldOf(record, "activity")?.value ?? "";
                    const concepts = fieldOf(record, "biomedical_concepts")?.value;
                    const rowIssues = [
                      ...(issues.get(`schedule|${rowId}|scheduled_at`) ?? []),
                      ...(issues.get(`schedule|${rowId}|activity`) ?? []),
                    ];
                    const blocking = rowIssues.some((i) => i.severity === "blocking");
                    const low = (scheduled?.provenance?.confidence ?? 1) < threshold && scheduled?.provenance?.origin === "extracted";
                    const edited = scheduled?.provenance?.origin === "human";
                    const selected = selection?.sheet === "schedule" && selection.rowId === rowId;
                    const toggle = (tp: string) => {
                      const next = new Set(marks);
                      if (next.has(tp)) next.delete(tp);
                      else next.add(tp);
                      const ordered = [...columnNames.filter((c) => next.has(c)), ...unknown];
                      onRun([
                        {
                          op: "set",
                          sheet: "schedule",
                          row_id: rowId,
                          field: "scheduled_at",
                          value: ordered.length ? ordered.join(", ") : null,
                        },
                      ]);
                    };
                    return (
                      <tr key={rowId} className={selected ? "selected" : undefined}>
                        <th
                          className={`soa-activity${blocking ? " blocking" : low ? " low" : edited ? " edited" : ""}`}
                          title={rowIssues.map((i) => i.message).join("; ") || undefined}
                        >
                          <button className="link" onClick={() => onSelect({ sheet: "schedule", rowId, field: "scheduled_at" })}>
                            {activities.get(activity) || activity}
                          </button>
                          {concepts && (
                            <button
                              className="link soa-bc"
                              onClick={() => onSelect({ sheet: "schedule", rowId, field: "biomedical_concepts" })}
                              title="Biomedical Concepts"
                            >
                              {concepts}
                            </button>
                          )}
                          {unknown.length > 0 && <span className="error-text small"> also at unknown: {unknown.join(", ")}</span>}
                        </th>
                        {columnNames.map((tp) => (
                          <td
                            key={tp}
                            className={`soa-cell${marks.has(tp) ? " marked" : ""}`}
                            onClick={() => toggle(tp)}
                            title={`${activities.get(activity) || activity} at ${tp}`}
                          >
                            {marks.has(tp) ? "X" : ""}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}
    </div>
  );
}
