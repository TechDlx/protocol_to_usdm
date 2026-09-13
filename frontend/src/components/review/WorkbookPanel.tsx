import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiError } from "../../api";
import type { WorkbookReport } from "../../types";

interface Props {
  slug: string;
  runId: string;
  confirmed: boolean;
  revision: number;
}

/** Stage B: generate the USDM workbook from the confirmed review, and download it. */
export default function WorkbookPanel({ slug, runId, confirmed, revision }: Props) {
  const [report, setReport] = useState<WorkbookReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getWorkbookReport(slug, runId)
      .then((r) => !cancelled && setReport(r))
      .catch((e) => {
        if (!cancelled && !(e instanceof ApiError && e.status === 404)) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [slug, runId]);

  const generate = async (force: boolean) => {
    setBusy(true);
    setError(null);
    try {
      setReport(await api.generateWorkbook(slug, runId, force));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const current = report !== null && report.review_revision === revision;
  const rows = report ? Object.values(report.sheets).reduce((a, b) => a + b, 0) : 0;

  return (
    <div className="panel wb-panel">
      <div className="row-inline">
        <h3 className="wb-title">USDM workbook</h3>
        {confirmed ? (
          <button className="btn small primary" disabled={busy} onClick={() => void generate(current)}>
            {busy ? "Writing…" : current ? "Regenerate" : report ? "Generate from this revision" : "Generate workbook"}
          </button>
        ) : (
          <span className="muted small">Confirm the review to generate the workbook.</span>
        )}
        {report && (
          <a className="btn small" href={api.workbookDownloadUrl(slug, runId)} download>
            Download .xlsx
          </a>
        )}
        {confirmed && (
          <Link className="btn small" to={`/studies/${slug}/runs/${runId}/results`}>
            USDM JSON &amp; validation →
          </Link>
        )}
      </div>
      {error && <div className="alert error small">{error}</div>}
      {report && (
        <div className="small">
          <div className={current ? "muted" : "error-text"}>
            {current
              ? `Written ${new Date(report.generated_at).toLocaleString()} from review revision ${report.review_revision}${report.reused ? " (already up to date)" : ""}.`
              : `Written from review revision ${report.review_revision}; the review is now at revision ${revision}. Confirm and generate again.`}
          </div>
          <div className="muted">
            {Object.keys(report.sheets).length} sheets, {rows} rows · CT {report.ct_version} · sha256 {report.sha256.slice(0, 12)}…
          </div>
          {report.stale_extraction && (
            <div className="alert warn small">Extraction was re-run after this review started; the workbook reflects the reviewed data.</div>
          )}
          {report.warnings.length > 0 && (
            <ul className="rv-issue-list">
              {report.warnings.map((w) => (
                <li key={w} className="warning">
                  {w}
                </li>
              ))}
            </ul>
          )}
          <details>
            <summary>Sheets</summary>
            <table className="grid static small">
              <tbody>
                {Object.entries(report.sheets).map(([sheet, count]) => (
                  <tr key={sheet}>
                    <td className="mono">{sheet}</td>
                    <td>{count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </div>
      )}
    </div>
  );
}
