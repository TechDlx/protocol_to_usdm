"""Reviewer corrections of where sections start, applied to the parsed document.

Heading detection sometimes starts a section too early (a heading-like line on a signature page)
or too late. A reviewer can set the page a section starts on: whole pages before it move to the
previous section in reading order, and pages of the previous section from that page on move to
this one, with their tables. Content is moved by the `[[PAGE n]]` markers the parser writes into
every section's text, so nothing is re-extracted from the PDF.

Files, inside the run folder:
    parsed_document.raw.json     the parser's own output, kept once boundaries are in use
    section_boundaries.json      the reviewer's start pages (survive re-parsing)
    parsed_document.json         the document every later stage reads, boundaries applied

A boundary whose section is gone or re-titled after a re-parse is not applied; the document's
warnings say so.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

from backend.models.document import ParsedDocument, Section, SectionKind
from backend.models.segmentation import SectionBoundaries, SectionBoundary
from backend.storage.fs import write_model

RAW_DOCUMENT_FILE = "parsed_document.raw.json"
BOUNDARIES_FILE = "section_boundaries.json"

_PAGE = re.compile(r"^\[\[PAGE (\d+)\]\]$")
_TABLE = re.compile(r"\[\[TABLE ([^\]]+)\]\]")


class BoundaryError(ValueError):
    """The requested start page is not possible for this section."""


def load_boundaries(run_dir: Path) -> SectionBoundaries:
    path = run_dir / BOUNDARIES_FILE
    if not path.is_file():
        return SectionBoundaries()
    return SectionBoundaries.model_validate_json(path.read_text(encoding="utf-8"))


def load_raw_document(run_dir: Path) -> ParsedDocument | None:
    path = run_dir / RAW_DOCUMENT_FILE
    if not path.is_file():
        return None
    return ParsedDocument.model_validate_json(path.read_text(encoding="utf-8"))


def _pages(text: str) -> list[tuple[int, list[str]]]:
    """A section's text as (page, lines) segments, in order."""
    segments: list[tuple[int, list[str]]] = []
    for line in text.split("\n") if text else []:
        marker = _PAGE.match(line)
        if marker:
            segments.append((int(marker.group(1)), []))
        elif segments:
            segments[-1][1].append(line)
        else:  # content before any marker (not produced by the parser); keep it in place
            segments.append((0, [line]))
    return segments


def _text(segments: list[tuple[int, list[str]]]) -> str:
    merged: list[tuple[int, list[str]]] = []
    for page, lines in sorted(segments, key=lambda s: s[0]):  # stable: reading order within a page
        if merged and merged[-1][0] == page:
            merged[-1][1].extend(lines)
        else:
            merged.append((page, list(lines)))
    out: list[str] = []
    for page, lines in merged:
        if page:
            out.append(f"[[PAGE {page}]]")
        out.extend(lines)
    return "\n".join(out)


def own_pages(section: Section) -> list[int]:
    return [page for page, _ in _pages(section.text) if page]


def allowed_start_pages(document: ParsedDocument, section_id: str) -> tuple[Section, int, int]:
    """The previous section and the range of start pages a section may take."""
    index = next((i for i, s in enumerate(document.sections) if s.id == section_id), None)
    if index is None:
        raise KeyError(section_id)
    if index == 0:
        raise BoundaryError("the first section has no previous section to exchange pages with")
    section, previous = document.sections[index], document.sections[index - 1]
    if section.kind == SectionKind.TOC or previous.kind == SectionKind.TOC:
        raise BoundaryError("the table of contents' pages are fixed by the parser")
    low = previous.page_start + 1  # the previous section keeps at least its first page
    high = max([section.page_start, *own_pages(section), *own_pages(previous)])
    return previous, low, high


