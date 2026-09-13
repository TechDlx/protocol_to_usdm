"""Table structure, quality signals and Schedule-of-Activities scoring."""

import re
from dataclasses import dataclass, field
from typing import Any

from backend.models.document import BBox
from backend.pipeline.extractors.layout import Row, clean_text

_SOA_HEADER = re.compile(
    r"\b(screening|baseline|day|days|week|weeks|wk|visit|cycle|month|months|follow[- ]?up|"
    r"end of (treatment|study|trial)|randomi[sz]ation|treatment (period|phase)|eot|eos|"
    r"unscheduled|early (termination|discontinuation)|run[- ]in|washout|v\d+|d-?\d+|w-?\d+|c\d+)\b",
    re.IGNORECASE,
)
_MARK = re.compile(r"^(x|✓|✔|√|•|●|y|yes|\(x\)|x\s?[a-z,\d]{1,6})$", re.IGNORECASE)
_CAPTION = re.compile(r"^(table|figure|exhibit)\s+[\w.\-]+", re.IGNORECASE)
SOA_TITLE = re.compile(
    r"schedule of (activities|events|assessments|study procedures|evaluations)|"
    r"time (and|&) events|flow ?chart|study calendar|visit schedule",
    re.IGNORECASE,
)


@dataclass
class RawTable:
    page: int
    index: int
    bbox: BBox
    cells: list[list[str | None]]
    caption: str | None = None
    soa_score: float = 0.0
    group_id: str = ""
    reasons: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"tbl-p{self.page:04d}-{self.index + 1}"

    @property
    def row_count(self) -> int:
        return len(self.cells)

    @property
    def col_count(self) -> int:
        return max((len(r) for r in self.cells), default=0)

    @property
    def merged_cells(self) -> int:
        return sum(c is None for r in self.cells for c in r)

    @property
    def empty_ratio(self) -> float:
        flat = [c for r in self.cells for c in r]
        return round(sum(1 for c in flat if not (c or "").strip()) / len(flat), 3) if flat else 1.0

    def header_signature(self) -> str:
        head = self.cells[0] if self.cells else []
        return "|".join((c or "").lower()[:20] for c in head)


def extract_tables(page: Any, page_number: int) -> list[RawTable]:
    found: list[RawTable] = []
    for i, table in enumerate(page.find_tables().tables):
        cells = [
            [None if c is None else clean_text(str(c)) for c in row] for row in table.extract()
        ]
        if not cells or len(cells) * max(len(r) for r in cells) < 2:
            continue
        found.append(RawTable(page=page_number, index=i, bbox=tuple(table.bbox), cells=cells))
    return found


def attach_caption(table: RawTable, rows: list[Row]) -> None:
    above = [
        r
        for r in rows
        if r.bbox[3] <= table.bbox[1] + 2
        and table.bbox[1] - r.bbox[3] <= 40
        and _CAPTION.match(r.text)
    ]
    if above:
        table.caption = max(above, key=lambda r: r.bbox[3]).text


def soa_score(table: RawTable, page_text_hint: bool) -> float:
    if table.row_count < 3 or table.col_count < 3:
        return 0.0
    header_cells = [c for row in table.cells[:3] for c in row if c]
    header_hits = sum(1 for c in header_cells if _SOA_HEADER.search(c))
    body = [c for row in table.cells[1:] for c in row[1:] if c and c.strip()]
    mark_ratio = sum(1 for c in body if _MARK.match(c.strip())) / len(body) if body else 0.0
    score = (
        0.40 * min(header_hits / 4, 1.0)
        + 0.40 * min(mark_ratio / 0.25, 1.0)
        + 0.05 * (table.col_count >= 5)
        + 0.15 * page_text_hint
    )
    return round(min(score, 1.0), 3)


def to_markdown(cells: list[list[str | None]]) -> str:
    width = max(len(r) for r in cells)

    def fmt(row: list[str | None]) -> str:
        padded = [*row, *([""] * (width - len(row)))]
        return "| " + " | ".join((c or "").replace("|", "\\|") for c in padded) + " |"

    lines = [fmt(cells[0]), "|" + "---|" * width]
    lines.extend(fmt(r) for r in cells[1:])
    return "\n".join(lines)


def group_tables(tables: list[RawTable]) -> None:
    """Chain tables continuing onto the next page (same column count or same header)."""
    previous: RawTable | None = None
    for t in sorted(tables, key=lambda t: (t.page, t.index)):
        continues = (
            previous is not None
            and t.page == previous.page + 1
            and t.index == 0
            and (
                t.col_count == previous.col_count
                or t.header_signature() == previous.header_signature()
            )
        )
        t.group_id = previous.group_id if continues and previous else f"grp-{t.id}"
        previous = t
