# Protocol to USDM

Local web application that converts a clinical trial protocol PDF into a CDISC **USDM v4** JSON
file, with a mandatory human review step:

```
Stage A  PDF -> structured extraction -> intermediate JSON model
                   |
            [HUMAN REVIEW + CORRECTION IN WEB UI]
                   |
Stage B  reviewed model -> USDM Excel workbook (.xlsx)          (deterministic, no LLM)
Stage C  workbook -> USDM JSON via usdm4-excel -> validation
```

Design decisions and their reasons are recorded in [docs/decisions.md](docs/decisions.md).
The workbook format reference is [docs/usdm_workbook_spec.md](docs/usdm_workbook_spec.md).

## Build status

| Phase | Scope | Status |
|---|---|---|
| 1 | Scaffold, storage layer, study/run folders, Studies page | done |
| 2 | PDF ingestion + ICH M11 segmentation, run inspector page | done |
| 3 | Extraction agents (study, arms, eligibility) + terminology + provenance | done |
| 4 | Review UI | done |
| 5 | Remaining non-SoA agents | done |
| 6 | SoA / timeline agent + SoA grid | done |
| 7 | Workbook writer + reference validation (Stage B) | done |
| 8 | USDM generation + validation + Results page (Stage C) | done |
| 9 | Evaluation harness | done |

---

## Setting up on a new machine

### 1. Prerequisites

| Tool | Version | Why |
|---|---|---|
| **uv** | 0.5+ | Installs the pinned Python and all Python dependencies |
| **Node.js** | 20+ (developed on 26) | Frontend |
| **pnpm** | 9+ (developed on 12) | Frontend package manager |
| git | any | |

You do **not** need to install Python yourself. uv downloads the exact interpreter the project
pins (`.python-version` -> 3.12) into its own managed location, separate from any system Python.

> **Why Python 3.12 exactly?** `usdm4` depends on `cdisc-rules-engine`, and every release of
> that package declares `requires-python >=3.12,<3.13`. On 3.13 or 3.14 dependency resolution
> fails. The pin is enforced in `pyproject.toml`; don't loosen it.

Install uv (pick one):

```powershell
# Windows, official installer
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# ...or with any existing Python
python -m pip install --user uv      # then invoke as: python -m uv ...
```

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Install pnpm (after Node):

```bash
npm install -g pnpm
```

### 2. Clone and install

```bash
git clone https://github.com/TechDlx/protocol_to_usdm.git
cd protocol_to_usdm

uv sync                          # downloads Python 3.12 if needed, creates .venv, installs from uv.lock
cd frontend && pnpm install && cd ..
```

Both lockfiles (`uv.lock`, `frontend/pnpm-lock.yaml`) are committed, so every machine gets
identical dependency versions.

### 3. Secrets

```bash
cp .env.example .env             # Windows: copy .env.example .env
```

Edit `.env` in the repo root:

| Variable | Required | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes, for extraction | Extraction agents call the Claude API (default model `claude-sonnet-5`) |
| `CDISC_API_KEY` | **no** | The pipeline runs without CDISC Library membership — see [docs/decisions.md](docs/decisions.md#d3) |

`.env` and everything under `studies/` are gitignored. Secrets are redacted from all log output.

### 4. Run

Two terminals, from the repo root:

```bash
# Terminal 1 — backend on http://127.0.0.1:8000
uv run uvicorn backend.main:create_app --factory --reload --port 8000

# Terminal 2 — frontend on http://localhost:5173  (proxies /api to the backend)
cd frontend && pnpm dev
```

Open <http://localhost:5173>. (`API_TARGET=http://127.0.0.1:8010 pnpm dev --port 5180` runs a second
frontend against another backend, e.g. one started with `STUDIES_ROOT` pointing at a scratch copy.)

### 5. Checks

```bash
uv run pytest                    # unit + integration tests
uv run ruff check backend tests
uv run ruff format --check backend tests
uv run mypy                      # strict
cd frontend && pnpm build        # strict TypeScript + production build
```

---

## Where data lives

