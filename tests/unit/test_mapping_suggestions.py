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
