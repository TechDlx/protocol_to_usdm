import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { api } from "../api";
import ExtractionTab from "../components/ExtractionTab";
import type {
  M11Coverage,
  ParsedDocument,
  RunDetail,
  Section,
  SectionAssignment,
  SectionMapping,
  Table,
} from "../types";

type Tab = "sections" | "coverage" | "tables" | "extraction";

const METHOD_LABEL: Record<SectionAssignment["method"], string> = {
  number_and_title: "number + title",
  title_match: "title",
  alias_match: "alias",
  content_soa: "SoA content",
  structural: "structural",
  appendix_default: "appendix default",
  inherited: "inherited",
  unmapped: "unmapped",
  excluded: "excluded",
};

const POLL_MS = 1000;

const TAB_LABEL: Record<Tab, string> = {
  sections: "Sections → M11",
  coverage: "M11 coverage",
  tables: "Tables",
  extraction: "Extraction",
};

export default function RunInspectorPage() {
  const { slug = "", runId = "" } = useParams();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [doc, setDoc] = useState<ParsedDocument | null>(null);
  const [mapping, setMapping] = useState<SectionMapping | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The active tab lives in the URL (?tab=extraction) so views can be linked and bookmarked.
  const [searchParams, setSearchParams] = useSearchParams();
  const tab: Tab = (["sections", "coverage", "tables", "extraction"] as Tab[]).includes(
    searchParams.get("tab") as Tab,
  )
    ? (searchParams.get("tab") as Tab)
    : "sections";
  const setTab = (next: Tab) => setSearchParams({ tab: next }, { replace: true });
  const [selected, setSelected] = useState<string | null>(null);
  // Incremented to restart polling after a re-run is requested.
  const [pollKey, setPollKey] = useState(0);

  const loadArtefacts = useCallback(async () => {
    const [d, m] = await Promise.all([api.getDocument(slug, runId), api.getSectionMapping(slug, runId)]);
    setDoc(d);
    setMapping(m);
  }, [slug, runId]);

  // Poll the run while it is processing; load the artefacts once parsed.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const state = await api.getRun(slug, runId);
        if (cancelled) return;
        setRun(state);
        setError(null);
        if (state.status === "running") {
          timer = setTimeout(tick, POLL_MS);
        } else if (state.stages.segment?.status === "done") {
          // Load whenever parsing finished, even if a later stage (extraction) failed.
          await loadArtefacts();
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [slug, runId, loadArtefacts, pollKey]);

  async function rerun(force: boolean) {
    try {
      setDoc(null);
      setMapping(null);
      setRun(await api.rerunIngestion(slug, runId, force));
      setPollKey((k) => k + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const reviewCount = mapping?.assignments.filter((a) => a.needs_review).length ?? 0;

  return (
    <section className="inspector">
      <div className="page-head">
        <div>
          <div className="muted small">
            <Link to="/">Studies</Link> / {slug} / <span className="mono">{runId}</span>
          </div>
          <h1>Parsed document</h1>
          {doc && <p className="muted">{doc.source.filename}</p>}
        </div>
        <div className="actions">
          <button className="btn small" disabled={run?.status === "running"} onClick={() => void rerun(false)}>
            Re-run (resume)
          </button>
          <button className="btn small" disabled={run?.status === "running"} onClick={() => void rerun(true)}>
            Force re-parse
          </button>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}
      {run && <StageBar run={run} />}

      {doc && mapping && (
        <>
          <div className="stats">
            <Stat label="Pages" value={doc.pages.length} />
            <Stat label="Sections" value={doc.sections.length} />
            <Stat label="Tables" value={`${doc.stats.tables} (${doc.stats.tables_needing_vision} need vision)`} />
            <Stat label="SoA pages" value={doc.stats.soa_pages.join(", ") || "none found"} />
            <Stat label="Flagged for review" value={`${reviewCount} of ${mapping.assignments.length}`} warn={reviewCount > 0} />
            <Stat label="Parse time" value={`${doc.stats.elapsed_seconds.toFixed(1)} s`} />
          </div>
          {doc.warnings.length > 0 && (
            <div className="alert warn small">
              {doc.warnings.map((w) => (
                <div key={w}>{w}</div>
              ))}
            </div>
          )}

          <div className="tabs">
            {(["sections", "coverage", "tables", "extraction"] as Tab[]).map((t) => (
              <button key={t} className={`tab${tab === t ? " active" : ""}`} onClick={() => setTab(t)}>
                {TAB_LABEL[t]}
              </button>
            ))}
          </div>

          {tab === "sections" && (
            <SectionsTab
              slug={slug}
              runId={runId}
              doc={doc}
              mapping={mapping}
              selected={selected}
              onSelect={setSelected}
            />
          )}
          {tab === "coverage" && (
            <CoverageTab
              mapping={mapping}
              doc={doc}
              onOpen={(sid) => {
                setSelected(sid);
                setTab("sections");
              }}
            />
          )}
          {tab === "tables" && <TablesTab slug={slug} runId={runId} doc={doc} />}
          {tab === "extraction" && run && (
            <ExtractionTab
              slug={slug}
              runId={runId}
              run={run}
              onStarted={(state) => {
                setRun(state);
                setPollKey((k) => k + 1);
              }}
            />
          )}
        </>
      )}
    </section>
  );
}

function StageBar({ run }: { run: RunDetail }) {
  const stages = [
    ["ingest", "1. Parse PDF"],
    ["segment", "2. Map to ICH M11"],
    ["extract", "3. Extract"],
  ] as const;
  return (
    <div className="stage-bar">
      <span className={`badge ${run.status}`}>{run.status}</span>
      {stages.map(([key, label]) => {
        const s = run.stages[key];
        if (key === "extract" && !s) return null;
        return (
          <div key={key} className={`stage ${s?.status ?? "pending"}`}>
            <strong>{label}</strong> <span className="small">{s?.status ?? "pending"}</span>
            {s?.detail && <div className="small muted">{s.detail}</div>}
            {s?.error && <div className="small error-text">{s.error}</div>}
          </div>
        );
      })}
    </div>
  );
}

function Stat({ label, value, warn }: { label: string; value: string | number; warn?: boolean }) {
  return (
    <div className={`stat${warn ? " warn" : ""}`}>
      <div className="small muted">{label}</div>
      <div className="stat-value">{value}</div>
    </div>
  );
}

function Confidence({ a, threshold }: { a: SectionAssignment; threshold: number }) {
  if (a.method === "excluded") return <span className="muted small">—</span>;
  const tone = a.confidence >= threshold ? "ok" : a.confidence >= 0.5 ? "mid" : "low";
  return (
    <span className="conf" title={`confidence ${a.confidence.toFixed(2)}`}>
      <span className={`conf-bar ${tone}`} style={{ width: `${Math.round(a.confidence * 100)}%` }} />
      <span className="conf-num">{a.confidence.toFixed(2)}</span>
    </span>
  );
}

// ----- sections ------------------------------------------------------------------------------

function SectionsTab(props: {
  slug: string;
  runId: string;
  doc: ParsedDocument;
  mapping: SectionMapping;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const { doc, mapping, selected, onSelect } = props;
  const [reviewOnly, setReviewOnly] = useState(false);
  const byId = useMemo(() => new Map(mapping.assignments.map((a) => [a.section_id, a])), [mapping]);
  const rows = doc.sections.filter((s) => !reviewOnly || byId.get(s.id)?.needs_review);
  const current = doc.sections.find((s) => s.id === selected) ?? null;

  return (
    <div className="split">
      <div className="split-main panel">
        <label className="small filter">
          <input type="checkbox" checked={reviewOnly} onChange={(e) => setReviewOnly(e.target.checked)} /> Show only
          sections flagged for review
        </label>
        <div className="scroll-x">
          <table className="grid">
            <thead>
              <tr>
                <th>Protocol section</th>
                <th>Pages</th>
                <th>ICH M11</th>
                <th>Confidence</th>
                <th>Method</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => {
                const a = byId.get(s.id);
                return (
                  <tr
                    key={s.id}
                    className={`${selected === s.id ? "selected" : ""}${a?.needs_review ? " review" : ""}`}
                    onClick={() => onSelect(s.id)}
                  >
                    <td style={{ paddingLeft: `${8 + (s.level - 1) * 16}px` }}>
                      {a?.needs_review && <span className="flag" title="Needs review">●</span>}
                      <span className="mono muted">{s.number ?? ""}</span> {s.title}
                    </td>
                    <td className="mono small nowrap">
                      {s.page_start === s.page_end ? s.page_start : `${s.page_start}–${s.page_end}`}
                    </td>
                    <td>
                      {a?.m11_number ? (
                        <>
                          <span className="mono">{a.m11_number}</span> <span className="small">{a.m11_title}</span>
                        </>
                      ) : (
                        <span className="muted small">{a?.method === "excluded" ? "not protocol content" : "unmapped"}</span>
                      )}
                    </td>
                    <td>{a && <Confidence a={a} threshold={mapping.review_threshold} />}</td>
                    <td className="small muted nowrap">{a ? METHOD_LABEL[a.method] : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      <aside className="split-side panel">
        {current ? (
          <SectionDetail {...props} section={current} assignment={byId.get(current.id) ?? null} />
        ) : (
          <p className="muted">Select a section to see its text, mapping candidates and source pages.</p>
        )}
      </aside>
    </div>
  );
}

function SectionDetail(props: {
  slug: string;
  runId: string;
  doc: ParsedDocument;
  section: Section;
  assignment: SectionAssignment | null;
}) {
  const { slug, runId, doc, section, assignment: a } = props;
  const [full, setFull] = useState(false);
  const [page, setPage] = useState(section.page_start);
  useEffect(() => setPage(section.page_start), [section.id, section.page_start]);
  const pageInfo = doc.pages.find((p) => p.number === page);
  const text = full || section.text.length <= 3000 ? section.text : `${section.text.slice(0, 3000)}…`;

  return (
    <div className="detail">
      <h2>
        <span className="mono muted">{section.number ?? ""}</span> {section.title}
      </h2>
      <div className="small muted">
        {section.kind.replace("_", " ")} · pages {section.page_start}–{section.page_end} · heading from{" "}
        {section.heading_source.replace(/_/g, " ")} · <span className="mono">{section.id}</span>
      </div>

      {a && (
        <div className="card-section">
          <h3>M11 mapping</h3>
          {a.m11_number ? (
            <div>
              <span className="mono">{a.m11_number}</span> {a.m11_title}
            </div>
          ) : (
            <div className="muted">No mapping</div>
          )}
          <div className="small muted">
            {METHOD_LABEL[a.method]}
            {a.matched_text ? ` via “${a.matched_text}”` : ""} · confidence {a.confidence.toFixed(2)}
            {a.needs_review && <span className="error-text"> · needs review</span>}
          </div>
          {a.candidates.length > 0 && (
            <ul className="list small">
              {a.candidates.map((c) => (
                <li key={c.m11_number} className="row-inline">
                  <span className="mono">{c.m11_number}</span> {c.m11_title}
                  <span className="muted"> {c.score.toFixed(2)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="card-section">
        <h3>Section text {section.text ? `(${section.text.length.toLocaleString()} chars, own content only)` : ""}</h3>
        {section.text ? <pre className="text">{text}</pre> : <p className="muted small">No own text — content is in subsections.</p>}
        {section.text.length > 3000 && (
          <button className="btn small" onClick={() => setFull(!full)}>
            {full ? "Show less" : "Show all"}
          </button>
        )}
      </div>

      {pageInfo && (
        <div className="card-section">
          <h3>Source page {page}</h3>
          <div className="row-inline">
            <button className="btn small" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              ‹ Prev
            </button>
            <button className="btn small" disabled={page >= doc.pages.length} onClick={() => setPage(page + 1)}>
              Next ›
            </button>
          </div>
          <img className="page-img" src={api.pageImageUrl(slug, runId, pageInfo.image_path)} alt={`Page ${page}`} />
        </div>
      )}
    </div>
  );
}

// ----- coverage ------------------------------------------------------------------------------

function CoverageTab({ mapping, doc, onOpen }: { mapping: SectionMapping; doc: ParsedDocument; onOpen: (id: string) => void }) {
  const [hideOptional, setHideOptional] = useState(true);
  const titles = useMemo(() => new Map(doc.sections.map((s) => [s.id, s])), [doc]);
  const rows = mapping.coverage.filter((c: M11Coverage) => !hideOptional || !c.optional);
  const counts = mapping.coverage.reduce<Record<string, number>>((acc, c) => {
    acc[c.status] = (acc[c.status] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="panel">
      <div className="row-inline small">
        <span className="chip found">found {counts.found ?? 0}</span>
        <span className="chip low_confidence">low confidence {counts.low_confidence ?? 0}</span>
        <span className="chip missing">not found {counts.missing ?? 0}</span>
        <label className="filter">
          <input type="checkbox" checked={hideOptional} onChange={(e) => setHideOptional(e.target.checked)} /> Hide
          optional M11 sections
        </label>
        <span className="muted">
          {mapping.template} {mapping.template_version}
        </span>
      </div>
      <div className="scroll-x">
        <table className="grid">
          <thead>
            <tr>
              <th>ICH M11 section</th>
              <th>Status</th>
              <th>Protocol sections</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.m11_number}>
                <td style={{ paddingLeft: `${8 + (c.level - 1) * 16}px` }}>
                  <span className="mono muted">{c.m11_number}</span> {c.m11_title}
                  {c.optional && <span className="muted small"> (optional)</span>}
                </td>
                <td>
                  <span className={`chip ${c.status}`}>{c.status.replace("_", " ")}</span>
                </td>
                <td className="small">
                  {c.section_ids.map((sid) => {
                    const s = titles.get(sid);
                    return (
                      <button key={sid} className="link" onClick={() => onOpen(sid)}>
                        {s?.number ? `${s.number} ` : ""}
                        {s?.title ?? sid}
                      </button>
                    );
                  })}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ----- tables --------------------------------------------------------------------------------

function TablesTab({ slug, runId, doc }: { slug: string; runId: string; doc: ParsedDocument }) {
  const [open, setOpen] = useState<string | null>(doc.tables.find((t) => t.is_soa_candidate)?.id ?? null);
  const sections = useMemo(() => new Map(doc.sections.map((s) => [s.id, s])), [doc]);

  return (
    <div className="panel">
      <p className="small muted">
        Tables parsed from ruling lines. Merged cells come back empty and multi-page grids are grouped; anything flagged
        “needs vision” is read from the page image by the extraction agents rather than trusted as parsed.
      </p>
      {doc.tables.map((t) => (
        <div key={t.id} className="table-block">
          <button className="table-head" onClick={() => setOpen(open === t.id ? null : t.id)}>
            <span className="mono">{t.id}</span> · page {t.page} · {t.row_count}×{t.col_count}
            {t.is_soa_candidate && <span className="chip soa">SoA {t.soa_score.toFixed(2)}</span>}
            {t.needs_vision && <span className="chip low_confidence">needs vision: {t.vision_reasons.join(", ")}</span>}
            <span className="muted small"> {t.caption ?? sections.get(t.section_id ?? "")?.title ?? ""}</span>
          </button>
          {open === t.id && <TableDetail slug={slug} runId={runId} doc={doc} table={t} />}
        </div>
      ))}
    </div>
  );
}

function TableDetail({ slug, runId, doc, table }: { slug: string; runId: string; doc: ParsedDocument; table: Table }) {
  const page = doc.pages.find((p) => p.number === table.page);
  return (
    <div className="split tight">
      <div className="split-main scroll-x">
        <table className="grid cells">
          <tbody>
            {table.cells.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td key={c} className={cell === null ? "merged" : r === 0 ? "head" : ""}>
                    {cell ?? ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {page && (
        <div className="split-side">
          <img className="page-img" src={api.pageImageUrl(slug, runId, page.image_path)} alt={`Page ${table.page}`} />
        </div>
      )}
    </div>
  );
}
