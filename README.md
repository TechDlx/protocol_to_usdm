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
| 5 | Remaining non-SoA agents | next |
| 6 | SoA / timeline agent + SoA grid | |
| 7 | Workbook writer + reference validation (Stage B) | |
| 8 | USDM generation + validation + Results page (Stage C) | |
| 9 | Evaluation harness | |

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
            └── ...                       # later stages add their artefacts here
```

Uploads are validated (real PDF, not encrypted, has pages, under `MAX_UPLOAD_MB`), written
atomically, and deduplicated by content hash — re-uploading an identical file is a no-op, while
a different file with the same name is kept alongside as `name (2).pdf`.

## Using it (so far)

1. **Studies page:** create a study, upload a protocol PDF, press **Parse** on it.
2. **Run inspector:** watch the two stages run, then review:
   - **Sections → M11:** every detected section with its ICH M11 mapping, confidence and method.
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

Extraction is resumable too: an agent whose inputs are unchanged is not sent to the model again.
When only deterministic post-processing changed (quote verification, terminology, naming), records
are rebuilt from the stored model output at no cost. Typical cost with `claude-sonnet-5`: about
$0.25 per protocol for the three agents implemented so far.

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
    workbook/          workbook sheet layouts (the Stage B writer arrives in Phase 7)
    usdm_gen/          later phases
frontend/              React + Vite + TypeScript
docs/                  workbook spec + design decisions
goldstandard/
  cdisc_pilot/         CDISC Pilot (LZZT) protocol PDF + reference workbook + reference USDM JSON
  sample/              additional protocols for manual testing
tests/                 unit/, integration/, fixtures/
```
