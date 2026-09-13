"""Stage B: cell conventions, sheet layouts and the confirmation gate. The importer round trip is
in test_usdm_stage.py, which imports this workbook with usdm4-excel."""

import shutil
from pathlib import Path

import pytest
from openpyxl import load_workbook

from backend.models.extraction import ExtractedField, TerminologyResolution, TerminologyStatus
from backend.models.review import SetValue
from backend.pipeline.review.service import ReviewService
from backend.pipeline.terminology.ct import get_ct_resolver
from backend.pipeline.workbook.cells import resolve_cell
from backend.pipeline.workbook.layout import (
    AMENDMENTS,
    DATES,
    ELIGIBILITY,
    INTERVENTIONS,
    SHEETS,
    STUDY,
    STUDY_DESIGN,
)
from backend.pipeline.workbook.stage import (
    REPORT_FILE,
    WorkbookNotReadyError,
    generate_workbook,
    load_report,
    workbook_path,
)
from backend.pipeline.workbook.writer import ACTIVITY_HEADER, TIMELINE_HEADINGS, cell_text
from tests.fixtures.extracted_run import build_extracted_run


def field(value: str, terminology: TerminologyResolution | None = None) -> ExtractedField[str]:
    return ExtractedField(value=value, terminology=terminology)


# ----- cell conventions ------------------------------------------------------------------------


def test_terminology_cells_are_written_as_preferred_terms() -> None:
    resolver = get_ct_resolver()
    column = STUDY_DESIGN.column("study_phase")
    # "PHASE II TRIAL" is the submission value; the importer matches preferred terms.
    value = field("phase ii trial", resolve_cell(column, "phase ii trial", resolver))
    assert value.terminology and value.terminology.status == TerminologyStatus.EXACT
    assert cell_text(column, value) == value.terminology.preferred_term


def test_multi_valued_and_other_reason_cells() -> None:
    resolver = get_ct_resolver()
    characteristics = STUDY_DESIGN.column("characteristics")
    text = "randomized controlled clinical trial, multicenter study"
    assert (
        cell_text(characteristics, field(text, resolve_cell(characteristics, text, resolver)))
        == "Randomized Controlled Clinical Trial, Multicenter Study"
    )
    reasons = AMENDMENTS.column("secondary_reasons")
    text = "irb/iec feedback, Other=Typographical fixes"
    written = cell_text(reasons, field(text, resolve_cell(reasons, text, resolver)))
    # The importer splits reasons on "," without trimming: no spaces after the separators.
    assert written == "IRB/IEC Feedback,Other=Typographical fixes"


def test_unresolved_terminology_is_written_as_reviewed() -> None:
    column = STUDY.column("protocol_status")
    unresolved = TerminologyResolution(
        status=TerminologyStatus.UNRESOLVED, codelist="C1", codelist_name="x", ct_version="v"
    )
    assert cell_text(column, field("Nearly final", unresolved)) == "Nearly final"


def test_dates_and_xhtml_text() -> None:
    assert cell_text(DATES.column("date"), field("2015-10-20")) == "2015-10-20 00:00:00"
    assert (
        cell_text(ELIGIBILITY.column("text"), field("ANC < 1.5 & platelets > 100"))
        == "<p>ANC &lt; 1.5 &amp; platelets &gt; 100</p>"
    )
    assert cell_text(STUDY.column("name"), ExtractedField()) == ""


# ----- a synthetic run through review, confirmation and the writer -----------------------------

REQUIRED_FILL = [
    SetValue(sheet="study", field="study_version", value="1.0"),
    SetValue(sheet="study", field="protocol_status", value="Final"),
    SetValue(sheet="organizations", row_id="org-1", field="identifier_scheme", value="DUNS"),
    SetValue(sheet="organizations", row_id="org-1", field="identifier", value="123456789"),
    SetValue(sheet="study_design", field="rationale", value="To test the writer."),
    SetValue(sheet="study_design", field="intervention_model", value="Parallel Study"),
]


@pytest.fixture(scope="module")
def template_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    run_dir, _ = build_extracted_run(tmp_path_factory.mktemp("extracted"))
    return run_dir


@pytest.fixture
def review(template_run: Path, tmp_path: Path) -> ReviewService:
    run_dir = tmp_path / "run"
    shutil.copytree(template_run, run_dir)
    return ReviewService(run_dir, get_ct_resolver(), confidence_threshold=0.7)


def _confirmed(review: ReviewService) -> int:
    review.open()
    doc = review.apply(0, REQUIRED_FILL)
    blocking = [
        i.message for i in review.state().validation.issues if i.severity.value == "blocking"
    ]
    assert blocking == []
    return review.confirm(doc.revision).revision


