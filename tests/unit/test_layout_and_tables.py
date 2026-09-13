from backend.pipeline.extractors.layout import (
    Row,
    clean_text,
    find_running_rows,
    is_noise,
    is_toc_page,
)
from backend.pipeline.extractors.pymupdf_extractor import SOA_THRESHOLD
from backend.pipeline.extractors.tables import RawTable, group_tables, soa_score, to_markdown


def _row(page: int, index: int, text: str, y0: float, size: float = 10.0) -> Row:
    return Row(
        page=page,
        index=index,
        text=text,
        bbox=(72, y0, 400, y0 + 10),
        size=size,
        bold=False,
        block=0,
    )


def test_running_header_and_page_numbers_detected_across_pages() -> None:
    pages = [
        [
            _row(p, 0, "Protocol EX-001 Confidential", 30),
            _row(p, 1, f"Unique body content {p}", 300),
            _row(p, 2, f"Page {p} of 6", 770),
        ]
        for p in range(1, 7)
    ]

    running = find_running_rows(pages, [792.0] * 6)

    assert {(p, 0) for p in range(1, 7)} <= running
    assert {(p, 2) for p in range(1, 7)} <= running
    assert not any((p, 1) in running for p in range(1, 7))


def test_headings_differing_only_by_number_are_not_running_headers() -> None:
    pages = [[_row(p, 0, f"{p}. Chapter Title Number {p}", 60)] for p in range(1, 7)]
    assert find_running_rows(pages, [792.0] * 6) == set()


def test_toc_page_detected_from_dot_leaders() -> None:
    rows = [_row(1, 0, "Table of Contents", 50)] + [
        _row(1, i, f"{i}. Section {i} ........................ {i + 4}", 70 + 15 * i)
        for i in range(1, 7)
    ]
    assert is_toc_page(rows)
    assert not is_toc_page([_row(1, 0, "1. Introduction", 50), _row(1, 1, "Body text.", 70)])


def test_redaction_stamp_is_noise() -> None:
    assert is_noise(_row(1, 0, "CCI", 100, size=144), body_size=12)
    assert not is_noise(_row(1, 0, "SCHEDULE OF ACTIVITIES", 100, size=16), body_size=12)


def test_clean_text_normalises_whitespace_and_artifacts() -> None:
    nbsp, replacement, soft_hyphen = chr(0xA0), chr(0xFFFD), chr(0xAD)
    raw = f"Table{nbsp}1.{replacement}  Dose{soft_hyphen}Levels "
    assert clean_text(raw) == "Table 1. DoseLevels"


def _table(cells: list[list[str | None]], page: int = 1, index: int = 0) -> RawTable:
    return RawTable(page=page, index=index, bbox=(0, 0, 100, 100), cells=cells)


SOA = [
    ["Procedure", "Screening", "Day 1", "Week 4"],
    ["Vital signs", "X", "X", "X"],
    ["ECG", "X", "", "X"],
    ["Labs", "X", "X", ""],
]


def test_soa_grid_is_candidate_and_prose_table_is_not() -> None:
    prose = [
        ["Dose level", "Palbociclib"],
        ["Starting dose", "125 mg/day"],
        ["First reduction", "100 mg/day"],
    ]
    assert soa_score(_table(SOA), page_text_hint=False) >= SOA_THRESHOLD
    assert soa_score(_table(prose), page_text_hint=False) < 0.3


def test_group_tables_chains_next_page_continuation() -> None:
    first, second, unrelated = (
        _table(SOA, page=15),
        _table(SOA, page=16),
        _table([["a", "b"], ["c", "d"]], page=30),
    )
    group_tables([first, second, unrelated])
    assert first.group_id == second.group_id != unrelated.group_id


def test_markdown_escapes_pipes_and_pads_ragged_rows() -> None:
    md = to_markdown([["a|b", "c"], ["d"]])
    assert md.splitlines() == ["| a\\|b | c |", "|---|---|", "| d |  |"]


def test_merged_cells_counted() -> None:
    assert _table([["Header", None, None], ["a", "b", "c"]]).merged_cells == 2


def test_page_numbers_embedded_in_a_longer_footer_are_still_running() -> None:
    pages = [[_row(p, 0, f"Protocol EX-001 Page {p} of 6 Confidential", 770)] for p in range(1, 7)]
    assert find_running_rows(pages, [792.0] * 6) == {(p, 0) for p in range(1, 7)}
