import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { api } from "../api";
import CellPanel from "../components/review/CellPanel";
import ScheduleGrid from "../components/review/ScheduleGrid";
import WorkbookPanel from "../components/review/WorkbookPanel";
import SheetGrid, { type Selection } from "../components/review/SheetGrid";
import { cellKey, useReview } from "../components/review/useReview";
import type { AuditEntry, ReviewIssue, ReviewSheetKey } from "../types";

/** The schedule-of-activities matrix: a view over the timepoints and schedule sheets. */
const GRID_KEY = "schedule-grid";

export default function ReviewPage() {
  const { slug = "", runId = "" } = useParams();
  const review = useReview(slug, runId);
  const { state, pending, saving, error, savedAt, save } = review;
  const [searchParams, setSearchParams] = useSearchParams();
  const [selection, setSelection] = useState<Selection | null>(null);
  const [showWarnings, setShowWarnings] = useState(false);
  const [audit, setAudit] = useState<AuditEntry[] | null>(null);

  const sheetKey = (searchParams.get("sheet") as ReviewSheetKey | null) ?? "study";
  const showGrid = sheetKey === GRID_KEY;
  const layout = state?.layouts.find((l) => l.key === sheetKey) ?? state?.layouts[0];

  // Ctrl+S saves the draft.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void save();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [save]);

  const counts = useMemo(() => {
    const map = new Map<string, { blocking: number; warnings: number }>();
    for (const issue of state?.validation.issues ?? []) {
      const entry = map.get(issue.sheet) ?? { blocking: 0, warnings: 0 };
      if (issue.severity === "blocking") entry.blocking += 1;
      else entry.warnings += 1;
      map.set(issue.sheet, entry);
    }
    return map;
  }, [state]);

  if (!state || !layout) {
    return (
      <section>
        <Breadcrumb slug={slug} runId={runId} />
        {error ? <div className="alert error">{error}</div> : <p className="muted">Loading review…</p>}
      </section>
    );
  }

  const { document: doc, validation } = state;
  const selectSheet = (key: string) => setSearchParams({ sheet: key }, { replace: true });
  const goTo = (issue: ReviewIssue) => {
    if (!issue.field) return;
    selectSheet(issue.sheet);
    setSelection({ sheet: issue.sheet as ReviewSheetKey, rowId: issue.row_id, field: issue.field });
    setTimeout(() => {
      document
        .getElementById(`cell-${issue.sheet}-${issue.row_id ?? "study"}-${issue.field}`)
        ?.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
    }, 50);
  };

  const blocking = validation.issues.filter((i) => i.severity === "blocking");
  const warnings = validation.issues.filter((i) => i.severity === "warning");
  const canConfirm = validation.blocking === 0 && pending.size === 0 && !saving && doc.status === "draft";
  const selectedLayout = selection ? state.layouts.find((l) => l.key === selection.sheet) : undefined;

  return (
    <section className="review">
      <div className="page-head">
        <div>
          <Breadcrumb slug={slug} runId={runId} />
          <h1>Review extracted data</h1>
          <div className="row-inline small">
            <span className={`badge ${doc.status === "confirmed" ? "completed" : "awaiting_review"}`}>{doc.status}</span>
            <span className="muted">revision {doc.revision}</span>
            <span className="muted">CT {doc.ct_version}</span>
            <span className={pending.size ? "rv-unsaved" : "muted"}>
              {saving ? "Saving…" : pending.size ? `${pending.size} unsaved edit${pending.size > 1 ? "s" : ""}` : savedAt ? `Saved ${savedAt.toLocaleTimeString()}` : "All changes saved"}
            </span>
          </div>
        </div>
        <div className="actions">
          <button className="btn" onClick={() => void review.save()} disabled={saving || pending.size === 0} title="Ctrl+S">
            Save draft
          </button>
          <button
            className="btn"
            onClick={async () => setAudit(audit ? null : await api.getReviewAudit(slug, runId, 100))}
          >
            {audit ? "Hide audit trail" : "Audit trail"}
          </button>
          <button
            className="btn primary"
            disabled={!canConfirm}
            title={
              doc.status === "confirmed"
                ? "Already confirmed"
                : validation.blocking
                  ? `${validation.blocking} blocking issue(s) must be resolved first`
                  : pending.size
                    ? "Save your edits first"
                    : "Confirm the reviewed data"
            }
            onClick={() => {
              if (window.confirm("Confirm this review? Workbook and USDM generation arrive in later phases; editing again reopens the review.")) {
                void review.confirm();
              }
            }}
          >
            Confirm review
          </button>
        </div>
      </div>

      {error && (
        <div className="alert error row-inline">
          {error}
          <button className="btn small" onClick={() => review.setError(null)}>
            Dismiss
          </button>
        </div>
      )}
      {state.stale && (
        <div className="alert warn row-inline">
          Extraction was re-run after this review started, so the review is based on older extracted values.
          <button
            className="btn small"
            onClick={() => {
              if (window.confirm("Discard this review and start again from the latest extraction? The current review is archived and the restart is audited.")) {
                void review.restart();
              }
            }}
          >
            Restart from latest extraction
          </button>
        </div>
      )}
      {doc.status === "confirmed" && (
        <div className="alert ok small">
          Confirmed {doc.confirmed_at ? new Date(doc.confirmed_at).toLocaleString() : ""}. Any further edit reopens the review as a draft.
        </div>
      )}
      <WorkbookPanel slug={slug} runId={runId} confirmed={doc.status === "confirmed"} revision={doc.revision} />

      {audit && <AuditTable entries={audit} />}

      <div className="tabs">
        <button className={`tab${showGrid ? " active" : ""}`} onClick={() => selectSheet(GRID_KEY)}>
          Schedule grid <span className="mono muted small">timelines</span>
        </button>
        {state.layouts.map((l) => {
          const c = counts.get(l.key);
          return (
            <button key={l.key} className={`tab${!showGrid && l.key === layout.key ? " active" : ""}`} onClick={() => selectSheet(l.key)}>
              {l.title}
              <span className="mono muted small">{l.workbook_sheet}</span>
              {c?.blocking ? <span className="rv-count blocking">{c.blocking}</span> : null}
              {c?.warnings ? <span className="rv-count warning">{c.warnings}</span> : null}
            </button>
          );
        })}
      </div>

      <div className="rv-layout">
        <div className="rv-main">
          {showGrid ? (
            <ScheduleGrid state={state} selection={selection} onSelect={setSelection} onRun={(ops) => void review.run(ops)} />
          ) : (
            <>
              <Legend />
              <SheetGrid
                layout={layout}
                state={state}
                pending={pending}
                selection={selection}
                onSelect={setSelection}
                onEdit={(sel, value) => review.queueSet({ op: "set", sheet: sel.sheet, row_id: sel.rowId, field: sel.field, value })}
                onRun={(ops) => void review.run(ops)}
              />
            </>
          )}
        </div>

        <aside className="rv-side">
          <div className="panel rv-validation">
            <h3>
              Validation · <span className={validation.blocking ? "error-text" : "ok-text"}>{validation.blocking} blocking</span> ·{" "}
              {validation.warnings} warnings
            </h3>
            {blocking.length === 0 ? (
              <p className="small ok-text">No blocking issues. {doc.status === "draft" ? "The review can be confirmed." : ""}</p>
            ) : (
              <ul className="rv-issues">
                {blocking.map((issue, i) => (
                  <li key={i}>
                    <button className="link" onClick={() => goTo(issue)}>
                      <span className="mono">{issue.cell ?? issue.sheet}</span>
                    </button>{" "}
                    {issue.message}
                  </li>
                ))}
              </ul>
            )}
            {warnings.length > 0 && (
              <>
                <button className="link small" onClick={() => setShowWarnings(!showWarnings)}>
                  {showWarnings ? "Hide" : "Show"} {warnings.length} warning(s) — low confidence or unverified source
                </button>
                {showWarnings && (
                  <ul className="rv-issues warnings">
                    {warnings.map((issue, i) => (
                      <li key={i}>
                        <button className="link" onClick={() => goTo(issue)}>
                          <span className="mono">{issue.cell}</span>
                        </button>{" "}
                        {issue.message}
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>

          <div className="panel">
            {selection && selectedLayout ? (
              <CellPanel
                slug={slug}
                runId={runId}
                state={state}
                layout={selectedLayout}
                selection={selection}
                pendingValue={pending.get(cellKey(selection.sheet, selection.rowId, selection.field))?.value}
                onEdit={(value) =>
                  review.queueSet({ op: "set", sheet: selection.sheet, row_id: selection.rowId, field: selection.field, value })
                }
                onRun={(ops) => void review.run(ops)}
              />
            ) : (
              <p className="muted">Select a cell to see its source, confidence and terminology, or double-click to edit it.</p>
            )}
          </div>
        </aside>
      </div>
    </section>
  );
}

function Breadcrumb({ slug, runId }: { slug: string; runId: string }) {
  return (
    <div className="muted small">
      <Link to="/">Studies</Link> / {slug} / <Link to={`/studies/${slug}/runs/${runId}?tab=extraction`}>run {runId}</Link> / review
    </div>
  );
}

function Legend() {
  const items: [string, string][] = [
    ["ok", "high confidence"],
    ["derived", "generated"],
    ["low", "low confidence"],
    ["term", "terminology unresolved"],
    ["edited", "user-edited"],
    ["empty-required", "required, empty"],
    ["na", "not extracted yet"],
  ];
  return (
    <div className="legend rv-legend">
      {items.map(([cls, label]) => (
        <span key={cls} className={`rv-cell ${cls}`}>
          {label}
        </span>
      ))}
    </div>
  );
}

function AuditTable({ entries }: { entries: AuditEntry[] }) {
  return (
    <div className="panel scroll-x rv-audit">
      <h3>Audit trail (latest {entries.length}, append-only)</h3>
      {entries.length === 0 ? (
        <p className="muted small">No review changes yet.</p>
      ) : (
        <table className="grid static small">
          <thead>
            <tr>
              <th>Time</th>
              <th>Rev</th>
              <th>Action</th>
              <th>Cell</th>
              <th>Old</th>
              <th>New</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {[...entries].reverse().map((e, i) => (
              <tr key={i}>
                <td className="nowrap">{new Date(e.ts).toLocaleString()}</td>
                <td className="mono">{e.revision}</td>
                <td>{e.action}</td>
                <td className="mono">{e.cell ?? "—"}</td>
                <td className="rv-audit-value">
                  {e.old_value ?? ""}
                  {e.old_code ? ` (${e.old_code})` : ""}
                </td>
                <td className="rv-audit-value">
                  {e.new_value ?? ""}
                  {e.new_code ? ` (${e.new_code})` : ""}
                </td>
                <td className="muted">{e.detail ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
