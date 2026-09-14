import pytest

from backend.models.extraction import (
    ExtractedField,
    ExtractionSheets,
    IdentifiersSheet,
    ObjectiveEndpointRecord,
    TerminologyStatus,
)
from backend.models.review import IssueKind
from backend.pipeline.review.validation import validate_review
from backend.pipeline.terminology.ct import (
    AMENDMENT_REASON,
    TRIAL_INTENT_TYPES,
    CtResolver,
    get_ct_resolver,
)
from backend.pipeline.workbook import formats
from backend.pipeline.workbook.cells import other_problem, resolve_cell
from backend.pipeline.workbook.formats import ValueFormat
from backend.pipeline.workbook.layout import (
    AMENDMENTS,
    DATES,
    IDENTIFIERS,
    OBJECTIVES_ENDPOINTS,
    ORGANIZATIONS,
)
from backend.pipeline.workbook.sources import empty_record, ensure_rows, sheet_rows


@pytest.fixture(scope="module")
def resolver() -> CtResolver:
    return get_ct_resolver()


@pytest.mark.parametrize(
    ("fmt", "value", "expected"),
    [
        (ValueFormat.BOOLEAN, "Y", None),
        (ValueFormat.BOOLEAN, "maybe", True),
        (ValueFormat.DATE, "2015-10-20", None),
        (ValueFormat.DATE, "20 October 2015", True),
        (ValueFormat.QUANTITY, "125 mg", None),
        (ValueFormat.QUANTITY, "24 WEEKS", None),
        (ValueFormat.QUANTITY, "300", None),
        (ValueFormat.QUANTITY, "125 mg once daily", True),  # the importer would read a bad unit
        (ValueFormat.QUANTITY, "2.5 mg", False),  # importer truncates decimals: warning only
        (ValueFormat.RANGE, "18..75 YEARS", None),
        (ValueFormat.RANGE, "18..75 years", None),  # the importer tolerates plural unit words
        (ValueFormat.RANGE, "18..75", True),
        (ValueFormat.RANGE, "18 YEARS", True),
        (ValueFormat.COUNT, "300", None),
        (ValueFormat.COUNT, "280..320", None),
        (ValueFormat.COUNT, "about 300", True),
        (ValueFormat.GEOGRAPHIC_SCOPE, "Global", None),
        (ValueFormat.GEOGRAPHIC_SCOPE, "Region: Europe, Country: GBR", None),
        (ValueFormat.GEOGRAPHIC_SCOPE, "Europe", True),
        (ValueFormat.ENROLLMENT, "Global: 300", None),
        (ValueFormat.ENROLLMENT, "Country: GBR=40", None),
        (ValueFormat.ENROLLMENT, "300", True),
    ],
)
def test_format_checks_mirror_the_importer(
    resolver: CtResolver, fmt: ValueFormat, value: str, expected: bool | None
) -> None:
    result = formats.check(fmt, value, resolver)
    assert (result[0] if result else None) == expected


def test_empty_values_pass_format_checks(resolver: CtResolver) -> None:
    assert all(formats.check(f, "", resolver) is None for f in ValueFormat)


def test_formatting_helpers_use_cdisc_unit_submission_values(resolver: CtResolver) -> None:
    assert formats.format_quantity("125", "mg", resolver) == "125 mg"
    assert formats.format_quantity("24", "weeks", resolver) == "24 WEEKS"
    assert formats.format_quantity("5", "widgets", resolver) == "5 widgets"  # kept, review flags it
    assert formats.format_range("18", "75", "years", resolver) == "18..75 YEARS"
    assert formats.format_range("18", None, "years", resolver) is None  # open range: no form
    assert formats.number_text("1,000") == "1000"
    assert formats.format_boolean(False) == "N"


