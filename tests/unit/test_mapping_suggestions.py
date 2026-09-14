"""Mapping suggestions: what Claude is shown, how its answer is checked, batching and storage."""

import asyncio
import re
from pathlib import Path

import pytest

from backend.models.document import SectionKind
from backend.models.segmentation import MappingMethod
from backend.pipeline.llm import LlmRequest
from backend.pipeline.segmentation import suggest
from backend.pipeline.segmentation.m11 import load_template, map_sections
from backend.pipeline.segmentation.suggest import (
    SUGGESTIONS_FILE,
    SuggestionOut,
    SuggestionsOut,
    build_prompt,
    in_scope,
    load_suggestions,
    suggest_mappings,
)
from tests.fixtures.fake_llm import FakeLlm
from tests.unit.test_m11_segmentation import _document, _section

_ID = re.compile(r'<section id="([^"]+)"')


@pytest.fixture
def document():  # type: ignore[no-untyped-def]
    sections = [
        _section("title-page", "Title Page", kind=SectionKind.TITLE_PAGE),
        _section("fm-sig", "Signature Page"),
        _section("sec-5", "STUDY TREATMENTS", "5"),
        _section("sec-5.3", "Administration", "5.3", "sec-5"),
        _section("sec-5.3.1", "Palbociclib/Placebo", "5.3.1", "sec-5.3"),
    ]
    sections[3].text = "\n".join(
        ["[[PAGE 30]]", "The study drug is taken [[TABLE t1]] once daily with food."]
    )
    return _document(sections)


def responder(request: LlmRequest) -> SuggestionsOut:
    ids = _ID.findall(request.user_content)
    answers = {
        "fm-sig": SuggestionOut(
            section_id="fm-sig",
            m11_number=None,
            also_m11_numbers=[],
            not_protocol_content=True,
            confidence=0.95,
            reason="Signature page.",
        ),
        "sec-5.3": SuggestionOut(
            section_id="sec-5.3",
            m11_number="6.3",
            also_m11_numbers=["6.3", "99.9", "6.6"],
            not_protocol_content=False,
            confidence=1.4,
            reason="Describes how the intervention is taken.",
        ),
        "sec-5.3.1": SuggestionOut(
            section_id="sec-5.3.1",
            m11_number="42",
            also_m11_numbers=[],
            not_protocol_content=False,
            confidence=0.5,
            reason="Unsure.",
        ),
    }
    return SuggestionsOut(
        suggestions=[answers[i] for i in ids if i in answers]
        + [answers["fm-sig"].model_copy(update={"section_id": "not-asked"})]
    )


def test_the_prompt_shows_template_structure_and_clean_excerpts(document) -> None:  # type: ignore[no-untyped-def]
    mapping = map_sections(document)
    prompt = build_prompt(document, mapping, ["sec-5", "sec-5.3"], load_template())
    assert "6.3 " in prompt and "12.X" in prompt  # the whole template
    assert 'id="sec-5.3" number="5.3" title="Administration"' in prompt
    assert "parent: 5 STUDY TREATMENTS" in prompt
    assert "subsections: 5.3 Administration" in prompt
    assert "excerpt: The study drug is taken once daily with food." in prompt  # markers removed
    assert "[[PAGE" not in prompt.split("<protocol_sections>")[1]


def test_scope_skips_structural_and_reviewed_sections(document) -> None:  # type: ignore[no-untyped-def]
    mapping = map_sections(document, review_threshold=0.9)
    flagged = in_scope(mapping, document, "flagged")
    assert "title-page" not in flagged and "sec-5.3.1" in flagged
    assert set(in_scope(mapping, document, "all")) == {"fm-sig", "sec-5", "sec-5.3", "sec-5.3.1"}


