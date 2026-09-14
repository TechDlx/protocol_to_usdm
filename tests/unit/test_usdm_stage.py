"""Stage C: the report's building blocks, and the synthetic review through import and validation."""

import json
import shutil
from pathlib import Path

import pytest
import usdm4
from simple_error_log.error_location import KlassMethodLocation
from simple_error_log.errors import Errors
from usdm4_excel.import_.excel_sheet_reader.sheet_location import SheetLocation

from backend.pipeline.review.service import ReviewService
from backend.pipeline.terminology.ct import get_ct_resolver
from backend.pipeline.usdm_gen.stage import (
    FINDING_NOTES,
    REPORT_FILE,
    FindingKind,
    UsdmNotReadyError,
    _import_issues,
    entity_counts,
    generate_usdm,
    load_report,
    usdm_path,
)
from backend.pipeline.workbook.stage import generate_workbook
from tests.fixtures.extracted_run import build_extracted_run
from tests.unit.test_workbook_writer import _confirmed


def test_stage_c_needs_a_written_workbook(tmp_path: Path) -> None:
    with pytest.raises(UsdmNotReadyError, match="workbook has not been generated"):
        generate_usdm(tmp_path, "syn")
    assert not (tmp_path / REPORT_FILE).exists()


def test_entity_counts_walk_the_whole_document() -> None:
    document = {
        "study": {
            "instanceType": "Study",
            "versions": [
                {"instanceType": "StudyVersion", "titles": [{"instanceType": "StudyTitle"}] * 2}
            ],
        },
        "usdmVersion": "4.0.0",
    }
    assert entity_counts(document) == {"Study": 1, "StudyTitle": 2, "StudyVersion": 1}


def test_import_issues_keep_errors_and_warnings_apart_with_locations() -> None:
    errors = Errors()
    errors.error("Bad cell", SheetLocation("studyDesign", 3, 1))
    errors.warning("notes not found but optional", KlassMethodLocation("a.b.BaseSheet", "__init__"))
    errors.info("read the study sheet")
    found_errors, warnings = _import_issues(errors)
    assert [(i.message, i.location) for i in found_errors] == [
        ("Bad cell", "sheet studyDesign, row 4, column 2")
    ]
    assert [(i.message, i.location) for i in warnings] == [
        ("notes not found but optional", "BaseSheet.__init__")
    ]
    assert _import_issues(None) == ([], [])


def test_finding_notes_name_rules_of_the_usdm4_library() -> None:
    library = Path(usdm4.__file__).parent / "rules" / "library"
    rules = {p.stem.removeprefix("rule_").upper() for p in library.glob("rule_*.py")}
    assert set(FINDING_NOTES) <= rules
    assert {kind for kind, _ in FINDING_NOTES.values()} == set(FindingKind)


@pytest.fixture(scope="module")
def template_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    run_dir, _ = build_extracted_run(tmp_path_factory.mktemp("extracted"))
    return run_dir


@pytest.mark.slow
def test_the_confirmed_synthetic_review_becomes_validated_usdm(
    template_run: Path, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    shutil.copytree(template_run, run_dir)
    review = ReviewService(run_dir, get_ct_resolver(), confidence_threshold=0.7)
    revision = _confirmed(review)
    workbook = generate_workbook(run_dir, "syn", review)

    report = generate_usdm(run_dir, "syn")
    assert [i.message for i in report.import_errors] == []
    assert report.file == "usdm/syn.json" and report.sha256
    document = json.loads(usdm_path(run_dir, "syn").read_text(encoding="utf-8"))
    assert document["usdmVersion"] == report.usdm_version == "4.0.0"
    assert (report.workbook_sha256, report.review_revision) == (workbook.sha256, revision)
    assert report.entities["Study"] == 1 and report.entities["ScheduleTimeline"] >= 1
    assert report.rules.rules > 200 and report.rules.exceptions == 0
    assert report.rules.findings == len(report.findings)
    assert report.core.ran or report.core.reason
    assert set(report.seconds) >= {"import", "rules"}
    # Every finding on the synthetic study (it has no objectives) is one the report explains.
    unexpected = [(f.rule_id, f.message) for f in report.findings if f.kind is None]
    assert unexpected == []
    assert load_report(run_dir) == report

    again = generate_usdm(run_dir, "syn")
    assert again.reused and again.sha256 == report.sha256