def test_multi_value_terminology_is_exact_only_when_every_item_is(resolver: CtResolver) -> None:
    both = resolver.resolve_many("Treatment Study, Prevention Study", TRIAL_INTENT_TYPES)
    assert both is not None and both.status == TerminologyStatus.EXACT
    assert both.code is not None and both.code.count(",") == 1
    partial = resolver.resolve_many("Treatment Study, cure-ish", TRIAL_INTENT_TYPES)
    assert (
        partial is not None and partial.status != TerminologyStatus.EXACT and partial.code is None
    )


def test_other_reasons_resolve_to_the_other_term(resolver: CtResolver) -> None:
    column = AMENDMENTS.column("primary_reason")
    resolved = resolve_cell(column, "Other=Fix typographical errors", resolver)
    assert resolved is not None and resolved.status == TerminologyStatus.EXACT
    assert resolved.code == resolver.resolve("Other", AMENDMENT_REASON).code  # type: ignore[union-attr]
    assert other_problem(column, "Other") is not None
    assert other_problem(column, "Other=reason") is None
    secondary = AMENDMENTS.column("secondary_reasons")
    both = resolve_cell(secondary, "IRB/IEC Feedback, Other=typos", resolver)
    assert both is not None and both.status == TerminologyStatus.EXACT


def test_dotted_sources_create_their_containers() -> None:
    sheets = ExtractionSheets()
    assert sheet_rows(sheets, ORGANIZATIONS) == []
    rows = ensure_rows(sheets, IDENTIFIERS)
    assert rows == [] and isinstance(sheets.identifiers, IdentifiersSheet)
    assert ensure_rows(sheets, DATES) is None  # no study record to hold dates


def _oe(**values: str) -> ObjectiveEndpointRecord:
    record = empty_record(OBJECTIVES_ENDPOINTS, row_id=values.pop("row_id"))
    for name, value in values.items():
        setattr(record, name, ExtractedField(value=value))
    return record  # type: ignore[return-value]


def test_two_level_sheets_require_group_columns_only_where_the_group_starts(
    resolver: CtResolver,
) -> None:
    first = _oe(row_id="oe-1", objective_text="To assess", endpoint_text="PFS")
    continuation = _oe(row_id="oe-2", endpoint_name="END2", endpoint_text="OS")
    empty = _oe(row_id="oe-3")
    validation = validate_review(
        ExtractionSheets(objectives_endpoints=[first, continuation, empty]), 0.7, resolver
    )
    missing = {
        (i.row_id, i.field) for i in validation.issues if i.kind == IssueKind.MISSING_REQUIRED
    }
    assert ("oe-1", "objective_name") in missing and ("oe-1", "endpoint_level") in missing
    assert not any(row == "oe-2" and field.startswith("objective") for row, field in missing)
    assert ("oe-2", "endpoint_level") in missing
    structure = [i for i in validation.issues if i.kind == IssueKind.INVALID_STRUCTURE]
    assert [(i.row_id, i.message.split(" is")[0]) for i in structure] == [("oe-3", "row 4")]


def test_a_two_level_sheet_must_start_with_its_leading_group(resolver: CtResolver) -> None:
    validation = validate_review(
        ExtractionSheets(objectives_endpoints=[_oe(row_id="oe-1", endpoint_text="PFS")]),
        0.7,
        resolver,
    )
    assert any(
        i.kind == IssueKind.INVALID_STRUCTURE and "first row" in i.message
        for i in validation.issues
    )


def test_format_and_choice_problems_block(resolver: CtResolver) -> None:
    from backend.models.extraction import GovernanceDateRecord, StudyRecord
    from backend.pipeline.workbook.layout import STUDY

    study = empty_record(STUDY)
    date = empty_record(DATES, row_id="date-1")
    assert isinstance(study, StudyRecord) and isinstance(date, GovernanceDateRecord)
    date.category = ExtractedField(value="approval")
    date.date = ExtractedField(value="1 Jan 2020")
    study.governance_dates = [date]
    issues = validate_review(ExtractionSheets(study=study), 0.7, resolver).issues
    formats_found = {i.field for i in issues if i.kind == IssueKind.INVALID_FORMAT}
    assert formats_found == {"category", "date"}
