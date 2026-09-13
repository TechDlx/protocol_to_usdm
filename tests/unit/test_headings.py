import pytest

from backend.models.document import SectionKind
from backend.pipeline.extractors.headings import (
    Heading,
    _continuation,
    _plausible_title,
    _valid_next,
    best_numbered_chain,
)
from backend.pipeline.extractors.layout import Row


def _h(number: str, weight: float = 1.0, page: int = 1) -> Heading:
    return Heading(
        page=page,
        row_index=0,
        y0=0.0,
        bbox=None,
        title=f"Title {number}",
        number=number,
        kind=SectionKind.BODY,
        level=len(number.split(".")),
        weight=weight,
    )


@pytest.mark.parametrize(
    ("prev", "cur", "ok"),
    [
        ((1,), (1, 1), True),  # first child
        ((1, 2), (1, 2, 2), True),  # child with a missing first child tolerated
        ((1, 2), (1, 2, 1, 1), True),  # skipped level
        ((1, 1), (1, 2), True),  # next sibling
        ((1, 2, 4, 3), (2,), True),  # back up to the next chapter
        ((5, 3, 4), (5, 4), True),  # back up one level
        ((9,), (9, 2), True),  # 9.1 redacted
        ((2,), (2,), False),  # repeat
        ((5,), (3,), False),  # backwards
        ((1,), (7,), False),  # implausible jump
        ((1, 1), (1, 1, 9), False),  # child numbering starting high
    ],
)
def test_valid_next(prev: tuple[int, ...], cur: tuple[int, ...], ok: bool) -> None:
    assert _valid_next(prev, cur) is ok


def test_best_chain_rejects_numbered_list_item_inside_a_section() -> None:
    # "3. Patients must..." styled bold inside section 1.1, then the real sections 2 and 3.
    candidates = [_h("1"), _h("1.1"), _h("3"), _h("2"), _h("2.1"), _h("3"), _h("3.1")]

    kept, rejected = best_numbered_chain(candidates)

    assert [h.number for h in kept] == ["1", "1.1", "2", "2.1", "3", "3.1"]
    assert len(rejected) == 1 and rejected[0] is candidates[2]


def test_best_chain_prefers_stronger_evidence_when_sequences_conflict() -> None:
    weak_intruder = _h("2", weight=0.5)
    candidates = [_h("1", 2.0), weak_intruder, _h("1.1", 2.0), _h("1.2", 2.0), _h("2", 2.0)]

    kept, _ = best_numbered_chain(candidates)

    assert weak_intruder not in kept
    assert [h.number for h in kept] == ["1", "1.1", "1.2", "2"]


def test_best_chain_with_no_valid_start_keeps_nothing() -> None:
    kept, rejected = best_numbered_chain([_h("17"), _h("18")])
    assert kept == [] and len(rejected) == 2


@pytest.mark.parametrize(
    ("title", "ok"),
    [
        ("Inclusion Criteria", True),
        ("28-day Post-treatment Follow-up", True),
        ("months after the last dose of study drug.", False),  # sentence, lower-case start
        ("Patients must have measurable disease,", False),
        ("x", False),
        ("A " * 30, False),
    ],
)
def test_plausible_title(title: str, ok: bool) -> None:
    assert _plausible_title(title) is ok


def _row(index: int, text: str, x1: float, y0: float, bold: bool = True) -> Row:
    return Row(
        page=1, index=index, text=text, bbox=(72, y0, x1, y0 + 12), size=12, bold=bold, block=0
    )


def test_wrapped_heading_joins_following_line() -> None:
    rows = [
        _row(0, "5.3.4. A Heading Long Enough To Reach The Edge", 530, 100),
        _row(1, "Continued", 200, 112),
    ]
    assert [r.index for r in _continuation(rows, 0, right_edge=540)] == [1]


def test_short_heading_does_not_swallow_next_bold_line() -> None:
    # CDISC Pilot 3.9.3.4 "Other Safety Measures" followed by a bold run-in sub-heading.
    rows = [
        _row(0, "3.9.3.4. Other Safety Measures", 290, 276),
        _row(1, "Patients experiencing Rash", 356, 289),
    ]
    assert _continuation(rows, 0, right_edge=512) == []


def test_continuation_stops_at_body_text_style() -> None:
    rows = [
        _row(0, "1.2. Long Heading Reaching The Right Edge Of The Page", 535, 100),
        _row(1, "body text", 400, 112, bold=False),
    ]
    assert _continuation(rows, 0, right_edge=540) == []
