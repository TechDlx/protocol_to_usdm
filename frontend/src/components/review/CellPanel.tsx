import { useEffect, useState } from "react";

import { api } from "../../api";
import type { Codelist, ReviewOperation, ReviewState, SheetLayout, TerminologyResolution } from "../../types";
import { cellRef, cellState, entityNames, fieldOf, issuesByCell, sheetRows, STATE_LABEL } from "./cells";
import type { Selection } from "./SheetGrid";
import { cellKey } from "./useReview";

interface Props {
  slug: string;
  runId: string;
  state: ReviewState;
  layout: SheetLayout;
  selection: Selection;
  pendingValue: string | null | undefined;
  onEdit: (value: string | null) => void;
  onRun: (operations: ReviewOperation[]) => void;
}

export default function CellPanel({ slug, runId, state, layout, selection, pendingValue, onEdit, onRun }: Props) {
  const column = layout.columns.find((c) => c.field === selection.field);
  const rows = sheetRows(state.document.sheets, layout);
  const rowIndex = Math.max(0, rows.findIndex((r) => r.rowId === selection.rowId));
  const record = rows[rowIndex]?.record;
  if (!column || !record) return <p className="muted">The selected cell no longer exists.</p>;

  const field = fieldOf(record, selection.field);
  const value = pendingValue !== undefined ? pendingValue : (field?.value ?? null);
  const issues = issuesByCell(state).get(cellKey(selection.sheet, selection.rowId, selection.field)) ?? [];
  const stateName = cellState(column, field, issues, pendingValue !== undefined);
  const p = field?.provenance;
  const canAccept = p?.origin === "extracted" && !p.reviewer_accepted && issues.some((i) => i.severity === "warning");

  return (
    <div className="detail rv-panel">
      <div>
        <div className="mono small muted">{cellRef(layout, column, rowIndex)}</div>
        <h2>
          {column.header}
          {column.required && <span className="req"> *</span>}
        </h2>
        <span className={`chip rv-state ${stateName}`}>{STATE_LABEL[stateName]}</span>
        {column.format_hint && <div className="muted small">Format: {column.format_hint}</div>}
        {column.group && <div className="muted small">Part of the {column.group}: fill it only on the row that starts one.</div>}
      </div>

      {issues.length > 0 && (
        <ul className="rv-issue-list small">
          {issues.map((i) => (
            <li key={`${i.kind}-${i.message}`} className={i.severity}>
              {i.message}
            </li>
          ))}
        </ul>
      )}

      {(column.ct_klass && column.ct_attribute) || column.bc ? (
        <TerminologyPicker
          key={cellKey(selection.sheet, selection.rowId, selection.field)}
          search={(q) => (column.bc ? api.getBiomedicalConcepts(q) : api.getCodelist(column.ct_klass!, column.ct_attribute!, q))}
          validate={(phrase) =>
            column.bc ? api.resolveBiomedicalConcept(phrase) : api.resolveTerm(column.ct_klass!, column.ct_attribute!, phrase)
          }
          conceptNote={column.bc}
          multi={column.multi}
          otherAllowed={column.other_allowed}
          value={value}
          current={field?.terminology ?? null}
          onPick={(code, preferredTerm) =>
            onRun([{ op: "set", sheet: selection.sheet, row_id: selection.rowId, field: selection.field, value: preferredTerm, code }])
          }
          onSetText={(text) =>
            onRun([{ op: "set", sheet: selection.sheet, row_id: selection.rowId, field: selection.field, value: text === "" ? null : text }])
          }
          onFreeText={(text) => onEdit(text === "" ? null : text)}
        />
      ) : column.ref.length > 0 && column.multi ? (
        <MultiRefEditor
          key={cellKey(selection.sheet, selection.rowId, selection.field)}
          value={value}
          options={entityNames(state.document.sheets, state.layouts, column.ref, scopeOf(record))}
          what={column.ref.join(" or ")}
          onChoose={(text) =>
            onRun([{ op: "set", sheet: selection.sheet, row_id: selection.rowId, field: selection.field, value: text }])
          }
        />
      ) : column.ref.length > 0 || column.choices.length > 0 ? (
        <ChoiceEditor
          key={cellKey(selection.sheet, selection.rowId, selection.field)}
          value={value}
          options={
            column.ref.length > 0
              ? [...column.ref_literals, ...entityNames(state.document.sheets, state.layouts, column.ref, scopeOf(record))]
              : column.choices
          }
          what={column.ref.length > 0 ? `a ${column.ref.join(" or ")} name` : "a value"}
          onChoose={(choice) =>
            onRun([{ op: "set", sheet: selection.sheet, row_id: selection.rowId, field: selection.field, value: choice }])
          }
        />
      ) : (
        <ValueEditor key={cellKey(selection.sheet, selection.rowId, selection.field)} value={value} multiline={column.multiline} onSave={onEdit} />
      )}

      {canAccept && (
        <button
          className="btn small"
          onClick={() => onRun([{ op: "accept", sheet: selection.sheet, row_id: selection.rowId, field: selection.field }])}
          title="Mark this flagged value as reviewed and correct as extracted"
        >
          Accept as extracted
        </button>
      )}

      {p && (
        <div className="card-section">
          <h3>Provenance</h3>
          <dl className="facts small">
            <dt>Origin</dt>
            <dd>
              {p.origin}
              {p.reviewer_accepted ? " · accepted by reviewer" : ""}
            </dd>
            <dt>Confidence</dt>
            <dd>{p.confidence.toFixed(2)}</dd>
            <dt>Source verified</dt>
            <dd>
              {p.origin === "derived"
                ? "not applicable — generated by the application"
                : p.origin === "human"
                  ? "not applicable — entered by the reviewer"
                  : p.verified
                    ? "yes — quote found in the protocol"
                    : "no"}
            </dd>
            <dt>Section</dt>
            <dd className="mono">{p.source_section_id ?? "—"}</dd>
            <dt>Page</dt>
            <dd>{p.source_page ?? "—"}</dd>
            <dt>Extracted phrase</dt>
            <dd>{p.raw_phrase ? `“${p.raw_phrase}”` : "—"}</dd>
            {p.note && (
              <>
                <dt>Note</dt>
                <dd>{p.note}</dd>
              </>
            )}
          </dl>
        </div>
      )}

      {p?.source_page && <SourcePreview slug={slug} runId={runId} page={p.source_page} quote={p.raw_phrase} />}
    </div>
  );
}

