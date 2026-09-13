# Design decisions

Decisions that shape the build, with the evidence behind them. Newest at the bottom.

---

## D1 — Target USDM v4 via `usdm4-excel`, legacy single-workbook format

**Decision.** Stage C uses `usdm4-excel` (with `usdm4`), not `cdisc-org/usdm`. Stage B writes the
*legacy single-workbook* format.

**Why.**
- USDM v4 output is a requirement. `cdisc-org/usdm` emits v3.
- `usdm4-excel` has **no public source repository**; its README ships only inside the wheel. It is
  saved verbatim as [usdm_workbook_spec.md](usdm_workbook_spec.md), extracted from the 0.10.0 wheel
  metadata. Column layouts are to be pinned from the package source, which is the machine truth.
- The spec states unmodified legacy workbooks import directly. The CDISC Pilot reference workbook
  is legacy format, so it stays usable as the gold standard.
- The multi-workbook format exists for studies with several study designs. Not needed yet.

**v4 differs from the v3 README** in ways that affect Pydantic models and writers: separate
`dates` sheet; new `documents`, `documentVersions`, `extensions`, `studyDesignSpecimen` sheets;
eligibility split into `eligibilityCriteria` + `eligibilityCriteriaItems` (preferred) with the
legacy single sheet still accepted; one study-wide namespace for cross-reference names.

---

## D2 — Python pinned to 3.12, managed by uv

**Decision.** `requires-python = ">=3.12,<3.13"`, `.python-version = 3.12`, interpreter and
dependencies installed by uv from `uv.lock`.

**Why.** `usdm4` -> `cdisc-rules-engine`, and **every** `cdisc-rules-engine` release (0.14.0 through
0.17.1, checked 2026-09-12) declares `requires-python >=3.12,<3.13`. On Python 3.14 pip cannot
select any release, backtracks, and ends up trying to compile an old `pydantic-core` from source
with Rust. uv installs a project-scoped 3.12 without touching the system Python, and makes the
setup reproducible on another machine.

---

## D3 — No CDISC Library API key required  <a id="d3"></a>

**Decision.** The pipeline runs end to end without CDISC Library membership. `CDISC_API_KEY` is
optional; if a working key is added later, a Library-backed source can plug into the same
interface.

**Evidence.**
- The supplied key returns `401 Members-only content` for every documented auth header.
- `usdm4` loads CT and Biomedical Concepts **from caches bundled in the wheel** and only calls the
  API when a cache file is missing (`if self._cache.exists(): load from cache`). Bundled in 0.29.0:

  | Cache | Content |
  |---|---|
  | `ct/cdisc/library_cache/library_cache_usdm.yaml` | 59 codelists, 4,139 terms — DDF CT + SDTM CT, 2026-03-27 |
  | `ct/cdisc/library_cache/library_cache_all.yaml` | 1,273 codelists, 44,390 terms — SDTM, DDF, Protocol CT, 2025-03-28 |
  | `bc/cdisc/library_cache/library_cache.yaml` | 1,487 Biomedical Concepts in USDM shape |

- **Verified:** with `CDISC_API_KEY` removed from the environment, `USDM4Excel().from_excel()` on
  the CDISC Pilot workbook produced valid USDM 4.0.0 JSON in 28 s: 3 arms, 5 epochs, 12 encounters,
  36 activities, 4 timelines, 33 BCs, Phase coded `C15601` at CT version 2026-03-27. The error log
  had zero API or auth entries. Its only errors were a missing image file referenced by the
  workbook's narrative, since added to `goldstandard/cdisc_pilot/`.

**Terminology resolver design (Phase 3), consequences:**
1. **Primary source is the same bundled cache `usdm4` validates against.** This isn't only a
   workaround. A C-code or BC name the resolver accepts but `usdm4` can't find would fail in Stage
   C, so resolving against the identical data keeps Stage A and Stage C consistent.
2. **Newer or pinned CT versions** come from the free NCI EVS publications, which need no
   membership and carry code, codelist, extensible flag, submission value, synonyms, definition
   and NCI preferred term:
   - `https://evs.nci.nih.gov/ftp1/CDISC/DDF/DDF%20Terminology.txt`
   - `https://evs.nci.nih.gov/ftp1/CDISC/SDTM/SDTM%20Terminology.txt`
   - `https://evs.nci.nih.gov/ftp1/CDISC/Protocol/Protocol%20Terminology.txt`

   (current files dated 2026-07-11). Downloads are cached locally under `data/cache/`.
