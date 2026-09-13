import pytest

from backend.models.document import HeadingSource, Section, SectionKind, Table
from backend.pipeline.agents.common import Cited
from backend.pipeline.agents.context import (
    NO_QUOTE_CAP,
    UNVERIFIED_QUOTE_CAP,
    AgentContext,
    input_hash,
    locate_quote,
    provenance_for,
)


def _section(sid: str, text: str, start: int = 10, end: int = 11) -> Section:
    return Section(
        id=sid,
        number=None,
        title=sid,
        level=1,
        kind=SectionKind.BODY,
        parent_id=None,
        page_start=start,
        page_end=end,
        heading_bbox=None,
        heading_source=HeadingSource.TEXT,
        text=text,
    )


INCLUSION = _section(
    "sec-4.1",
    "[[PAGE 10]]\nPatients must meet all criteria:\n1. Female aged 18 years or older.\n"
    "[[TABLE tbl-p0010-1]]\n[[PAGE 11]]\n2. Histologically confirmed HR-positive,\n"
    "HER2-negative breast cancer.",
)
EXCLUSION = _section("sec-4.2", "[[PAGE 12]]\n1. Prior treatment with any CDK inhibitor.", 12, 12)
CONTEXT = AgentContext(sections=[INCLUSION, EXCLUSION], rendered="", used_fallback=False)


def test_quote_page_comes_from_the_page_marker() -> None:
    assert locate_quote("Female aged 18 years or older.", INCLUSION) == 10
    # Spans a line break and differs in case/whitespace.
    assert locate_quote("HR-positive, her2-negative   breast cancer", INCLUSION) == 11


def test_near_verbatim_long_quote_is_found() -> None:
    assert (
        locate_quote("Histologically confirmed HR positive, HER2-negative breast cancer", INCLUSION)
        == 11
    )


@pytest.mark.parametrize(
    "quote",
    [
        "",
        "Male aged 18 years or older.",  # "male" inside "Female" is not a word match
        "Female aged 65 years or older.",  # a changed number never verifies
        "Histologically confirmed HR-positive, HER2-negative breast cancer not",  # negation added
        "Prior treatment with any CDK inhibitor",  # wrong section
    ],
)
def test_quotes_that_do_not_support_the_value_are_not_found(quote: str) -> None:
    assert locate_quote(quote, INCLUSION) is None


def test_short_whole_word_quote_is_found() -> None:
    assert locate_quote("cancer", INCLUSION) == 11


def test_verified_provenance_uses_found_page_not_model_claim() -> None:
    cited = Cited(
        value="Female 18+", quote="Female aged 18 years", section_id="sec-4.1", confidence=0.9
    )
    p = provenance_for(cited, CONTEXT)
    assert (p.verified, p.source_section_id, p.source_page, p.confidence) == (
        True,
        "sec-4.1",
        10,
        0.9,
    )


def test_quote_found_in_another_section_is_relocated_with_a_note() -> None:
    cited = Cited(
        value="x",
        quote="Prior treatment with any CDK inhibitor",
        section_id="sec-4.1",
        confidence=0.9,
    )
    p = provenance_for(cited, CONTEXT)
    assert p.verified and p.source_section_id == "sec-4.2" and p.source_page == 12
    assert p.note and "not the cited" in p.note


def test_unfound_quote_is_flagged_and_confidence_capped() -> None:
    cited = Cited(
        value="x",
        quote="Patients must be over 80 years of age",
        section_id="sec-4.1",
        confidence=0.95,
    )
    p = provenance_for(cited, CONTEXT)
    assert not p.verified and p.source_page is None
    assert p.confidence == UNVERIFIED_QUOTE_CAP


def test_missing_quote_caps_confidence() -> None:
    cited = Cited(value="x", quote=None, section_id="sec-4.1", confidence=0.95)
    p = provenance_for(cited, CONTEXT)
    assert not p.verified and p.confidence == NO_QUOTE_CAP


def test_unknown_section_id_is_not_trusted() -> None:
    cited = Cited(value="x", quote=None, section_id="sec-99", confidence=0.5)
    assert provenance_for(cited, CONTEXT).source_section_id is None


def test_input_hash_is_order_sensitive_and_unambiguous() -> None:
    assert input_hash("a", "b") != input_hash("b", "a")
    assert input_hash("ab", "c") != input_hash("a", "bc")


def _table_section() -> tuple[Section, dict[str, Table]]:
    section = _section(
        "fm-document-history", "[[PAGE 2]]\nDocument History\n[[TABLE tbl-p0002-1]]", 2, 2
    )
    table = Table(
        id="tbl-p0002-1",
        page=2,
        bbox=(0, 0, 1, 1),
        section_id=section.id,
        caption=None,
        row_count=2,
        col_count=3,
        cells=[["Document", "Version Date", "Summary"], ["Amendment 2", "30 September 2014", None]],
        markdown="",
        merged_cell_count=1,
        empty_cell_ratio=0.1,
        group_id="g",
        soa_score=0,
        is_soa_candidate=False,
        needs_vision=False,
    )
    return section, {table.id: table}


def test_quote_from_a_table_is_verified_on_the_table_page() -> None:
    section, tables = _table_section()
    assert locate_quote("30 September 2014", section, tables) == 2
    assert (
        locate_quote("30 September 2014", section) is None
    )  # without the table it cannot be found


def test_whitespace_lost_in_extraction_does_not_block_verification() -> None:
    section = _section("title-page", "[[PAGE 1]]\nFinal Protocol Amendment 3, 20 October2015", 1, 1)
    assert locate_quote("20 October 2015", section) == 1
    assert locate_quote("21 October 2015", section) is None


def test_literal_unicode_escapes_in_model_output_are_decoded() -> None:
    from backend.pipeline.agents.common import decode_literal_escapes

    bs = chr(92)  # a backslash, built at runtime so the source holds no escape sequence
    raw = {"text": {"value": f"score of {bs}u22644 (Alzheimer{bs}u2019s)", "quote": None}, "n": 3}
    assert decode_literal_escapes(raw) == {
        "text": {"value": f"score of {chr(0x2264)}4 (Alzheimer{chr(0x2019)}s)", "quote": None},
        "n": 3,
    }
    windows_path = f"C:{bs}users{bs}path"
    assert decode_literal_escapes(windows_path) == windows_path
