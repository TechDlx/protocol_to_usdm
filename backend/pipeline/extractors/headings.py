"""Heading detection.

Protocols style headings inconsistently (PALOMA-3: bold at body size; CDISC Pilot: larger Arial)
and PDF bookmarks are often missing or incomplete, so headings are found from the page text and
bookmarks are used as corroborating evidence.

Numbered candidates are filtered with a best-chain search: of all candidates, keep the
highest-evidence subsequence whose numbers form a valid outline progression. A bold numbered list
item ("3. Patients must...") inside section 2 cannot then displace the real section 3.
"""

import re
from dataclasses import dataclass, field

from backend.models.document import HeadingSource, SectionKind
from backend.pipeline.extractors.layout import Row

_NUMBERED = re.compile(r"^(?P<num>\d{1,2}(?:\.\d{1,3}){0,5})\.?\s+(?P<title>\S.*)$")
_APPENDIX = re.compile(
    r"^(?P<kw>(?:protocol\s+)?(?:appendix|attachment|annex))\s+"
    # Optional separator after the id: any single punctuation mark (".", ":", "-", en dash...).
    r"(?P<id>[A-Z0-9]{1,6}(?:[.\-][A-Z0-9]{1,6}){0,3})\.?\s*[^\w\s(]?\s*(?P<title>.*)$",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[,;]$|\.\s*$")
_WORD = re.compile(r"[A-Za-z]")


@dataclass
class Heading:
    page: int
    row_index: int
    y0: float
    bbox: tuple[float, float, float, float] | None
    title: str
    number: str | None
    kind: SectionKind
    level: int
    source: HeadingSource = HeadingSource.TEXT
    weight: float = 1.0
    extra_row_indexes: list[int] = field(default_factory=list)  # page-level Row.index values
    appendix_key: str | None = None  # "LZZT.1", "4" — identifies repeated appendix headings

    @property
    def number_parts(self) -> tuple[int, ...]:
        return tuple(int(p) for p in self.number.split(".")) if self.number else ()


def _styled(row: Row, body_size: float) -> bool:
    return row.bold or row.size >= body_size + 1.5


def _plausible_title(title: str) -> bool:
    letters = _WORD.findall(title)
    if len(letters) < 2 or len(title) > 160 or len(title.split()) > 22:
        return False
    first_alpha = next((ch for ch in title if ch.isalpha()), "")
    starts_ok = title[0].isdigit() or first_alpha.isupper()
    return starts_ok and not _SENTENCE_END.search(title)


def _continuation(rows: list[Row], pos: int, right_edge: float, max_rows: int = 2) -> list[Row]:
    """Rows continuing a heading that wrapped onto following lines.

    `pos` is the anchor's position in `rows`. A heading only wraps if it runs close to the right
    text edge; a short heading followed by another bold line is two separate things
    ("Other Safety Measures" then a bold run-in "Patients experiencing Rash...").
    """
    anchor = rows[pos]
    if anchor.bbox[2] < right_edge - 0.18 * (right_edge - anchor.bbox[0]):
        return []
    extra: list[Row] = []
    prev = anchor
    for row in rows[pos + 1 : pos + 1 + max_rows]:
        gap = row.bbox[1] - prev.bbox[3]
        line_h = prev.bbox[3] - prev.bbox[1]
        same_style = row.bold == anchor.bold and abs(row.size - anchor.size) < 0.6
        if not same_style or gap > 0.8 * line_h or _NUMBERED.match(row.text):
            break
        extra.append(row)
        if row.bbox[2] < right_edge - 0.18 * (right_edge - row.bbox[0]):
            break  # this line did not reach the edge, so the heading ends here
        prev = row
    return extra


def right_text_edge(rows: list[Row]) -> float:
    """Typical right edge of full-width text lines on the page."""
    edges = sorted(r.bbox[2] for r in rows)
    return edges[int(0.9 * (len(edges) - 1))] if edges else 0.0


def numbered_candidates(rows: list[Row], body_size: float) -> list[Heading]:
    found: list[Heading] = []
    edge = right_text_edge(rows)
    for i, row in enumerate(rows):
        m = _NUMBERED.match(row.text)
        if not m or not _styled(row, body_size):
            continue
        parts = [int(p) for p in m["num"].split(".")]
        if parts[0] == 0 or parts[0] > 30 or any(p > 99 for p in parts):
            continue
        extra = _continuation(rows, i, edge)
        title = " ".join([m["title"].strip(), *(r.text for r in extra)])
        if not _plausible_title(title):
            continue
        weight = 1.0 + (1.0 if row.size >= body_size + 1.5 else 0.0) + (0.5 if row.bold else 0.0)
        found.append(
            Heading(
                page=row.page,
                row_index=row.index,
                y0=row.y0,
                bbox=row.bbox,
                title=title,
                number=".".join(str(p) for p in parts),
                kind=SectionKind.BODY,
                level=len(parts),
                weight=weight,
                extra_row_indexes=[r.index for r in extra],
            )
        )
    return found


def appendix_candidates(rows: list[Row], body_size: float) -> list[Heading]:
    found: list[Heading] = []
    for i, row in enumerate(rows):
        m = _APPENDIX.match(row.text)
        if not m or not _styled(row, body_size) or len(row.text) > 160:
            continue
        title = m["title"].strip()
        extra: list[Row] = []
        if not title:
            # "Protocol Attachment LZZT.1" with the title on the next line(s): take the directly
            # following rows in the same style, regardless of line length.
            extra = _continuation(rows, i, right_edge=0.0)
            title = " ".join(r.text for r in extra)
        label = f"{m['kw'].strip().title()} {m['id']}"
        found.append(
            Heading(
                page=row.page,
                row_index=row.index,
                y0=row.y0,
                bbox=row.bbox,
                title=f"{label}. {title}".strip(" .") if title else label,
                number=None,
                kind=SectionKind.APPENDIX,
                level=1,
                weight=2.0,
                extra_row_indexes=[r.index for r in extra],
                appendix_key=m["id"].upper(),
            )
        )
    return found


def front_matter_candidates(rows: list[Row], body_size: float, page_width: float) -> list[Heading]:
    """Unnumbered headings such as PROTOCOL SUMMARY or SCHEDULE OF ACTIVITIES."""
    found: list[Heading] = []
    skip: set[int] = set()
    for i, row in enumerate(rows):
        if row.index in skip or not row.bold or row.size < body_size - 0.5:
            continue
        text = row.text
        if not 4 <= len(text) <= 90 or text.endswith(":") or _NUMBERED.match(text):
            continue
        letters = [ch for ch in text if ch.isalpha()]
        if len(letters) < 4:
            continue
        caps = sum(ch.isupper() for ch in letters) / len(letters)
        centered = abs(row.x_center - page_width / 2) < 0.08 * page_width
        if not (caps >= 0.85 or centered or row.size >= body_size + 2.5):
            continue
        extra = _continuation(rows, i, right_edge=0.0)
        skip.update(r.index for r in extra)
        found.append(
            Heading(
                page=row.page,
                row_index=row.index,
                y0=row.y0,
                bbox=row.bbox,
                title=" ".join([text, *(r.text for r in extra)]),
                number=None,
                kind=SectionKind.FRONT_MATTER,
                level=1,
                weight=1.0,
                extra_row_indexes=[r.index for r in extra],
            )
        )
    return found


def _valid_start(parts: tuple[int, ...]) -> bool:
    return parts[0] <= 3 and all(p <= 2 for p in parts[1:])


def _valid_next(prev: tuple[int, ...], cur: tuple[int, ...], gap: int = 3) -> bool:
    # child (tolerate a missing first child: 1.2 -> 1.2.2)
    if len(cur) == len(prev) + 1 and cur[:-1] == prev and 1 <= cur[-1] <= gap:
        return True
    # skipped level: 1.2 -> 1.2.1.1 when 1.2.1 was not detected
    if (
        len(cur) > len(prev) + 1
        and cur[: len(prev)] == prev
        and all(p <= 2 for p in cur[len(prev) :])
    ):
        return True
    # sibling at this depth or at any ancestor depth, possibly skipping a few numbers
    for depth in range(len(prev), 0, -1):
        if (
            len(cur) >= depth
            and cur[: depth - 1] == prev[: depth - 1]
            and prev[depth - 1] < cur[depth - 1] <= prev[depth - 1] + gap
            and all(p <= 2 for p in cur[depth:])
        ):
            return True
    return False


def best_numbered_chain(candidates: list[Heading]) -> tuple[list[Heading], list[Heading]]:
    """Maximum-weight subsequence forming a valid numbering progression: (kept, rejected)."""
    n = len(candidates)
    if n == 0:
        return [], []
    best = [0.0] * n
    back = [-1] * n
    for i, cand in enumerate(candidates):
        parts = cand.number_parts
        best[i] = cand.weight if _valid_start(parts) else float("-inf")
        for j in range(i):
            if best[j] == float("-inf"):
                continue
            if _valid_next(candidates[j].number_parts, parts) and best[j] + cand.weight > best[i]:
                best[i] = best[j] + cand.weight
                back[i] = j
    end = max(range(n), key=lambda k: best[k])
    if best[end] == float("-inf"):
        return [], list(candidates)
    keep: set[int] = set()
    k = end
    while k != -1:
        keep.add(k)
        k = back[k]
    kept = [c for idx, c in enumerate(candidates) if idx in keep]
    rejected = [c for idx, c in enumerate(candidates) if idx not in keep]
    return kept, rejected