Every study is a fully isolated folder. Nothing in one study folder references another, and
paths recorded inside a study are relative to that study, so a folder can be moved or archived
whole.

```
studies/                                  # gitignored; override with STUDIES_ROOT in .env
└── <study-slug>/
    ├── study.json                        # name, sponsor, protocol id, uploaded sources (+ sha256, pages)
    ├── source/                           # uploaded protocol PDFs, original filenames kept
    └── runs/
        └── <YYYYMMDDTHHMMSSZ-xxxxxx>/    # one folder per run
            ├── run_config.json           # settings for this run (source PDF, PDF backend, DPI, ...)
            ├── run_state.json            # overall status + per-stage status, timings, errors
            ├── page_images/page-NNNN.png # one rendered image per PDF page
            ├── parsed_document.json      # sections, text, tables, outline, page info
            ├── section_mapping.json      # each section mapped to ICH M11, with confidence
            ├── extraction/<sheet>.json   # per agent: run record, raw model output, records
            ├── extraction.json           # the intermediate model (every value with provenance)
            ├── provenance.json           # flat audit view: source, confidence, review flags
            ├── reference_validation.json # duplicate / missing names, dangling references
            ├── run.log                   # one JSON line per model call: tokens, latency, cost
            ├── reviewed.json             # the review working copy (revision, status, edited sheets)
            ├── review_audit.jsonl        # append-only: every review change with cell, old, new
            ├── review_archive/           # reviews discarded by "Restart from latest extraction"
            ├── workbook/<study>.xlsx     # the USDM workbook written from the confirmed review
            ├── workbook_report.json      # review revision, sha256, rows per sheet, warnings
            ├── usdm/<study>.json         # USDM v4 JSON imported from the workbook
            ├── usdm_report.json          # import issues, rule findings, CORE status, entity counts
            └── ...                       # later stages add their artefacts here
```

Uploads are validated (real PDF, not encrypted, has pages, under `MAX_UPLOAD_MB`), written
atomically, and deduplicated by content hash — re-uploading an identical file is a no-op, while
a different file with the same name is kept alongside as `name (2).pdf`.

## Using it (so far)

1. **Studies page:** create a study, upload a protocol PDF, press **Parse** on it.
2. **Run inspector:** watch the two stages run, then review:
   - **Sections → M11:** every detected section with its ICH M11 mapping, confidence and method.
     To correct a mapping, select the section and use a suggested candidate, **Map to M11
     section…** (search the template by number or title) or **Not protocol content**; **Revert to
     automatic** undoes it. Manual mappings are kept when segmentation re-runs and recorded in
     `section_mapping_audit.jsonl`. After a change the panel names the extraction agents whose input
     changed, and the Extraction tab lists them until **Run extraction (resume)** updates them.
     When a section starts on the wrong page (e.g. a synopsis detected on page 2 while pages 2–3
     still belong to the title page), set **Starts on page** in the section's **Pages** box:
     earlier pages move to the previous section, with their tables. **Revert to parsed start**
     undoes it; corrections are kept when parsing re-runs.
   - **M11 coverage:** which M11 sections have protocol content. On a *missing* or *low confidence*
     row, **Map a section…** lists the protocol sections (likely ones first): **Map here** replaces
     a section's mapping, **Also map here** keeps it and adds this M11 section. A section that
     covers two M11 sections (e.g. "Synopsis and Schedule of Evaluations") can be mapped to both,
     also from the section panel with **Also map to…**; both sets of agents then read it.
   - **Mapping with Claude (default):** after the title-based mapping, Claude reads every section
     (title, subsections and the start of its text) and its mapping is used, with its confidence
     and reason shown. Sections where Claude has low confidence or disagrees with a confident title
     match are flagged for review; the **Where Claude changed the title-based mapping** list and each
     section's panel offer **Use title-based mapping**, and every manual choice still overrides
     Claude. Answers are cached per section, so re-parsing only asks about changed sections. About
     $0.34 and 50 s for PALOMA-3's 146 sections with `claude-sonnet-5`. Without an API key, or if the
     call fails, the title-based mapping is used and the stage note says so. Set `"mapping_assist"`
     in the run's `run_config.json` to `"flagged"` (only sections the title match flags or cannot
     map) or `"off"`.
     Rows with an amber bar are flagged for review. Click a row for its text, the next-best M11
     candidates and the source page image.
   - **M11 coverage:** which M11 sections were found, found with low confidence, or not found.
   - **Tables:** parsed grids beside the page image; SoA candidates and tables needing vision are
     marked.

   - **Extraction:** run the extraction agents (this calls the Claude API and costs money), watch
     each agent's status, tokens and cost, and inspect the extracted sheets. Cells are coloured by
     state (verified, generated, needs review, terminology not exact, empty); click one for its
     source page, section, verbatim quote, confidence and terminology candidates. **Open review**
     goes to the review page.