3. **Known limit: BC coverage is the 1,487 bundled concepts.** An activity needing a BC outside
   that set resolves as `unresolved` and goes to the reviewer. Nothing is guessed.
4. **Effective CT version.** The pinned version recorded in `run_config.json` must match the data
   actually used. Unless a newer package is loaded from NCI EVS, that is the bundled 2026-03-27
   release, and the run config will say so rather than claim a version that wasn't applied.

---

## D4 — PDF backend: PyMuPDF, with Claude vision for the Schedule of Activities

**Decision.** PyMuPDF is the default extraction backend: text, section hierarchy, basic tables,
page images. SoA grids go to the model as rendered page images alongside the parsed table, and
the two are reconciled. Docling is an optional local upgrade behind `extractors/base.py`. Cloud
backends (Azure Document Intelligence, Textract) are not used, because they would send the
protocol outside the Anthropic API.

---

## D5 — Deviations from the requested folder layout

- `runs/<run-id>/run_state.json` is added. The run's status needs a home for the Studies page to
  show it, and none of the listed artefacts fits. `run_config.json` stays reserved for the user's
  run settings (Phase 2 onward).
- The FastAPI entry point is an app factory (`backend.main:create_app --factory`), not a
  module-level `app`. Importing the backend (for example in tests) then never reads `.env` or
  creates a `studies/` folder as a side effect.

---

## D6 — Upstream v3 test fixtures deferred to Phase 7

`cdisc-org/usdm` publishes about 40 matched `.xlsx`/`.json` fixture pairs. The workbooks are still
useful (legacy format imports into `usdm4-excel`), but the paired JSON files are **USDM v3**, which
would be wrong expected output for a v4 pipeline. They'll be pulled in Phase 7, when the writers
exist, and their v4 expectations regenerated with `usdm4-excel`.

---

## D7 — PDF parsing: headings from the page, bookmarks as corroboration

**Decision.** Section headings are detected from typography and numbering on the page. PDF
bookmarks confirm them and fill gaps; they are never the primary source.

**Why: what the two real protocols showed.**
- PALOMA-3 has a 146-entry outline that skips sections (1.2.1.1, 1.2.1.4, Appendices 5–7; some
  redacted) and truncates titles at about 90 characters.
- The CDISC Pilot PDF has **no outline at all**.
- Heading styles differ completely: PALOMA-3 uses bold at body size, the Pilot uses larger Arial.
- Running headers sit at the top in one protocol and at the bottom (but first in text order) in the
  other, so they are removed by position and repetition, not reading order. Digits are ignored only
  for page numbering, so "Appendix 1 / 2 / 3" at page tops are not mistaken for a running header.
- PALOMA-3 carries "CCI" redaction stamps up to 144 pt, filtered as noise.
- The SoA is front matter before section 1 in PALOMA-3 (landscape pp15–19) and an attachment at the
  end of the Pilot (pp53–54).

**How numbered headings are accepted.** Every bold or large numbered line is a candidate. The kept
set is the highest-evidence subsequence whose numbers form a valid outline progression (children,
siblings, tolerated gaps for redacted sections), so a bold numbered list item inside a section cannot
displace the real next section. Numbering that restarts inside appendices ("7. Orientation" in the
Pilot ADAS-Cog attachment) is not treated as protocol sections.

**Tables.** PyMuPDF ruled-line detection; merged cells come back as `None`. Each table gets an SoA
score (visit-style headers, X marks, page or section title) and a `needs_vision` flag with reasons
(SoA candidate, merged cells, sparse grid, wide landscape table). PALOMA-3's SoA parses as 7 columns
with blank merged headers, which is exactly why the SoA agent will read page images (D4).

**Measured.** Pilot: 97 pages, 76 sections, 14 tables, SoA pp53–54, 12–14 s. PALOMA-3: 125 pages,
148 sections (146 corroborated by bookmarks), 23 tables, SoA pp15, 16, 20, 18–22 s. Most of the time
is rendering page images at 150 DPI.

---

## D8 — ICH M11 segmentation: deterministic and reviewable, no LLM

**Decision.** Mapping sections onto ICH M11 is deterministic code, not a model call. Low-confidence
mappings are flagged for review rather than resolved by guessing.

