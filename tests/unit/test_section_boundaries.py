"""Reviewer corrections of section start pages: moving whole pages between neighbouring sections."""

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
    Table,
)
from backend.models.segmentation import SectionBoundaries
from backend.pipeline.segmentation.boundaries import (
    RAW_DOCUMENT_FILE,
    BoundaryError,
    apply_boundaries,
    clear_boundary,
    finalise_document,
    load_boundaries,
    set_boundary,
)


def _section(sid: str, title: str, kind: SectionKind, start: int, end: int, text: str) -> Section:
    return Section(
        id=sid,
        number=None,
        title=title,
        level=1,
        kind=kind,
        parent_id=None,
        page_start=start,
        page_end=end,
        heading_bbox=None,
        heading_source=HeadingSource.TEXT,
        text=text,
        table_ids=[m for m in ("tbl-3", "tbl-5") if f"[[TABLE {m}]]" in text],
    )


def _table(tid: str, page: int, section_id: str) -> Table:
    return Table(
        id=tid,
        page=page,
        bbox=(0.0, 0.0, 1.0, 1.0),
        section_id=section_id,
        caption=None,
        row_count=1,
        col_count=1,
        cells=[["x"]],
        markdown="| x |",
        merged_cell_count=0,
        empty_cell_ratio=0,
        group_id=tid,
        soa_score=0,
        is_soa_candidate=False,
        needs_vision=False,
    )


@pytest.fixture
def document() -> ParsedDocument:
    """A title page, then a synopsis whose heading was found on page 2 although pages 2-3 are the
    rest of the title pages, then section 1."""
    synopsis = "\n".join(
        [
            "[[PAGE 2]]",
            "Sponsor signature",
            "[[PAGE 3]]",
            "[[TABLE tbl-3]]",
            "Investigator signature",
            "[[PAGE 4]]",
            "Synopsis text",
            "[[PAGE 5]]",
            "[[TABLE tbl-5]]",
            "[[PAGE 6]]",
            "Schedule of evaluations",
        ]
    )
    return ParsedDocument(
        source=SourceInfo(filename="x.pdf", sha256="0" * 64, page_count=7),
        extractor=ExtractorInfo(name="test", version="1", library_version="-", page_image_dpi=50),
        pages=[],
        sections=[
            _section("title-page", "Title Page", SectionKind.TITLE_PAGE, 1, 1, "[[PAGE 1]]\nTitle"),
            _section("fm-synopsis", "Synopsis", SectionKind.FRONT_MATTER, 2, 6, synopsis),
            _section("sec-1", "Introduction", SectionKind.BODY, 7, 7, "[[PAGE 7]]\nIntro"),
        ],
        tables=[_table("tbl-3", 3, "fm-synopsis"), _table("tbl-5", 5, "fm-synopsis")],
        outline=[],
        stats=DocumentStats(
            body_font_size=11,
            headings_from_text=0,
            headings_from_outline_only=0,
            rejected_heading_candidates=0,
            tables=2,
            tables_needing_vision=0,
            soa_pages=[],
            elapsed_seconds=0,
        ),
    )


def test_pages_before_the_new_start_move_to_the_previous_section(
    document: ParsedDocument, tmp_path: Path
) -> None:
    set_boundary(tmp_path, document, "fm-synopsis", 4)
    adjusted = apply_boundaries(document, load_boundaries(tmp_path))
    title, synopsis = adjusted.section("title-page"), adjusted.section("fm-synopsis")

    assert title.text.split("\n") == [
        "[[PAGE 1]]",
        "Title",
        "[[PAGE 2]]",
        "Sponsor signature",
        "[[PAGE 3]]",
        "[[TABLE tbl-3]]",
        "Investigator signature",
    ]
    assert (title.page_start, title.page_end, title.table_ids) == (1, 3, ["tbl-3"])
    assert synopsis.text.startswith("[[PAGE 4]]\nSynopsis text")
    assert (synopsis.page_start, synopsis.page_end, synopsis.parsed_page_start) == (4, 6, 2)
    assert synopsis.table_ids == ["tbl-5"]
    assert {t.id: t.section_id for t in adjusted.tables} == {
        "tbl-3": "title-page",
        "tbl-5": "fm-synopsis",
    }
    assert document.section("fm-synopsis").page_start == 2  # the raw document is untouched


def test_a_start_can_move_earlier_taking_pages_from_the_previous_section(
    document: ParsedDocument, tmp_path: Path
) -> None:
    set_boundary(tmp_path, document, "fm-synopsis", 4)
    first = apply_boundaries(document, load_boundaries(tmp_path))
    # Treat the corrected document as a parse, then move section 1's start back into it.
    set_boundary(tmp_path, first, "sec-1", 6)
    adjusted = apply_boundaries(
        first,
        SectionBoundaries(boundaries={"sec-1": load_boundaries(tmp_path).boundaries["sec-1"]}),
    )
    assert adjusted.section("sec-1").text.split("\n")[:2] == [
        "[[PAGE 6]]",
        "Schedule of evaluations",
    ]
    assert adjusted.section("fm-synopsis").page_end == 5
    assert adjusted.section("sec-1").page_start == 6


def test_start_pages_are_validated(document: ParsedDocument, tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        set_boundary(tmp_path, document, "nope", 3)
    with pytest.raises(BoundaryError, match="first section"):
        set_boundary(tmp_path, document, "title-page", 2)
    with pytest.raises(BoundaryError, match="can start on pages 2-6"):
        set_boundary(tmp_path, document, "fm-synopsis", 1)
    with pytest.raises(BoundaryError):
        set_boundary(tmp_path, document, "fm-synopsis", 9)

    set_boundary(tmp_path, document, "fm-synopsis", 4)
    set_boundary(tmp_path, document, "fm-synopsis", 2)  # what the parser found: no correction
    assert load_boundaries(tmp_path).boundaries == {}


def test_the_raw_document_is_kept_while_corrections_exist(
    document: ParsedDocument, tmp_path: Path
) -> None:
    assert finalise_document(tmp_path, document) == document
    assert not (tmp_path / RAW_DOCUMENT_FILE).exists()

    set_boundary(tmp_path, document, "fm-synopsis", 4)
    adjusted = finalise_document(tmp_path, document)
    assert adjusted.section("fm-synopsis").page_start == 4
    assert (tmp_path / RAW_DOCUMENT_FILE).is_file()

    clear_boundary(tmp_path, "fm-synopsis")
    assert finalise_document(tmp_path, document) == document
    assert not (tmp_path / RAW_DOCUMENT_FILE).exists()
    with pytest.raises(KeyError):
        clear_boundary(tmp_path, "fm-synopsis")


def test_a_correction_for_a_changed_section_is_not_applied(
    document: ParsedDocument, tmp_path: Path
) -> None:
    set_boundary(tmp_path, document, "fm-synopsis", 4)
    reparsed = document.model_copy(deep=True)
    reparsed.sections[1].title = "Protocol Synopsis"
    adjusted = apply_boundaries(reparsed, load_boundaries(tmp_path))
    assert adjusted.section("fm-synopsis").page_start == 2
    assert any("now titled 'Protocol Synopsis'" in w for w in adjusted.warnings)
