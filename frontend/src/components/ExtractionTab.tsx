import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import type {
  AgentRun,
  Extraction,
  ExtractedField,
  ReferenceValidation,
  RunDetail,
  SheetRecord,
} from "../types";

interface ProvenanceEntry {
  sheet: string;
  row: number | null;
  field: string;
  needs_review: boolean;
  review_reasons: string[];
}

interface Props {
  slug: string;
  runId: string;
  run: RunDetail;
  onStarted: (run: RunDetail) => void;
}

type SheetKey = "study" | "study_design_arms" | "eligibility_criteria";

const SHEETS: { key: SheetKey; label: string; columns: string[] }[] = [
  {
    key: "study",
    label: "study",
    columns: [
      "name",
      "label",
      "acronym",
      "sponsor_protocol_identifier",
      "official_title",
      "brief_title",
      "public_title",
      "scientific_title",
      "study_version",
      "protocol_version",
      "protocol_status",
      "rationale",
      "description",
    ],
  },
  {
    key: "study_design_arms",
    label: "studyDesignArms",
    columns: ["name", "label", "description", "type", "data_origin_description", "data_origin_type"],
  },
  {
    key: "eligibility_criteria",
    label: "eligibility criteria",
    columns: ["name", "category", "identifier", "label", "text"],
  },
];

const DATE_COLUMNS = ["name", "category", "label", "type", "date", "geographic_scopes"];

function isField(value: unknown): value is ExtractedField {
  return !!value && typeof value === "object" && "value" in value && "provenance" in value;
}

function cellState(field: ExtractedField | undefined, review: ProvenanceEntry | undefined): string {
  if (!field || field.value === null || field.value === "") return "empty";
  if (field.terminology && field.terminology.status !== "exact") return "term";
  if (review?.needs_review) return "low";
  if (field.provenance?.origin === "derived") return "derived";
  return "ok";
}

