"""Page layout primitives: visual rows, running headers/footers, noise and printed-TOC detection."""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any

from backend.models.document import BBox

_BOLD_FONT = re.compile(r"bold|black|heavy|semibold|demi|,b\b", re.IGNORECASE)
_WS = re.compile(r"\s+")
_DOT_LEADER = re.compile(r"(\.{4,}|…{2,}|(\. ){4,})\s*\d{1,4}\s*$")
_TOC_TITLE = re.compile(
    r"^(table of contents|contents|"
    r"list of (tables|figures|appendices|attachments|in-text tables))\b",
    re.IGNORECASE,
)
_PAGE_LABEL = re.compile(
    r"^((document\s+)?page\s*)?#{1,4}(\s*(of|/)\s*#{1,4})?$|^-\s*#{1,4}\s*-$", re.IGNORECASE
)
_DIGITS = re.compile(r"\d+")


_DROP_CHARS = frozenset({chr(0xFFFD), chr(0x00AD)})  # replacement character, soft hyphen


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    # Also drop private-use glyphs: symbol fonts often map (R)/(TM) marks into the private-use
    # area, which renders as an empty box.
    text = "".join(ch for ch in text if ch not in _DROP_CHARS and unicodedata.category(ch) != "Co")
    return _WS.sub(" ", text).strip()


@dataclass
class Row:
    """A visual line of text: PDF lines that share a baseline, joined left to right."""

    page: int  # 1-based
    index: int  # position within the page's reading order
    text: str
    bbox: BBox
    size: float  # character-weighted font size
    bold: bool
    block: int

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x_center(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2


def _is_bold(span: dict[str, Any]) -> bool:
    return bool(span["flags"] & 16) or bool(_BOLD_FONT.search(span["font"]))


def extract_rows(page: Any, page_number: int) -> list[Row]:
    raw: list[tuple[BBox, str, float, float, int]] = []  # bbox, text, size, bold_frac, block
    for block_no, block in enumerate(page.get_text("dict")["blocks"]):
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            text = clean_text(" ".join(s["text"] for s in spans))
            if not text:
                continue
            weights = [max(len(s["text"].strip()), 1) for s in spans]
            total = sum(weights)
            size = sum(s["size"] * w for s, w in zip(spans, weights, strict=True)) / total
            bold = sum(w for s, w in zip(spans, weights, strict=True) if _is_bold(s)) / total
            raw.append((tuple(line["bbox"]), text, size, bold, block_no))

    # Merge PDF lines sharing a baseline (e.g. "1.2.1." and its title emitted as separate lines).
    raw.sort(key=lambda r: (round(r[0][1]), r[0][0]))
    merged: list[list[tuple[BBox, str, float, float, int]]] = []
    for item in raw:
        bbox = item[0]
        if merged:
            last = merged[-1][-1][0]
            height = min(bbox[3] - bbox[1], last[3] - last[1]) or 1.0
            overlap = min(bbox[3], last[3]) - max(bbox[1], last[1])
            if overlap >= 0.6 * height:
                merged[-1].append(item)
                continue
        merged.append([item])

    rows: list[Row] = []
    for group in merged:
        group.sort(key=lambda r: r[0][0])
        chars = [max(len(g[1]), 1) for g in group]
        total = sum(chars)
        rows.append(
            Row(
                page=page_number,
                index=len(rows),
                text=" ".join(g[1] for g in group),
                bbox=(
                    min(g[0][0] for g in group),
                    min(g[0][1] for g in group),
                    max(g[0][2] for g in group),
                    max(g[0][3] for g in group),
                ),
                size=round(sum(g[2] * c for g, c in zip(group, chars, strict=True)) / total, 2),
                bold=sum(g[3] * c for g, c in zip(group, chars, strict=True)) / total >= 0.6,
                block=group[0][4],
            )
        )
    return rows


def body_font_size(pages: list[list[Row]]) -> float:
    counter: Counter[float] = Counter()
    for rows in pages:
        for r in rows:
            counter[round(r.size * 2) / 2] += len(r.text)
    return counter.most_common(1)[0][0] if counter else 11.0


def _band_limits(height: float) -> tuple[float, float]:
    band = max(0.10 * height, 80.0)
    return band, height - band


_PAGE_WORD = re.compile(r"\bpage\b", re.IGNORECASE)


def _repeat_key(text: str) -> str:
    """Comparison key for running header/footer detection.

    Digits are ignored only where they are page numbering ("Page 5 of 97", a bare "12").
    Elsewhere the text must repeat exactly: "Appendix 1", "Appendix 2", ... at the top of
    consecutive pages are distinct headings, not a running header.
    """
    lowered = text.lower()
    if _PAGE_WORD.search(lowered) or len(lowered) <= 12:
        return _DIGITS.sub("#", lowered)
    return lowered


def find_running_rows(pages: list[list[Row]], heights: list[float]) -> set[tuple[int, int]]:
    """Rows in the top/bottom band whose text repeats across many pages."""
    min_pages = max(3, int(0.25 * len(pages)))
    seen: Counter[str] = Counter()
    for rows, height in zip(pages, heights, strict=True):
        top, bottom = _band_limits(height)
        keys = {_repeat_key(r.text) for r in rows if r.bbox[3] <= top or r.bbox[1] >= bottom}
        seen.update(keys)
    running = {k for k, n in seen.items() if n >= min_pages}

    result: set[tuple[int, int]] = set()
    for rows, height in zip(pages, heights, strict=True):
        top, bottom = _band_limits(height)
        for r in rows:
            if not (r.bbox[3] <= top or r.bbox[1] >= bottom):
                continue
            key = _repeat_key(r.text)
            if key in running or _PAGE_LABEL.match(key):
                result.add((r.page, r.index))
    return result


def is_noise(row: Row, body_size: float) -> bool:
    """Redaction stamps and watermarks: very large glyphs carrying almost no text."""
    return row.size >= 2.5 * body_size and len(row.text) <= 8


def is_toc_page(rows: list[Row]) -> bool:
    if not rows:
        return False
    leaders = sum(1 for r in rows if _DOT_LEADER.search(r.text))
    titled = any(_TOC_TITLE.match(r.text) for r in rows[:8])
    return leaders >= 5 or (titled and leaders >= 2) or leaders >= 0.4 * len(rows)


def inside(bbox: BBox, region: BBox, tolerance: float = 2.0) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return (
        region[0] - tolerance <= cx <= region[2] + tolerance
        and region[1] - tolerance <= cy <= region[3] + tolerance
    )
