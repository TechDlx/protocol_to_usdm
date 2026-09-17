# REBUILD PROMPT (Summary)

> **For the human.** This is the condensed version of [REBUILD_PROMPT.md](REBUILD_PROMPT.md) — use it only for a quick orientation or a status check. To actually build, send the agent the full file: *"Read REBUILD_PROMPT.md completely, then follow it. Start with Phase 1 and stop after each phase for my confirmation."* This summary omits the verbatim prompts, schemas, and file-format contracts (appendices A–G) that the real build needs.

You are rebuilding an existing, working application exactly: a local, single-user web app that converts a clinical trial protocol PDF into a **CDISC USDM v4 JSON** file via a pipeline with mandatory human review in the middle:

```
Stage A  PDF -> parse -> ICH M11 section mapping -> 14 extraction agents -> intermediate JSON
                          |                              |
                  [reviewer corrects mapping]   [HUMAN REVIEW in web UI, confirm]
Stage B  confirmed review -> USDM Excel workbook (.xlsx)   (deterministic, no LLM)
Stage C  workbook -> USDM JSON via usdm4-excel -> usdm4 rule validation (+ CORE if available)
```

Every extracted value must carry provenance (page, section, verbatim quote, confidence) — nothing reaches the output without a traceable origin or an explicit human edit.

## Ground rules

1. Build in phases (14 of them, listed below). Stop after each with a short report and wait for confirmation.
2. Do not re-ask scoping questions — they're answered below. Ask only if the installed libraries contradict this document.
3. Never `git commit` or push. The user commits manually.
4. Secrets live in `.env` only — never in code, the repo, a study folder, or logs.
5. No real protocol text in prompts or examples, ever — invented content only ("examplumab"). Grep the agents folder before any live run.
6. The LLM only reads; code decides the workbook, CDISC codes, and entity/cross-reference names.
7. Reproduce Appendices A–G verbatim from the full file (data models, agent prompts/schemas, ICH M11 template, workbook layouts, rule-finding notes). Build everything else from the spec.
8. Where this document and the installed `usdm4`/`usdm4-excel` code disagree, follow the installed code and report the difference.
9. Test every UI change end to end on scratch data (a second backend on :8010 / Vite on :5180) — never touch the user's real studies.
10. Keep `docs/decisions.md` (numbered decisions) and `README.md` (build-status table) current as you go.
11. Windows is the primary platform: no heredocs with quotes/backslashes in Git Bash (write scripts to a file instead); build `\uXXXX`-bearing strings with `chr(92)`; set `PYTHONIOENCODING=utf-8` when printing protocol text.

## Settled scope — do not re-litigate

- Target **USDM v4** via `usdm4` + `usdm4-excel`, legacy single-workbook dialect — not `cdisc-org/usdm` (v3).
- **No CDISC Library key required.** `usdm4` ships bundled CT/BC caches; `CDISC_API_KEY` stays optional everywhere.
- **Python 3.12 exactly**, managed with uv (`usdm4` → `cdisc-rules-engine` requires it).
- **Frontend:** React + Vite + TypeScript, pnpm, no HTMX, no state library.
- **No auth, no GxP.** Single-user localhost; a plain append-only JSONL audit trail is enough.
- **ICH M11 only** for now; sponsor templates can come later behind an interface.
- **PDF backend:** PyMuPDF, plus Claude vision for SoA pages. No cloud OCR.
- **Concurrency defaults to 5**, configurable; **no per-run cost cap** — runs must complete.
- **Evaluation** is tiered (exact/normalized/fuzzy); accept a ceiling below 100%.
- Reference files go in `goldstandard/cdisc_pilot/` (CDISC Pilot PDF + xlsx) and optionally `goldstandard/sample/`.

## Architecture to build

```
Browser (React :5173) --/api (Vite proxy)--> FastAPI (:8000, app factory)
  FastAPI --> JobRunner (background stages, one active job per run)
  FastAPI --sync--> mapping edits, review ops, workbook writing
  pipeline --atomic file writes--> studies/ (no database)
  pipeline --structured output--> Anthropic API (only external call in normal use)
  pipeline --> usdm4 / usdm4-excel (bundled CT/BC, DDF rules; CORE only if cached)
```