export default function ExtractionTab({ slug, runId, run, onStarted }: Props) {
  const [extraction, setExtraction] = useState<Extraction | null>(null);
  const [references, setReferences] = useState<ReferenceValidation | null>(null);
  const [review, setReview] = useState<Map<string, ProvenanceEntry>>(new Map());
  const [sheet, setSheet] = useState<SheetKey>("study");
  const [selected, setSelected] = useState<{ label: string; field: ExtractedField; entry?: ProvenanceEntry } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const running = run.status === "running";
  const extractStage = run.stages.extract;

  useEffect(() => {
    if (running || !extractStage) return;
    let cancelled = false;
    (async () => {
      try {
        const [ex, refs, prov] = await Promise.all([
          api.getExtraction(slug, runId),
          api.getReferenceValidation(slug, runId),
          fetch(`/api/studies/${encodeURIComponent(slug)}/runs/${encodeURIComponent(runId)}/provenance`).then(
            (r) => r.json() as Promise<ProvenanceEntry[]>,
          ),
        ]);
        if (cancelled) return;
        setExtraction(ex);
        setReferences(refs);
        setReview(new Map(prov.map((e) => [`${e.sheet}|${e.row ?? ""}|${e.field}`, e])));
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [slug, runId, running, extractStage?.finished_at]);

  async function start(force: boolean) {
    const note = force
      ? "Re-run every extraction agent, even those whose inputs are unchanged?"
      : "Run the extraction agents? Agents with unchanged inputs are reused.";
    if (!window.confirm(`${note}\n\nThis calls the Claude API and costs money.`)) return;
    setBusy(true);
    setError(null);
    try {
      onStarted(await api.startExtraction(slug, runId, force));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const agents = Object.values(run.agents ?? {});
  const totalCost = agents.reduce((sum, a) => sum + (a.status === "skipped" ? 0 : (a.usage?.cost_usd ?? 0)), 0);
  const flagged = useMemo(() => [...review.values()].filter((e) => e.needs_review).length, [review]);

  return (
    <div className="panel extraction">
      <div className="row-inline">
        <button className="btn primary small" disabled={running || busy} onClick={() => void start(false)}>
          {extractStage ? "Run extraction (resume)" : "Run extraction"}
        </button>
        <button className="btn small" disabled={running || busy || !extractStage} onClick={() => void start(true)}>
          Re-run all agents
        </button>
        {extraction && (
          <Link className="btn small" to={`/studies/${slug}/runs/${runId}/review`}>
            Open review
          </Link>
        )}
        <span className="muted small">Calls the Claude API. Values are read-only here; edit and confirm them on the review page.</span>
      </div>
      {error && <div className="alert error">{error}</div>}

      {agents.length > 0 && <AgentTable agents={agents} totalCost={totalCost} />}

      {extraction && (
        <>
          <div className="row-inline small">
            <span className="chip found">CT {extraction.ct_version}</span>
            <span className={`chip ${flagged ? "low_confidence" : "found"}`}>{flagged} values flagged for review</span>
            {references && (
              <span className={`chip ${references.valid ? "found" : "missing"}`}>
                references {references.valid ? "valid" : `${references.issues.length} issue(s)`}
              </span>
            )}
            <span className="legend">
              <span className="cell ok">verified</span>
              <span className="cell derived">generated</span>
              <span className="cell low">needs review</span>
              <span className="cell term">terminology not exact</span>
              <span className="cell empty">empty</span>
            </span>
          </div>
          {references && !references.valid && (
            <ul className="alert warn small">
              {references.issues.map((i) => (
                <li key={`${i.kind}-${i.name}-${i.locations.join()}`}>{i.message}</li>
              ))}
            </ul>
          )}

          <div className="tabs">
            {SHEETS.map((s) => (
              <button key={s.key} className={`tab${sheet === s.key ? " active" : ""}`} onClick={() => setSheet(s.key)}>
                {s.label}
              </button>
            ))}
          </div>

          <div className="split">
            <div className="split-main scroll-x">
              <SheetView
                sheetKey={sheet}
                extraction={extraction}
                review={review}
                onSelect={(label, field, entry) => setSelected({ label, field, entry })}
              />
            </div>
            <aside className="split-side panel">
              {selected ? (
                <FieldDetail slug={slug} runId={runId} {...selected} />
              ) : (
                <p className="muted">Select a cell to see its source, confidence and terminology.</p>
              )}
            </aside>
          </div>
        </>
      )}
    </div>
  );
}

function AgentTable({ agents, totalCost }: { agents: AgentRun[]; totalCost: number }) {
  return (
    <div className="scroll-x">
      <table className="grid static">
        <thead>
          <tr>
            <th>Agent</th>
            <th>Status</th>
            <th>Model</th>
            <th>Tokens in / out</th>
            <th>Cost</th>
            <th>Time</th>
            <th>Sections read</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {agents.map((a) => (
            <tr key={a.sheet}>
              <td className="mono">{a.sheet}</td>
              <td>
                <span className={`badge agent-${a.status}`}>{a.status}</span>
              </td>
              <td className="small">{a.usage?.model ?? "—"}</td>
              <td className="mono small">
                {a.usage ? `${a.usage.input_tokens.toLocaleString()} / ${a.usage.output_tokens.toLocaleString()}` : "—"}
              </td>
              <td className="mono small">
                {a.usage ? `$${a.usage.cost_usd.toFixed(3)}${a.status === "skipped" ? " (earlier)" : ""}` : "—"}
              </td>
              <td className="mono small">{a.usage ? `${a.usage.latency_seconds.toFixed(0)} s` : "—"}</td>
              <td className="small">{a.section_ids.length || "—"}</td>
              <td className="small">
                {a.error && <div className="error-text">{a.error}</div>}
                {a.warnings.map((w) => (
                  <div key={w} className="muted">
                    {w}
                  </div>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="muted small">This run's model cost: ${totalCost.toFixed(3)}</div>
    </div>
  );
}

function SheetView(props: {
  sheetKey: SheetKey;
  extraction: Extraction;
  review: Map<string, ProvenanceEntry>;
  onSelect: (label: string, field: ExtractedField, entry?: ProvenanceEntry) => void;
}) {
  const { sheetKey, extraction, review, onSelect } = props;
  const spec = SHEETS.find((s) => s.key === sheetKey)!;
  const data = extraction.sheets[sheetKey];
  const agent = extraction.agents[sheetKey];

  if (!data) {
    return (
      <p className="muted">
        No data for this sheet{agent?.status === "failed" ? ` — the agent failed: ${agent.error}` : " yet"}.
      </p>
    );
  }

  const cell = (record: SheetRecord, column: string, row: number | null, prefix = "") => {
    const field = record[column];
    if (!isField(field)) return <td key={column} />;
    const entry = review.get(`${sheetKey}|${row ?? ""}|${prefix}${column}`);
    const state = cellState(field, entry);
    return (
      <td
        key={column}
        className={`cell ${state}`}
        title={entry?.review_reasons.join("; ") || undefined}
        onClick={() => onSelect(`${prefix}${column}`, field, entry)}
      >
        <span className="clamp">{field.value ?? ""}</span>
      </td>
    );
  };

  if (sheetKey === "study") {
    const record = data as SheetRecord;
    const dates = (record.governance_dates as SheetRecord[] | undefined) ?? [];
    return (
      <>
        <table className="grid cells-table">
          <tbody>
            {spec.columns.map((column) => (
              <tr key={column}>
                <th className="key">{column}</th>
                {cell(record, column, null)}
              </tr>
            ))}
          </tbody>
        </table>
        <h3 className="gap">Governance dates</h3>
        {dates.length === 0 ? (
          <p className="muted small">None extracted.</p>
        ) : (
          <table className="grid cells-table">
            <thead>
              <tr>
                {DATE_COLUMNS.map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {dates.map((d, i) => (
                <tr key={i}>{DATE_COLUMNS.map((c) => cell(d, c, null, `governance_dates[${i + 1}].`))}</tr>
              ))}
            </tbody>
          </table>
        )}
      </>
    );
  }

  const rows = data as SheetRecord[];
  return (
    <table className="grid cells-table">
      <thead>
        <tr>
          <th>#</th>
          {spec.columns.map((c) => (
            <th key={c}>{c}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((record, i) => (
          <tr key={i}>
            <td className="muted mono small">{i + 1}</td>
            {spec.columns.map((c) => cell(record, c, i + 1))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function FieldDetail({
  slug,
  runId,
  label,
  field,
  entry,
}: {
  slug: string;
  runId: string;
  label: string;
  field: ExtractedField;
  entry?: ProvenanceEntry;
}) {
  const p = field.provenance;
  const t = field.terminology;
  const page = p?.source_page;
  return (
    <div className="detail">
      <h2 className="mono">{label}</h2>
      <pre className="text">{field.value ?? "(empty)"}</pre>
      {entry?.needs_review && (
        <div className="alert warn small">Needs review: {entry.review_reasons.join("; ")}</div>
      )}

      {p && (
        <div className="card-section">
          <h3>Provenance</h3>
          <dl className="facts small">
            <dt>Origin</dt>
            <dd>{p.origin}</dd>
            <dt>Confidence</dt>
            <dd>{p.confidence.toFixed(2)}</dd>
            <dt>Source verified</dt>
            <dd>{p.verified ? "yes — quote found in the protocol" : "no"}</dd>
            <dt>Section</dt>
            <dd className="mono">{p.source_section_id ?? "—"}</dd>
            <dt>Page</dt>
            <dd>{page ?? "—"}</dd>
            <dt>Quote</dt>
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

      {t && (
        <div className="card-section">
          <h3>Terminology — {t.codelist_name}</h3>
          <dl className="facts small">
            <dt>Status</dt>
            <dd>
              <span className={`chip ${t.status === "exact" ? "found" : t.status === "fuzzy" ? "low_confidence" : "missing"}`}>
                {t.status}
              </span>
              {t.matched_on && <span className="muted"> via {t.matched_on}</span>}
            </dd>
            <dt>Code</dt>
            <dd className="mono">{t.code ?? "— (not assigned)"}</dd>
            <dt>Preferred term</dt>
            <dd>{t.preferred_term ?? "—"}</dd>
            <dt>Submission value</dt>
            <dd>{t.submission_value ?? "—"}</dd>
            <dt>Codelist</dt>
            <dd className="mono">
              {t.codelist} · CT {t.ct_version}
            </dd>
          </dl>
          {t.candidates.length > 0 && (
            <>
              <div className="small muted">Candidates (a reviewer chooses; nothing is assigned automatically)</div>
              <ul className="list small">
                {t.candidates.map((c) => (
                  <li key={c.code} className="row-inline">
                    <span className="mono">{c.code}</span> {c.preferred_term}
                    <span className="muted">
                      ({c.submission_value}) {c.score.toFixed(0)}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {page && (
        <div className="card-section">
          <h3>Source page {page}</h3>
          <img
            className="page-img"
            src={api.pageImageUrl(slug, runId, `page_images/page-${String(page).padStart(4, "0")}.png`)}
            alt={`Page ${page}`}
          />
        </div>
      )}
    </div>
  );
}