**Template.** `backend/pipeline/segmentation/m11_template.yaml`, authored from the section headings
in the ICH M11 technical specification v0.17.0 (June 2026). Four numbering glitches in the source are
corrected and listed in the file header. The Pilot workbook's `m11Format` sheet reflects an older M11
draft with different numbering, so it was not used. `data4knowledge/usdm_m11_resources` has no
licence, so nothing was copied from it.

**Scoring.**
- Titles are normalised for sponsor vs M11 vocabulary (study → trial, patient/subject → participant,
  medication → therapy, compliance → adherence, ...).
- Similarity is **word overlap weighted by rarity** across all M11 titles and aliases. Character-level
  fuzzy ratios were tried first and rejected: they produced confident nonsense ("Indication" →
  Introduction at 0.76, "Intraocular Pressure Measurement" → Pharmacokinetics at 0.83).
- Aliases record names real protocols use ("Patient Selection" → Trial Population). A test freezes
  the reviewed set of normalised titles shared by more than one M11 section, so an alias that
  collapses to something too generic fails the build ("Other Objectives" → "objective" did exactly
  that and mis-mapped PALOMA-3 section 2.1).
- Structure: candidates under the parent's M11 section get a bonus. A child jumping to a different
  M11 chapter than a confident parent needs a score of at least 0.75; otherwise it inherits the
  parent's mapping at 0.75 × confidence. Drug-named subsections ("Palbociclib/Placebo") therefore
  inherit "Administration" instead of matching arbitrary titles. Exact ties go to the less specific
  M11 section.
- A section holding a detected SoA table maps to 1.3. Unrecognised level-1 appendices map to M11's
  own catch-all, 12.X. The table of contents is excluded.

**Result.** Pilot: 0 of 76 sections flagged. PALOMA-3: 17 of 148 flagged, all deep subsections whose
mapping comes through two levels of inheritance (ophthalmology exams, background data). Regression
anchors for the Pilot are in `tests/integration/test_ingest_pipeline.py`.

**Not done yet, deliberately.** An LLM-assisted pass for flagged sections could reuse the Phase 3
client later. Reviewer overrides of mappings belong with the review UI (Phase 4).

---

## D9 — Licences of the core dependencies

`usdm4` / `usdm4-excel` are **GPL-3.0**; PyMuPDF is **AGPL-3.0** (or commercial from Artifex). Running
this as a local, single-user tool raises no issue. Distributing it, or offering it to other people as
a network service, brings (A)GPL source-availability obligations, and a commercial PyMuPDF licence
would not remove the GPL-3.0 obligation from `usdm4`. Worth checking before any wider rollout.

---

## D10 — Deviation: section_mapping.json

The requested run-folder layout has no file for the M11 mapping, which A2 says to store.
`section_mapping.json` holds it, next to `parsed_document.json`.

---

## D11 — Extraction agents: the model reads, code decides

**Decision.** Each sheet agent asks the model only for what it read: a value, a short verbatim
quote, the section id and a confidence. Everything else is deterministic code after the call:
quote verification and page attribution (D12), CDISC terminology resolution (D13), entity names and
the reference graph. The model never produces C-codes or cross-reference names.

**How an agent is built.**
- It reads only the protocol sections mapped to its ICH M11 sections (D8). Subtree matches give a
  section and everything beneath it; exact matches give a chapter's own text without its
  subsections, so the study agent reads the protocol summary but not the schedule of activities.
- Structured output (`messages.parse` / streaming with `output_format`) validates the response
  against the agent's Pydantic schema. Calls stream so long outputs never hit HTTP timeouts.
- Controlled-terminology fields list the codelist's preferred terms (never codes) as allowed
  wording, with an instruction to use the protocol's own wording when no term fits.
- Few-shot examples are invented ("examplumab", "COUGH-2"). No text from the CDISC Pilot (the
  evaluation gold standard) or PALOMA-3 (the working sample) appears in any prompt; an early draft
  used PALOMA-3's arm names as examples and was caught and fixed.
- Default model `claude-sonnet-5` (per the brief), set per run in `run_config.json`.

