from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.models.document import (
    DocumentStats,
    ExtractorInfo,
    HeadingSource,
    ParsedDocument,
    Section,
    SectionKind,
    SourceInfo,
)
from backend.models.run_config import RunConfig
from backend.models.segmentation import MappingMethod, SectionOverride
from backend.pipeline.ingest import segment
from backend.pipeline.segmentation.m11 import (
    load_template,
    map_sections,
    normalise,
    sections_for,
    title_similarity,
)
from backend.pipeline.segmentation.overrides import (
    OverrideError,
    add_also,
    clear_override,
    load_overrides,
    remove_also,
    set_override,
)


def test_template_numbers_are_unique_and_every_parent_exists() -> None:
    template = load_template()
    numbers = [s.number for s in template.sections]
    assert len(numbers) == len(set(numbers))
    for number in numbers:
        if "." in number:
            assert number.rsplit(".", 1)[0] in numbers, number


def test_template_covers_all_m11_chapters() -> None:
    chapters = {s.chapter for s in load_template().sections}
    assert chapters == {str(n) for n in range(15)}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Study Population", "population"),  # "study" -> "trial", a stopword
        ("Patient Selection", "participant selection"),
        ("Concomitant Medications", "concomitant therapy"),
        ("Randomization", "randomisation"),
        ("Primary Objective(s) and Associated Estimand(s)", "primary objective estimand"),
        ("Schedule of Events", "schedule activitie"),
    ],
)
def test_normalise(raw: str, expected: str) -> None:
    assert normalise(raw) == expected


def _sim(a: str, b: str) -> float:
    template = load_template()
    from backend.pipeline.segmentation.m11 import _Scorer

    return title_similarity(normalise(a), normalise(b), _Scorer(template).vocab)


def test_lookalike_words_do_not_match() -> None:
    assert _sim("Indication", "Introduction") == 0.0
    assert _sim("Hospitalization", "Trial Population") == 0.0
    assert _sim("Screening", "Rescreening") == 0.0


def test_generic_shared_word_is_weak_evidence() -> None:
    assert _sim("Intraocular Pressure Measurement", "Pharmacokinetic Assessments") < 0.4


def test_specific_shared_words_are_strong_evidence() -> None:
    assert _sim("Study Population", "Trial Population") == 1.0
    # The specific shared word carries the score well past the 0.55 match threshold even though
    # "assessment" is unmatched.
    assert _sim("Pharmacokinetic Assessments", "Pharmacokinetics") >= 0.7


# ----- map_sections on a hand-built document ------------------------------------------------


def _section(
    sid: str,
    title: str,
    number: str | None = None,
    parent: str | None = None,
    kind: SectionKind = SectionKind.BODY,
) -> Section:
    return Section(
        id=sid,
        number=number,
        title=title,
        level=len(number.split(".")) if number else 1,
        kind=kind,
        parent_id=parent,
        page_start=1,
        page_end=1,
        heading_bbox=None,
        heading_source=HeadingSource.TEXT,
        text="",
    )


def _document(sections: list[Section]) -> ParsedDocument:
    return ParsedDocument(
        source=SourceInfo(filename="x.pdf", sha256="0" * 64, page_count=1),
        extractor=ExtractorInfo(name="test", version="1", library_version="-", page_image_dpi=50),
        pages=[],
        sections=sections,
        tables=[],
        outline=[],
        stats=DocumentStats(
            body_font_size=11,
            headings_from_text=0,
            headings_from_outline_only=0,
            rejected_heading_candidates=0,
            tables=0,
            tables_needing_vision=0,
            soa_pages=[],
            elapsed_seconds=0,
        ),
    )


@pytest.fixture
def mapping_doc() -> ParsedDocument:
    return _document(
        [
            _section("title-page", "Title Page", kind=SectionKind.TITLE_PAGE),
            _section("toc", "Table of Contents", kind=SectionKind.TOC),
            _section("sec-5", "STUDY TREATMENTS", "5"),
            _section("sec-5.3", "Administration", "5.3", "sec-5"),
            _section("sec-5.3.1", "Palbociclib/Placebo", "5.3.1", "sec-5.3"),
            _section("sec-9", "DATA ANALYSIS/STATISTICAL METHODS", "9"),
            _section("sec-9.2", "Analysis Population", "9.2", "sec-9"),
            _section("sec-9.2.1", "Intent-to-Treat Population (ITT)", "9.2.1", "sec-9.2"),
            _section("app-x", "Appendix 2. Hachinski Ischemic Scale", kind=SectionKind.APPENDIX),
        ]
    )


