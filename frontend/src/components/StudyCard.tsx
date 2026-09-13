import { useRef, useState, type DragEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api";
import type { RunStatus, StudySummary } from "../types";

interface Props {
  study: StudySummary;
  onChanged: () => void | Promise<void>;
}

const STATUS_LABEL: Record<RunStatus, string> = {
  created: "Created",
  running: "Running",
  parsed: "Parsed",
  awaiting_review: "Awaiting review",
  reviewed: "Reviewed",
  generating: "Generating",
  completed: "Completed",
  failed: "Failed",
};

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export default function StudyCard({ study, onChanged }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [message, setMessage] = useState<{ kind: "error" | "ok"; text: string } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [starting, setStarting] = useState<string | null>(null);
  const navigate = useNavigate();

  async function parse(filename: string) {
    setStarting(filename);
    setMessage(null);
    try {
      const run = await api.createRun(study.slug, filename);
      navigate(`/studies/${study.slug}/runs/${run.run_id}`);
    } catch (e) {
      setMessage({ kind: "error", text: e instanceof Error ? e.message : String(e) });
      setStarting(null);
    }
  }

  async function upload(file: File) {
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setMessage({ kind: "error", text: `${file.name} is not a PDF.` });
      return;
    }
    setMessage(null);
    setProgress(0);
    try {
      const doc = await api.uploadSource(study.slug, file, setProgress);
      // The backend deduplicates by content hash; `study` still holds the pre-upload list.
      const duplicate = study.sources.some((s) => s.sha256 === doc.sha256);
      await onChanged();
      setMessage({
        kind: "ok",
        text: duplicate
          ? `Identical file already stored as ${doc.filename}.`
          : `Saved to studies/${study.slug}/${doc.relative_path}`,
      });
    } catch (e) {
      setMessage({ kind: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setProgress(null);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) void upload(file);
  }

  const uploading = progress !== null;

  return (
    <article
      className={`panel card${dragging ? " dragging" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      <header className="card-head">
        <div>
          <h2>{study.name}</h2>
          <div className="meta">
            {study.sponsor && <span>{study.sponsor}</span>}
            {study.protocol_identifier && <span className="mono">{study.protocol_identifier}</span>}
            <span className="mono muted">studies/{study.slug}/</span>
          </div>
        </div>
      </header>

      <div className="card-section">
        <h3>Protocol PDFs</h3>
        {study.sources.length === 0 ? (
          <p className="muted small">None uploaded. Drop a PDF on this card or use the button.</p>
        ) : (
          <ul className="list">
            {study.sources.map((s) => (
              <li key={s.sha256}>
                <a href={api.sourceUrl(study.slug, s.filename)} target="_blank" rel="noreferrer">
                  {s.filename}
                </a>
                <span className="muted small">
                  {s.page_count} pages · {formatBytes(s.size_bytes)} · {formatDate(s.uploaded_at)}
                </span>
                <button
                  className="btn small"
                  disabled={starting !== null}
                  onClick={() => void parse(s.filename)}
                  title="Create a run: parse the PDF and map its sections to ICH M11"
                >
                  {starting === s.filename ? "Starting…" : "Parse"}
                </button>
              </li>
            ))}
          </ul>
        )}
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void upload(file);
          }}
        />
        <button className="btn small" disabled={uploading} onClick={() => inputRef.current?.click()}>
          {uploading ? `Uploading… ${Math.round((progress ?? 0) * 100)}%` : "Upload protocol PDF"}
        </button>
        {uploading && (
          <div className="progress" aria-hidden>
            <div style={{ width: `${Math.round((progress ?? 0) * 100)}%` }} />
          </div>
        )}
        {message && <div className={`alert ${message.kind} small`}>{message.text}</div>}
      </div>

      <div className="card-section">
        <h3>Runs</h3>
        {study.runs.length === 0 ? (
          <p className="muted small">No runs yet. Use Parse on a protocol PDF to start one.</p>
        ) : (
          <ul className="list">
            {study.runs.map((r) => (
              <li key={r.run_id} className="row-inline">
                <Link className="mono" to={`/studies/${study.slug}/runs/${r.run_id}`}>
                  {r.run_id}
                </Link>
                <span className={`badge ${r.status}`}>{STATUS_LABEL[r.status]}</span>
                {r.source_filename && <span className="muted small">{r.source_filename}</span>}
                {(r.status === "completed" || r.status === "generating") && (
                  <Link className="small" to={`/studies/${study.slug}/runs/${r.run_id}/results`}>
                    Results
                  </Link>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <footer className="muted small">Created {formatDate(study.created_at)}</footer>
    </article>
  );
}
