"""Default, fully local PDF backend built on PyMuPDF."""

import bisect
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf
from rapidfuzz import fuzz
from slugify import slugify

from backend.models.document import (
    DocumentStats,
    ExtractorInfo,
    HeadingSource,
    OutlineEntry,
    PageInfo,
    ParsedDocument,
    Section,
    SectionKind,
    SourceInfo,
    Table,
)
from backend.pipeline.extractors.base import PdfExtractor
from backend.pipeline.extractors.headings import (
    Heading,
    appendix_candidates,
    best_numbered_chain,
    front_matter_candidates,
    numbered_candidates,
)
from backend.pipeline.extractors.layout import (
    Row,
    body_font_size,
    clean_text,
    extract_rows,
    find_running_rows,
    inside,
    is_noise,
    is_toc_page,
)
from backend.pipeline.extractors.tables import (
    SOA_TITLE,
    RawTable,
    attach_caption,
    extract_tables,
    group_tables,
    soa_score,
    to_markdown,
)

log = logging.getLogger(__name__)

_OUTLINE_SKIP = re.compile(
    r"^(table|figure|listing|exhibit)\s*[\w.\-]*\d|^(table of contents|contents|list of )",
    re.IGNORECASE,
)
_OUTLINE_NUMBER = re.compile(r"^(\d{1,2}(?:\.\d{1,3}){0,5})\.?\s+(.*)$")
SOA_THRESHOLD = 0.5
TITLE_PAGE_ID = "title-page"
TOC_ID = "toc"


def page_marker(page: int) -> str:
    return f"[[PAGE {page}]]"


