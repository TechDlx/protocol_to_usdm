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


class OrganizationRecord(BaseModel):
    row_id: str | None = None
    name: ExtractedField[str]
    label: ExtractedField[str]
    type: ExtractedField[str]  # CT C188724
    identifier_scheme: ExtractedField[str]
    identifier: ExtractedField[str]
    address: ExtractedField[str]  # lines|district|city|state|postal code|country code


class StudyIdentifierRecord(BaseModel):
    row_id: str | None = None
    identifier: ExtractedField[str]
    organization: ExtractedField[str]  # reference: an organization name


class IdentifiersSheet(BaseModel):
    organizations: list[OrganizationRecord] = Field(default_factory=list)
    identifiers: list[StudyIdentifierRecord] = Field(default_factory=list)


class StudyDesignRecord(BaseModel):
    name: ExtractedField[str]
    label: ExtractedField[str]
    description: ExtractedField[str]
    rationale: ExtractedField[str]
    blinding_schema: ExtractedField[str]  # CT C66735
    intent_types: ExtractedField[str]  # CT C66736, comma-separated
    sub_types: ExtractedField[str]  # CT C66739, comma-separated
    intervention_model: ExtractedField[str]  # CT C99076
    characteristics: ExtractedField[str]  # CT C207416, comma-separated
    study_type: ExtractedField[str]  # CT C99077
    study_phase: ExtractedField[str]  # CT C66737


class PopulationRecord(BaseModel):
    row_id: str | None = None
    level: ExtractedField[str]  # Main | Cohort
    name: ExtractedField[str]
    label: ExtractedField[str]
    description: ExtractedField[str]
    planned_completion_number: ExtractedField[str]  # "300" or "280..320"
    planned_enrollment_number: ExtractedField[str]
    planned_age: ExtractedField[str]  # "18..75 YEARS"
    planned_sex: ExtractedField[str]  # CT C66732, comma-separated
    includes_healthy_subjects: ExtractedField[str]  # Y | N


class ObjectiveEndpointRecord(BaseModel):
    """One workbook row: an endpoint, with its objective's columns filled on the objective's first
    row only (the importer attaches following rows to the objective above)."""

    row_id: str | None = None
    objective_name: ExtractedField[str]
    objective_label: ExtractedField[str]
    objective_description: ExtractedField[str]
    objective_text: ExtractedField[str]
    objective_level: ExtractedField[str]  # CT C188725
    endpoint_name: ExtractedField[str]
    endpoint_label: ExtractedField[str]
    endpoint_description: ExtractedField[str]
    endpoint_text: ExtractedField[str]
    endpoint_purpose: ExtractedField[str]
    endpoint_level: ExtractedField[str]  # CT C188726


class InterventionRecord(BaseModel):
    """One workbook row: an administration, with the intervention's columns on its first row."""

    row_id: str | None = None
    name: ExtractedField[str]
    label: ExtractedField[str]
    description: ExtractedField[str]
    role: ExtractedField[str]  # CT C207417
    type: ExtractedField[str]  # CT C99078
    minimum_response_duration: ExtractedField[str]  # quantity
    administration_name: ExtractedField[str]
    administration_label: ExtractedField[str]
    administration_description: ExtractedField[str]
    administration_route: ExtractedField[str]  # CT C66729
    administration_dose: ExtractedField[str]  # quantity, e.g. "125 mg"
    administration_frequency: ExtractedField[str]  # CT C71113
    duration_description: ExtractedField[str]
    duration_will_vary: ExtractedField[str]  # Y | N
    duration_will_vary_reason: ExtractedField[str]
    duration_quantity: ExtractedField[str]  # quantity, e.g. "24 WEEKS"


class IndicationRecord(BaseModel):
    row_id: str | None = None
    name: ExtractedField[str]
    label: ExtractedField[str]
    description: ExtractedField[str]
    is_rare_disease: ExtractedField[str]  # Y | N


class EstimandRecord(BaseModel):
    """One workbook row: an intercurrent event, with the estimand's columns on its first row."""

    row_id: str | None = None
    name: ExtractedField[str]
    summary_measure: ExtractedField[str]
    population_description: ExtractedField[str]
    population: ExtractedField[str]  # reference: a population or cohort name
    treatment: ExtractedField[str]  # reference: an intervention name
    endpoint: ExtractedField[str]  # reference: an endpoint name
    event_name: ExtractedField[str]
    event_description: ExtractedField[str]
    event_strategy: ExtractedField[str]
    event_text: ExtractedField[str]


class AmendmentRecord(BaseModel):
    row_id: str | None = None
    name: ExtractedField[str]
    label: ExtractedField[str]
    description: ExtractedField[str]
    number: ExtractedField[str]
    summary: ExtractedField[str]
    primary_reason: ExtractedField[str]  # CT C207415 term, or "Other=<text>"
    secondary_reasons: ExtractedField[str]  # comma-separated, same form
    geographic_scope: ExtractedField[str]  # "Global" | "Region: X" | "Country: Y"
    enrollment: ExtractedField[str]  # "Global: 300"
    date: ExtractedField[str]  # reference: a governance date name


class AbbreviationRecord(BaseModel):
    row_id: str | None = None
    abbreviated_text: ExtractedField[str]
    expanded_text: ExtractedField[str]


