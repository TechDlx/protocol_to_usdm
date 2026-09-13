"""Blocking issues and warnings for the review page.

Blocking (Confirm stays disabled): missing required fields, controlled terminology that is not an
exact match, and reference problems (duplicate or missing names, dangling references).
Warnings (shown, not blocking): extracted values below the confidence threshold or with an
unverified source, unless a reviewer accepted or edited them.
"""

from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel

from backend.models.extraction import (
    ExtractedField,
    ExtractionSheets,
    ReferenceIssueKind,
    TerminologyStatus,
    ValueOrigin,
)
from backend.models.review import IssueKind, IssueSeverity, ReviewIssue, ReviewValidation
from backend.pipeline.identifiers.references import validate_references
from backend.pipeline.workbook.layout import SHEETS, SheetKind, SheetSpec


def sheet_rows(sheets: ExtractionSheets, spec: SheetSpec) -> list[Any]:
    """The records shown on a sheet: one object for key/value sheets, a list for tables."""
    if spec.source == "study":
        return [sheets.study] if sheets.study is not None else []
    if spec.source == "study.governance_dates":
        return sheets.study.governance_dates if sheets.study is not None else []
    rows = getattr(sheets, spec.source)
    return rows if rows is not None else []


def _fields(
    spec: SheetSpec, rows: list[Any]
) -> Iterator[tuple[int, BaseModel, str, ExtractedField[str]]]:
    for index, row in enumerate(rows):
        for column in spec.columns:
            if column.field is None:
                continue
            yield index, row, column.field, getattr(row, column.field)


_REFERENCE_KIND = {
    ReferenceIssueKind.DUPLICATE_NAME: IssueKind.DUPLICATE_NAME,
    ReferenceIssueKind.MISSING_NAME: IssueKind.MISSING_NAME,
    ReferenceIssueKind.DANGLING_REFERENCE: IssueKind.DANGLING_REFERENCE,
}


def _field_issues(
    spec: SheetSpec, index: int, row: Any, field: str, value: ExtractedField[str], threshold: float
) -> list[tuple[IssueSeverity, IssueKind, str]]:
    column = spec.column(field)
    found: list[tuple[IssueSeverity, IssueKind, str]] = []
    if value.is_empty:
        if column.required:
            found.append(
                (IssueSeverity.BLOCKING, IssueKind.MISSING_REQUIRED, f"{column.header} is required")
            )
        return found
    if column.ct is not None:
        t = value.terminology
        if t is None or t.status != TerminologyStatus.EXACT or not t.code:
            status = t.status.value if t else "not resolved"
            found.append(
                (
                    IssueSeverity.BLOCKING,
                    IssueKind.TERMINOLOGY_NOT_EXACT,
                    f"{column.header} '{value.value}' is {status} against the codelist; "
                    "pick a term",
                )
            )
    p = value.provenance
    if p is None or p.origin != ValueOrigin.EXTRACTED or p.reviewer_accepted:
        return found
    if p.confidence < threshold:
        found.append(
            (
                IssueSeverity.WARNING,
                IssueKind.LOW_CONFIDENCE,
                f"{column.header}: confidence {p.confidence:.2f} is below {threshold:.2f}",
            )
        )
    if not p.verified:
        found.append(
            (
                IssueSeverity.WARNING,
                IssueKind.UNVERIFIED_SOURCE,
                f"{column.header}: the source quote could not be verified in the protocol",
            )
        )
    return found


def validate_review(sheets: ExtractionSheets, confidence_threshold: float) -> ReviewValidation:
    issues: list[ReviewIssue] = []

    for spec in SHEETS.values():
        for index, row, field, value in _fields(spec, sheet_rows(sheets, spec)):
            row_id = None if spec.kind == SheetKind.KEY_VALUE else getattr(row, "row_id", None)
            for severity, kind, message in _field_issues(
                spec, index, row, field, value, confidence_threshold
            ):
                issues.append(
                    ReviewIssue(
                        severity=severity,
                        kind=kind,
                        sheet=spec.key,
                        row_id=row_id,
                        field=field,
                        cell=spec.cell(field, index),
                        message=message,
                    )
                )

    for ref in validate_references(sheets).issues:
        if not ref.anchors:
            issues.append(
                ReviewIssue(
                    severity=IssueSeverity.BLOCKING,
                    kind=_REFERENCE_KIND[ref.kind],
                    sheet="",
                    row_id=None,
                    field=None,
                    cell=None,
                    message=ref.message,
                )
            )
        for anchor in ref.anchors:
            anchor_spec = SHEETS.get(anchor.sheet)
            cell: str | None = None
            if anchor_spec is not None:
                rows = sheet_rows(sheets, anchor_spec)
                position = next(
                    (i for i, r in enumerate(rows) if getattr(r, "row_id", None) == anchor.row_id),
                    0,
                )
                cell = anchor_spec.cell(anchor.field, position)
            issues.append(
                ReviewIssue(
                    severity=IssueSeverity.BLOCKING,
                    kind=_REFERENCE_KIND[ref.kind],
                    sheet=anchor.sheet,
                    row_id=anchor.row_id if anchor.sheet != "study" else None,
                    field=anchor.field,
                    cell=cell,
                    message=ref.message,
                )
            )

    blocking = sum(1 for i in issues if i.severity == IssueSeverity.BLOCKING)
    return ReviewValidation(blocking=blocking, warnings=len(issues) - blocking, issues=issues)
