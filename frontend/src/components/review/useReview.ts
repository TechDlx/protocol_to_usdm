import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "../../api";
import type { ReviewOperation, ReviewState } from "../../types";

type SetOperation = Extract<ReviewOperation, { op: "set" }>;

const AUTOSAVE_MS = 1500;

export const cellKey = (sheet: string, rowId: string | null, field: string) => `${sheet}|${rowId ?? ""}|${field}`;

/**
 * Review state plus a queue of unsaved value edits.
 *
 * Value edits are queued and saved together after a short pause (or on "Save draft"). Structural
 * operations (add/delete/move row, accept, terminology picks) save immediately along with anything
 * queued, in order. Each save sends the revision it was based on; if the review changed elsewhere
 * the server refuses, the fresh state is loaded, and queued edits are kept for the reviewer to
 * save again rather than silently discarded.
 */
export function useReview(slug: string, runId: string) {
  const [state, setState] = useState<ReviewState | null>(null);
  const [pending, setPending] = useState<Map<string, SetOperation>>(new Map());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<Date | null>(null);

  const stateRef = useRef<ReviewState | null>(null);
  const pendingRef = useRef(pending);
  const savingRef = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  stateRef.current = state;
  pendingRef.current = pending;

  const load = useCallback(async () => {
    try {
      setState(await api.getReview(slug, runId));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [slug, runId]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = useCallback(
    async (extra: ReviewOperation[] = []): Promise<boolean> => {
      const current = stateRef.current;
      if (!current || savingRef.current) return false;
      const queued = [...pendingRef.current.values()];
      const operations: ReviewOperation[] = [...queued, ...extra];
      if (operations.length === 0) return true;

      if (timer.current) clearTimeout(timer.current);
      savingRef.current = true;
      setSaving(true);
      setPending(new Map());
      try {
        const next = await api.applyReviewOperations(slug, runId, current.document.revision, operations);
        stateRef.current = next; // callers chaining another request need the new revision now
        setState(next);
        setSavedAt(new Date());
        setError(null);
        return true;
      } catch (e) {
        // Put queued value edits back (newer edits made while saving win).
        setPending((latest) => {
          const merged = new Map(queued.map((op) => [cellKey(op.sheet, op.row_id, op.field), op]));
          latest.forEach((op, key) => merged.set(key, op));
          return merged;
        });
        if (e instanceof ApiError && e.status === 409) {
          await load();
          setError("The review changed in another window. It has been reloaded; your unsaved edits are kept — save again to apply them.");
        } else {
          setError(e instanceof Error ? e.message : String(e));
        }
        return false;
      } finally {
        savingRef.current = false;
        setSaving(false);
      }
    },
    [slug, runId, load],
  );

  const queueSet = useCallback(
    (op: SetOperation) => {
      setPending((latest) => new Map(latest).set(cellKey(op.sheet, op.row_id, op.field), op));
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void save(), AUTOSAVE_MS);
    },
    [save],
  );

  const run = useCallback((operations: ReviewOperation[]) => save(operations), [save]);

  const confirm = useCallback(async () => {
    if (!state || !(await save())) return;
    const latest = stateRef.current ?? state;
    try {
      setState(await api.confirmReview(slug, runId, latest.document.revision));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [state, save, slug, runId]);

  const restart = useCallback(async () => {
    if (!state) return;
    try {
      setPending(new Map());
      setState(await api.restartReview(slug, runId, state.document.revision));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [state, slug, runId]);

  // Warn before leaving with unsaved edits.
  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {
      if (pendingRef.current.size > 0 || savingRef.current) {
        event.preventDefault();
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, []);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  return { state, pending, saving, error, savedAt, queueSet, run, save, confirm, restart, reload: load, setError };
}