function ValueEditor({ value, multiline, onSave }: { value: string | null; multiline: boolean; onSave: (value: string | null) => void }) {
  const [draft, setDraft] = useState(value ?? "");
  useEffect(() => setDraft(value ?? ""), [value]);
  const dirty = draft !== (value ?? "");
  return (
    <div className="card-section">
      <h3>Value</h3>
      <textarea
        className="rv-panel-editor"
        rows={multiline ? 8 : 2}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) onSave(draft === "" ? null : draft);
        }}
      />
      <div className="row-inline">
        <button className="btn small primary" disabled={!dirty} onClick={() => onSave(draft === "" ? null : draft)}>
          Apply
        </button>
        <button className="btn small" disabled={!dirty} onClick={() => setDraft(value ?? "")}>
          Revert
        </button>
        <span className="muted small">Ctrl+Enter to apply · double-click a cell to edit in place</span>
      </div>
    </div>
  );
}

function ChoiceEditor(props: { value: string | null; options: string[]; what: string; onChoose: (value: string | null) => void }) {
  const { value, options, what, onChoose } = props;
  const listed = value === null || options.includes(value);
  return (
    <div className="card-section">
      <h3>Value</h3>
      <select className="rv-search" value={value ?? ""} onChange={(e) => onChoose(e.target.value === "" ? null : e.target.value)}>
        <option value="">(empty)</option>
        {!listed && <option value={value!}>{value} (not a valid choice)</option>}
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
      <span className="muted small">
        Choose {what}.{options.length === 0 ? " None exist yet on the other sheets." : ""}
      </span>
    </div>
  );
}

const OTHER = "other";

/** Pickers only offer names from the same timeline as the record being edited. */
function scopeOf(record: Record<string, unknown>): Record<string, string | null> {
  const timeline = fieldOf(record, "timeline");
  return timeline ? { timeline: timeline.value } : {};
}