Implement the 12-step pipeline: create study → create run → parse (PyMuPDF) → title-based M11 mapping → Claude M11 mapping (default, reviewer overridable) → reviewer corrections → 14 concurrent extraction agents (semaphore 5) → assembly (linking, BCs, provenance) → human review (confirm) → workbook write (Stage B, gated on zero blocking issues) → USDM generation + validation (Stage C, background) → Results page. Nothing advances automatically past ingestion — every later step needs an explicit action.

## What to build, at a glance

- **Repo:** `backend/` (FastAPI app; `pipeline/` with `ingest`, `segmentation/`, 14 `agents/`, `terminology/`, `identifiers/`, `review/`, `workbook/`, `usdm_gen/`, `evaluation/`), `frontend/` (Studies, Run Inspector, Review, Results pages), `tests/` (314 cases, no network — use `FakeLlm`), `goldstandard/` (CDISC Pilot reference pair + eval CLI). Data lives under `studies/<slug>/runs/<run-id>/...`.
- **Extractors:** heading/layout/table detection from PDF typography, hardened against real-world quirks (bold-vs-size headings, numbered-list false positives, appendix renumbering, running headers).
- **Segmentation:** deterministic title/alias-based M11 mapping first, then Claude mapping as the default (reviewer can revert to title-based); reviewer overrides and start-page corrections must survive re-parsing.
- **14 extraction agents:** study, identifiers, study_design, arms, populations, eligibility, objectives_endpoints, estimands, interventions, indications, amendments, abbreviations, schedule (vision), assessments — each with its own prompt, invented worked example, Pydantic schema, and deterministic post-processing (verbatim in Appendices B/C).
- **Terminology:** resolve CT/BC only via exact matches against usdm4's bundled Library — never accept a hallucinated code.
- **Review:** edit a working copy with row ids, optimistic revision locking, append-only audit, and a blocking-issue gate on Confirm.
- **Workbook writer (Stage B):** write only from a confirmed, clean review; byte-identical output for identical content.
- **USDM gen (Stage C):** `usdm4-excel` import → `usdm4` DDF rules (+ CORE if its cache exists) → findings report explaining expected vs. needs-review findings.
- **Evaluation harness:** score an unreviewed run against the CDISC Pilot reference workbook with tiered cell comparison; reference-vs-itself must score 100%.

## Build order — stop after every phase

1. Scaffold, storage, Studies page.
2. PDF ingestion, M11 segmentation, run inspector.
3. Three agents end to end (study, arms, eligibility), terminology, provenance.
4. Review UI on those three sheets.
5. Remaining non-SoA agents.
6. SoA/schedule agent (vision) and grid.
7. Workbook writer (Stage B).
8. USDM generation, validation, Results page (Stage C).
9. Evaluation harness.
10. Reviewer section-mapping overrides.
11. Section start-page corrections.
12. Multiple M11 sections per protocol section.
13. Claude section mapping as the default.
14. Review editing fix (double-click inline editing on every cell type).

At the end of each phase: all checks pass (`pytest`, `ruff check`, `ruff format --check`, `mypy` strict, `pnpm build`), README build-status updated, decisions logged, UI changes verified in-browser on scratch data — then report and wait.

## Acceptance bar

- All 314 tests pass, with no network access (mock the LLM).
- Live checks (cost money — ask before running): CDISC Pilot parses to 97 pages / 76 sections / SoA pp53–54; full extraction ≈ $0.87–1.10; written workbooks import with **0 errors**; evaluation baseline F1 ≈ 53.3% (±5 pts), reference-vs-itself = 100%.
- Walk the manual UI checklist on scratch data: upload, parse, mapping panels, coverage, extraction (resume), review editing/confirm/reopen/conflicts, workbook download, results page, stale-data handling.

## Reproduce these limitations — don't silently "fix" them

Unreviewed accuracy is modest by design (review is mandatory); no OCR; several fields are deliberately not extracted (administrable products, roles, sites, procedures, narrative content, amendment impacts, indication/intervention codes); single-process/single-user with in-process locks; Claude re-mapping is a synchronous ~50s call; run settings besides PDF backend/DPI are file-edit only; a failed Claude mapping batch loses that batch's answers, not the whole run; review edits made during extraction aren't blocked, just marked stale.

## Appendices — reproduce verbatim, don't paraphrase

Pull these straight from REBUILD_PROMPT.md: **A** data models · **B** agent framework (`common.py`, `base.py`) · **C** the 14 agent modules + registry · **D** Claude section-mapping (`suggest.py`) · **E** ICH M11 template YAML · **F** workbook layouts/formats/cells · **G** USDM rule finding notes.