def test_structural_and_excluded_sections(mapping_doc: ParsedDocument) -> None:
    m = map_sections(mapping_doc)
    assert m.assignment("title-page").m11_number == "0"
    toc = m.assignment("toc")
    assert toc.method == MappingMethod.EXCLUDED and not toc.needs_review


def test_alias_mapping_for_non_m11_titles(mapping_doc: ParsedDocument) -> None:
    m = map_sections(mapping_doc)
    assert m.assignment("sec-5").m11_number == "6"
    assert m.assignment("sec-5.3").m11_number == "6.3"
    assert m.assignment("sec-9").m11_number == "10"
    assert m.assignment("sec-9.2").m11_number == "10.2"


def test_drug_named_subsection_inherits_parent(mapping_doc: ParsedDocument) -> None:
    a = map_sections(mapping_doc).assignment("sec-5.3.1")
    assert a.m11_number == "6.3"
    assert a.method == MappingMethod.INHERITED
    assert a.confidence == pytest.approx(0.75)


def test_weak_cross_chapter_jump_falls_back_to_parent(mapping_doc: ParsedDocument) -> None:
    # "Population" alone would pull this towards chapter 5 (Trial Population).
    a = map_sections(mapping_doc).assignment("sec-9.2.1")
    assert a.m11_number == "10.2"


def test_unrecognised_appendix_goes_to_additional_appendices(mapping_doc: ParsedDocument) -> None:
    a = map_sections(mapping_doc).assignment("app-x")
    assert a.m11_number == "12.X"
    assert a.method == MappingMethod.APPENDIX_DEFAULT


def test_low_confidence_is_flagged_for_review(mapping_doc: ParsedDocument) -> None:
    m = map_sections(mapping_doc, review_threshold=0.9)
    assert m.assignment("sec-5.3.1").needs_review
    assert not m.assignment("sec-5").needs_review


def test_sections_for_returns_subtree_in_document_order(mapping_doc: ParsedDocument) -> None:
    m = map_sections(mapping_doc)
    assert sections_for(m, mapping_doc, ["6"]) == ["sec-5", "sec-5.3", "sec-5.3.1"]
    assert sections_for(m, mapping_doc, ["6"], include_inherited=False) == ["sec-5", "sec-5.3"]


def test_coverage_marks_missing_sections(mapping_doc: ParsedDocument) -> None:
    coverage = {c.m11_number: c for c in map_sections(mapping_doc).coverage}
    assert coverage["6.3"].status == "found"
    assert coverage["5.2"].status == "missing"


# ----- reviewer overrides ---------------------------------------------------------------------


def _override(sid: str, title: str, m11: str | None, excluded: bool = False) -> SectionOverride:
    return SectionOverride(
        section_id=sid,
        doc_title=title,
        m11_number=m11,
        excluded=excluded,
        updated_at=datetime.now(UTC),
    )


def test_reviewer_override_maps_a_section_and_subsections_follow(
    mapping_doc: ParsedDocument,
) -> None:
    m = map_sections(
        mapping_doc, overrides={"sec-5.3": _override("sec-5.3", "Administration", "6.1")}
    )
    overridden = m.assignment("sec-5.3")
    assert (overridden.m11_number, overridden.m11_title) == (
        "6.1",
        load_template().get("6.1").title,
    )
    assert overridden.method == MappingMethod.REVIEWER and overridden.reviewer_override
    assert overridden.confidence == 1.0 and not overridden.needs_review
    assert overridden.candidates  # the computed suggestions stay visible
    child = m.assignment("sec-5.3.1")
    assert (child.m11_number, child.method) == ("6.1", MappingMethod.INHERITED)
    coverage = {c.m11_number: c for c in m.coverage}
    assert coverage["6.1"].section_ids == ["sec-5.3"] and coverage["6.3"].status == "missing"
    assert m.ignored_overrides == []