**Resumability and cost.** An agent's `input_hash` covers everything the model sees (shared and
agent prompt versions, schema, model, effort, CT version, section content). Unchanged inputs mean
no model call. The raw model output is stored with the records, so a change to post-processing
(`postprocess_version` or the verification rules version) rebuilds records from it for free. This
was used for real: fixing table-quote verification and escape decoding (D12) re-verified both
protocols with zero model calls.

**Isolation.** Agents run concurrently (default limit 5, per run). Each writes its own file; a
failed agent leaves its previous output untouched but contributes nothing to `extraction.json`, so
stale results never pass for current ones. Run status is `awaiting_review` if any sheet produced
records.

**Measured (claude-sonnet-5).**

| Protocol | study | arms | eligibility | Total |
|---|---|---|---|---|
| CDISC Pilot | $0.02 / 12 s | $0.02 / 9 s | $0.21 / 208 s (31 criteria) | ~$0.26 |
| PALOMA-3 | $0.09 / 24 s | $0.04 / 9 s | $0.13 / 82 s (28 criteria) | ~$0.26 |

Against the Pilot gold workbook, eligibility got all 31 criteria with identifiers (including "16b",
"27b"), categories and generated names (IN01…EX23) identical to the reference, each verified on
the correct page. Arms got all 3 arms; the two xanomeline arms are typed "Investigational Arm"
where the reference says "Active Comparator Arm". The study sheet is sparse for the Pilot because
the protocol does not state an acronym, status or dates; the reference workbook's values were
authored by hand. That is expected, and the reason for the tiered scoring agreed for Phase 9.

---

## D12 — Every quote is verified against the parsed protocol

**Decision.** A value's provenance is only `verified` when its quote is found in the protocol
text. The page is taken from where the quote is found (via `[[PAGE n]]` markers in section text),
never from the model. Unverified values have their confidence capped: 0.3 when the quote cannot be
found (possible hallucination), 0.6 when no quote was given.

**Matching rules, each added because a real case required it.**
- Exact matches must fall on word boundaries: "male" is not found inside "female".
- Near matches are allowed only for quotes of 20+ characters, starting on a word boundary, with
  exactly the same numbers and negations. "Female aged 65 years or older" never verifies against
  "Female aged 18 years or older".