function MultiRefEditor(props: { value: string | null; options: string[]; what: string; onChoose: (value: string | null) => void }) {
  const { value, options, what, onChoose } = props;
  const chosen = new Set(
    (value ?? "")
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean),
  );
  const unknown = [...chosen].filter((v) => !options.includes(v));
  const toggle = (name: string) => {
    const next = new Set(chosen);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    // Keep the order of the options (timepoints left to right), unknown names last.
    const ordered = [...options.filter((o) => next.has(o)), ...unknown.filter((u) => next.has(u))];
    onChoose(ordered.length ? ordered.join(", ") : null);
  };
  return (
    <div className="card-section">
      <h3>Value — {what} names</h3>
      <div className="rv-checklist">
        {options.map((o) => (
          <label key={o} className="small">
            <input type="checkbox" checked={chosen.has(o)} onChange={() => toggle(o)} /> {o}
          </label>
        ))}
        {unknown.map((u) => (
          <label key={u} className="small error-text">
            <input type="checkbox" checked onChange={() => toggle(u)} /> {u} (does not exist)
          </label>
        ))}
      </div>
      {options.length === 0 && <span className="muted small">None exist yet on the other sheets.</span>}
    </div>
  );
}

function TerminologyPicker(props: {
  search: (query: string) => Promise<Codelist>;
  validate: (phrase: string) => Promise<TerminologyResolution | null>;
  conceptNote: boolean;
  multi: boolean;
  otherAllowed: boolean;
  value: string | null;
  current: TerminologyResolution | null;
  onPick: (code: string, preferredTerm: string) => void;
  onSetText: (text: string) => void;
  onFreeText: (text: string) => void;
}) {
  const { search, validate, conceptNote, multi, otherAllowed, value, current, onPick, onSetText, onFreeText } = props;
  const [otherReason, setOtherReason] = useState<string | null>(null);
  const items = (value ?? "")
    .split(",")
    .map((i) => i.trim())
    .filter(Boolean);
  const itemTerm = (item: string) => item.split("=")[0]!.trim().toLowerCase();
  const chosen = (term: string) => items.some((i) => itemTerm(i) === term.toLowerCase());

  // Picking a term: single cells take its code; list cells toggle it; "Other" asks for the reason.
  const use = (code: string, preferredTerm: string) => {
    if (otherAllowed && preferredTerm.toLowerCase() === OTHER) {
      setOtherReason("");
      return;
    }
    if (!multi) {
      if (otherAllowed) onSetText(preferredTerm);
      else onPick(code, preferredTerm);
      return;
    }
    const next = chosen(preferredTerm)
      ? items.filter((i) => itemTerm(i) !== preferredTerm.toLowerCase())
      : [...items, preferredTerm];
    onSetText(next.join(", "));
  };
  const applyOther = () => {
    const reason = (otherReason ?? "").replace(/[,=]/g, " ").trim();
    if (!reason) return;
    const kept = multi ? items.filter((i) => itemTerm(i) !== OTHER) : [];
    onSetText([...kept, `Other=${reason}`].join(", "));
    setOtherReason(null);
  };
  const [query, setQuery] = useState("");
  const [codelist, setCodelist] = useState<Codelist | null>(null);
  const [check, setCheck] = useState<TerminologyResolution | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const handle = setTimeout(async () => {
      try {
        setCodelist(await search(query));
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    }, 200);
    return () => clearTimeout(handle);
    // `search` is recreated on every render; only the query changes the results.
  }, [query]); // eslint-disable-line react-hooks/exhaustive-deps

  const candidateCodes = new Set((current?.candidates ?? []).map((c) => c.code));

  return (
    <div className="card-section rv-picker">
      <h3>Controlled terminology{codelist ? ` — ${codelist.codelist_name}` : ""}</h3>
      <div className="small">
        Current: <strong>{value ?? "(empty)"}</strong>{" "}
        {current && (
          <span className={`chip ${current.status === "exact" ? "found" : current.status === "fuzzy" ? "low_confidence" : "missing"}`}>
            {current.status}
            {current.code ? ` · ${current.code}` : ""}
          </span>
        )}
      </div>
      {multi && <div className="muted small">A list: add or remove terms; they are stored comma-separated.</div>}
      {conceptNote && (
        <div className="muted small">
          CDISC Biomedical Concepts from the bundled catalogue. A name that is not in it becomes a concept surrogate with no
          definition.
        </div>
      )}
      {otherReason !== null && (
        <div className="row-inline small">
          <input
            className="rv-search"
            autoFocus
            placeholder="Reason for Other"
            value={otherReason}
            onChange={(e) => setOtherReason(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") applyOther();
            }}
          />
          <button className="btn small primary" disabled={!otherReason.trim()} onClick={applyOther}>
            Use Other
          </button>
          <button className="btn small" onClick={() => setOtherReason(null)}>
            Cancel
          </button>
        </div>
      )}
      <input
        className="rv-search"
        placeholder="Search terms, submission values or C-codes, or type a phrase"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setCheck(undefined);
        }}
      />
      {error && <div className="alert error small">{error}</div>}
      {codelist && (
        <div className="rv-terms">
          <table className="grid static small">
            <thead>
              <tr>
                <th>C-code</th>
                <th>Submission value</th>
                <th>Preferred term</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {codelist.terms.slice(0, 40).map((t) => (
                <tr
                  key={t.code}
                  className={
                    (multi || otherAllowed ? chosen(t.preferred_term) : t.code === current?.code)
                      ? "selected"
                      : candidateCodes.has(t.code)
                        ? "candidate"
                        : ""
                  }
                >
                  <td className="mono">{t.code}</td>
                  <td>{t.submission_value}</td>
                  <td>{t.preferred_term}</td>
                  <td>
                    <button
                      className="btn small"
                      disabled={!multi && !otherAllowed && t.code === current?.code}
                      onClick={() => use(t.code, t.preferred_term)}
                    >
                      {multi ? (chosen(t.preferred_term) ? "Remove" : "Add") : "Use"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="muted small">
            {codelist.codelist} · CT {codelist.ct_version} · {codelist.extensible ? "extensible" : "not extensible"}
            {candidateCodes.size > 0 && " · shaded rows were suggested for the extracted phrase"}
          </div>
        </div>
      )}
      {query.trim() && (
        <div className="row-inline small">
          <button
            className="btn small"
            onClick={async () => {
              try {
                setCheck(await validate(query));
              } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
              }
            }}
          >
            Validate “{query}”
          </button>
          {check !== undefined && (
            <>
              <span className={`chip ${check?.status === "exact" ? "found" : "missing"}`}>
                {check ? `${check.status}${check.code ? ` · ${check.code} ${check.preferred_term}` : ""}` : "no match"}
              </span>
              {check?.status === "exact" && check.code && !multi ? (
                <button className="btn small primary" onClick={() => use(check.code!, check.preferred_term ?? query)}>
                  Use {check.preferred_term}
                </button>
              ) : (
                <button className="btn small" onClick={() => onFreeText(query)} title="Stored as typed; it stays a blocking issue until it matches a term">
                  Keep as typed
                </button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function SourcePreview({ slug, runId, page, quote }: { slug: string; runId: string; page: number; quote: string | null }) {
  const url = api.sourceHighlightUrl(slug, runId, page, quote);
  const [found, setFound] = useState<boolean | null>(null);
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    let revoke: string | null = null;
    let cancelled = false;
    setSrc(null);
    setFound(null);
    (async () => {
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok || cancelled) return;
      setFound(res.headers.get("X-Quote-Found") === "true");
      revoke = URL.createObjectURL(await res.blob());
      if (!cancelled) setSrc(revoke);
    })();
    return () => {
      cancelled = true;
      if (revoke) URL.revokeObjectURL(revoke);
    };
  }, [url]);

  return (
    <div className="card-section">
      <h3>Source — page {page}</h3>
      {found === false && quote && <div className="muted small">The phrase could not be highlighted on this page; showing the whole page.</div>}
      {src ? <img className="page-img" src={src} alt={`Protocol page ${page}`} /> : <div className="muted small">Loading page…</div>}
    </div>
  );
}
