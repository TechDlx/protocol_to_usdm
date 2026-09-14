import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, ApiError } from "../api";
import type { FindingKind, RuleFinding, RunDetail, UsdmResult } from "../types";

const POLL_MS = 1000;
const IDLE_POLL_MS = 10000;

const KIND_LABEL: Record<FindingKind | "other", string> = {
  review: "fix in review",
  expected: "expected",
  other: "check",
};

interface RuleGroup {
  rule_id: string;
  kind: FindingKind | "other";
  rule_text: string;
  note: string | null;
  findings: RuleFinding[];
}

function formatBytes(n: number): string {
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** Stage C: generate the USDM JSON from the workbook, and show how it validates. */
export default function ResultsPage() {
  const { slug = "", runId = "" } = useParams();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [result, setResult] = useState<UsdmResult | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [pollKey, setPollKey] = useState(0);

  const loadResult = useCallback(async () => {
    try {
      setResult(await api.getUsdm(slug, runId));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setResult(null);
      else setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoaded(true);
    }
  }, [slug, runId]);

  // Poll the run every second while it is generating, slowly otherwise, and reload the report
  // whenever the usdm stage finishes anew.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let finished: string | null | undefined;
    const tick = async () => {
      if (timer) clearTimeout(timer);
      try {
        const state = await api.getRun(slug, runId);
        if (cancelled) return;
        setRun(state);
        const stage = state.stages.usdm;
        const active = state.status === "generating" || state.status === "running";
        if (!active && (finished === undefined || stage?.finished_at !== finished)) {
          finished = stage?.finished_at ?? null;
          await loadResult();
        }
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
  }, [slug, runId, loadResult, pollKey]);

  async function generate(force: boolean) {
    setStarting(true);
    setError(null);
    try {
      setRun(await api.generateUsdm(slug, runId, force));
      setPollKey((k) => k + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(false);
    }
  }

  const generating = run?.status === "generating";
  const busy = generating || run?.status === "running" || starting;
  const stage = run?.stages.usdm;
  const report = result?.report ?? null;

  return (
    <section className="results">
      <div className="page-head">
        <div>
          <div className="muted small">
            <Link to="/">Studies</Link> / {slug} /{" "}
            <Link className="mono" to={`/studies/${slug}/runs/${runId}`}>
              {runId}
            </Link>{" "}
            / <Link to={`/studies/${slug}/runs/${runId}/review`}>Review</Link> / Results
          </div>
          <h1>USDM results</h1>
          <p className="muted small">
            The reviewed workbook imported with usdm4-excel into USDM v4 JSON, then checked with the usdm4 rule library.
          </p>
        </div>
        <div className="actions">
          {run && <span className={`badge ${run.status}`}>{run.status.replace("_", " ")}</span>}
          <button className="btn small primary" disabled={busy} onClick={() => void generate(report !== null)}>
            {generating ? "Generating…" : report ? "Regenerate" : "Generate USDM"}
          </button>
        </div>
      </div>

      {error && <div className="alert error">{error}</div>}
      {generating && (
        <div className="alert small">Importing the workbook and validating the JSON; this takes about half a minute.</div>
      )}
      {stage?.status === "failed" && stage.error && <div className="alert error small">{stage.error}</div>}
      {loaded && !report && !generating && (
        <div className="panel muted">
          No USDM generated yet. Confirm the review, then use Generate USDM: the workbook is brought up to date with the
          confirmed review first.
        </div>
      )}

      {report && result && (
        <>
          {result.stale.length > 0 && (
            <div className="alert warn small">
              This USDM may be out of date: {result.stale.join("; ")}. Confirm the review and regenerate.
            </div>
          )}
          <Summary report={report} />
          <div className="panel row-inline">
            <strong>Downloads</strong>
            {report.file && (
              <a className="btn small" href={api.usdmDownloadUrl(slug, runId)} download>
                USDM JSON
              </a>
            )}
            <a className="btn small" href={api.usdmReportDownloadUrl(slug, runId)} download>
              Validation report
            </a>
            <a className="btn small" href={api.workbookDownloadUrl(slug, runId)} download>
              Workbook .xlsx
            </a>
            <span className="muted small">
              Generated {new Date(report.generated_at).toLocaleString()} from review revision {report.review_revision}
              {report.reused ? " (already up to date)" : ""} · workbook sha256 {report.workbook_sha256.slice(0, 12)}… ·{" "}
              {report.system}
            </span>
          </div>
          <ImportIssues report={report} />
          <Findings findings={report.findings} total={report.rules.findings} />
          <Core report={report} />
          <Entities entities={report.entities} />
        </>
      )}
    </section>
  );
}

function Stat({ label, value, tone }: { label: string; value: string | number; tone?: "warn" | "ok" }) {
  return (
    <div className={`stat${tone ? ` ${tone}` : ""}`}>
      <div className="small muted">{label}</div>
      <div className="stat-value">{value}</div>
    </div>
  );
}

function Summary({ report }: { report: UsdmResult["report"] }) {
  const { rules } = report;
  const review = report.findings.filter((f) => f.kind === "review").length;
  const other = report.findings.filter((f) => f.kind === null).length;
  const seconds = Object.values(report.seconds).reduce((a, b) => a + b, 0);
  return (
    <div className="stats">
      <Stat
        label="USDM JSON"
        value={report.file ? `v${report.usdm_version} · ${formatBytes(report.size_bytes)}` : "not produced"}
        tone={report.file ? "ok" : "warn"}
      />
      <Stat label="Import errors" value={report.import_errors.length} tone={report.import_errors.length ? "warn" : "ok"} />
      <Stat
        label={`Rules (${rules.engine})`}
        value={`${rules.passed} passed · ${rules.failed} failed${rules.exceptions ? ` · ${rules.exceptions} errored` : ""}`}
        tone={rules.failed ? "warn" : "ok"}
      />
      <Stat
        label="Findings"
        value={`${rules.findings}: ${review} fix in review · ${rules.expected_findings} expected · ${other} to check`}
        tone={review + other ? "warn" : undefined}
      />
      <Stat label="CDISC CORE" value={report.core.ran ? `${report.core.findings} findings` : "not run"} />
      <Stat label="Time" value={`${seconds.toFixed(0)} s`} />
    </div>
  );
}

function ImportIssues({ report }: { report: UsdmResult["report"] }) {
  const { import_errors: errors, import_warnings: warnings } = report;
  return (
    <div className="panel">
      <h3>Workbook import</h3>
      {errors.length === 0 ? (
        <p className="small">usdm4-excel read the workbook without errors.</p>
      ) : (
        <IssueTable issues={errors} tone="error" />
      )}
      {warnings.length > 0 && (
        <details>
          <summary className="small">
            {warnings.length} warning(s), mostly optional sheets the pipeline does not write
          </summary>
          <IssueTable issues={warnings} tone="warning" />
        </details>
      )}
    </div>
  );
}

function IssueTable({ issues, tone }: { issues: UsdmResult["report"]["import_errors"]; tone: "error" | "warning" }) {
  return (
    <div className="scroll-x">
      <table className="grid static small">
        <tbody>
          {issues.map((i, n) => (
            <tr key={n} className={tone}>
              <td className="mono nowrap">{i.location}</td>
              <td>{i.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Findings({ findings, total }: { findings: RuleFinding[]; total: number }) {
  const [showExpected, setShowExpected] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const groups = useMemo(() => {
    const byRule = new Map<string, RuleGroup>();
    for (const f of findings) {
      const group = byRule.get(f.rule_id) ?? {
        rule_id: f.rule_id,
        kind: f.kind ?? "other",
        rule_text: f.rule_text,
        note: f.note,
        findings: [],
      };
      group.findings.push(f);
      byRule.set(f.rule_id, group);
    }
    const order = { other: 0, review: 1, expected: 2 };
    return [...byRule.values()].sort((a, b) => order[a.kind] - order[b.kind] || a.rule_id.localeCompare(b.rule_id));
  }, [findings]);
  const shown = groups.filter((g) => showExpected || g.kind !== "expected");
  const hidden = groups.length - shown.length;

  return (
    <div className="panel">
      <div className="row-inline">
        <h3>Rule findings by rule</h3>
        <label className="small filter">
          <input type="checkbox" checked={showExpected} onChange={(e) => setShowExpected(e.target.checked)} /> Show
          expected findings{hidden ? ` (${hidden} rule${hidden === 1 ? "" : "s"} hidden)` : ""}
        </label>
      </div>
      <p className="small muted">
        <strong>Fix in review</strong>: change the reviewed values, confirm and regenerate. <strong>Expected</strong>: the
        pipeline does not produce this part of USDM yet, or the importer causes it. <strong>Check</strong>: not a finding
        the pipeline is known to produce; look at the data.
        {findings.length < total ? ` Showing the first ${findings.length} of ${total} findings.` : ""}
      </p>
      {groups.length === 0 ? (
        <p className="small">No rule findings.</p>
      ) : (
        <div className="scroll-x">
          <table className="grid findings">
            <thead>
              <tr>
                <th>Rule</th>
                <th></th>
                <th>Findings</th>
                <th>What it checks / what to do</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((g) => (
                <FindingRow key={g.rule_id} group={g} open={open === g.rule_id} onToggle={() => setOpen(open === g.rule_id ? null : g.rule_id)} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function FindingRow({ group, open, onToggle }: { group: RuleGroup; open: boolean; onToggle: () => void }) {
  return (
    <>
      <tr className="clickable" onClick={onToggle}>
        <td className="mono nowrap">
          {open ? "▾" : "▸"} {group.rule_id}
        </td>
        <td>
          <span className={`chip finding-${group.kind}`}>{KIND_LABEL[group.kind]}</span>
        </td>
        <td>{group.findings.length}</td>
        <td className="small">
          <div>{group.rule_text}</div>
          {group.note && <div className={group.kind === "review" ? "strong-note" : "muted"}>{group.note}</div>}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={4}>
            <table className="grid static small">
              <tbody>
                {group.findings.map((f, n) => (
                  <tr key={n}>
                    <td className="nowrap">
                      {f.klass}
                      {f.attribute ? `.${f.attribute}` : ""}
                    </td>
                    <td>{f.message}</td>
                    <td className="mono muted">{f.path}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </td>
        </tr>
      )}
    </>
  );
}

function Core({ report }: { report: UsdmResult["report"] }) {
  const { core } = report;
  return (
    <div className="panel">
      <h3>CDISC CORE</h3>
      {!core.ran ? (
        <p className="small muted">Not run: {core.reason}</p>
      ) : (
        <>
          <p className="small">
            {core.rules_executed} rules executed · {core.findings} findings · {core.execution_errors} rule execution errors
          </p>
          {core.results.length > 0 && (
            <table className="grid static small">
              <tbody>
                {core.results.map((r) => (
                  <tr key={r.rule_id}>
                    <td className="mono nowrap">{r.rule_id}</td>
                    <td>{r.count}</td>
                    <td>{r.description || r.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  );
}

function Entities({ entities }: { entities: Record<string, number> }) {
  const rows = Object.entries(entities);
  if (rows.length === 0) return null;
  const total = rows.reduce((a, [, n]) => a + n, 0);
  return (
    <div className="panel">
      <details>
        <summary>
          <strong>Entities in the JSON</strong> <span className="muted small">{rows.length} classes, {total} instances</span>
        </summary>
        <div className="entity-grid small">
          {rows.map(([klass, n]) => (
            <div key={klass}>
              <span className="mono">{klass}</span> <span className="muted">{n}</span>
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}
