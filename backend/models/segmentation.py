"""Mapping of a parsed protocol's sections onto the ICH M11 template (section_mapping.json)."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

SECTION_MAPPING_SCHEMA_VERSION = 1


class MappingMethod(StrEnum):
    NUMBER_AND_TITLE = "number_and_title"  # M11-native protocol: same number, similar title
    TITLE_MATCH = "title_match"  # title similar to the M11 title
    ALIAS_MATCH = "alias_match"  # title similar to a known non-M11 name for the same content
    CONTENT_SOA = "content_soa"  # section holds a Schedule of Activities table
    STRUCTURAL = "structural"  # title page -> section 0
    APPENDIX_DEFAULT = "appendix_default"  # unrecognised appendix -> 12.X additional appendices
    INHERITED = "inherited"  # no good match; takes the parent section's mapping
    UNMAPPED = "unmapped"
    EXCLUDED = "excluded"  # table of contents, or marked by a reviewer: not protocol content
    REVIEWER = "reviewer"  # mapped by a reviewer (section_overrides.json)


class Candidate(BaseModel):
    m11_number: str
    m11_title: str
    score: float


class SectionAssignment(BaseModel):
    section_id: str
    doc_number: str | None
    doc_title: str
    page_start: int
    page_end: int
    m11_number: str | None
    m11_title: str | None
    confidence: float = Field(ge=0, le=1)
    method: MappingMethod
    matched_text: str | None  # the M11 title or alias that produced the match
    candidates: list[Candidate]
    needs_review: bool
    reviewer_override: bool = False  # set by a reviewer; survives re-segmentation


class CoverageStatus(StrEnum):
    FOUND = "found"
    LOW_CONFIDENCE = "low_confidence"
    MISSING = "missing"


class M11Coverage(BaseModel):
    m11_number: str
    m11_title: str
    level: int
    optional: bool
    section_ids: list[str]
    best_confidence: float | None
    status: CoverageStatus


class SectionMapping(BaseModel):
    schema_version: int = SECTION_MAPPING_SCHEMA_VERSION
    template: str
    template_version: str
    source_sha256: str
    m11_native: bool
    m11_native_ratio: float
    review_threshold: float
    assignments: list[SectionAssignment]
    coverage: list[M11Coverage]
    #: Reviewer overrides that no longer fit the parsed document (e.g. after a re-parse).
    ignored_overrides: list[str] = Field(default_factory=list)

    def assignment(self, section_id: str) -> SectionAssignment:
        for a in self.assignments:
            if a.section_id == section_id:
                return a
        raise KeyError(section_id)


class SectionOverride(BaseModel):
    """A reviewer's mapping of one document section, kept apart from the computed mapping so that
    re-running segmentation cannot lose it."""

    section_id: str
    doc_title: str  # the section's title when the override was made, to detect a changed parse
    m11_number: str | None  # None when excluded
    excluded: bool = False
    updated_at: datetime


class SectionOverrides(BaseModel):
    schema_version: int = 1
    overrides: dict[str, SectionOverride] = Field(default_factory=dict)


class SectionOverrideRequest(BaseModel):
    m11_number: str | None = None
    excluded: bool = False


class M11TemplateSectionOut(BaseModel):
    number: str
    title: str
    level: int
    optional: bool


class AgentInputChange(BaseModel):
    """An extraction agent whose protocol sections differ from those it last read."""

    sheet: str
    added: list[str]  # section ids it would now read
    removed: list[str]  # section ids it no longer reads