def norm_title(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


@dataclass
class _Layout:
    """Everything read from the PDF in one pass, before interpretation."""

    rows: list[list[Row]]  # per page, all rows
    content: list[list[Row]]  # per page, rows that are real content
    widths: list[float]
    body_size: float
    toc_pages: set[int]
    tables: list[RawTable]
    pages: list[PageInfo]
    outline: list[list[Any]]


class PyMuPdfExtractor(PdfExtractor):
    name = "pymupdf"
    version = "3"  # 2: private-use glyphs stripped; 3: [[PAGE n]] markers in section text

    def extract(
        self,
        pdf_path: Path,
        source: SourceInfo,
        page_images_dir: Path,
        image_path_prefix: str,
        dpi: int,
    ) -> ParsedDocument:
        started = time.perf_counter()
        warnings: list[str] = []
        layout = self._read(pdf_path, page_images_dir, image_path_prefix, dpi)

        headings, rejected = self._detect_headings(layout)
        outline, outline_headings, headings = self._reconcile_outline(layout, headings, warnings)

        page_hint = {
            i + 1
            for i, rows in enumerate(layout.rows)
            if i + 1 not in layout.toc_pages and any(SOA_TITLE.search(r.text) for r in rows)
        }
        for t in layout.tables:
            attach_caption(t, layout.content[t.page - 1])
            t.soa_score = soa_score(t, t.page in page_hint)
        group_tables(layout.tables)

        sections, section_of_heading = self._build_sections(headings, layout)
        for entry_index, heading in outline_headings.items():
            outline[entry_index].matched_section_id = section_of_heading.get(id(heading))
        tables = self._finalise_tables(layout.tables, sections, layout.pages)
        soa_pages = sorted({t.page for t in tables if t.is_soa_candidate})

        if not any(s.kind == SectionKind.BODY for s in sections):
            warnings.append("no numbered body sections detected; section mapping will be weak")
        if not soa_pages:
            warnings.append("no Schedule of Activities table detected from table structure")

        return ParsedDocument(
            source=source,
            extractor=ExtractorInfo(
                name=self.name,
                version=self.version,
                library_version=str(pymupdf.VersionBind),
                page_image_dpi=dpi,
            ),
            pages=layout.pages,
            sections=sections,
            tables=tables,
            outline=outline,
            stats=DocumentStats(
                body_font_size=layout.body_size,
                headings_from_text=sum(1 for h in headings if h.source != HeadingSource.OUTLINE),
                headings_from_outline_only=sum(
                    1 for h in headings if h.source == HeadingSource.OUTLINE
                ),
                rejected_heading_candidates=len(rejected),
                tables=len(tables),
                tables_needing_vision=sum(1 for t in tables if t.needs_vision),
                soa_pages=soa_pages,
                elapsed_seconds=round(time.perf_counter() - started, 2),
            ),
            warnings=warnings,
        )

    # ----- reading -----------------------------------------------------------------------------

    @staticmethod
    def _read(pdf_path: Path, images_dir: Path, prefix: str, dpi: int) -> _Layout:
        images_dir.mkdir(parents=True, exist_ok=True)
        with pymupdf.open(stream=pdf_path.read_bytes(), filetype="pdf") as doc:  # type: ignore[no-untyped-call]
            pdf_pages: list[Any] = list(doc)
            rows = [extract_rows(p, i + 1) for i, p in enumerate(pdf_pages)]
            body = body_font_size(rows)
            running = find_running_rows(rows, [float(p.rect.height) for p in pdf_pages])
            toc_pages = {i + 1 for i, page_rows in enumerate(rows) if is_toc_page(page_rows)}

            tables: list[RawTable] = []
            pages: list[PageInfo] = []
            for i, page in enumerate(pdf_pages):
                number = i + 1
                if number not in toc_pages:
                    tables.extend(extract_tables(page, number))
                filename = f"page-{number:04d}.png"
                page.get_pixmap(dpi=dpi).save(images_dir / filename)
                pages.append(
                    PageInfo(
                        number=number,
                        width=round(float(page.rect.width), 1),
                        height=round(float(page.rect.height), 1),
                        rotation=int(page.rotation),
                        landscape=bool(page.rect.width > page.rect.height),
                        image_path=f"{prefix}/{filename}",
                        char_count=sum(len(r.text) for r in rows[i]),
                        is_toc_page=number in toc_pages,
                        removed_header_footer_lines=sum(
                            1 for r in rows[i] if (r.page, r.index) in running
                        ),
                        redaction_marks=sum(1 for r in rows[i] if is_noise(r, body)),
                    )
                )
            outline: list[list[Any]] = doc.get_toc(simple=True)
            widths = [float(p.rect.width) for p in pdf_pages]

        by_page: dict[int, list[RawTable]] = {}
        for t in tables:
            by_page.setdefault(t.page, []).append(t)
        content = [
            [
                r
                for r in page_rows
                if (r.page, r.index) not in running
                and not is_noise(r, body)
                and not any(inside(r.bbox, t.bbox) for t in by_page.get(r.page, []))
            ]
            for page_rows in rows
        ]
        return _Layout(rows, content, widths, body, toc_pages, tables, pages, outline)

    # ----- headings ----------------------------------------------------------------------------

    @staticmethod
    def _detect_headings(layout: _Layout) -> tuple[list[Heading], list[Heading]]:
        numbered: list[Heading] = []
        appendices: list[Heading] = []
        for page_number, rows in enumerate(layout.content, start=1):
            if page_number in layout.toc_pages:
                continue
            numbered.extend(numbered_candidates(rows, layout.body_size))
            appendices.extend(appendix_candidates(rows, layout.body_size))

        # Appendices restart their own numbering ("7. Orientation" inside an ADAS-Cog attachment).
        # Once the appendix block begins, numbered lines are appendix content, not protocol
        # sections. Bookmarks can still reinstate a genuine numbered section in reconciliation.
        first_numbered = min(((h.page, h.y0) for h in numbered), default=None)
        appendix_start = min(
            (
                (h.page, h.y0)
                for h in appendices
                if first_numbered is not None and (h.page, h.y0) > first_numbered
            ),
            default=None,
        )
        if appendix_start is not None:
            numbered = [h for h in numbered if (h.page, h.y0) < appendix_start]

        kept, rejected = best_numbered_chain(numbered)
        first_body = min(((h.page, h.y0) for h in kept if h.level == 1), default=None)
        appendix_rows = {(h.page, h.row_index) for h in appendices}
        title_page_text = (
            norm_title(" ".join(r.text for r in layout.content[0])) if layout.content else ""
        )

        front: list[Heading] = []
        for page_number, rows in enumerate(layout.content, start=1):
            if page_number == 1 or page_number in layout.toc_pages:
                continue  # page 1 is the title page
            for h in front_matter_candidates(
                rows, layout.body_size, layout.widths[page_number - 1]
            ):
                if first_body is not None and (h.page, h.y0) >= first_body:
                    continue
                if (h.page, h.row_index) in appendix_rows:
                    continue
                # The protocol title repeated above section 1 is not a section.
                bare = norm_title(h.title)
                if len(bare) >= 20 and fuzz.partial_ratio(bare, title_page_text) >= 90:
                    continue
                front.append(h)

        headings: list[Heading] = []
        for h in sorted([*kept, *appendices, *front], key=lambda h: (h.page, h.y0)):
            prev = headings[-1] if headings else None
            # A heading repeated at the top of the next page is a running continuation.
            if prev is not None and h.number is None and h.page in (prev.page, prev.page + 1):
                same_appendix = h.appendix_key is not None and h.appendix_key == prev.appendix_key
                if same_appendix or norm_title(h.title) == norm_title(prev.title):
                    continue
            headings.append(h)
        return headings, rejected

    @staticmethod
    def _reconcile_outline(
        layout: _Layout, headings: list[Heading], warnings: list[str]
    ) -> tuple[list[OutlineEntry], dict[int, Heading], list[Heading]]:
        """Corroborate detected headings with PDF bookmarks; add bookmarked headings we missed."""
        entries: list[OutlineEntry] = []
        matched: dict[int, Heading] = {}
        added: list[Heading] = []
        for level, raw_title, raw_page in layout.outline:
            title, page = clean_text(str(raw_title)), int(raw_page)
            entries.append(
                OutlineEntry(level=int(level), title=title, page=page, matched_section_id=None)
            )
            if _OUTLINE_SKIP.search(title) or not 1 <= page <= len(layout.content):
                continue
            m = _OUTLINE_NUMBER.match(title)
            number = m.group(1) if m else None
            bare_title = (m.group(2) if m else title).strip()
            bare = norm_title(bare_title)

            def agrees(
                h: Heading, page: int = page, number: str | None = number, bare: str = bare
            ) -> bool:
                if abs(h.page - page) > 1:
                    return False
                if number is not None:
                    # Same number on the same page: bookmark titles are often truncated.
                    return (
                        h.number == number and fuzz.partial_ratio(norm_title(h.title), bare) >= 80
                    )
                return fuzz.ratio(norm_title(h.title), bare) >= 85

            hit = next((h for h in [*headings, *added] if agrees(h)), None)
            if hit is not None:
                if hit.source == HeadingSource.TEXT:
                    hit.source = HeadingSource.TEXT_AND_OUTLINE
                matched[len(entries) - 1] = hit
                continue

            row = next(
                (
                    r
                    for r in layout.content[page - 1]
                    if len(r.text) < 170 and fuzz.partial_ratio(bare, norm_title(r.text)) >= 92
                ),
                None,
            )
            if number is None and row is None:
                warnings.append(f"bookmark not located on page {page}: {title!r}")
                continue
            heading = Heading(
                page=page,
                row_index=row.index if row else -1,
                y0=row.y0 if row else 0.0,
                bbox=row.bbox if row else None,
                title=bare_title,
                number=number,
                kind=SectionKind.BODY if number else SectionKind.APPENDIX,
                level=len(number.split(".")) if number else 1,
                source=HeadingSource.OUTLINE,
            )
            added.append(heading)
            matched[len(entries) - 1] = heading
            if row is None:
                warnings.append(f"bookmark heading placed at top of page {page}: {title!r}")

        merged = sorted([*headings, *added], key=lambda h: (h.page, h.y0))
        return entries, matched, merged

    # ----- sections ----------------------------------------------------------------------------

    @staticmethod
    def _build_sections(
        headings: list[Heading], layout: _Layout
    ) -> tuple[list[Section], dict[int, str]]:
        used: set[str] = {TITLE_PAGE_ID, TOC_ID}

        def new_id(h: Heading) -> str:
            if h.number:
                base = f"sec-{h.number}"
            elif h.kind == SectionKind.APPENDIX:
                base = f"app-{slugify(h.title, max_length=40) or 'appendix'}"
            else:
                base = f"fm-{slugify(h.title, max_length=40) or 'section'}"
            candidate, n = base, 2
            while candidate in used:
                candidate, n = f"{base}-{n}", n + 1
            used.add(candidate)
            return candidate

        def synthetic(sid: str, title: str, kind: SectionKind, page: int) -> Section:
            return Section(
                id=sid,
                number=None,
                title=title,
                level=1,
                kind=kind,
                parent_id=None,
                page_start=page,
                page_end=page,
                heading_bbox=None,
                heading_source=HeadingSource.SYNTHETIC,
                text="",
            )

        anchors: list[tuple[tuple[int, float], Heading | None]] = [
            ((h.page, h.y0), h) for h in headings
        ]
        if layout.toc_pages:
            anchors.append(((min(layout.toc_pages), -1.0), None))  # None = TOC anchor
        anchors.sort(key=lambda a: a[0])

        sections: list[Section] = [
            synthetic(TITLE_PAGE_ID, "Title Page", SectionKind.TITLE_PAGE, 1)
        ]
        anchor_section: list[Section] = []
        section_of_heading: dict[int, str] = {}
        stack: list[Section] = []
        for (page, _), heading in anchors:
            if heading is None:
                sec = synthetic(TOC_ID, "Table of Contents", SectionKind.TOC, page)
                stack = []
            else:
                while stack and stack[-1].level >= heading.level:
                    stack.pop()
                parent = stack[-1] if stack else None
                in_appendix = parent is not None and parent.kind == SectionKind.APPENDIX
                sec = Section(
                    id=new_id(heading),
                    number=heading.number,
                    title=heading.title,
                    level=heading.level,
                    kind=SectionKind.APPENDIX if in_appendix else heading.kind,
                    parent_id=parent.id if parent and parent.kind != SectionKind.TOC else None,
                    page_start=page,
                    page_end=page,
                    heading_bbox=heading.bbox,
                    heading_source=heading.source,
                    text="",
                )
                section_of_heading[id(heading)] = sec.id
                stack.append(sec)
            sections.append(sec)
            anchor_section.append(sec)

        heading_rows = {
            (h.page, i) for h in headings for i in [h.row_index, *h.extra_row_indexes] if i >= 0
        }
        events: list[tuple[int, float, Row | RawTable]] = [
            (r.page, r.y0, r)
            for rows in layout.content
            for r in rows
            if (r.page, r.index) not in heading_rows
        ]
        events.extend((t.page, t.bbox[1], t) for t in layout.tables)
        events.sort(key=lambda e: (e[0], e[1]))

        keys = [a[0] for a in anchors]
        texts: dict[str, list[str]] = {s.id: [] for s in sections}
        own_last_page: dict[str, int] = {}
        for page, y, obj in events:
            pos = bisect.bisect_right(keys, (page, y)) - 1
            sec = anchor_section[pos] if pos >= 0 else sections[0]
            if sec.kind == SectionKind.TOC and page not in layout.toc_pages:
                # Stray content after the printed TOC but before section 1 (e.g. the protocol
                # title repeated) is front-page material, not part of the contents listing.
                sec = sections[0]
            if own_last_page.get(sec.id) != page:
                # Page markers let downstream code attribute any quoted text to its page.
                texts[sec.id].append(page_marker(page))
            if isinstance(obj, Row):
                texts[sec.id].append(obj.text)
            else:
                texts[sec.id].append(f"[[TABLE {obj.id}]]")
                sec.table_ids.append(obj.id)
            own_last_page[sec.id] = max(own_last_page.get(sec.id, page), page)

        for s in sections:
            s.text = "\n".join(texts[s.id])
            s.page_end = max(s.page_start, own_last_page.get(s.id, s.page_start))

        # Extend page_end to cover each section's whole subtree.
        body = sections[1:]
        for i, s in enumerate(body):
            j = i + 1
            while j < len(body) and body[j].level > s.level and body[j].kind != SectionKind.TOC:
                s.page_end = max(s.page_end, body[j].page_end)
                j += 1
        return sections, section_of_heading

    # ----- tables ------------------------------------------------------------------------------

    @staticmethod
    def _finalise_tables(
        raw: list[RawTable], sections: list[Section], pages: list[PageInfo]
    ) -> list[Table]:
        section_of = {tid: s.id for s in sections for tid in s.table_ids}
        group_score: dict[str, float] = {}
        for t in raw:
            group_score[t.group_id] = max(group_score.get(t.group_id, 0.0), t.soa_score)
        by_id = {s.id: s for s in sections}

        def under_soa_heading(section_id: str | None) -> bool:
            while section_id:
                sec = by_id[section_id]
                if SOA_TITLE.search(sec.title):
                    return True
                section_id = sec.parent_id
            return False

        landscape = {p.number for p in pages if p.landscape}
        out: list[Table] = []
        for t in sorted(raw, key=lambda t: (t.page, t.index)):
            sid = section_of.get(t.id)
            score = group_score[t.group_id]
            if under_soa_heading(sid) and t.col_count >= 4:
                score = max(score, 0.6)
            reasons: list[str] = []
            if score >= SOA_THRESHOLD:
                reasons.append("schedule_of_activities_candidate")
            if t.merged_cells / max(t.row_count * t.col_count, 1) > 0.05:
                reasons.append("merged_cells")
            if t.empty_ratio > 0.5 and t.row_count > 3:
                reasons.append("sparse_grid")
            if t.page in landscape and t.col_count >= 5:
                reasons.append("landscape_wide_table")
            out.append(
                Table(
                    id=t.id,
                    page=t.page,
                    bbox=(
                        round(t.bbox[0], 1),
                        round(t.bbox[1], 1),
                        round(t.bbox[2], 1),
                        round(t.bbox[3], 1),
                    ),
                    section_id=sid,
                    caption=t.caption,
                    row_count=t.row_count,
                    col_count=t.col_count,
                    cells=t.cells,
                    markdown=to_markdown(t.cells),
                    merged_cell_count=t.merged_cells,
                    empty_cell_ratio=t.empty_ratio,
                    group_id=t.group_id,
                    soa_score=round(score, 3),
                    is_soa_candidate=score >= SOA_THRESHOLD,
                    needs_vision=bool(reasons),
                    vision_reasons=reasons,
                )
            )
        return out