3. **Review page** (`/studies/<slug>/runs/<run>/review`): the extracted data laid out as the
   workbook sheets it will become (column letters, header row, row numbers; governance dates are
   the table below the study block, as in the legacy workbook).
   - Double-click a cell to edit it (Enter saves, Esc cancels; Ctrl+Enter in long text), or use the
     panel on the right. Edits save automatically after a pause, or with **Save draft** / Ctrl+S.
   - Selecting a cell shows its workbook reference, provenance, the source page with the quoted
     phrase highlighted, and for controlled-terminology cells a searchable codelist — pick a term
     or validate a typed phrase. Only exact matches carry a C-code.
   - Row controls add, delete and reorder rows. **Accept as extracted** clears a low-confidence or
     unverified warning without changing the value.
   - The validation panel lists blocking issues (required value missing, terminology not exact,
     duplicate/missing names) — click one to jump to the cell — and warnings. **Confirm review** is
     available once nothing blocks; the run then shows as *Reviewed*. Editing a confirmed review
     reopens it as a draft.
   - Every change is written to `review_audit.jsonl` (**Audit trail** shows the latest). If
     extraction is re-run after a review started, a banner offers to restart from the new
     extraction; the old review is archived.
   - Two browser tabs cannot overwrite each other: a save based on an old revision is refused, the
     page reloads the latest review and keeps your unsaved edits for you to save again.

Parsing is resumable: **Re-run (resume)** reuses `parsed_document.json` when the source PDF, backend,
backend version and DPI are unchanged, and only re-runs segmentation. **Force re-parse** starts over.

**Sheets extracted (Phase 5):** study and governance dates, organizations and identifiers, study
design, arms, populations, eligibility criteria, objectives and endpoints, estimands, interventions,
indications, amendments and abbreviations: one agent each (identifiers and organizations share one).
**Schedule of activities (Phase 6):** the schedule agent reads the SoA page images and tables;
epochs, encounters, timelines, timepoints, timings, activities and marks are built from what it
reads, and an assessments agent supplies CDISC Biomedical Concepts (exact catalogue matches only).
On the review page the **Schedule grid** tab shows each timeline as a matrix; click a cell to add or
remove an activity at a timepoint, or a name to see its source page. Elements and arm-epoch cells
are generated as a documented default (every arm through every epoch) for review. Some cells need
information protocols rarely print, such as a sponsor's DUNS number, and stay empty for the
reviewer. Estimands, amendments and abbreviations are left empty when the protocol has none. Phrases
that refer to another sheet (an estimand's endpoint, an amendment's date) are linked to names
automatically when the match is clear, and otherwise listed on the Extraction tab for review. On the
review page, list cells (such as trial sub types) add or remove terms, amendment reasons accept
`Other=<reason>`, and reference cells offer the names that exist on the other sheets.

**USDM workbook (Phase 7):** once a review is confirmed with no blocking issues, the review page's
**USDM workbook** panel writes the workbook (`workbook/<study>.xlsx` in the run folder, with
`workbook_report.json`) and offers it for download. It is written by code from the reviewed values,
laid out as usdm4-excel's importer expects; the same review always produces the identical file.
Both sample protocols' workbooks import into usdm4-excel with no errors.

