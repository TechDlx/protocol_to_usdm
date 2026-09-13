"""The reviewed intermediate model (reviewed.json), review operations, and review issues."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from backend.models.extraction import ExtractionSheets

REVIEW_SCHEMA_VERSION = 1


class ReviewStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


class ReviewDocument(BaseModel):
    schema_version: int = REVIEW_SCHEMA_VERSION
    status: ReviewStatus = ReviewStatus.DRAFT
    # Incremented by every saved change; clients send the revision they edited (optimistic locking).
    revision: int = 0
    source_sha256: str
    ct_version: str
    # The extraction this review started from. If extraction is re-run, the review is stale.
    base_extraction_generated_at: datetime
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None = None
    next_row_seq: int = 1
    sheets: ExtractionSheets


# ----- operations ------------------------------------------------------------------------------


class SetValue(BaseModel):
    op: Literal["set"] = "set"
    sheet: str
    row_id: str | None = None  # None for key/value sheets (study)
    field: str
    value: str | None
    # For controlled-terminology fields: the C-code the reviewer picked. When omitted the value is
    # resolved against the codelist and may remain fuzzy/unresolved.
    code: str | None = None


class AddRow(BaseModel):
    op: Literal["add_row"] = "add_row"
    sheet: str
    after_row_id: str | None = None  # None inserts at the top


class DeleteRow(BaseModel):
    op: Literal["delete_row"] = "delete_row"
    sheet: str
    row_id: str


class MoveRow(BaseModel):
    op: Literal["move_row"] = "move_row"
    sheet: str
    row_id: str
    to_index: int = Field(ge=0)


class AcceptValue(BaseModel):
    op: Literal["accept"] = "accept"
    sheet: str
    row_id: str | None = None
    field: str


ReviewOperation = Annotated[
    SetValue | AddRow | DeleteRow | MoveRow | AcceptValue, Field(discriminator="op")
]


class OperationsRequest(BaseModel):
    base_revision: int
    operations: list[ReviewOperation] = Field(min_length=1, max_length=500)


class RevisionRequest(BaseModel):
    base_revision: int


# ----- validation ------------------------------------------------------------------------------


class IssueSeverity(StrEnum):
    BLOCKING = "blocking"
    WARNING = "warning"


class IssueKind(StrEnum):
    MISSING_REQUIRED = "missing_required"
    TERMINOLOGY_NOT_EXACT = "terminology_not_exact"
    DUPLICATE_NAME = "duplicate_name"
    MISSING_NAME = "missing_name"
    DANGLING_REFERENCE = "dangling_reference"
    LOW_CONFIDENCE = "low_confidence"
    UNVERIFIED_SOURCE = "unverified_source"


class ReviewIssue(BaseModel):
    severity: IssueSeverity
    kind: IssueKind
    sheet: str
    row_id: str | None
    field: str | None
    cell: str | None  # workbook reference, e.g. studyDesignArms!D3
    message: str


class ReviewValidation(BaseModel):
    blocking: int
    warnings: int
    issues: list[ReviewIssue]


class ColumnOut(BaseModel):
    letter: str
    header: str
    field: str | None
    required: bool
    multiline: bool
    ct_klass: str | None
    ct_attribute: str | None


class SheetLayoutOut(BaseModel):
    key: str
    workbook_sheet: str
    title: str
    kind: str
    first_row: int
    columns: list[ColumnOut]


class ReviewState(BaseModel):
    document: ReviewDocument
    validation: ReviewValidation
    stale: bool  # extraction was re-run after this review started
    confidence_threshold: float
    layouts: list[SheetLayoutOut]


class AuditEntry(BaseModel):
    ts: datetime
    actor: str
    action: str  # set | add_row | delete_row | move_row | accept | confirm | reopen | restart
    revision: int
    sheet: str | None = None
    workbook_sheet: str | None = None
    cell: str | None = None
    row_id: str | None = None
    field: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    old_code: str | None = None
    new_code: str | None = None
    detail: str | None = None
