import { useState, type FormEvent } from "react";

import { api } from "../api";

interface Props {
  onCreated: () => void | Promise<void>;
  onCancel: () => void;
}

export default function NewStudyForm({ onCreated, onCancel }: Props) {
  const [name, setName] = useState("");
  const [sponsor, setSponsor] = useState("");
  const [protocolIdentifier, setProtocolIdentifier] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createStudy({ name, sponsor, protocol_identifier: protocolIdentifier });
      await onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <form className="panel form" onSubmit={submit}>
      <h2>New study</h2>
      <label>
        Study name <span className="req">*</span>
        <input
          autoFocus
          required
          maxLength={200}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. PALOMA-3"
        />
      </label>
      <div className="row">
        <label>
          Sponsor
          <input maxLength={200} value={sponsor} onChange={(e) => setSponsor(e.target.value)} placeholder="e.g. Pfizer" />
        </label>
        <label>
          Protocol identifier
          <input
            maxLength={100}
            value={protocolIdentifier}
            onChange={(e) => setProtocolIdentifier(e.target.value)}
            placeholder="e.g. A5481023"
          />
        </label>
      </div>
      {error && <div className="alert error">{error}</div>}
      <div className="actions">
        <button type="button" className="btn" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button type="submit" className="btn primary" disabled={busy || !name.trim()}>
          {busy ? "Creating…" : "Create study"}
        </button>
      </div>
    </form>
  );
}
