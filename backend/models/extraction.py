"""The intermediate extraction model (extraction.json) and its provenance.

Every extracted value is a `Field`: the value plus where it came from (page, section, the verbatim
phrase, confidence) and, for CDISC controlled-terminology fields, the deterministic terminology
resolution. Nothing reaches the workbook without that trail or an explicit human override.

Records are workbook-dialect neutral: the eligibility model, for instance, can be written as the
split eligibilityCriteria + eligibilityCriteriaItems sheets or as the legacy one-sheet form.
"""

from datetime import datetime
from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

EXTRACTION_SCHEMA_VERSION = 1

T = TypeVar("T")


class ValueOrigin(StrEnum):
    EXTRACTED = "extracted"  # read from the protocol by an extraction agent
    DERIVED = "derived"  # computed deterministically (identifiers, defaults)
    HUMAN = "human"  # entered or corrected by a reviewer


class Provenance(BaseModel):
    origin: ValueOrigin
    source_section_id: str | None = None
    source_page: int | None = None
    raw_phrase: str | None = None  # verbatim text from the protocol supporting the value
    confidence: float = Field(default=1.0, ge=0, le=1)
    # True when raw_phrase was found in the cited section; the page then comes from the parse,
    # not from the model's claim. False means the model's citation could not be confirmed.
    verified: bool = False
    note: str | None = None
    # A reviewer looked at a flagged extracted value and accepted it unchanged.
    reviewer_accepted: bool = False


class TerminologyStatus(StrEnum):
    EXACT = "exact"  # matched a C-code, submission value, preferred term or CT synonym
    FUZZY = "fuzzy"  # close to a term but not identical: needs a reviewer's decision
    UNRESOLVED = "unresolved"  # no acceptable match: needs a reviewer's decision


class TermCandidate(BaseModel):
    code: str
    submission_value: str
    preferred_term: str
    score: float


class TerminologyResolution(BaseModel):
    status: TerminologyStatus
    codelist: str
    codelist_name: str
    ct_version: str
    code: str | None = None
    submission_value: str | None = None
    preferred_term: str | None = None
    matched_on: str | None = None  # conceptId | preferredTerm | submissionValue | synonym | fuzzy
    candidates: list[TermCandidate] = Field(default_factory=list)


class ExtractedField(BaseModel, Generic[T]):  # noqa: UP046 - explicit Generic for Pydantic
    value: T | None = None
    provenance: Provenance | None = None
    terminology: TerminologyResolution | None = None

    @property
    def is_empty(self) -> bool:
        return self.value is None or self.value == ""


class GovernanceDateRecord(BaseModel):
    row_id: str | None = None  # stable identity for review edits; assigned at review
    name: ExtractedField[str]
    category: ExtractedField[str]  # study_version | protocol_document | amendment
    label: ExtractedField[str]
    description: ExtractedField[str]
    type: ExtractedField[str]  # CT C207413
    date: ExtractedField[str]  # ISO 8601 yyyy-mm-dd
    geographic_scopes: ExtractedField[str]  # "Global" | "Region: X" | "Country: Y"


class StudyRecord(BaseModel):
    name: ExtractedField[str]
    label: ExtractedField[str]
    description: ExtractedField[str]
    study_version: ExtractedField[str]
    acronym: ExtractedField[str]
    rationale: ExtractedField[str]
    brief_title: ExtractedField[str]
    official_title: ExtractedField[str]
    public_title: ExtractedField[str]
    scientific_title: ExtractedField[str]
    protocol_version: ExtractedField[str]
    protocol_status: ExtractedField[str]  # CT C188723
    sponsor_protocol_identifier: ExtractedField[str]
    governance_dates: list[GovernanceDateRecord] = Field(default_factory=list)


class ArmRecord(BaseModel):
    row_id: str | None = None
    name: ExtractedField[str]
    label: ExtractedField[str]
    description: ExtractedField[str]
    type: ExtractedField[str]  # CT C174222
    data_origin_description: ExtractedField[str]
    data_origin_type: ExtractedField[str]  # CT C188727


class EligibilityCriterionRecord(BaseModel):
    row_id: str | None = None
    name: ExtractedField[str]
    category: ExtractedField[str]  # CT C66797
    identifier: ExtractedField[str]  # the protocol's own criterion number
    label: ExtractedField[str]
    description: ExtractedField[str]
    text: ExtractedField[str]


class AgentStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"  # reused from a previous run with identical inputs
    FAILED = "failed"


class LlmUsage(BaseModel):
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    latency_seconds: float = 0.0
    cost_usd: float = 0.0
    stop_reason: str | None = None
    request_id: str | None = None


class AgentRun(BaseModel):
    sheet: str
    status: AgentStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    input_hash: str | None = None  # everything the model saw; unchanged means no new model call
    postprocess_version: str | None = None  # agent post-processing + quote verification rules
    reprocessed: bool = False  # records rebuilt from stored model output, no model call
    section_ids: list[str] = Field(default_factory=list)
    usage: LlmUsage | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ExtractionSheets(BaseModel):
    study: StudyRecord | None = None
    study_design_arms: list[ArmRecord] | None = None
    eligibility_criteria: list[EligibilityCriterionRecord] | None = None


class Extraction(BaseModel):
    schema_version: int = EXTRACTION_SCHEMA_VERSION
    source_sha256: str
    ct_version: str
    generated_at: datetime
    agents: dict[str, AgentRun]
    sheets: ExtractionSheets


class ProvenanceEntry(BaseModel):
    """One row of provenance.json: a flat, audit-friendly view of every value."""

    sheet: str
    row: int | None  # None for key/value sheets such as study
    field: str
    value: str | None
    origin: ValueOrigin
    source_section_id: str | None
    source_page: int | None
    raw_phrase: str | None
    confidence: float
    verified: bool
    terminology_status: TerminologyStatus | None
    code: str | None
    needs_review: bool
    review_reasons: list[str]


class ReferenceIssueKind(StrEnum):
    DUPLICATE_NAME = "duplicate_name"
    DANGLING_REFERENCE = "dangling_reference"
    MISSING_NAME = "missing_name"


class ReferenceAnchor(BaseModel):
    """Where a named entity lives, precisely enough for the review page to jump to it."""

    sheet: str
    row_id: str | None
    field: str


class ReferenceIssue(BaseModel):
    kind: ReferenceIssueKind
    name: str
    locations: list[str]
    message: str
    anchors: list[ReferenceAnchor] = Field(default_factory=list)


class ReferenceValidation(BaseModel):
    valid: bool
    entities: int
    issues: list[ReferenceIssue]