**USDM JSON and validation (Phase 8):** from the workbook panel, open **USDM JSON & validation**
and use **Generate USDM**. The workbook is brought up to date with the confirmed review, imported by
usdm4-excel into USDM v4 JSON (`usdm/<study>.json`, about half a minute), and checked with the
usdm4 rule library. The Results page shows import errors, rule findings grouped by rule (each
explained as *fix in review*, *expected* for parts the pipeline does not produce yet, or *check*),
the CDISC CORE status and entity counts, with downloads for the JSON, the validation report and the
workbook. CDISC CORE runs only when its cache has been built with CDISC Library access; otherwise
the page says it was not run.

**Evaluation (Phase 9):** `goldstandard/eval.py` runs Stage A on the CDISC Pilot protocol with
review disabled, writes the workbook straight from the extraction, and compares it cell by cell
with the CDISC Pilot reference workbook:

```bash
python -m uv run python goldstandard/eval.py                     # Stage A (resumable) + score
python -m uv run python goldstandard/eval.py --run-dir studies/<study>/runs/<run-id>
python -m uv run python goldstandard/eval.py --workbook some.xlsx # score any workbook as it is
python -m uv run python goldstandard/eval.py --force             # re-run every agent (paid)
```

Rows are aligned by content first and entity names compared by what they point at, then each cell
is scored by tier (C-codes, references, normalised values, fuzzy prose). The report gives
field-level accuracy, precision and recall overall, per tier and per sheet, sample differences,
and the reference content outside the pipeline's scope. Each run writes
`goldstandard/results/<UTC time>_<commit>.json` and `.md` (tracked, so accuracy can be followed
across commits; the Markdown shows the change since the previous result). The Stage A workspace is
`goldstandard/runs/` (not committed). The first result, on the unreviewed Pilot extraction:
accuracy 39.5%, precision 64.7%, recall 45.3% (docs/decisions.md D30–D32 explain the method and
why the reference is not a perfect answer key).

Extraction is resumable too: an agent whose inputs are unchanged is not sent to the model again.
When only deterministic post-processing changed (quote verification, terminology, naming), records
are rebuilt from the stored model output at no cost. Typical cost with `claude-sonnet-5`: about
$0.90–1.10 per protocol for all fourteen agents (CDISC Pilot about $0.87, PALOMA-3 about $1.10);
the schedule agent, which reads page images, is about a quarter of that.

The same stage runs from the command line, which is handy for trying a protocol without the UI:

```bash
uv run python -m backend.pipeline.ingest "path/to/protocol.pdf" out/some-folder
```

## Repository layout

```
backend/
  main.py              FastAPI app factory
  config.py            settings from .env
  logging_setup.py     JSON-lines logging with secret redaction
  api/                 HTTP routes
  models/              Pydantic models
  storage/             study/run filesystem layer (the only code that touches studies/)
  pipeline/
    extractors/        PDF backends behind extractors/base.py (PyMuPDF today)
    segmentation/      ICH M11 template (m11_template.yaml) + section mapper
    ingest.py          Stage A1+A2 runner (also a CLI)
    agents/            one extraction agent per sheet, shared context + quote verification
    terminology/       CDISC CT resolution through usdm4's own CT library
    identifiers/       deterministic names + reference graph validation
    extract.py         Stage A3-A6 runner: concurrent agents, resumable, provenance outputs
    llm.py             Claude structured-output client with usage and cost accounting
    jobs.py            background execution; status written to run_state.json
    review/            review working copy, operations, audit trail, validation, source highlight
    workbook/          sheet layouts, cell formats, Stage B writer and gate
    usdm_gen/          Stage C: workbook import to USDM JSON, rule validation, CORE when available
    evaluation/        reference comparison: workbook reader, alignment, tiered scoring, report
frontend/              React + Vite + TypeScript
docs/                  workbook spec + design decisions
goldstandard/
  cdisc_pilot/         CDISC Pilot (LZZT) protocol PDF + reference workbook + reference USDM JSON
  eval.py              evaluation harness entry point
  results/             timestamped evaluation results (tracked)
  runs/                evaluation Stage A workspace (gitignored)
  sample/              additional protocols for manual testing
tests/                 unit/, integration/, fixtures/
```
