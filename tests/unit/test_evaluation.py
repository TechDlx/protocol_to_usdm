"""The evaluation harness: reading workbooks back, alignment, tiered scoring, results files."""

import copy
import json
from pathlib import Path

import pytest

from backend.models.extraction import AgentStatus
from backend.pipeline.evaluation.cli import REFERENCE_WORKBOOK, main
from backend.pipeline.evaluation.compare import evaluate, normal_value, text_similarity
from backend.pipeline.evaluation.reader import WorkbookTables, read_workbook
from backend.pipeline.evaluation.report import EvalReport, Tier, render_markdown
from backend.pipeline.evaluation.runner import (
    EVAL_STUDY_NAME,
    extraction_summary,
    run_stage_a,
    write_unreviewed_workbook,
)
from backend.pipeline.extract import load_extraction
from backend.pipeline.terminology.ct import CtResolver, get_ct_resolver
from backend.pipeline.workbook.layout import SCHEDULE, TIMEPOINTS
from backend.pipeline.workbook.sources import sheet_rows
from tests.fixtures.extracted_run import build_extracted_run
from tests.fixtures.fake_llm import FakeLlm, synthetic_responders

FIELDS = {
    "generated_at": "2026-01-01T00:00:00Z",
    "reference": "reference.xlsx",
    "reference_sha256": "r",
    "generated": "generated.xlsx",
    "generated_sha256": "g",
    "source": "test",
}


@pytest.fixture(scope="module")
def resolver() -> CtResolver:
    return get_ct_resolver()


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, WorkbookTables]:
    run_dir, _ = build_extracted_run(tmp_path_factory.mktemp("evaluation"))
    extraction = load_extraction(run_dir)
    assert extraction is not None
    path, _ = write_unreviewed_workbook(run_dir, extraction)
    return run_dir, read_workbook(path)


def score(generated: WorkbookTables, reference: WorkbookTables, resolver: CtResolver) -> EvalReport:
    return evaluate(generated, reference, resolver, **FIELDS)


# ----- normalisation -------------------------------------------------------------------------


def test_values_and_text_are_normalised() -> None:
    assert normal_value("2 Weeks") == normal_value("2 week") == "2 week"
    assert normal_value("50..100 YEARS") == normal_value("50 .. 100 years")
    assert normal_value("TRUE") == normal_value("Y") == normal_value("yes")
    assert normal_value("2015-10-20 00:00:00") == normal_value("2015-10-20")
    assert text_similarity("<p>ANC &lt; 1.5</p>", "ANC < 1.5.") == 1.0
    assert text_similarity("01", "1") == 1.0
    assert text_similarity("Placebo", "Placebo TTS twice daily") < 0.85


# ----- reading ---------------------------------------------------------------------------------


def test_the_reader_returns_what_the_writer_wrote(synthetic) -> None:  # type: ignore[no-untyped-def]
    run_dir, tables = synthetic
    extraction = load_extraction(run_dir)
    assert extraction is not None
    sheets = extraction.sheets
    assert tables.missing_sheets == [] and not tables.unmodelled
    assert tables.sheets["study"][0]["officialTitle"] == sheets.study.official_title.value  # type: ignore[union-attr]
    assert len(tables.sheets["timepoints"]) == len(sheet_rows(sheets, TIMEPOINTS))
    written = {r.activity.value: r.scheduled_at.value for r in sheet_rows(sheets, SCHEDULE)}
    assert {r["activity"]: r["scheduledAt"] for r in tables.sheets["schedule"]} == written
    assert [r["name"] for r in tables.sheets["encounters"]] == [
        e.name.value
        for e in sheets.schedule.encounters  # type: ignore[union-attr]
    ]


@pytest.mark.skipif(not REFERENCE_WORKBOOK.is_file(), reason="reference workbook not present")
def test_the_reference_workbook_scores_perfectly_against_itself(resolver: CtResolver) -> None:
    reference = read_workbook(REFERENCE_WORKBOOK)
    assert len(reference.sheets["timepoints"]) > 10 and reference.sheets["study_cells"]
    assert reference.unmodelled["studyDesignProcedures"] > 0  # outside the pipeline's scope
    report = score(reference, reference, resolver)
    assert report.overall.accuracy == 1.0
    assert all(sheet.tally.accuracy in (1.0, None) for sheet in report.sheets)
    assert report.recall_including_out_of_scope is not None
    assert report.recall_including_out_of_scope < 1.0


# ----- scoring ---------------------------------------------------------------------------------


def test_identical_workbooks_score_perfectly(synthetic, resolver: CtResolver) -> None:  # type: ignore[no-untyped-def]
    _, tables = synthetic
    report = score(tables, tables, resolver)
    assert (report.overall.accuracy, report.overall.precision, report.overall.recall) == (
        1.0,
        1.0,
        1.0,
    )
    assert report.overall.gold > 50
    assert {Tier.CODE, Tier.REFERENCE, Tier.VALUE, Tier.TEXT} <= set(report.tiers)