def test_reviewer_can_exclude_a_section(mapping_doc: ParsedDocument) -> None:
    title = "Intent-to-Treat Population (ITT)"
    m = map_sections(
        mapping_doc, overrides={"sec-9.2.1": _override("sec-9.2.1", title, None, True)}
    )
    excluded = m.assignment("sec-9.2.1")
    assert excluded.method == MappingMethod.EXCLUDED and excluded.reviewer_override
    assert "sec-9.2.1" not in sections_for(m, mapping_doc, ["10"])


def test_overrides_that_no_longer_fit_the_document_are_ignored(mapping_doc: ParsedDocument) -> None:
    m = map_sections(
        mapping_doc,
        overrides={
            "gone": _override("gone", "Old Section", "6.1"),
            "sec-5.3": _override("sec-5.3", "Dosing", "6.1"),  # the section was re-titled
        },
    )
    assert m.assignment("sec-5.3").m11_number == "6.3"
    assert len(m.ignored_overrides) == 2
    assert any("title changed" in note for note in m.ignored_overrides)


def test_overrides_are_validated_stored_and_kept_by_segmentation(
    mapping_doc: ParsedDocument, tmp_path: Path
) -> None:
    with pytest.raises(KeyError):
        set_override(tmp_path, mapping_doc, "nope", "6.1", excluded=False)
    with pytest.raises(OverrideError, match="not a section of the M11 template"):
        set_override(tmp_path, mapping_doc, "sec-5.3", "99.9", excluded=False)
    with pytest.raises(OverrideError):
        set_override(tmp_path, mapping_doc, "sec-5.3", "6.1", excluded=True)

    set_override(tmp_path, mapping_doc, "sec-5.3", "6.1", excluded=False)
    config = RunConfig(source_filename="x.pdf")
    assert segment(tmp_path, mapping_doc, config).assignment("sec-5.3").m11_number == "6.1"
    assert segment(tmp_path, mapping_doc, config).assignment("sec-5.3").reviewer_override

    clear_override(tmp_path, "sec-5.3")
    assert segment(tmp_path, mapping_doc, config).assignment("sec-5.3").m11_number == "6.3"
    with pytest.raises(KeyError):
        clear_override(tmp_path, "sec-5.3")


def test_a_section_can_also_map_to_further_m11_sections(mapping_doc: ParsedDocument) -> None:
    also = _override("sec-5.3", "Administration", None)
    also.also = ["1.3", "6.6"]
    m = map_sections(mapping_doc, overrides={"sec-5.3": also})
    a = m.assignment("sec-5.3")
    # The computed mapping stays; the further sections are added.
    assert (a.m11_number, a.method) == ("6.3", MappingMethod.ALIAS_MATCH)
    assert [r.m11_number for r in a.also_m11] == ["1.3", "6.6"] and a.reviewer_override
    assert "sec-5.3" in sections_for(m, mapping_doc, ["1"])
    assert sections_for(m, mapping_doc, ["6.6"]) == ["sec-5.3"]
    coverage = {c.m11_number: c for c in m.coverage}
    assert (coverage["1.3"].status, coverage["1.3"].section_ids) == ("found", ["sec-5.3"])
    assert coverage["6.3"].section_ids == ["sec-5.3"]

    # A main override keeps them (without repeating itself); excluding the section drops them.
    main = _override("sec-5.3", "Administration", "1.3")
    main.also = ["1.3", "6.6"]
    a = map_sections(mapping_doc, overrides={"sec-5.3": main}).assignment("sec-5.3")
    assert a.m11_number == "1.3" and [r.m11_number for r in a.also_m11] == ["6.6"]
    gone = _override("sec-5.3", "Administration", None, excluded=True)
    gone.also = ["6.6"]
    assert (
        map_sections(mapping_doc, overrides={"sec-5.3": gone}).assignment("sec-5.3").also_m11 == []
    )


