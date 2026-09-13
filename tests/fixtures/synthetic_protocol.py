"""A small generated protocol PDF exercising the layout traps seen in real protocols.

Page 1  title page
Page 2  printed table of contents (dot leaders)
Page 3  SCHEDULE OF ACTIVITIES front matter with a ruled grid of X marks
Page 4  1. Introduction / 1.1. Background, plus a bold numbered list item "3. ..." in the body
Page 5  2. Study Population / 2.1. Inclusion Criteria / 2.2. heading wrapped onto two lines
Page 6  3. Statistical Methods / 3.1. Sample Size / Appendix 1. List of Abbreviations

Pages 3-6 carry a running header and a "Page N" footer that must not leak into section text.
"""

from pathlib import Path

import pymupdf

BODY = "helv"
BOLD = "hebo"
LEFT, RIGHT = 72.0, 540.0
HEADER_TEXT = "Examplumab Protocol EX-001 Confidential"
WRAPPED_TITLE_1 = "2.2. Randomisation Stratification Factors and Procedures Applied Across"
WRAPPED_TITLE_2 = "All Participating Sites"
SOA_ROWS = [
    ["Procedure", "Screening", "Day 1", "Week 4", "Week 8"],
    ["Vital signs", "X", "X", "X", "X"],
    ["ECG", "X", "", "X", ""],
    ["Hematology", "X", "X", "", "X"],
    ["Adverse events", "", "X", "X", "X"],
]
FULL_LINE = (
    "This body line is deliberately long so that it reaches the right text edge of the page."
)


class _Writer:
    def __init__(self, page: pymupdf.Page) -> None:
        self.page = page
        self.y = 110.0

    def text(self, value: str, size: float = 11, bold: bool = False, gap: float = 6) -> None:
        self.page.insert_text((LEFT, self.y), value, fontname=BOLD if bold else BODY, fontsize=size)
        self.y += size + gap

    def body(self, lines: int = 3) -> None:
        for _ in range(lines):
            self.text(FULL_LINE)
        self.y += 6


def _running(page: pymupdf.Page, number: int) -> None:
    page.insert_text((LEFT, 40), HEADER_TEXT, fontname=BODY, fontsize=8)
    page.insert_text((LEFT, 770), f"Page {number}", fontname=BODY, fontsize=8)


def _grid(page: pymupdf.Page, top: float) -> float:
    col_w, row_h = 90.0, 22.0
    for r, row in enumerate(SOA_ROWS):
        for c, value in enumerate(row):
            rect = pymupdf.Rect(
                LEFT + c * col_w, top + r * row_h, LEFT + (c + 1) * col_w, top + (r + 1) * row_h
            )
            page.draw_rect(rect, color=(0, 0, 0), width=0.8)
            if value:
                page.insert_text(
                    (rect.x0 + 4, rect.y1 - 7), value, fontname=BOLD if r == 0 else BODY, fontsize=9
                )
    return top + len(SOA_ROWS) * row_h


def build(path: Path) -> Path:
    doc = pymupdf.open()

    title = doc.new_page()
    title.insert_text((LEFT, 200), "A Phase 3 Trial of Examplumab", fontname=BOLD, fontsize=20)
    title.insert_text((LEFT, 240), "Protocol EX-001", fontname=BODY, fontsize=12)

    toc = _Writer(doc.new_page())
    toc.text("Table of Contents", size=14, bold=True)
    for entry, pg in [
        ("1. Introduction", 4),
        ("1.1. Background", 4),
        ("2. Study Population", 5),
        ("2.1. Inclusion Criteria", 5),
        ("3. Statistical Methods", 6),
        ("3.1. Sample Size", 6),
    ]:
        toc.text(f"{entry} {'.' * (70 - len(entry))} {pg}")

    soa_page = doc.new_page()
    _running(soa_page, 3)
    soa = _Writer(soa_page)
    soa.text("SCHEDULE OF ACTIVITIES", size=12, bold=True)
    soa.y = _grid(soa_page, soa.y + 10) + 20
    soa.text("a. Vital signs are taken before dosing.")

    intro_page = doc.new_page()
    _running(intro_page, 4)
    intro = _Writer(intro_page)
    intro.text("1. Introduction", size=14, bold=True)
    intro.body()
    intro.text("1.1. Background", size=12, bold=True)
    intro.body()
    intro.text("3. Patients With Prior Exposure Are Described Here", size=11, bold=True)
    intro.body(2)

    pop_page = doc.new_page()
    _running(pop_page, 5)
    pop = _Writer(pop_page)
    pop.text("2. Study Population", size=14, bold=True)
    pop.body()
    pop.text("2.1. Inclusion Criteria", size=12, bold=True)
    pop.body()
    pop.text(WRAPPED_TITLE_1, size=11, bold=True, gap=2)
    pop.text(WRAPPED_TITLE_2, size=11, bold=True)
    pop.body()

    stats_page = doc.new_page()
    _running(stats_page, 6)
    stats = _Writer(stats_page)
    stats.text("3. Statistical Methods", size=14, bold=True)
    stats.body()
    stats.text("3.1. Sample Size", size=12, bold=True)
    stats.body()
    stats.text("Appendix 1. List of Abbreviations", size=14, bold=True)
    stats.text("AE    Adverse event")

    doc.save(path)
    doc.close()
    return path
