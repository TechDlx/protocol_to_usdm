// Mirrors backend/models/study.py. Keep the two in step.

export interface SourceDocument {
  filename: string;
  relative_path: string;
  sha256: string;
  size_bytes: number;
  page_count: number;
  uploaded_at: string;
}

export type RunStatus =
  | "created"
  | "running"
  | "parsed"
  | "awaiting_review"
  | "reviewed"
  | "generating"
  | "completed"
  | "failed";

export interface RunState {
  run_id: string;
  status: RunStatus;
  source_filename: string | null;
  created_at: string;
  updated_at: string;
}

export interface StudySummary {
  schema_version: number;
  slug: string;
  name: string;
  sponsor: string;
  protocol_identifier: string;
  created_at: string;
  updated_at: string;
  sources: SourceDocument[];
  runs: RunState[];
}

export interface StudyCreate {
  name: string;
  sponsor: string;
  protocol_identifier: string;
}

// ----- runs (backend/models/study.py) --------------------------------------------------------

export type StageStatus = "pending" | "running" | "done" | "skipped" | "failed";

export interface StageState {
  status: StageStatus;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  detail: string | null;
}

export interface RunDetail extends RunState {
  stages: Partial<Record<"ingest" | "segment" | "extract" | "workbook" | "usdm", StageState>>;
  agents: Record<string, AgentRun>;
}

// ----- parsed document (backend/models/document.py) ------------------------------------------

export type SectionKind = "title_page" | "front_matter" | "toc" | "body" | "appendix";

export interface PageInfo {
  number: number;
  width: number;
  height: number;
  landscape: boolean;
  image_path: string;
  is_toc_page: boolean;
  removed_header_footer_lines: number;
  redaction_marks: number;
}

export interface Section {
  id: string;
  number: string | null;
  title: string;
  level: number;
  kind: SectionKind;
  parent_id: string | null;
  page_start: number;
  page_end: number;
  heading_source: "text" | "outline" | "text_and_outline" | "synthetic";
  text: string;
  table_ids: string[];
}

export interface Table {
  id: string;
  page: number;
  section_id: string | null;
  caption: string | null;
  row_count: number;
  col_count: number;
  cells: (string | null)[][];
  merged_cell_count: number;
  empty_cell_ratio: number;
  group_id: string;
  soa_score: number;
  is_soa_candidate: boolean;
  needs_vision: boolean;
  vision_reasons: string[];
}

export interface ParsedDocument {
  source: { filename: string; sha256: string; page_count: number };
  extractor: { name: string; version: string; library_version: string; page_image_dpi: number };
  pages: PageInfo[];
  sections: Section[];
  tables: Table[];
  stats: {
    body_font_size: number;
    headings_from_text: number;
    headings_from_outline_only: number;
    rejected_heading_candidates: number;
    tables: number;
    tables_needing_vision: number;
    soa_pages: number[];
    elapsed_seconds: number;
  };
  warnings: string[];
}

// ----- section mapping (backend/models/segmentation.py) --------------------------------------

export type MappingMethod =
  | "number_and_title"
  | "title_match"
  | "alias_match"
  | "content_soa"
  | "structural"
  | "appendix_default"
  | "inherited"
  | "unmapped"
  | "excluded"
  | "reviewer";

export interface SectionAssignment {
  section_id: string;
  doc_number: string | null;
  doc_title: string;
  page_start: number;
  page_end: number;
  m11_number: string | null;
  m11_title: string | null;
  confidence: number;
  method: MappingMethod;
  matched_text: string | null;
  candidates: { m11_number: string; m11_title: string; score: number }[];
  needs_review: boolean;
  reviewer_override: boolean;
}

export interface M11Coverage {
  m11_number: string;
  m11_title: string;
  level: number;
  optional: boolean;
  section_ids: string[];
  best_confidence: number | null;
  status: "found" | "low_confidence" | "missing";
}

export interface SectionMapping {
  template: string;
  template_version: string;
  m11_native: boolean;
  review_threshold: number;
  assignments: SectionAssignment[];
  coverage: M11Coverage[];
  ignored_overrides?: string[];
}

export interface M11TemplateSection {
  number: string;
  title: string;
  level: number;
  optional: boolean;
}

/** An extraction agent whose protocol sections changed since it last ran. */
export interface AgentInputChange {
  sheet: string;
  added: string[];
  removed: string[];
}

// ----- extraction (backend/models/extraction.py) ----------------------------------------------

export type TerminologyStatus = "exact" | "fuzzy" | "unresolved";
export type AgentStatus = "queued" | "running" | "done" | "skipped" | "failed";

export interface Provenance {
  origin: "extracted" | "derived" | "human";
  source_section_id: string | null;
  source_page: number | null;
  raw_phrase: string | null;
  confidence: number;
  verified: boolean;
  note: string | null;
  reviewer_accepted?: boolean;
}

export interface TermCandidate {
  code: string;
  submission_value: string;
  preferred_term: string;
  score: number;
}

export interface TerminologyResolution {
  status: TerminologyStatus;
  codelist: string;
  codelist_name: string;
  ct_version: string;
  code: string | null;
  submission_value: string | null;
  preferred_term: string | null;
  matched_on: string | null;
  candidates: TermCandidate[];
}

export interface ExtractedField {
  value: string | null;
  provenance: Provenance | null;
  terminology: TerminologyResolution | null;
}

export type SheetRecord = Record<string, ExtractedField | SheetRecord[] | unknown>;