def test_further_mappings_are_validated_and_removed(
    mapping_doc: ParsedDocument, tmp_path: Path
) -> None:
    config = RunConfig(source_filename="x.pdf")
    current = segment(tmp_path, mapping_doc, config).assignment("sec-5.3")
    with pytest.raises(OverrideError, match=r"already mapped to 6.3"):
        add_also(tmp_path, mapping_doc, current, "6.3")
    with pytest.raises(OverrideError, match="not a section"):
        add_also(tmp_path, mapping_doc, current, "99")
    add_also(tmp_path, mapping_doc, current, "1.3")
    current = segment(tmp_path, mapping_doc, config).assignment("sec-5.3")
    with pytest.raises(OverrideError, match=r"already mapped to 1.3"):
        add_also(tmp_path, mapping_doc, current, "1.3")

    set_override(tmp_path, mapping_doc, "sec-5.3", "6.1", excluded=False)  # keeps 1.3
    current = segment(tmp_path, mapping_doc, config).assignment("sec-5.3")
    assert (current.m11_number, [r.m11_number for r in current.also_m11]) == ("6.1", ["1.3"])

    remove_also(tmp_path, "sec-5.3", "1.3")
    assert segment(tmp_path, mapping_doc, config).assignment("sec-5.3").m11_number == "6.1"
    with pytest.raises(KeyError):
        remove_also(tmp_path, "sec-5.3", "1.3")
    clear_override(tmp_path, "sec-5.3")
    add_also(
        tmp_path, mapping_doc, segment(tmp_path, mapping_doc, config).assignment("sec-5.3"), "1.3"
    )
    remove_also(tmp_path, "sec-5.3", "1.3")  # nothing of the reviewer's left: no override
    assert "sec-5.3" not in load_overrides(tmp_path).overrides


# Normalised titles/aliases shared by more than one M11 section. Each entry was reviewed: M11
# repeats titles itself (10.4.1.x / 10.5.1.x) or a parent and child legitimately share a name, and
# exact ties resolve to the less specific section. A new entry usually means an alias that
# normalises to something too generic ("Other Objectives" -> "objective") and must be fixed.
REVIEWED_COLLISIONS = {
    "clinical laboratory test": {"12.1", "8.4.4"},
    "discontinuation": {"7", "7.1", "7.2"},
    "exploratory objective": {"3.3", "3.3.1"},
    "laboratory test": {"12.1", "8.4.4"},
    "overall design": {"1.1.2", "4", "4.1"},
    "participant discontinuation": {"7", "7.2"},
    "primary objective": {"10.4.1", "3.1", "3.1.1"},
    "reporting period": {"9.2", "9.2.1"},
    "reporting requirement": {"9.2", "9.2.3"},
    "secondary objective": {"10.5.1", "3.2", "3.2.1"},
    "sensitivity analysis": {"10.4.1.4", "10.5.1.4"},
    "statistical analysis method": {"10.4.1.1", "10.5.1.1"},
    "supplementary analysis": {"10.4.1.5", "10.5.1.5"},
    "synopsis": {"1", "1.1"},
}


def test_no_unreviewed_title_collisions_between_m11_sections() -> None:
    seen: dict[str, set[str]] = {}
    for section in load_template().sections:
        for variant in [section.title, *section.aliases]:
            seen.setdefault(normalise(variant), set()).add(section.number)
    collisions = {k: v for k, v in seen.items() if len(v) > 1}
    assert collisions == REVIEWED_COLLISIONS


def test_child_matching_parent_topic_stays_at_parent_level() -> None:
    # PALOMA-3: "2 STUDY OBJECTIVES AND ENDPOINTS" > "2.1 Objectives" must map to chapter 3 as a
    # whole, not to 3.3 Exploratory Objective(s).
    doc = _document(
        [
            _section("sec-2", "STUDY OBJECTIVES AND ENDPOINTS", "2"),
            _section("sec-2.1", "Objectives", "2.1", "sec-2"),
            _section("sec-2.2", "Endpoints", "2.2", "sec-2"),
        ]
    )
    m = map_sections(doc)
    assert m.assignment("sec-2.1").m11_number == "3"
    assert m.assignment("sec-2.2").m11_number == "3"


def test_private_use_glyphs_are_removed() -> None:
    from backend.pipeline.extractors.layout import clean_text

    assert clean_text(f"Faslodex{chr(0xF0E2)})") == "Faslodex)"
