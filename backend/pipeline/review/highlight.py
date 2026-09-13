"""Render the protocol page region a value was quoted from, with the quote highlighted.

Rendered on demand from the original PDF, so the reviewer sees the source exactly as printed.
When the quote cannot be located on the page (a table cell, or text split oddly by the PDF), the
whole page is returned instead, and the caller is told the quote was not found.
"""

import re
from pathlib import Path
from typing import Any

import pymupdf

_WS = re.compile(r"\s+")
CONTEXT_POINTS = 90.0  # vertical context kept above and below the highlighted text
HIGHLIGHT_DPI = 120
FULL_PAGE_DPI = 90


def _search(page: Any, quote: str) -> list[Any]:
    """Rectangles of the quote on the page, trying progressively shorter prefixes."""
    words = _WS.sub(" ", quote).strip().split(" ")
    for size in dict.fromkeys((len(words), 12, 8, 5)):  # ordered, without repeats
        if size > len(words):
            continue
        rects: list[Any] = page.search_for(" ".join(words[:size]))
        if rects:
            return rects
    return []


def render_highlight(pdf_path: Path, page_number: int, quote: str | None) -> tuple[bytes, bool]:
    """PNG bytes of the highlighted region (or the full page), and whether the quote was found."""
    with pymupdf.open(stream=pdf_path.read_bytes(), filetype="pdf") as doc:  # type: ignore[no-untyped-call]
        if not 1 <= page_number <= doc.page_count:
            raise ValueError(f"page {page_number} is outside 1-{doc.page_count}")
        page = doc[page_number - 1]
        rects = _search(page, quote) if quote and quote.strip() else []
        if not rects:
            return page.get_pixmap(dpi=FULL_PAGE_DPI).tobytes("png"), False

        for rect in rects:
            annot = page.add_highlight_annot(rect)
            annot.set_colors(stroke=(1.0, 0.85, 0.2))
            annot.update()
        top = max(page.rect.y0, min(r.y0 for r in rects) - CONTEXT_POINTS)
        bottom = min(page.rect.y1, max(r.y1 for r in rects) + CONTEXT_POINTS)
        clip = pymupdf.Rect(page.rect.x0, top, page.rect.x1, bottom)  # type: ignore[no-untyped-call]
        pixmap = page.get_pixmap(dpi=HIGHLIGHT_DPI, clip=clip, annots=True)
        return pixmap.tobytes("png"), True
