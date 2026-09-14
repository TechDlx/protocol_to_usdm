import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { api, ApiError } from "../api";
import ExtractionTab from "../components/ExtractionTab";
import type {
  M11Coverage,
  M11TemplateSection,
  MappingSuggestions,
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
  reviewer: "reviewer",
  claude: "Claude",
};

const POLL_MS = 1000;
// While nothing is running, keep checking slowly: a run can be started from another tab or the API.
const IDLE_POLL_MS = 10000;

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

  // Poll the run: every second while it is processing, every ten seconds otherwise, and at once when
  // the window regains focus. Parsed artefacts are (re)loaded only when parsing finished anew;
  // the Extraction tab reloads its data when the extract stage's finish time changes.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let loadedSegment: string | null | undefined;
    const tick = async () => {
      if (timer) clearTimeout(timer);
      try {
        const state = await api.getRun(slug, runId);
        if (cancelled) return;
        setRun(state);
        setError(null);
        const segment = state.stages.segment;
        if (state.status !== "running" && segment?.status === "done" && segment.finished_at !== loadedSegment) {
          // Load whenever parsing finished, even if a later stage (extraction) failed.
          loadedSegment = segment.finished_at;
          await loadArtefacts();
        }
        const active = state.status === "running" || state.status === "generating";
        if (!cancelled) timer = setTimeout(tick, active ? POLL_MS : IDLE_POLL_MS);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        timer = setTimeout(tick, IDLE_POLL_MS);
      }
    };
    const onFocus = () => void tick();
    window.addEventListener("focus", onFocus);
    void tick();
    return () => {
      cancelled = true;
      window.removeEventListener("focus", onFocus);
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
      {run && <StageBar run={run} slug={slug} />}

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
          {(mapping.ignored_overrides ?? []).length > 0 && (
            <div className="alert warn small">
              Some manual mappings were not applied because the parsed document changed:
              {mapping.ignored_overrides!.map((w) => (
                <div key={w}>{w}</div>
              ))}
            </div>
          )}
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
              locked={run?.status === "running" || run?.status === "generating"}
              onMappingChanged={setMapping}
              onDocumentChanged={loadArtefacts}
            />
          )}
          {tab === "coverage" && (
            <CoverageTab
              slug={slug}
              runId={runId}
              locked={run?.status === "running" || run?.status === "generating"}
              onMappingChanged={setMapping}
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

function StageBar({ run, slug }: { run: RunDetail; slug: string }) {
  const stages = [
    ["ingest", "1. Parse PDF"],
    ["segment", "2. Map to ICH M11"],
    ["extract", "3. Extract"],
    ["workbook", "4. Workbook"],
    ["usdm", "5. USDM + validation"],
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
            {key === "usdm" && s && (
              <Link className="small" to={`/studies/${slug}/runs/${run.run_id}/results`}>
                Open results →
              </Link>
            )}
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
  locked: boolean;
  onMappingChanged: (mapping: SectionMapping) => void;
  onDocumentChanged: () => Promise<void>;
}) {
  const { slug, runId, doc, mapping, selected, onSelect } = props;
  const [reviewOnly, setReviewOnly] = useState(false);
  const [suggestions, setSuggestions] = useState<MappingSuggestions | null>(null);
  useEffect(() => {
    let cancelled = false;
    api
      .getMappingSuggestions(slug, runId)
      .then((s) => !cancelled && setSuggestions(s))
      .catch((e: unknown) => {
        if (!(e instanceof ApiError && e.status === 404)) console.warn(e);
      });
    return () => {
      cancelled = true;
    };
  }, [slug, runId]);
  const byId = useMemo(() => new Map(mapping.assignments.map((a) => [a.section_id, a])), [mapping]);
  const rows = doc.sections.filter((s) => !reviewOnly || byId.get(s.id)?.needs_review);
  const current = doc.sections.find((s) => s.id === selected) ?? null;

  return (
    <div className="split">
      <div className="split-main panel">
        <ClaudeMappingPanel
          slug={slug}
          runId={runId}
          doc={doc}
          mapping={mapping}
          suggestions={suggestions}
          locked={props.locked}
          onSuggestions={setSuggestions}
          onMappingChanged={props.onMappingChanged}
          onSelect={onSelect}
        />
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
                      {(a?.also_m11 ?? []).map((r) => (
                        <div key={r.m11_number} className="small">
                          <span className="muted">also </span>
                          <span className="mono">{r.m11_number}</span> {r.m11_title}
                        </div>
                      ))}
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
  locked: boolean;
  onMappingChanged: (mapping: SectionMapping) => void;
  onDocumentChanged: () => Promise<void>;
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

      <PagesPanel
        key={`pages-${section.id}`}
        slug={slug}
        runId={runId}
        doc={doc}
        section={section}
        locked={props.locked}
        onDocumentChanged={props.onDocumentChanged}
      />

      {a && (
        <MappingPanel
          key={section.id}
          slug={slug}
          runId={runId}
          assignment={a}
          locked={props.locked}
          onMappingChanged={props.onMappingChanged}
        />
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

// ----- section pages -------------------------------------------------------------------------

function PagesPanel(props: {
  slug: string;
  runId: string;
  doc: ParsedDocument;
  section: Section;
  locked: boolean;
  onDocumentChanged: () => Promise<void>;
}) {
  const { slug, runId, doc, section, locked } = props;
  const index = doc.sections.findIndex((s) => s.id === section.id);
  const previous = index > 0 ? (doc.sections[index - 1] ?? null) : null;
  const [start, setStart] = useState(String(section.page_start));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  useEffect(() => setStart(String(section.page_start)), [section.page_start]);

  const movable = previous !== null && section.kind !== "toc" && previous.kind !== "toc";
  const wanted = Number(start);
  const valid = Number.isInteger(wanted) && wanted >= 1;
  let preview = "";
  if (previous && movable && valid && wanted > section.page_start) {
    preview = `Pages ${section.page_start}–${wanted - 1} of this section will move to "${previous.title}".`;
  } else if (previous && movable && valid && wanted < section.page_start) {
    preview = `Pages ${wanted}–${section.page_start - 1} of "${previous.title}" will move into this section.`;
  }

  async function change(request: () => Promise<unknown>, message: string) {
    setBusy(true);
    setError(null);
    try {
      await request();
      await props.onDocumentChanged();
      const changes = await api.getExtractionInputChanges(slug, runId).catch(() => []);
      setSaved(
        changes.length
          ? `${message} Agents whose input changed: ${changes.map((c) => c.sheet).join(", ")}; run extraction (resume) on the Extraction tab to update them.`
          : `${message} No extraction agent's input changed (or extraction has not run).`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card-section">
      <h3>Pages</h3>
      <div>
        {section.page_start === section.page_end ? `Page ${section.page_start}` : `Pages ${section.page_start}–${section.page_end}`}
        {section.parsed_page_start != null && (
          <span className="chip found small"> start moved by reviewer (parsed: page {section.parsed_page_start})</span>
        )}
      </div>
      {movable ? (
        <>
          <div className="row-inline small">
            <label>
              Starts on page{" "}
              <input
                className="page-input"
                type="number"
                min={1}
                max={doc.pages.length}
                value={start}
                disabled={locked || busy}
                onChange={(e) => setStart(e.target.value)}
              />
            </label>
            <button
              className="btn small"
              disabled={locked || busy || !valid || wanted === section.page_start}
              onClick={() =>
                void change(
                  () => api.setSectionStartPage(slug, runId, section.id, wanted),
                  `"${section.title}" now starts on page ${wanted}.`,
                )
              }
            >
              Apply
            </button>
            {section.parsed_page_start != null && (
              <button
                className="btn small"
                disabled={locked || busy}
                onClick={() =>
                  void change(
                    () => api.clearSectionStartPage(slug, runId, section.id),
                    `Back to the parsed start, page ${section.parsed_page_start}.`,
                  )
                }
              >
                Revert to parsed start
              </button>
            )}
          </div>
          {preview && <div className="small muted">{preview}</div>}
        </>
      ) : (
        <div className="small muted">
          {previous === null ? "The first section's start is fixed." : "The table of contents' pages are fixed by the parser."}
        </div>
      )}
      {error && <div className="alert error small">{error}</div>}
      {saved && !error && <div className="alert ok small">{saved}</div>}
    </div>
  );
}

// ----- Claude mapping ------------------------------------------------------------------------

/** Override a section back to its title-based mapping (keeps an audit trail of the choice). */
async function switchToTitleBased(slug: string, runId: string, a: SectionAssignment): Promise<SectionMapping> {
  if (a.rule_method === "excluded") {
    return api.setSectionMapping(slug, runId, a.section_id, { excluded: true, source: "rule" });
  }
  if (!a.rule_m11_number) throw new Error("the title-based mapping left this section unmapped");
  return api.setSectionMapping(slug, runId, a.section_id, { m11_number: a.rule_m11_number, source: "rule" });
}

function mappingText(number: string | null | undefined, title: string | null | undefined, method?: string | null): string {
  if (number) return `${number} ${title ?? ""}`.trim();
  return method === "excluded" ? "not protocol content" : "unmapped";
}

function ClaudeMappingPanel(props: {
  slug: string;
  runId: string;
  doc: ParsedDocument;
  mapping: SectionMapping;
  suggestions: MappingSuggestions | null;
  locked: boolean;
  onSuggestions: (s: MappingSuggestions) => void;
  onMappingChanged: (mapping: SectionMapping) => void;
  onSelect: (id: string) => void;
}) {
  const { slug, runId, mapping, suggestions } = props;
  const [scope, setScope] = useState<"all" | "flagged">("all");
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const titles = useMemo(() => new Map(props.doc.sections.map((s) => [s.id, s])), [props.doc]);

  const read = mapping.assignments.filter((a) => a.claude_confidence != null);
  const changed = mapping.assignments
    .filter((a) => a.rule_method != null)
    .sort((x, y) => Number(y.needs_review) - Number(x.needs_review));
  const toReview = changed.filter((a) => a.needs_review && !a.reviewer_override).length;

  async function rerun() {
    const note = force
      ? "Ask Claude again about every section in scope, even unchanged ones?"
      : "Run Claude's mapping for the sections in scope? Sections whose text and title-based mapping are unchanged reuse Claude's earlier answer at no cost.";
    if (!window.confirm(`${note}\n\nSection titles and the start of each section's text are sent to the Anthropic API.`)) return;
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const result = await api.suggestMappings(slug, runId, scope, [], force);
      props.onSuggestions(result);
      props.onMappingChanged(await api.getSectionMapping(slug, runId));
      setNote(
        `Claude mapping updated: ${result.asked} section(s) asked, ${result.reused} reused, $${result.usage.cost_usd.toFixed(3)}.`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function titleBased(a: SectionAssignment) {
    setBusy(true);
    setError(null);
    try {
      props.onMappingChanged(await switchToTitleBased(slug, runId, a));
      setNote(
        await withAffectedAgents(
          slug,
          runId,
          a.section_id,
          `"${a.doc_title}" uses its title-based mapping (${mappingText(a.rule_m11_number, a.rule_m11_title, a.rule_method)}).`,
        ),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="suggestions">
      <div className="small">
        {read.length ? (
          <>
            <strong>Mapped with Claude:</strong> Claude read {read.length} sections and changed the title-based mapping
            of {changed.length}
            {toReview ? (
              <>
                ; <span className="error-text">{toReview} need review</span>
              </>
            ) : null}
            .
            {suggestions && (
              <span className="muted">
                {" "}
                Last run {new Date(suggestions.generated_at).toLocaleString()} with {suggestions.model}: {suggestions.asked}{" "}
                asked, {suggestions.reused} reused, ${suggestions.usage.cost_usd.toFixed(3)}.
              </span>
            )}
          </>
        ) : (
          <span className="muted">
            Title-based mapping only: Claude has not mapped this run's sections (no API key, the call failed, or
            mapping assistance is off).
          </span>
        )}
      </div>
      <div className="row-inline small">
        <button className="btn small" disabled={props.locked || busy} onClick={() => void rerun()}>
          {busy ? "Working…" : read.length ? "Re-run Claude mapping…" : "Map with Claude…"}
        </button>
        <select value={scope} disabled={busy} onChange={(e) => setScope(e.target.value as "all" | "flagged")}>
          <option value="all">all sections</option>
          <option value="flagged">sections the title match flags or cannot map</option>
        </select>
        <label className="filter">
          <input type="checkbox" checked={force} disabled={busy} onChange={(e) => setForce(e.target.checked)} /> ask
          again for unchanged sections
        </label>
      </div>
      {error && <div className="alert error small">{error}</div>}
      {note && <div className="alert ok small">{note}</div>}
      {changed.length > 0 && (
        <details className="small" open={toReview > 0}>
          <summary>
            Where Claude changed the title-based mapping ({changed.length}
            {toReview ? `, ${toReview} to review` : ""})
          </summary>
          <table className="grid static">
            <thead>
              <tr>
                <th>Protocol section</th>
                <th>Claude</th>
                <th>Title-based</th>
                <th>Why</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {changed.map((a) => {
                const section = titles.get(a.section_id);
                return (
                  <tr key={a.section_id} className={a.needs_review ? "review" : ""}>
                    <td>
                      <button className="link" onClick={() => props.onSelect(a.section_id)}>
                        <span className="mono muted">{section?.number ?? ""}</span> {a.doc_title}
                      </button>
                      {a.needs_review && <span className="chip low_confidence"> review</span>}
                    </td>
                    <td>
                      {a.reviewer_override ? (
                        <span className="muted">reviewer: {mappingText(a.m11_number, a.m11_title, a.method)}</span>
                      ) : (
                        <>
                          {mappingText(a.m11_number, a.m11_title, a.method)}
                          {a.also_m11.map((r) => `, also ${r.m11_number}`).join("")}{" "}
                          <span className="muted">({(a.claude_confidence ?? 0).toFixed(2)})</span>
                        </>
                      )}
                    </td>
                    <td>
                      {mappingText(a.rule_m11_number, a.rule_m11_title, a.rule_method)}{" "}
                      <span className="muted">({(a.rule_confidence ?? 0).toFixed(2)})</span>
                    </td>
                    <td className="muted">{a.claude_reason}</td>
                    <td className="nowrap">
                      {!a.reviewer_override && (a.rule_m11_number || a.rule_method === "excluded") && (
                        <button className="btn small" disabled={props.locked || busy} onClick={() => void titleBased(a)}>
                          Use title-based
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}

// ----- manual mapping ------------------------------------------------------------------------

/** The saved-message, followed by the extraction agents whose input this section's change altered. */
async function withAffectedAgents(slug: string, runId: string, sectionId: string, message: string): Promise<string> {
  const changes = await api.getExtractionInputChanges(slug, runId).catch(() => []);
  const affected = changes
    .filter((c) => c.added.includes(sectionId) || c.removed.includes(sectionId))
    .map((c) => c.sheet);
  return affected.length
    ? `${message} Agents whose input changed: ${affected.join(", ")}; run extraction (resume) on the Extraction tab to update them.`
    : `${message} No extraction agent's input changed (no agent reads these M11 sections, or extraction has not run).`;
}

let templateRequest: Promise<M11TemplateSection[]> | null = null;
function m11Template(): Promise<M11TemplateSection[]> {
  templateRequest ??= api.getM11Template().catch((e: unknown) => {
    templateRequest = null;
    throw e;
  });
  return templateRequest;
}

function MappingPanel(props: {
  slug: string;
  runId: string;
  assignment: SectionAssignment;
  locked: boolean;
  onMappingChanged: (mapping: SectionMapping) => void;
}) {
  const { slug, runId, assignment: a, locked } = props;
  // "main" replaces the section's mapping; "also" adds a further M11 section.
  const [editing, setEditing] = useState<"main" | "also" | null>(null);
  const [template, setTemplate] = useState<M11TemplateSection[]>([]);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    if (!editing || template.length) return;
    m11Template()
      .then(setTemplate)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [editing, template.length]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return template;
    return template.filter((t) => t.number.toLowerCase().startsWith(q) || t.title.toLowerCase().includes(q));
  }, [template, query]);

  async function save(change: () => Promise<SectionMapping>, message: string) {
    setBusy(true);
    setError(null);
    try {
      props.onMappingChanged(await change());
      setEditing(null);
      setQuery("");
      setSaved(await withAffectedAgents(slug, runId, a.section_id, message));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const mapTo = (number: string, title: string) =>
    void save(
      () => api.setSectionMapping(slug, runId, a.section_id, { m11_number: number }),
      `Mapped to ${number} ${title}.`,
    );
  const alsoMapTo = (number: string, title: string) =>
    void save(
      () => api.addSectionMapping(slug, runId, a.section_id, number),
      `Also mapped to ${number} ${title}.`,
    );
  const mapped = new Set([a.m11_number, ...a.also_m11.map((r) => r.m11_number)]);
  const disabled = locked || busy;

  return (
    <div className="card-section">
      <h3>M11 mapping</h3>
      {a.m11_number ? (
        <div>
          <span className="mono">{a.m11_number}</span> {a.m11_title}
        </div>
      ) : (
        <div className="muted">{a.method === "excluded" ? "Not protocol content" : "No mapping"}</div>
      )}
      {a.claude_reason && (
        <div className="small">
          <span className="muted">Claude ({(a.claude_confidence ?? 0).toFixed(2)}):</span> {a.claude_reason}
        </div>
      )}
      {a.rule_method != null && !a.reviewer_override && (
        <div className="row-inline small">
          <span className="muted">
            Title-based mapping: {mappingText(a.rule_m11_number, a.rule_m11_title, a.rule_method)} (
            {(a.rule_confidence ?? 0).toFixed(2)}, {METHOD_LABEL[a.rule_method]})
          </span>
          {(a.rule_m11_number || a.rule_method === "excluded") && (
            <button
              className="btn small"
              disabled={disabled}
              onClick={() =>
                void save(
                  () => switchToTitleBased(slug, runId, a),
                  `Using the title-based mapping (${mappingText(a.rule_m11_number, a.rule_m11_title, a.rule_method)}).`,
                )
              }
            >
              Use title-based mapping
            </button>
          )}
        </div>
      )}
      {a.also_m11.map((r) => (
        <div key={r.m11_number} className="row-inline small">
          <span className="muted">also</span> <span className="mono">{r.m11_number}</span> {r.m11_title}
          <button
            className="btn small"
            disabled={disabled}
            title="Remove this further mapping"
            onClick={() =>
              void save(
                () => api.removeSectionMapping(slug, runId, a.section_id, r.m11_number),
                `No longer mapped to ${r.m11_number}.`,
              )
            }
          >
            Remove
          </button>
        </div>
      ))}
      <div className="small muted">
        {a.reviewer_override && (a.method === "reviewer" || a.method === "excluded") ? (
          <span className="chip found">mapped by reviewer</span>
        ) : (
          <>
            {METHOD_LABEL[a.method]}
            {a.matched_text ? ` via "${a.matched_text}"` : ""} · confidence {a.confidence.toFixed(2)}
          </>
        )}
        {a.needs_review && <span className="error-text"> · needs review</span>}
      </div>

      {a.candidates.some((c) => c.score > 0) && (
        <ul className="list small">
          {a.candidates.filter((c) => c.score > 0).map((c) => (
            <li key={c.m11_number} className="row-inline">
              <span className="mono">{c.m11_number}</span> {c.m11_title}
              <span className="muted"> {c.score.toFixed(2)}</span>
              {c.m11_number !== a.m11_number && (
                <button className="btn small" disabled={disabled} onClick={() => mapTo(c.m11_number, c.m11_title)}>
                  Use
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="row-inline">
        {!editing && (
          <button className="btn small" disabled={disabled} onClick={() => setEditing("main")}>
            Map to M11 section…
          </button>
        )}
        {!editing && a.method !== "excluded" && (
          <button
            className="btn small"
            disabled={disabled}
            title="Keep the current mapping and add another M11 section (for a section that covers two)"
            onClick={() => setEditing("also")}
          >
            Also map to…
          </button>
        )}
        {a.method !== "excluded" && (
          <button
            className="btn small"
            disabled={disabled}
            onClick={() =>
              void save(
                () => api.setSectionMapping(slug, runId, a.section_id, { excluded: true }),
                "Marked as not protocol content; no agent will read it.",
              )
            }
          >
            Not protocol content
          </button>
        )}
        {a.reviewer_override && (
          <button
            className="btn small"
            disabled={disabled}
            onClick={() =>
              void save(() => api.clearSectionMapping(slug, runId, a.section_id), "Returned to the automatic mapping.")
            }
          >
            Revert to automatic
          </button>
        )}
      </div>
      {locked && <div className="small muted">The run is processing; the mapping can be changed when it ends.</div>}

      {editing && (
        <div className="mapping-editor">
          <div className="small muted">
            {editing === "main"
              ? "Choose the M11 section this protocol section maps to."
              : "Choose a further M11 section; the current mapping stays."}
          </div>
          <input
            autoFocus
            className="mapping-search"
            placeholder="Search M11 sections by number or title"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <ul className="mapping-options small">
            {matches.map((t) => (
              <li key={t.number}>
                <button
                  className={`mapping-option${mapped.has(t.number) ? " current" : ""}`}
                  style={{ paddingLeft: `${6 + (t.level - 1) * 14}px` }}
                  disabled={disabled || (editing === "also" && mapped.has(t.number))}
                  onClick={() => (editing === "also" ? alsoMapTo(t.number, t.title) : mapTo(t.number, t.title))}
                >
                  <span className="mono">{t.number}</span> {t.title}
                  {t.optional && <span className="muted"> (optional)</span>}
                </button>
              </li>
            ))}
            {template.length > 0 && matches.length === 0 && <li className="muted">No M11 section matches.</li>}
          </ul>
          <button className="btn small" onClick={() => setEditing(null)}>
            Cancel
          </button>
        </div>
      )}
      {error && <div className="alert error small">{error}</div>}
      {saved && !error && (
        <div className="alert ok small">{saved}</div>
      )}
    </div>
  );
}

// ----- coverage ------------------------------------------------------------------------------

function CoverageTab(props: {
  slug: string;
  runId: string;
  locked: boolean;
  mapping: SectionMapping;
  doc: ParsedDocument;
  onOpen: (id: string) => void;
  onMappingChanged: (mapping: SectionMapping) => void;
}) {
  const { mapping, doc, onOpen } = props;
  const [hideOptional, setHideOptional] = useState(true);
  const [target, setTarget] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const titles = useMemo(() => new Map(doc.sections.map((s) => [s.id, s])), [doc]);
  const rows = mapping.coverage.filter((c: M11Coverage) => !hideOptional || !c.optional || c.m11_number === target);
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
      {note && <div className="alert ok small">{note}</div>}
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
              <Fragment key={c.m11_number}>
                <tr>
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
                    {c.status !== "found" && target !== c.m11_number && (
                      <button
                        className="btn small"
                        disabled={props.locked}
                        onClick={() => {
                          setTarget(c.m11_number);
                          setNote(null);
                        }}
                      >
                        Map a section…
                      </button>
                    )}
                  </td>
                </tr>
                {target === c.m11_number && (
                  <tr>
                    <td colSpan={3}>
                      <SectionPicker
                        {...props}
                        target={c}
                        onDone={(message) => {
                          setTarget(null);
                          setNote(message);
                        }}
                        onCancel={() => setTarget(null)}
                      />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Choose the protocol section that holds an M11 section's content, from the coverage tab. */
function SectionPicker(props: {
  slug: string;
  runId: string;
  locked: boolean;
  mapping: SectionMapping;
  doc: ParsedDocument;
  target: M11Coverage;
  onMappingChanged: (mapping: SectionMapping) => void;
  onDone: (message: string) => void;
  onCancel: () => void;
}) {
  const { slug, runId, mapping, doc, target } = props;
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const byId = useMemo(() => new Map(mapping.assignments.map((a) => [a.section_id, a])), [mapping]);

  const options = useMemo(() => {
    const q = query.trim().toLowerCase();
    const score = (id: string) => byId.get(id)?.candidates.find((c) => c.m11_number === target.m11_number)?.score ?? 0;
    return doc.sections
      .filter((s) => s.kind !== "toc")
      .filter((s) => !q || (s.number ?? "").toLowerCase().startsWith(q) || s.title.toLowerCase().includes(q))
      .map((s) => ({ section: s, score: score(s.id) }))
      .sort((x, y) => (y.score >= 0.4 ? y.score : 0) - (x.score >= 0.4 ? x.score : 0));
  }, [doc, byId, query, target.m11_number]);

  async function apply(sectionId: string, also: boolean) {
    setBusy(true);
    setError(null);
    try {
      const updated = also
        ? await api.addSectionMapping(slug, runId, sectionId, target.m11_number)
        : await api.setSectionMapping(slug, runId, sectionId, { m11_number: target.m11_number });
      props.onMappingChanged(updated);
      const title = doc.sections.find((s) => s.id === sectionId)?.title ?? sectionId;
      props.onDone(
        await withAffectedAgents(
          slug,
          runId,
          sectionId,
          `"${title}" ${also ? "is also" : "is now"} mapped to ${target.m11_number} ${target.m11_title}.`,
        ),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  const disabled = props.locked || busy;
  return (
    <div className="mapping-editor">
      <div className="row-inline">
        <strong>
          Which protocol section holds <span className="mono">{target.m11_number}</span> {target.m11_title}?
        </strong>
        <button className="btn small" onClick={props.onCancel}>
          Cancel
        </button>
      </div>
      <div className="small muted">
        <strong>Map here</strong> replaces the section's current mapping. <strong>Also map here</strong> keeps it and adds this
        M11 section, for a section that covers both. Likely sections are listed first.
      </div>
      <input
        autoFocus
        className="mapping-search"
        placeholder="Search protocol sections by number or title"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <ul className="mapping-options small">
        {options.map(({ section: s, score }) => {
          const a = byId.get(s.id);
          const mappedHere = a?.m11_number === target.m11_number;
          const alsoHere = (a?.also_m11 ?? []).some((r) => r.m11_number === target.m11_number);
          const now = a?.m11_number
            ? `${a.m11_number} ${a.m11_title ?? ""}`
            : a?.method === "excluded"
              ? "not protocol content"
              : "unmapped";
          return (
            <li key={s.id} className="picker-option">
              <span style={{ paddingLeft: `${(s.level - 1) * 14}px` }}>
                <span className="mono muted">{s.number ?? ""}</span> {s.title}{" "}
                <span className="muted">
                  · p. {s.page_start === s.page_end ? s.page_start : `${s.page_start}–${s.page_end}`} · now: {now}
                  {(a?.also_m11 ?? []).map((r) => `, ${r.m11_number}`).join("")}
                </span>
                {score >= 0.4 && <span className="chip found"> suggested</span>}
              </span>
              <span className="row-inline">
                <button className="btn small" disabled={disabled || mappedHere} onClick={() => void apply(s.id, false)}>
                  Map here
                </button>
                <button
                  className="btn small"
                  disabled={disabled || mappedHere || alsoHere || a?.method === "excluded"}
                  onClick={() => void apply(s.id, true)}
                >
                  Also map here
                </button>
              </span>
            </li>
          );
        })}
      </ul>
      {error && <div className="alert error small">{error}</div>}
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