class EpochRecord(BaseModel):
    row_id: str | None = None
    name: ExtractedField[str]
    label: ExtractedField[str]
    description: ExtractedField[str]
    type: ExtractedField[str]  # CT C99079


class EncounterRecord(BaseModel):
    row_id: str | None = None
    name: ExtractedField[str]
    label: ExtractedField[str]
    description: ExtractedField[str]
    type: ExtractedField[str]  # CT C188728 ("Visit")
    environmental_settings: ExtractedField[str]  # CT, comma-separated
    contact_modes: ExtractedField[str]  # CT, comma-separated
    transition_start_rule: ExtractedField[str]
    transition_end_rule: ExtractedField[str]
    window: ExtractedField[str]  # reference: a timing name


class TimingRecord(BaseModel):
    row_id: str | None = None
    name: ExtractedField[str]
    label: ExtractedField[str]
    description: ExtractedField[str]
    type: ExtractedField[str]  # CT: Fixed Reference | Before | After
    relative_from: ExtractedField[str]  # reference: a timepoint name
    relative_to: ExtractedField[str]  # reference: a timepoint name
    value: ExtractedField[str]  # "2 weeks"
    relative_to_from: ExtractedField[str]  # S2S | S2E | E2S | E2E
    window: ExtractedField[str]  # "-3..3 days"


class TimelineRecord(BaseModel):
    row_id: str | None = None
    name: ExtractedField[str]
    label: ExtractedField[str]
    description: ExtractedField[str]
    main: ExtractedField[str]  # Y | N
    entry_condition: ExtractedField[str]
    sheet_name: ExtractedField[str]  # the workbook sheet holding this timeline


class TimepointRecord(BaseModel):
    """A scheduled activity instance: one column of a timeline sheet."""

    row_id: str | None = None
    timeline: ExtractedField[str]  # reference: a timeline name
    name: ExtractedField[str]
    label: ExtractedField[str]
    description: ExtractedField[str]
    type: ExtractedField[str]  # Activity | Decision
    default: ExtractedField[str]  # reference: the next timepoint, or (Exit)
    condition: ExtractedField[str]
    epoch: ExtractedField[str]  # reference: an epoch name
    encounter: ExtractedField[str]  # reference: an encounter name


class ActivityRecord(BaseModel):
    row_id: str | None = None
    name: ExtractedField[str]
    label: ExtractedField[str]
    description: ExtractedField[str]


class ScheduleRowRecord(BaseModel):
    """One activity row of a timeline sheet: where the activity is marked X."""

    row_id: str | None = None
    timeline: ExtractedField[str]  # reference: a timeline name
    activity: ExtractedField[str]  # reference: an activity name
    biomedical_concepts: ExtractedField[str]  # BC names, comma-separated
    scheduled_at: ExtractedField[str]  # references: timepoint names, comma-separated


class ScheduleSheet(BaseModel):
    """Everything read from the schedule of activities."""

    epochs: list[EpochRecord] = Field(default_factory=list)
    encounters: list[EncounterRecord] = Field(default_factory=list)
    timings: list[TimingRecord] = Field(default_factory=list)
    timelines: list[TimelineRecord] = Field(default_factory=list)
    timepoints: list[TimepointRecord] = Field(default_factory=list)
    activities: list[ActivityRecord] = Field(default_factory=list)
    rows: list[ScheduleRowRecord] = Field(default_factory=list)


class AssessmentRecord(BaseModel):
    """What an assessment measures, as the assessments agent read it (used to assign BCs)."""

    assessment: ExtractedField[str]
    measurements: list[ExtractedField[str]] = Field(default_factory=list)


class ElementRecord(BaseModel):
    row_id: str | None = None
    name: ExtractedField[str]
    label: ExtractedField[str]
    description: ExtractedField[str]
    transition_start_rule: ExtractedField[str]
    transition_end_rule: ExtractedField[str]


class StudyCellRecord(BaseModel):
    """One cell of the studyDesign arm-by-epoch grid."""

    row_id: str | None = None
    arm: ExtractedField[str]  # reference: an arm name
    epoch: ExtractedField[str]  # reference: an epoch name
    elements: ExtractedField[str]  # references: element names, comma-separated


class DesignStructure(BaseModel):
    """Elements and the arm-by-epoch grid, derived from arms and epochs at assembly."""

    elements: list[ElementRecord] = Field(default_factory=list)
    cells: list[StudyCellRecord] = Field(default_factory=list)


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
    identifiers: IdentifiersSheet | None = None
    study_design: StudyDesignRecord | None = None
    populations: list[PopulationRecord] | None = None
    objectives_endpoints: list[ObjectiveEndpointRecord] | None = None
    interventions: list[InterventionRecord] | None = None
    indications: list[IndicationRecord] | None = None
    estimands: list[EstimandRecord] | None = None
    amendments: list[AmendmentRecord] | None = None
    abbreviations: list[AbbreviationRecord] | None = None
    schedule: ScheduleSheet | None = None
    assessments: list[AssessmentRecord] | None = None
    design: DesignStructure | None = None


class Extraction(BaseModel):
    schema_version: int = EXTRACTION_SCHEMA_VERSION
    source_sha256: str
    ct_version: str
    generated_at: datetime
    agents: dict[str, AgentRun]
    sheets: ExtractionSheets
    #: Cross-sheet references the linking step could not resolve (see identifiers/linking.py).
    link_notes: list[str] = Field(default_factory=list)


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