export interface LlmUsage {
  model: string;
  input_tokens: number;
  output_tokens: number;
  latency_seconds: number;
  cost_usd: number;
  stop_reason: string | null;
}

export interface AgentRun {
  sheet: string;
  status: AgentStatus;
  started_at: string | null;
  finished_at: string | null;
  section_ids: string[];
  usage: LlmUsage | null;
  error: string | null;
  warnings: string[];
}

/** ExtractionSheets: records keyed by agent sheet; layouts' dotted `source` paths find them. */
export type ExtractionSheets = Record<string, unknown>;

export interface Extraction {
  ct_version: string;
  generated_at: string;
  agents: Record<string, AgentRun>;
  sheets: ExtractionSheets;
  link_notes?: string[];
}

export interface ReferenceValidation {
  valid: boolean;
  entities: number;
  issues: { kind: string; name: string; locations: string[]; message: string }[];
}

// ----- review (backend/models/review.py) ------------------------------------------------------

export type ReviewSheetKey = string;

export interface ColumnLayout {
  letter: string;
  header: string;
  field: string | null;
  required: boolean;
  multiline: boolean;
  ct_klass: string | null;
  ct_attribute: string | null;
  multi: boolean;
  other_allowed: boolean;
  format: string | null;
  format_hint: string | null;
  choices: string[];
  group: string | null;
  entity: string | null;
  ref: string[];
  ref_literals: string[];
  bc: boolean;
}

export interface SheetLayout {
  key: ReviewSheetKey;
  workbook_sheet: string;
  title: string;
  kind: "key_value" | "table";
  source: string;
  first_row: number;
  leading_group: string | null;
  columns: ColumnLayout[];
}

export type IssueKind =
  | "missing_required"
  | "terminology_not_exact"
  | "duplicate_name"
  | "missing_name"
  | "dangling_reference"
  | "invalid_format"
  | "invalid_structure"
  | "low_confidence"
  | "unverified_source";

export interface ReviewIssue {
  severity: "blocking" | "warning";
  kind: IssueKind;
  sheet: string;
  row_id: string | null;
  field: string | null;
  cell: string | null;
  message: string;
}

export interface ReviewDocument {
  status: "draft" | "confirmed";
  revision: number;
  ct_version: string;
  base_extraction_generated_at: string;
  updated_at: string;
  confirmed_at: string | null;
  sheets: Extraction["sheets"];
}

export interface ReviewState {
  document: ReviewDocument;
  validation: { blocking: number; warnings: number; issues: ReviewIssue[] };
  stale: boolean;
  confidence_threshold: number;
  layouts: SheetLayout[];
}

export type ReviewOperation =
  | { op: "set"; sheet: ReviewSheetKey; row_id: string | null; field: string; value: string | null; code?: string | null }
  | { op: "add_row"; sheet: ReviewSheetKey; after_row_id: string | null }
  | { op: "delete_row"; sheet: ReviewSheetKey; row_id: string }
  | { op: "move_row"; sheet: ReviewSheetKey; row_id: string; to_index: number }
  | { op: "accept"; sheet: ReviewSheetKey; row_id: string | null; field: string };

export interface AuditEntry {
  ts: string;
  actor: string;
  action: string;
  revision: number;
  cell: string | null;
  field: string | null;
  old_value: string | null;
  new_value: string | null;
  old_code: string | null;
  new_code: string | null;
  detail: string | null;
}

export interface Codelist {
  codelist: string;
  codelist_name: string;
  ct_version: string;
  extensible: boolean;
  terms: TermCandidate[];
}

// ----- workbook (backend/pipeline/workbook/stage.py) ------------------------------------------

export interface WorkbookReport {
  generated_at: string;
  file: string;
  sha256: string;
  size_bytes: number;
  review_revision: number;
  review_confirmed_at: string | null;
  ct_version: string;
  stale_extraction: boolean;
  sheets: Record<string, number>;
  warnings: string[];
  reused: boolean;
}

// ----- USDM JSON and validation (backend/pipeline/usdm_gen/stage.py) ---------------------------

export interface ImportIssue {
  level: string;
  message: string;
  location: string;
}

/** expected: the pipeline or importer does not produce this yet. review: fix the reviewed values. */
export type FindingKind = "expected" | "review";

export interface RuleFinding {
  rule_id: string;
  status: string;
  level: string;
  message: string;
  klass: string;
  attribute: string;
  path: string;
  rule_text: string;
  kind: FindingKind | null;
  note: string | null;
}

export interface RulesSummary {
  engine: string;
  rules: number;
  passed: number;
  failed: number;
  exceptions: number;
  not_implemented: number;
  findings: number;
  expected_findings: number;
}

export interface CoreSummary {
  ran: boolean;
  reason: string | null;
  rules_executed: number;
  findings: number;
  execution_errors: number;
  results: { rule_id: string; description: string; message: string; count: number }[];
}

export interface UsdmReport {
  generated_at: string;
  file: string | null;
  sha256: string | null;
  size_bytes: number;
  usdm_version: string;
  system: string;
  workbook_file: string;
  workbook_sha256: string;
  review_revision: number;
  ct_version: string;
  import_errors: ImportIssue[];
  import_warnings: ImportIssue[];
  rules: RulesSummary;
  findings: RuleFinding[];
  core: CoreSummary;
  entities: Record<string, number>;
  seconds: Record<string, number>;
  reused: boolean;
}

export interface UsdmResult {
  report: UsdmReport;
  stale: string[];
}

