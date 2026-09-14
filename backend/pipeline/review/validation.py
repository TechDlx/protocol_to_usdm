"""Blocking issues and warnings for the review page.

Blocking (Confirm stays disabled): missing required fields, controlled terminology that is not an
exact match, values the importer cannot parse (formats, fixed choices), badly structured two-level
sheets, and reference problems (duplicate or missing names, dangling references).
Warnings (shown, not blocking): extracted values below the confidence threshold or with an
unverified source, unless a reviewer accepted or edited them, and format quirks the importer
tolerates with a loss (such as decimals it truncates).
"""

from typing import Any

from backend.models.extraction import (
    ExtractedField,
    ExtractionSheets,
    ReferenceIssueKind,
    TerminologyStatus,
    ValueOrigin,
)
from backend.models.review import IssueKind, IssueSeverity, ReviewIssue, ReviewValidation
from backend.pipeline.identifiers.references import validate_references
from backend.pipeline.terminology.ct import CtResolver
from backend.pipeline.workbook import formats
from backend.pipeline.workbook.cells import other_problem
from backend.pipeline.workbook.layout import SHEETS, ColumnSpec, SheetKind, SheetSpec
from backend.pipeline.workbook.sources import group_active, sheet_rows

__all__ = ["sheet_rows", "validate_review"]

_REFERENCE_KIND = {
    ReferenceIssueKind.DUPLICATE_NAME: IssueKind.DUPLICATE_NAME,
    ReferenceIssueKind.MISSING_NAME: IssueKind.MISSING_NAME,
    ReferenceIssueKind.DANGLING_REFERENCE: IssueKind.DANGLING_REFERENCE,
}

Found = list[tuple[IssueSeverity, IssueKind, str]]


def _field_issues(
    column: ColumnSpec,
    value: ExtractedField[str],
    required: bool,
    threshold: float,
    resolver: CtResolver,
) -> Found:
    found: Found = []
    if value.is_empty:
        if required:
            found.append(
                (IssueSeverity.BLOCKING, IssueKind.MISSING_REQUIRED, f"{column.header} is required")
            )
        return found
    text = value.value or ""
    if column.ct is not None:
        t = value.terminology
        if t is None or t.status != TerminologyStatus.EXACT or not t.code:
            status = t.status.value if t else "not resolved"
            found.append(
                (
                    IssueSeverity.BLOCKING,
                    IssueKind.TERMINOLOGY_NOT_EXACT,
                    f"{column.header} '{text}' is {status} against the codelist; pick a term",
                )
            )
        problem = other_problem(column, text)
        if problem:
            found.append((IssueSeverity.BLOCKING, IssueKind.INVALID_FORMAT, problem))
    if column.bc:
        t = value.terminology
        if t is None or t.status != TerminologyStatus.EXACT:
            found.append(
                (
                    IssueSeverity.WARNING,
                    IssueKind.TERMINOLOGY_NOT_EXACT,
                    f"{column.header}: not every name is a CDISC Biomedical Concept; unmatched "
                    "names become concept surrogates without a definition",
                )
            )
    if column.choices and text.strip().casefold() not in {c.casefold() for c in column.choices}:
        found.append(
            (
                IssueSeverity.BLOCKING,
                IssueKind.INVALID_FORMAT,
                f"{column.header} must be one of: {', '.join(column.choices)}",
            )
        )
    if column.format is not None:
        checked = formats.check(column.format, text, resolver)
        if checked:
            blocking, message = checked
            found.append(
                (
                    IssueSeverity.BLOCKING if blocking else IssueSeverity.WARNING,
                    IssueKind.INVALID_FORMAT,
                    f"{column.header} {message}",
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


def _row_issues(
    spec: SheetSpec, index: int, row: Any, threshold: float, resolver: CtResolver
) -> list[ReviewIssue]:
    row_id = None if spec.kind == SheetKind.KEY_VALUE else getattr(row, "row_id", None)
    issues: list[ReviewIssue] = []

    def add(severity: IssueSeverity, kind: IssueKind, field: str | None, message: str) -> None:
        issues.append(
            ReviewIssue(
                severity=severity,
                kind=kind,
                sheet=spec.key,
                row_id=row_id,
                field=field,
                cell=spec.cell(field, index) if field else None,
                message=message,
            )
        )

    active = {group: group_active(spec, row, group) for group in spec.groups}
    if spec.groups and not any(active.values()):
        ungrouped = [c for c in spec.columns if c.field and not c.group]
        if not any(not getattr(row, c.field).is_empty for c in ungrouped):  # type: ignore[arg-type]
            first = spec.group_columns(spec.groups[-1])[0]
            add(
                IssueSeverity.BLOCKING,
                IssueKind.INVALID_STRUCTURE,
                first.field,
                f"row {spec.row_number(index)} is empty; fill it in or delete it",
            )
            return issues
    if spec.leading_group and index == 0 and not active.get(spec.leading_group, True):
        first = spec.group_columns(spec.leading_group)[0]
        add(
            IssueSeverity.BLOCKING,
            IssueKind.INVALID_STRUCTURE,
            first.field,
            f"the first row must start a new {spec.leading_group}; later rows without one "
            f"belong to the {spec.leading_group} above",
        )

    for column in spec.columns:
        if column.field is None:
            continue
        in_active_group = column.group is None or active[column.group]
        value: ExtractedField[str] = getattr(row, column.field)
        for severity, kind, message in _field_issues(
            column, value, column.required and in_active_group, threshold, resolver
        ):
            add(severity, kind, column.field, message)
    return issues


def validate_review(
    sheets: ExtractionSheets, confidence_threshold: float, resolver: CtResolver
) -> ReviewValidation:
    issues: list[ReviewIssue] = []

    for spec in SHEETS.values():
        for index, row in enumerate(sheet_rows(sheets, spec)):
            issues.extend(_row_issues(spec, index, row, confidence_threshold, resolver))

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
            key_value = anchor_spec is not None and anchor_spec.kind == SheetKind.KEY_VALUE
            issues.append(
                ReviewIssue(
                    severity=IssueSeverity.BLOCKING,
                    kind=_REFERENCE_KIND[ref.kind],
                    sheet=anchor.sheet,
                    row_id=None if key_value else anchor.row_id,
                    field=anchor.field,
                    cell=cell,
                    message=ref.message,
                )
            )

    blocking = sum(1 for i in issues if i.severity == IssueSeverity.BLOCKING)
    return ReviewValidation(blocking=blocking, warnings=len(issues) - blocking, issues=issues)