def test_the_gate_refuses_unreviewed_and_unconfirmed_reviews(review: ReviewService) -> None:
    with pytest.raises(WorkbookNotReadyError, match="not been reviewed"):
        generate_workbook(review.run_dir, "syn", review)
    review.open()
    with pytest.raises(WorkbookNotReadyError, match="not confirmed"):
        generate_workbook(review.run_dir, "syn", review)
    assert not (review.run_dir / REPORT_FILE).exists()


def test_the_gate_refuses_blocking_issues_even_after_confirmation(review: ReviewService) -> None:
    revision = _confirmed(review)
    # Break a required value directly in the saved review, bypassing the confirm check.
    doc = review.document()
    assert doc is not None and doc.sheets.study is not None
    doc.sheets.study.study_version = ExtractedField()
    from backend.pipeline.review.service import REVIEWED_FILE
    from backend.storage.fs import write_model

    write_model(review.run_dir / REVIEWED_FILE, doc)
    with pytest.raises(WorkbookNotReadyError) as raised:
        generate_workbook(review.run_dir, "syn", review)
    assert any(i.field == "study_version" for i in raised.value.issues)
    assert revision == doc.revision


@pytest.fixture
def written(review: ReviewService) -> tuple[ReviewService, Path]:
    revision = _confirmed(review)
    report = generate_workbook(review.run_dir, "syn", review)
    assert report.review_revision == revision and not report.reused
    return review, workbook_path(review.run_dir, "syn")


def test_regenerating_an_unchanged_review_reuses_the_workbook(written) -> None:  # type: ignore[no-untyped-def]
    review, path = written
    again = generate_workbook(review.run_dir, "syn", review)
    assert again.reused and again.sha256 == load_report(review.run_dir).sha256  # type: ignore[union-attr]
    forced = generate_workbook(review.run_dir, "syn", review, force=True)
    assert not forced.reused and path.is_file()


def test_sheet_layouts_match_the_importer(written) -> None:  # type: ignore[no-untyped-def]
    _, path = written
    book = load_workbook(path)
    table_sheets = {
        s.workbook_sheet
        for s in SHEETS.values()
        if s.key
        not in {
            "study",
            "dates",
            "study_design",
            "timelines",
            "timepoints",
            "schedule",
            "study_cells",
        }
    }
    assert table_sheets <= set(book.sheetnames)
    assert INTERVENTIONS.workbook_sheet == "studyInterventions"
    for name in table_sheets:
        spec = next(s for s in SHEETS.values() if s.workbook_sheet == name)
        headers = [c.value for c in book[name][1]][: len(spec.columns)]
        assert headers == [c.header for c in spec.columns], name

    study = book["study"]
    keys = [study.cell(row=r, column=1).value for r in range(1, len(STUDY.columns) + 1)]
    assert keys == [c.header for c in STUDY.columns]
    assert study.cell(row=len(STUDY.columns) + 1, column=1).value is None  # the blank row
    assert [c.value for c in study[len(STUDY.columns) + 2]][:7] == [c.header for c in DATES.columns]
    assert study["B4"].value == "1.0" and study["B15"].value == "Final"

    design = book["studyDesign"]
    rows = {
        design.cell(row=r, column=1).value: design.cell(row=r, column=2).value
        for r in range(1, design.max_row + 1)
    }
    assert rows["mainTimeline"] == "main-timeline" and rows["otherTimelines"] is None  # empty
    assert rows["interventionModel"] == "Parallel Study"

    timeline = book["main-timeline"]
    assert [timeline.cell(row=r, column=1).value for r in range(1, 5)] == [
        "Name",
        "Label",
        "Description",
        "Condition",
    ]
    assert [timeline.cell(row=r, column=3).value for r in range(1, 10)] == list(TIMELINE_HEADINGS)
    assert [timeline.cell(row=1, column=c).value for c in range(4, 8)] == [
        "Screening",
        "Day 1",
        "Week 4",
        "Week 8",
    ]
    assert timeline.cell(row=5, column=7).value == "(EXIT)"
    assert [timeline.cell(row=10, column=c).value for c in range(1, 4)] == list(ACTIVITY_HEADER)
    ecg = next(
        r for r in range(11, timeline.max_row + 1) if timeline.cell(row=r, column=2).value == "ECG"
    )
    assert [timeline.cell(row=ecg, column=c).value for c in range(4, 8)] == ["X", None, "X", None]

    criteria = book["studyDesignEligibilityCriteria"]
    assert str(criteria["F2"].value).startswith("<p>")


def test_the_same_review_writes_a_byte_identical_workbook(written) -> None:  # type: ignore[no-untyped-def]
    review, path = written
    first = load_report(review.run_dir)
    import time

    time.sleep(1.1)  # zip entry times have two-second resolution; make a difference visible
    second = generate_workbook(review.run_dir, "syn", review, force=True)
    assert first is not None and second.sha256 == first.sha256
    assert load_workbook(path).properties.creator == "protocol_to_usdm"
