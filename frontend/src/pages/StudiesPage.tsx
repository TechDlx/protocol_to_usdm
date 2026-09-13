import { useCallback, useEffect, useState } from "react";

import { api } from "../api";
import NewStudyForm from "../components/NewStudyForm";
import StudyCard from "../components/StudyCard";
import type { StudySummary } from "../types";

export default function StudiesPage() {
  const [studies, setStudies] = useState<StudySummary[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setStudies(await api.listStudies());
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <section>
      <div className="page-head">
        <div>
          <h1>Studies</h1>
          <p className="muted">Each study is an isolated folder under <code>studies/</code>.</p>
        </div>
        {!showForm && (
          <button className="btn primary" onClick={() => setShowForm(true)}>
            New Study
          </button>
        )}
      </div>

      {showForm && (
        <NewStudyForm
          onCancel={() => setShowForm(false)}
          onCreated={async () => {
            setShowForm(false);
            await refresh();
          }}
        />
      )}

      {loadError && (
        <div className="alert error" role="alert">
          Could not load studies: {loadError}{" "}
          <button className="btn small" onClick={() => void refresh()}>
            Retry
          </button>
        </div>
      )}

      {studies === null && !loadError && <p className="muted">Loading…</p>}

      {studies?.length === 0 && !showForm && (
        <div className="empty">
          <p>No studies yet.</p>
          <button className="btn primary" onClick={() => setShowForm(true)}>
            Create your first study
          </button>
        </div>
      )}

      <div className="card-grid">
        {studies?.map((s) => <StudyCard key={s.slug} study={s} onChanged={refresh} />)}
      </div>
    </section>
  );
}
