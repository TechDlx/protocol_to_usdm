import pytest

from backend.pipeline.workbook.layout import (
    ARMS,
    DATES,
    ELIGIBILITY,
    SHEETS,
    STUDY,
    SheetKind,
    column_letter,
)


@pytest.mark.parametrize(
    ("index", "letter"), [(0, "A"), (25, "Z"), (26, "AA"), (27, "AB"), (701, "ZZ"), (702, "AAA")]
)
def test_column_letters(index: int, letter: str) -> None:
    assert column_letter(index) == letter


def test_table_cell_references_skip_the_header_row() -> None:
    assert ARMS.cell("name", 0) == "studyDesignArms!A2"
    assert ARMS.cell("data_origin_type", 2) == "studyDesignArms!F4"
    assert ELIGIBILITY.cell("text", 0) == "studyDesignEligibilityCriteria!F2"


def test_key_value_cells_are_column_b_at_the_key_row() -> None:
    assert STUDY.kind == SheetKind.KEY_VALUE
    assert STUDY.cell("name", None) == "study!B1"
    assert STUDY.cell("protocol_status", None) == "study!B15"


def test_dates_are_the_legacy_table_below_the_study_block() -> None:
    # usdm4-excel StudySheet: a blank key row ends the block, the next row is the header.
    assert DATES.workbook_sheet == "study"
    assert DATES.first_row == len(STUDY.columns) + 3
    assert [c.header for c in DATES.columns] == [
        "category",
        "name",
        "description",
        "label",
        "type",
        "date",
        "scopes",
    ]
    assert DATES.cell("category", 0) == "study!A18"
    assert DATES.cell("date", 1) == "study!F19"


def test_required_flags_follow_the_usdm4_model() -> None:
    from usdm4.api.governance_date import GovernanceDate
    from usdm4.api.study import Study

    assert not STUDY.column("description").required
    assert not STUDY.column("label").required
    assert Study.model_fields["name"].is_required() == STUDY.column("name").required
    for header, attribute in (("name", "name"), ("type", "type"), ("date", "dateValue")):
        column = next(c for c in DATES.columns if c.header == header)
        assert column.required == GovernanceDate.model_fields[attribute].is_required()


def test_every_modelled_field_exists_on_its_record() -> None:
    from backend.pipeline.workbook.sources import record_class

    for key, spec in SHEETS.items():
        fields = record_class(spec).model_fields
        for column in spec.columns:
            if column.field is not None:
                assert column.field in fields, f"{key}.{column.field}"


def test_terminology_columns_have_codelists() -> None:
    from backend.pipeline.terminology.ct import get_ct_resolver

    resolver = get_ct_resolver()
    for spec in SHEETS.values():
        for column in spec.columns:
            if column.ct is not None:
                assert resolver.codelist(column.ct)["terms"]