- Whitespace lost in PDF text extraction is tolerated for quotes with a digit or 3+ words
  (PALOMA-3's title page prints "20 October2015"), still on word boundaries.
- Table cells are searchable. Without this, all four PALOMA-3 amendment dates, quoted from its
  Document History table, were wrongly flagged as possible hallucinations.
- Eligibility criterion text must match verbatim as a whole, not just its quote; a paraphrase is
  capped at 0.5 and flagged.
- Literal unicode escapes the model sometimes writes into strings (six characters instead of
  "≤" or "’") are decoded before post-processing. One Pilot run produced them in 9 of 31 criteria;
  the verbatim check caught it before it could reach the workbook. The raw output is kept unchanged
  for audit.

**Review flags.** `provenance.json` marks a value `needs_review` when its confidence is below the
run's threshold (default 0.7), its source is unverified, or its terminology is not an exact match.

**Values the model judged rather than read** (governance date category, a criterion's short label)
inherit the source of the value they rest on, never exceed 0.8 confidence, and say so in a note.
Values computed by code (names) are `derived`.

---

## D13 — Terminology resolution through usdm4's own CT library

**Decision.** Phrases are resolved with `usdm4`'s `Library` object, the same one Stage C uses, with
codelists chosen through usdm4's class/attribute configuration (e.g. `StudyArm.type` → C174222).
Terminology accepted in review is therefore exactly what usdm4 accepts in conversion.

- **Exact:** the phrase equals a term's C-code, submission value, preferred term or CT synonym
  (case-insensitive). This mirrors usdm4's importer, which matches code, preferred term or
  submission value case-insensitively (stricter wording in the spec notwithstanding).
- **Fuzzy / unresolved:** no code is assigned. Up to five candidates are offered for the reviewer.
  A hallucinated code ("C99999") or a valid code from the wrong codelist is never accepted.
- **CT version pinning:** the loaded CT version is written to `run_config.json` on first
  extraction. A later extraction against a different installed CT release is refused rather than
  mixing terminology releases in one run.

The two bundled usdm4 caches disagree (the 2026-03-27 USDM package and the 2025-03-28 "all"
package use different governance-date terms, for example). Loading through usdm4's `Library` picks
the same package Stage C will use, which is why the resolver does not read the YAML files itself.

**Not done yet:** Biomedical Concept resolution arrives with the SoA agent (Phase 6), the first
consumer of BCs. The bundled cache of 1,487 BCs is the source (D3).

---

## D14 — Reference graph starts now; SQLite terminology cache deferred

- `reference_validation.json` is produced from Phase 3 onwards: every named entity registered in
  one study-wide namespace, with duplicate names, missing names and dangling references reported
  with locations. It has little to find until later sheets add cross-references (study design →
  arms, timelines → encounters); Stage B will refuse to write while issues remain.
- The brief's SQLite cache for CDISC Library API lookups is not built. There are no API calls to
  cache (D3): terminology comes from usdm4's bundled cache, which is already local, offline and
  free. If a Library API key is added later, the cache belongs with that source.

---

## D15 — Review edits a working copy with an append-only audit trail

- `extraction.json` is never edited. Opening the review copies it to `reviewed.json` and gives
  every table row a stable `row_id`; all reviewer changes go there. Stage B will read only a
  confirmed `reviewed.json`.
- Changes are small operations (`set`, `add_row`, `delete_row`, `move_row`, `accept`), each
  recorded in `review_audit.jsonl` with the workbook cell, old and new value (and C-codes). A
  deleted row's full content is kept in the audit line.
- Optimistic locking: a save carries the revision it was based on; a mismatch is refused with 409.
  Single-user local (Phase 0), so this guards against two tabs, not concurrent users.
- A reviewer-entered value has origin `human`, confidence 1.0, and keeps the source page/section of
  the value it replaced. **Accept** keeps the extracted value and records that a person checked it.
- Controlled terminology is re-resolved on the server for every change; a picked code must belong
  to the field's codelist. A typed phrase that is not an exact match stays blocking.
- **Blocking:** missing required values, non-exact terminology, duplicate or missing names.
  **Warnings only:** low confidence and unverified quotes — they need a look, not a correction, and
  confirmation records how many were outstanding.
- Re-running extraction after a review started marks the review stale; **Restart** archives the old
  review to `review_archive/` and starts again. Merging old edits into new extraction is not
  attempted.
- **Confirm** is available before Stages B/C exist; it sets the run status to `reviewed`. Generation
  (Phases 7–8) will start from that status.

## D16 — Review layout follows the legacy workbook dialect; required flags follow the usdm4 model

The review grid shows each sheet as the workbook will contain it, so the cell references in the
UI and audit trail are the cells Stage B writes. Two corrections came from checking the
usdm4-excel importer and the CDISC Pilot gold workbook rather than the README's tables alone:

- **Governance dates** are the legacy table on the `study` sheet: the key/value block, a blank row,
  a header row (`category, name, description, label, type, date, scopes`), then one row per date.
  The separate `dates` sheet is the newer dialect; D1 chose the legacy single workbook, which is
  what the gold workbook uses.
- **Required** means required by the usdm4 model. The spec README marks `study.description` and
  `study.label` (and date description/label) as required, but `Study` and `GovernanceDate` make
  them optional and the gold workbook omits them; blocking a reviewer on them would be wrong. A unit
  test ties the flags to the usdm4 model fields.
- **Eligibility** uses the legacy one-sheet `studyDesignEligibilityCriteria` layout, one row per
  criterion, matching the gold workbook and the intermediate record 1:1.
- Sheets for later phases are not shown yet; the SoA grid arrives with the SoA agent (Phase 6).
  Columns not extracted yet (notes, dictionary, therapeutic areas) are shown greyed so letters match.

---

## D17 — Phase 5 sheets, and what is deliberately left out

Nine more agents, one per workbook area, each reading only its M11 sections:

| Agent | Workbook sheet(s) | Notes |
|---|---|---|
| identifiers | studyOrganizations, studyIdentifiers | one agent: identifiers point at their issuing organization |
| study_design | studyDesign (key/value block) | the epoch × arm grid below it needs epochs/elements: Phase 6 |
| populations | studyDesignPopulations | main population plus separately enrolled cohorts |
| objectives_endpoints | studyDesignOE | one row per endpoint |
| estimands | studyDesignEstimands | one row per intercurrent event; empty for protocols without estimands |
| interventions | studyInterventions | one row per administration |
| indications | studyDesignIndications | |
| amendments | studyAmendments | empty when there is no amendment history |
| abbreviations | abbreviations | empty when there is no list |

Not extracted (yet): `studyProducts` (dose forms, product designation), `amendmentImpact` /
`amendmentChanges`, `studyReferences`, `studyDesignSites`, `roles`, `studyDesignConditions`,
`studyDesignCharacteristics`, dictionaries. Indication and intervention codes (SNOMED, ICD-10,
UNII, MedDRA) are left empty: no licensed dictionary ships with the keyless setup, and the model
must not invent codes. Organizations' identifier scheme and identifier are required by USDM and are
rarely printed for sponsors, so they stay empty for the reviewer; only the public registries
(ClinicalTrials.gov, EudraCT, CTIS, ISRCTN) get a generated identifier, their web address, marked as
generated. Sheets whose protocol section is missing (amendments, estimands, abbreviations) finish
as empty sheets rather than failed agents.

## D18 — Layouts drive validation, references, provenance and the UI

`backend/pipeline/workbook/layout.py` is the single description of every sheet, and everything else
reads it: review validation, the reference graph, cross-sheet linking, provenance.json, the review
grid and the Extraction tab (`GET /api/workbook/layouts`). A column declares:

- `format` — boolean, quantity, range, count, date, geographic scope, enrollment. Checks mirror the
  usdm4-excel parsers (including unit lookup by C-code, preferred term or submission value, no
  synonyms; plural unit words only in ranges). Agents write these formats themselves from the numbers
  and units the model reports, using CDISC unit submission values ("18..75 YEARS", "125 mg").
  Decimals the importer truncates are a warning.
- `multi` — comma-separated terminology (trial intent types, sub types, characteristics, planned sex,
  amendment secondary reasons); exact only when every item is exact.
- `other_allowed` — amendment reasons accept `Other=<reason>`, resolved against the "Other" term.
- `choices` — small fixed vocabularies that are not CDISC CT (population level, date category).
- `group` / `leading_group` — two-level sheets (objective → endpoints, intervention →
  administrations, estimand → intercurrent events) are one workbook row per lower-level item, with
  the upper level's columns filled only on its first row, exactly as the importer reads them. Group
  columns are required only where the group starts; the first row must start one; empty rows block.
- `entity` / `ref` — names and references (below).

## D19 — References are typed and case-sensitive; linked deterministically across agents

usdm4 keys cross-references by class and exact name, so the reference graph now does the same:
names must be unique within an entity kind (an arm and an intervention may both be called
"Placebo"), and a reference must match a name of an allowed kind exactly. This replaces D1's
conservative single study-wide namespace, which would have blocked legitimate reviews.

Agents run independently and in parallel, so an agent cannot know another sheet's generated names.
Where a sheet refers to another (estimand → population, intervention, endpoint; amendment → governance
date), the agent records the protocol's phrase, and `identifiers/linking.py` replaces it with a name
whenever the model is assembled: only for extracted values, only on a clear best match (token-set /
partial fuzzy score ≥ 70 and 5 points ahead of the runner-up, partial matching only against texts at
least as long as the phrase), keeping the quote as provenance and capping confidence at the match
score. Unmatched phrases stay visible, are listed on the Extraction tab, and block review until a
reviewer picks a name from the reference picker. Agent dependencies (running estimands after the
sheets they refer to) were rejected: they would serialise the pipeline and make an estimand re-run
whenever an endpoint's wording changed.

## D20 — Review row ids are numbered per sheet

Row ids are `<prefix>-<n>` with a counter per prefix (`arm-1`, `crit-12`), never reused after a delete.
Reviews created in Phase 4 continue from their old global counter, so no id repeats.

---

## D21 — The schedule agent reads page images; USDM structure is built deterministically

Schedules of activities are the least reliable tables to parse (merged and spanning headers,
arrows, footnote letters, rotated pages; every SoA table in both sample protocols is flagged
`needs_vision`). The schedule agent therefore receives the page images of the M11 1.3 sections (at
most 12 pages, 150 dpi) alongside the parsed text, and the image hashes are part of its input hash.
It reports only what a reader sees: epochs, visit columns with their stated planned time and
window, activity rows with the columns they are marked in, and which tables are separate
timelines. From that, code builds the USDM structure:

- one timeline per schedule, the main one on sheet `main-timeline`, others `timeline-2`, ...;
- one encounter per main-timeline visit; one timepoint (scheduled activity instance) per column of
  every timeline, chained by default to the next column and `(Exit)` after the last; a column of a
  secondary timeline points at the encounter of the main visit it happens at;
- timings relative to the timeline's anchor visit: the anchor is a Fixed Reference, a visit with a
  stated planned time is Before/After it by that whole amount, with its window; visits without a
  fixed time (screening "within N days", end of treatment, repeating cycles) get no timing and a
  warning rather than an invented offset;
- USDM requires an entry condition for each timeline and protocols rarely state one, so a generic,
  visibly generated sentence is used unless the protocol gives one.

Quotes (column headings, row names) are verified like any other; the marks inherit the row's
provenance with the model's own marks confidence, noted as read from the page images.

## D22 — The schedule is reviewed as sheets and as a grid

The workbook stores a timeline as a matrix (timepoints across, activities down, X marks). Review
keeps one representation for editing, validation and audit: three table views that map onto the
timeline sheets one to one: **timelines** (the sheet-level rows), **timepoints** (one row per
column) and **schedule** (one row per activity row, with `scheduledAt` listing the timepoints it is
marked at). The **Schedule grid** tab draws the matrix from those views; clicking a cell edits the
row's `scheduledAt` list, so a mark change is an ordinary audited `set` with old and new lists.
Reference columns may hold several names (`multi`) and fixed literals such as `(Exit)`; pickers
only offer names from the same timeline. The Phase 7 writer lays the views out as the importer's
matrix. Not modelled yet: parent activity groups (kept as the activity description), footnote
conditions (`studyDesignConditions`), procedures, and decision instances.

## D23 — Biomedical Concepts only from exact catalogue matches

The assessments agent reads the assessment chapter and the laboratory appendix and lists each
assessment's measured parameters as printed. At assembly a schedule row is matched to an assessment
by name (word matching, score at least 90 and clearly ahead), and each parameter is looked up in
usdm4's bundled catalogue. Only exact matches become concepts: the concept's name, or a synonym
that belongs to that concept alone ("WBC" names both blood and urine leukocytes, so it matches
neither). Trial-summary "(TS)" and retired concepts are never scheduled. Parameters that are not in
the catalogue are listed in the value's note; an activity whose own name is a concept (e.g.
"Weight") gets it when no assessment matches. Review warns, without blocking, when a reviewer
enters a name that is not a concept: the importer then creates a concept surrogate, which is valid
USDM without a definition.

The linking score used for cross-sheet references was changed at the same time from
character-level partial matching to word matching, which scored "Medical History" against
"Clinical Chemistry" at 76.

## D24 — Elements and study cells are a documented default

USDM's study design needs an element for every arm in every epoch; protocols rarely define them.
At assembly, when arms and epochs both exist: one shared element per non-treatment epoch and one
element per arm per treatment-type epoch (by the epoch's CDISC term), with the arm-by-epoch cells
pointing at them. Every value is marked generated with a note, so crossover or unequal designs are
corrected in review (the **Arm-epoch cells** sheet).

---

## D25 — Stage B writes only from a confirmed, clean review

`POST .../workbook` writes `workbook/<slug>.xlsx` and `workbook_report.json` only when the review is
confirmed and re-validating it finds no blocking issue (required values, exact terminology,
importer-readable formats, a clean reference graph). Otherwise it answers 409 with the reasons and
issues; nothing is written from unreviewed extraction. The report records the review revision the
workbook came from, its sha256, the CT version and the rows written per sheet; asking again for an
unchanged revision reuses the file. The output is byte-identical for identical content: document
properties and zip entry times are pinned to the review's confirmation time, so the sha256 is a
meaningful fingerprint for evaluation and audit.

## D26 — The workbook follows what usdm4-excel's importer reads, verified by importing it

The writer mirrors the importer and usdm4-excel's own exporter rather than the README tables:
the `study` sheet with the legacy dates table, the `studyDesign` key/value block with
`mainTimeline`/`otherTimelines` and the arm-by-epoch element grid, and one sheet per timeline
(meta rows, timepoint heading rows in column C, activity table from row 10 with X marks and
`BC: ...` cells). Acceptance is an import with `usdm4_excel.USDM4Excel().from_excel()`: the unit
test round-trips the synthetic protocol, and both sample protocols, once their blocking cells were
filled, import with **0 errors** (the CDISC Pilot gold workbook also imports with 0).

Importing our first workbooks exposed importer requirements that USDM itself does not have, now
reflected in review:

- `studyAmendments.enrollment`, `studyDesignEncounters.environmentalSetting` / `contactModes`,
  `study.protocolStatus`, `studyDesign.studyType` / `studyPhase` are required: the importer
  rejects empty cells. Encounters get a visible generated default ("Clinic", "In Person") for the
  reviewer to change for telephone or home visits; the others have no honest default.
- Secondary amendment reasons are joined with "," without spaces: the reader splits without
  trimming, so " IRB/IEC Feedback" is not found in the codelist.
- Eligibility criterion text is read as XHTML: it is escaped and wrapped in `<p>`, so "< 1.5" is
  not "repaired".
- Interventions are written to `studyInterventions`; `studyDesignInterventions` is deprecated.
- Controlled-terminology cells are written as the resolution's preferred term (the importer does
  not match CT synonyms); dates as text `yyyy-mm-dd 00:00:00`, the form its date reader parses.

Remaining import warnings are expected: timepoints without a stated planned time have no timing
(D21), and optional sheets the pipeline does not produce yet are reported as absent.

---

## D27 — Stage C imports the reviewed workbook with usdm4-excel and never edits the JSON

`POST .../usdm` first brings the workbook up to date with the confirmed review (the Stage B gate
and reuse rules apply), then runs in the background (run status `generating`): usdm4-excel imports
`workbook/<slug>.xlsx`, the Wrapper is serialised to `usdm/<slug>.json`, and the JSON is validated.
The JSON is exactly what the CDISC reference tooling makes of the workbook; any correction belongs
in the review, where it is audited, not in a JSON patch. `usdm_report.json` records the workbook
sha256 and review revision it came from, import errors and warnings, rule outcomes and findings,
the CORE status, entity counts per `instanceType`, and timings. It is reused while the workbook is
unchanged. `GET .../usdm` adds `stale` reasons when the workbook or the review has moved on since.
While a Stage C job runs, the workbook cannot be rewritten underneath it (409).

## D28 — Validation: usdm4 rule library always, CDISC CORE only when its cache is ready

The usdm4 DDF rule library (213 rules) runs offline in about 3 seconds and is always run. CDISC CORE
needs a cache of rules, JSONata functions, schemas and a CT index downloaded with CDISC Library API
access; without it (the current setup, D-keyless terminology) the report says CORE did not run and
lists what is missing, instead of failing the stage. When the cache is present CORE runs and its
per-rule counts are reported; a CORE failure is recorded without losing the rule results.

## D29 — Every known rule finding is explained as "expected" or "fix in review"

Rule findings are grouped by rule on the Results page. Findings the pipeline is known to produce
carry a note (`FINDING_NOTES` in `usdm_gen/stage.py`):

- **expected**: parts of USDM not extracted yet (study roles and their organisations: DDF00172,
  DDF00192, DDF00201; procedures DDF00101; timeline planned duration DDF00153; administrable
  products DDF00185) or importer behaviour (anchor timing related to itself DDF00031, duplicate
  ids DDF00083, catalogue BC synonyms equal to the label DDF00236). The CDISC Pilot reference
  workbook gets several of the same findings.
- **fix in review**: the reviewed values break a USDM expectation, and the note names the sheet and
  the change (one primary objective, a planned age range with both ends, dose/route/frequency,
  duration text or quantity, at most one randomisation characteristic, ...).

Anything else is shown as **check**. Two findings were pipeline defects and are fixed at the
source: planned sex "Both" is now written as "Female, Male" (DDF00188), and a timeline's anchor
timing never carries a window (DDF00025). Every timeline now gets one Fixed Reference timing: when
the model marks no anchor, the first timepoint is used and flagged for review (DDF00009); planned
times are only built relative to an anchor the protocol names.

Measured on the sample protocols after re-processing (no model calls) and filling blocking cells
with test values: both import with **0 errors**; CDISC Pilot fails 12 of 213 rules with 14 findings
(9 expected, 5 fix in review), PALOMA-3 fails 15 with 27 findings (21 expected, 6 fix in review),
none unexplained. The CDISC Pilot reference JSON fails 17 rules with 42 findings.