def test_names_and_order_do_not_matter_but_what_they_point_at_does(
    synthetic, resolver: CtResolver
) -> None:  # type: ignore[no-untyped-def]
    _, tables = synthetic
    reference = copy.deepcopy(tables)
    renamed = {"Screening": "V1", "Day 1": "V2", "Week 4": "V3", "Week 8": "V4"}
    for row in reference.sheets["timepoints"]:
        for header in ("name", "default"):
            row[header] = renamed.get(row[header], row[header])
    for row in reference.sheets["timings"]:
        for header in ("from", "to"):
            row[header] = renamed.get(row[header], row[header])
    for row in reference.sheets["schedule"]:
        row["scheduledAt"] = ", ".join(renamed[n] for n in row["scheduledAt"].split(", "))
    reference.sheets["eligibility_criteria"].reverse()

    report = score(tables, reference, resolver)
    assert report.overall.accuracy == 1.0
    assert report.identifiers.accuracy is not None and report.identifiers.accuracy < 1.0

    # One mark moved to another visit: one mark missing, one extra.
    row = next(r for r in reference.sheets["schedule"] if len(r["scheduledAt"].split(", ")) < 4)
    marks = row["scheduledAt"].split(", ")
    other = next(n for n in renamed.values() if n not in marks)
    row["scheduledAt"] = ", ".join([*marks[1:], other])
    schedule = next(s for s in score(tables, reference, resolver).sheets if s.key == "schedule")
    assert schedule.tally.gold == schedule.tally.generated
    assert schedule.tally.matched == schedule.tally.gold - 1


def test_terminology_is_compared_as_codes_and_values_normalised(
    synthetic, resolver: CtResolver
) -> None:  # type: ignore[no-untyped-def]
    _, tables = synthetic
    reference = copy.deepcopy(tables)
    reference.sheets["epochs"][0]["type"] = "SCREENING"  # submission value, not preferred term
    reference.sheets["encounters"][0]["contactModes"] = "C175574"  # the C-code of In Person
    reference.sheets["populations"][0]["plannedSexOfParticipants"] = "BOTH"
    timing = next(r for r in reference.sheets["timings"] if r["timingValue"] != "0 days")
    timing["timingValue"] = timing["timingValue"].upper().replace("DAYS", "Days")
    report = score(tables, reference, resolver)
    assert report.overall.accuracy == 1.0

    reference.sheets["epochs"][0]["type"] = "Follow-Up Epoch"
    epochs = next(s for s in score(tables, reference, resolver).sheets if s.key == "epochs")
    assert epochs.tally.matched == epochs.tally.gold - 1
    assert any(d.column == "type" and d.outcome == "mismatch" for d in epochs.units[0].differences)


def test_missing_and_extra_rows_affect_recall_and_precision(
    synthetic, resolver: CtResolver
) -> None:  # type: ignore[no-untyped-def]
    _, tables = synthetic
    fewer = copy.deepcopy(tables)
    fewer.sheets["eligibility_criteria"].pop()
    report = score(fewer, tables, resolver)
    assert report.overall.precision == 1.0
    assert report.overall.recall is not None and report.overall.recall < 1.0
    report = score(tables, fewer, resolver)
    assert report.overall.recall == 1.0
    assert report.overall.precision is not None and report.overall.precision < 1.0
    criteria = next(s for s in report.sheets if s.key == "eligibility_criteria")
    assert any(d.outcome == "extra" for d in criteria.units[0].differences)


# ----- Stage A and the command line -------------------------------------------------------


def test_stage_a_runs_without_review_and_resumes(tmp_path: Path, resolver: CtResolver) -> None:
    (tmp_path / "template").mkdir()
    _, pdf = build_extracted_run(tmp_path / "template")
    workspace = tmp_path / "workspace"
    llm = FakeLlm(synthetic_responders())
    first = run_stage_a(workspace, pdf, llm, resolver)
    assert first.sheets.study is not None and first.sheets.study.name.value
    assert EVAL_STUDY_NAME.split()[0].casefold() in first.sheets.study.name.value.casefold()
    # Agents that called the model (a sheet with no relevant section is left empty without one).
    done = {
        sheet
        for sheet, run in first.agents.items()
        if run.status == AgentStatus.DONE and run.usage is not None
    }
    assert done and extraction_summary(first, workspace)["model calls this run"]

    second = run_stage_a(workspace, pdf, llm, resolver)
    assert all(second.agents[sheet].status == AgentStatus.SKIPPED for sheet in done)
    summary = extraction_summary(second, workspace)
    assert (
        summary["cost of the stored outputs (USD)"]
        == extraction_summary(first, workspace)["cost this run (USD)"]
    )
    path, _ = write_unreviewed_workbook(workspace, second)
    assert path.is_file()


@pytest.mark.skipif(not REFERENCE_WORKBOOK.is_file(), reason="reference workbook not present")
def test_the_command_line_writes_timestamped_results_with_deltas(synthetic, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    run_dir, _ = synthetic
    results = tmp_path / "results"
    extraction = load_extraction(run_dir)
    assert extraction is not None
    # The synthetic model has no answer for some agents: scored, but reported as not clean.
    failed = any(a.status == AgentStatus.FAILED for a in extraction.agents.values())
    assert main(["--run-dir", str(run_dir), "--results", str(results)]) == int(failed)
    assert main(["--workbook", str(REFERENCE_WORKBOOK), "--results", str(results)]) == 0
    files = sorted(results.glob("*.json"))
    assert len(files) == 2
    latest = EvalReport.model_validate_json(files[-1].read_text(encoding="utf-8"))
    assert latest.overall.accuracy == 1.0 and latest.reference.endswith("CDISC_Pilot_Study.xlsx")
    markdown = files[-1].with_suffix(".md").read_text(encoding="utf-8")
    assert "| Field-level accuracy | 100.0%" in markdown
    assert json.loads(files[0].read_text(encoding="utf-8"))["sheets"]
    assert render_markdown(latest, latest).count("(+0.0)") > 0