def test_suggestions_are_validated_batched_and_stored(
    document, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(suggest, "BATCH_SIZE", 2)
    llm = FakeLlm({"SuggestionsOut": responder})
    mapping = map_sections(document)
    ids = ["fm-sig", "sec-5.3", "sec-5.3.1"]
    result = asyncio.run(suggest_mappings(tmp_path, document, mapping, ids, llm, "claude-sonnet-5"))

    assert len(llm.calls) == 2 and result.requested == 3
    assert result.usage.input_tokens == 2000 and result.usage.cost_usd == pytest.approx(0.008)
    by_id = {s.section_id: s for s in result.suggestions}
    assert list(by_id) == ids  # answers for sections not asked about are ignored
    assert by_id["fm-sig"].excluded and by_id["fm-sig"].m11_number is None
    administration = by_id["sec-5.3"]
    assert (administration.m11_number, administration.also_m11_numbers) == ("6.3", ["6.6"])
    assert administration.confidence == 1.0 and not administration.agrees
    assert by_id["sec-5.3.1"].m11_number is None and by_id["sec-5.3.1"].notes
    assert (tmp_path / SUGGESTIONS_FILE).is_file()
    assert (tmp_path / "run.log").read_text(encoding="utf-8").count('"llm_call"') == 2

    stored = load_suggestions(tmp_path, document)
    assert stored is not None and len(stored.suggestions) == 3
    document.sections[1].title = "Approval Page"  # re-parsed: that suggestion no longer applies
    stored = load_suggestions(tmp_path, document)
    assert stored is not None and [s.section_id for s in stored.suggestions] == [
        "sec-5.3",
        "sec-5.3.1",
    ]


def test_a_suggestion_matching_the_current_mapping_is_marked_as_agreeing(
    document, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    mapping = map_sections(document)
    assert mapping.assignment("sec-5.3").method != MappingMethod.EXCLUDED

    def same(request: LlmRequest) -> SuggestionsOut:
        return SuggestionsOut(
            suggestions=[
                SuggestionOut(
                    section_id="sec-5.3",
                    m11_number=mapping.assignment("sec-5.3").m11_number,
                    also_m11_numbers=[],
                    not_protocol_content=False,
                    confidence=0.9,
                    reason="Same.",
                )
            ]
        )

    result = asyncio.run(
        suggest_mappings(
            tmp_path, document, mapping, ["sec-5.3"], FakeLlm({"SuggestionsOut": same}), "m"
        )
    )
    assert result.suggestions[0].agrees


def test_stored_suggestions_are_reused_until_a_section_changes(document, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    llm = FakeLlm({"SuggestionsOut": responder})
    mapping = map_sections(document)
    ids = ["fm-sig", "sec-5.3"]
    first = asyncio.run(suggest_mappings(tmp_path, document, mapping, ids, llm, "m"))
    assert (first.asked, first.reused, len(llm.calls)) == (2, 0, 1)

    again = asyncio.run(suggest_mappings(tmp_path, document, mapping, ids, llm, "m"))
    assert (again.asked, again.reused, len(llm.calls)) == (0, 2, 1)
    assert again.usage.cost_usd == 0 and len(again.suggestions) == 2

    document.sections[3].text += "\nAdded in an amendment."
    changed = asyncio.run(suggest_mappings(tmp_path, document, mapping, ids, llm, "m"))
    assert (changed.asked, changed.reused, len(llm.calls)) == (1, 1, 2)

    forced = asyncio.run(suggest_mappings(tmp_path, document, mapping, ids, llm, "m", force=True))
    assert forced.asked == 2 and len(llm.calls) == 3


def test_claude_mapping_replaces_the_title_based_one_and_keeps_it_visible(
    document, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    llm = FakeLlm({"SuggestionsOut": responder})
    rule = map_sections(document)
    stored = asyncio.run(
        suggest_mappings(tmp_path, document, rule, ["fm-sig", "sec-5.3"], llm, "m")
    )
    applied = map_sections(document, suggestions={s.section_id: s for s in stored.suggestions})

    # "Signature Page" has a confident title match here, so Claude's exclusion waits for review.
    sig, rule_sig = applied.assignment("fm-sig"), rule.assignment("fm-sig")
    assert rule_sig.confidence >= rule.review_threshold
    assert (sig.method, sig.m11_number) == (rule_sig.method, rule_sig.m11_number)
    assert sig.needs_review and "Signature page." in (sig.claude_reason or "")
    administration = applied.assignment("sec-5.3")
    assert administration.method == MappingMethod.CLAUDE
    assert (administration.m11_number, [r.m11_number for r in administration.also_m11]) == (
        "6.3",
        ["6.6"],
    )
    assert administration.rule_m11_number == rule.assignment("sec-5.3").m11_number
    assert administration.confidence == 1.0 and not administration.needs_review
    coverage = {c.m11_number: c for c in applied.coverage}
    assert coverage["6.6"].section_ids == ["sec-5.3"]


def test_disagreeing_with_a_confident_title_match_needs_review(document) -> None:  # type: ignore[no-untyped-def]
    from backend.models.segmentation import MappingSuggestion

    rule = map_sections(document)
    confident = rule.assignment("sec-5")
    assert confident.confidence >= rule.review_threshold
    suggestion = MappingSuggestion(
        section_id="sec-5",
        doc_title="STUDY TREATMENTS",
        m11_number="8",
        m11_title="Trial Assessments and Procedures",
        confidence=0.95,
        reason="Wrong on purpose.",
        current_m11_number=confident.m11_number,
        agrees=False,
    )
    applied = map_sections(document, suggestions={"sec-5": suggestion}).assignment("sec-5")
    assert applied.m11_number == "8" and applied.needs_review
    assert applied.rule_m11_number == confident.m11_number


def test_claude_cannot_silently_exclude_a_confidently_mapped_section(document) -> None:  # type: ignore[no-untyped-def]
    from backend.models.segmentation import MappingSuggestion

    rule = map_sections(document)
    confident = rule.assignment("sec-5")
    suggestion = MappingSuggestion(
        section_id="sec-5",
        doc_title="STUDY TREATMENTS",
        m11_number=None,
        m11_title=None,
        excluded=True,
        confidence=0.9,
        reason="Looks administrative.",
        current_m11_number=confident.m11_number,
        agrees=False,
    )
    applied = map_sections(document, suggestions={"sec-5": suggestion}).assignment("sec-5")
    assert applied.m11_number == confident.m11_number and applied.method == confident.method
    assert applied.needs_review and applied.claude_reason
    assert applied.claude_reason.startswith("Suggests this is not protocol content")
