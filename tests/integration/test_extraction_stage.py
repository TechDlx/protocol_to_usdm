"""The extraction stage end to end on the synthetic protocol, with a fake model client."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.models.extraction import AgentStatus, TerminologyStatus
from backend.models.run_config import RunConfig
from backend.models.study import StudyMeta
from backend.pipeline.extract import (
    EXTRACTION_FILE,
    PROVENANCE_FILE,
    REFERENCE_VALIDATION_FILE,
    RUN_LOG_FILE,
    run_extraction,
)
from backend.pipeline.ingest import run_ingestion
from backend.pipeline.llm import LlmRequest
from backend.pipeline.terminology.ct import get_ct_resolver
from tests.fixtures import synthetic_protocol
from tests.fixtures.fake_llm import FakeLlm, synthetic_responders

STUDY = StudyMeta(
    slug="synthetic",
    name="Synthetic, Study",
    created_at=datetime.now(UTC),
    updated_at=datetime.now(UTC),
)


@pytest.fixture
def parsed(tmp_path: Path):  # type: ignore[no-untyped-def]
    pdf = synthetic_protocol.build(tmp_path / "p.pdf")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    config = RunConfig(source_filename=pdf.name, page_image_dpi=50)
    result = run_ingestion(run_dir, pdf, config)
    return run_dir, result.document, result.mapping, config


def _extract(parsed, llm, **kwargs):  # type: ignore[no-untyped-def]
    run_dir, document, mapping, config = parsed
    return asyncio.run(
        run_extraction(run_dir, document, mapping, config, STUDY, llm, get_ct_resolver(), **kwargs)
    )


def test_agents_run_and_outputs_are_written(parsed) -> None:  # type: ignore[no-untyped-def]
    run_dir = parsed[0]
    extraction = _extract(parsed, FakeLlm(synthetic_responders()))

    assert extraction.agents["study"].status == AgentStatus.DONE
    assert extraction.agents["eligibility_criteria"].status == AgentStatus.DONE
    # The synthetic protocol has no design/intervention sections: that agent fails on its own.
    arms = extraction.agents["study_design_arms"]
    assert arms.status == AgentStatus.FAILED and "no protocol sections" in (arms.error or "")
    assert extraction.sheets.study_design_arms is None

    for name in (EXTRACTION_FILE, PROVENANCE_FILE, REFERENCE_VALIDATION_FILE, RUN_LOG_FILE):
        assert (run_dir / name).is_file(), name
    log_lines = (run_dir / RUN_LOG_FILE).read_text(encoding="utf-8").splitlines()
    assert {json.loads(line)["sheet"] for line in log_lines} == {"study", "eligibility_criteria"}
    assert all(json.loads(line)["cost_usd"] > 0 for line in log_lines)


def test_study_record_provenance_and_terminology(parsed) -> None:  # type: ignore[no-untyped-def]
    study = _extract(parsed, FakeLlm(synthetic_responders())).sheets.study
    assert study is not None

    title = study.official_title
    assert title.value == "A Phase 3 Trial of Examplumab"
    assert title.provenance and title.provenance.verified and title.provenance.source_page == 1

    status = study.protocol_status
    assert status.terminology and status.terminology.status == TerminologyStatus.EXACT
    assert status.terminology.code == "C25508"
    # ...but its quote is not in the protocol, so it is unverified and low confidence.
    assert status.provenance and not status.provenance.verified
    assert status.provenance.confidence <= 0.3

    assert study.name.value == "Synthetic Study"  # comma removed: names go in reference lists
    assert study.name.provenance and study.name.provenance.origin == "derived"


def test_eligibility_records(parsed) -> None:  # type: ignore[no-untyped-def]
    criteria = _extract(parsed, FakeLlm(synthetic_responders())).sheets.eligibility_criteria
    assert criteria is not None
    first, second = criteria

    assert [first.name.value, second.name.value] == ["IN01", "IN02"]
    assert first.category.terminology and first.category.terminology.code == "C25532"
    assert first.text.provenance and first.text.provenance.verified
    assert first.text.provenance.source_page == 5
    assert first.text.provenance.confidence == 0.95

    # The paraphrased criterion is caught by the whole-text verbatim check.
    assert second.text.provenance and second.text.provenance.confidence == 0.5
    assert "not a verbatim match" in (second.text.provenance.note or "")


def test_provenance_json_flags_values_for_review(parsed) -> None:  # type: ignore[no-untyped-def]
    run_dir = parsed[0]
    _extract(parsed, FakeLlm(synthetic_responders()))
    entries = json.loads((run_dir / PROVENANCE_FILE).read_text(encoding="utf-8"))
    by_field = {(e["sheet"], e["row"], e["field"]): e for e in entries}

    status = by_field[("study", None, "protocol_status")]
    assert status["needs_review"] and "source not verified" in status["review_reasons"]
    assert status["code"] == "C25508"
    title = by_field[("study", None, "official_title")]
    assert not title["needs_review"]
    paraphrased = by_field[("eligibility_criteria", 2, "text")]
    assert paraphrased["needs_review"]


def test_rerun_skips_unchanged_agents_without_calling_the_model(parsed) -> None:  # type: ignore[no-untyped-def]
    _extract(parsed, FakeLlm(synthetic_responders()))

    llm = FakeLlm(synthetic_responders())
    extraction = _extract(parsed, llm)
    assert llm.calls == []
    assert extraction.agents["study"].status == AgentStatus.SKIPPED
    assert extraction.sheets.study is not None

    forced = FakeLlm(synthetic_responders())
    _extract(parsed, forced, force=True)
    assert sorted(name for name, _ in forced.calls) == ["EligibilityOut", "StudyOut"]


def test_one_failing_agent_does_not_corrupt_others_or_reuse_stale_output(parsed) -> None:  # type: ignore[no-untyped-def]
    _extract(parsed, FakeLlm(synthetic_responders()))

    responders = synthetic_responders()

    def boom(_: LlmRequest):  # type: ignore[no-untyped-def]
        raise RuntimeError("rate limited forever")

    responders["EligibilityOut"] = boom
    extraction = _extract(parsed, FakeLlm(responders), force=True)

    assert extraction.agents["eligibility_criteria"].status == AgentStatus.FAILED
    assert "rate limited forever" in (extraction.agents["eligibility_criteria"].error or "")
    # The earlier successful eligibility output is not presented as current...
    assert extraction.sheets.eligibility_criteria is None
    # ...and the study sheet from this run is intact.
    assert extraction.sheets.study is not None


def test_running_a_subset_keeps_other_sheets(parsed) -> None:  # type: ignore[no-untyped-def]
    _extract(parsed, FakeLlm(synthetic_responders()))
    llm = FakeLlm(synthetic_responders())
    extraction = _extract(parsed, llm, sheets=["study"], force=True)
    assert [name for name, _ in llm.calls] == ["StudyOut"]
    assert extraction.sheets.eligibility_criteria is not None


def test_agent_prompt_contains_only_its_sections(parsed) -> None:  # type: ignore[no-untyped-def]
    llm = FakeLlm(synthetic_responders())
    _extract(parsed, llm)
    prompts = dict(llm.calls)
    eligibility = prompts["EligibilityOut"].user_content
    assert 'id="sec-2.1"' in eligibility
    assert 'id="sec-3"' not in eligibility  # statistics is not eligibility content
    assert 'id="title-page"' in prompts["StudyOut"].user_content
    assert "Allowed terms:" in eligibility and "C25532" not in eligibility  # terms, never codes


def test_postprocessing_change_reprocesses_stored_output_without_a_model_call(
    parsed, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from backend.pipeline.agents.registry import AGENTS

    _extract(parsed, FakeLlm(synthetic_responders()))
    monkeypatch.setattr(type(AGENTS["study"]), "postprocess_version", "999")

    llm = FakeLlm(synthetic_responders())
    extraction = _extract(parsed, llm)
    assert llm.calls == []
    study_run = extraction.agents["study"]
    assert study_run.status == AgentStatus.SKIPPED and study_run.reprocessed
    assert extraction.sheets.study is not None
    assert extraction.agents["eligibility_criteria"].reprocessed is False