def _move(document: ParsedDocument, boundary: SectionBoundary) -> str | None:
    """Apply one boundary in place. Returns a warning when it cannot be applied."""
    section = next((s for s in document.sections if s.id == boundary.section_id), None)
    if section is None:
        return f"page start for '{boundary.doc_title}' not applied: the section is gone"
    if section.title != boundary.doc_title:
        return (
            f"page start for '{boundary.doc_title}' not applied: the section is now titled "
            f"'{section.title}'"
        )
    try:
        previous, low, high = allowed_start_pages(document, section.id)
    except BoundaryError as exc:
        return f"page start for '{section.title}' not applied: {exc}"
    start = boundary.start_page
    if not low <= start <= high:
        return (
            f"page start {start} for '{section.title}' not applied: pages {low}-{high} are "
            "possible after re-parsing"
        )

    mine, theirs = _pages(section.text), _pages(previous.text)
    to_previous = [seg for seg in mine if 0 < seg[0] < start]
    to_section = [seg for seg in theirs if seg[0] >= start]
    section.text = _text([*to_section, *(seg for seg in mine if not 0 < seg[0] < start)])
    previous.text = _text([*(seg for seg in theirs if seg[0] < start), *to_previous])

    def moved_tables(segments: list[tuple[int, list[str]]]) -> list[str]:
        return [
            m.group(1) for _, lines in segments for line in lines for m in _TABLE.finditer(line)
        ]

    tables = {t.id: t for t in document.tables}
    for table_id in moved_tables(to_previous):
        section.table_ids = [t for t in section.table_ids if t != table_id]
        previous.table_ids.append(table_id)
        if table_id in tables:
            tables[table_id].section_id = previous.id
    for table_id in moved_tables(to_section):
        previous.table_ids = [t for t in previous.table_ids if t != table_id]
        section.table_ids.insert(0, table_id)
        if table_id in tables:
            tables[table_id].section_id = section.id

    if section.parsed_page_start is None:
        section.parsed_page_start = section.page_start
    section.page_start = start
    return None


def _page_ends(document: ParsedDocument) -> None:
    """Recompute page ends as the parser does: own content, then each section's subtree."""
    sections = document.sections
    for s in sections:
        s.page_end = max([s.page_start, *own_pages(s)])
    body = sections[1:]
    for i, s in enumerate(body):
        j = i + 1
        while j < len(body) and body[j].level > s.level and body[j].kind != SectionKind.TOC:
            s.page_end = max(s.page_end, body[j].page_end)
            j += 1


def apply_boundaries(raw: ParsedDocument, boundaries: SectionBoundaries) -> ParsedDocument:
    """The raw document with the reviewer's start pages applied, in reading order."""
    document = raw.model_copy(deep=True)
    if not boundaries.boundaries:
        return document
    order = {s.id: i for i, s in enumerate(raw.sections)}
    ordered = sorted(boundaries.boundaries.values(), key=lambda b: order.get(b.section_id, -1))
    for boundary in ordered:
        warning = _move(document, boundary)
        if warning:
            document.warnings.append(warning)
    _page_ends(document)
    return document


def set_boundary(run_dir: Path, raw: ParsedDocument, section_id: str, start_page: int) -> None:
    """Record a start page after checking it against the document with the other boundaries."""
    boundaries = load_boundaries(run_dir)
    others = SectionBoundaries(
        boundaries={k: v for k, v in boundaries.boundaries.items() if k != section_id}
    )
    current = apply_boundaries(raw, others)
    previous, low, high = allowed_start_pages(current, section_id)  # KeyError / BoundaryError
    section = current.section(section_id)
    if not low <= start_page <= high:
        raise BoundaryError(
            f"'{section.title}' can start on pages {low}-{high} (the previous section, "
            f"'{previous.title}', starts on page {previous.page_start})"
        )
    parsed = next(s for s in raw.sections if s.id == section_id).page_start
    if start_page == parsed:
        boundaries.boundaries.pop(section_id, None)  # back to what the parser found
    else:
        boundaries.boundaries[section_id] = SectionBoundary(
            section_id=section_id,
            doc_title=section.title,
            start_page=start_page,
            updated_at=datetime.now(UTC),
        )
    write_model(run_dir / BOUNDARIES_FILE, boundaries)


def clear_boundary(run_dir: Path, section_id: str) -> None:
    """Raises KeyError when the section's start page was not changed."""
    boundaries = load_boundaries(run_dir)
    del boundaries.boundaries[section_id]
    write_model(run_dir / BOUNDARIES_FILE, boundaries)


def raw_document(run_dir: Path, current: ParsedDocument | None) -> ParsedDocument | None:
    """The parser's own output: the kept raw file, or the current document when no boundary has
    ever been applied (it is then unchanged)."""
    return load_raw_document(run_dir) or current


def finalise_document(run_dir: Path, raw: ParsedDocument) -> ParsedDocument:
    """The document later stages read. With boundaries the raw output is kept alongside, so a
    boundary can be changed or reverted later; without, the raw file is not needed."""
    boundaries = load_boundaries(run_dir)
    if not boundaries.boundaries:
        (run_dir / RAW_DOCUMENT_FILE).unlink(missing_ok=True)
        return raw
    write_model(run_dir / RAW_DOCUMENT_FILE, raw)
    return apply_boundaries(raw, boundaries)
