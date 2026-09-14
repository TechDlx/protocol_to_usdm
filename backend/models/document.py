"""The parsed, layout-faithful representation of a protocol PDF (parsed_document.json).

Page numbers are 1-based physical PDF pages throughout, so they line up with the page images and
with what a PDF viewer shows in its page box (not the protocol's printed page labels).
"""

from enum import StrEnum

from pydantic import BaseModel, Field

PARSED_DOCUMENT_SCHEMA_VERSION = 1

BBox = tuple[float, float, float, float]  # x0, y0, x1, y1 in PDF points, top-left origin


class SectionKind(StrEnum):
    TITLE_PAGE = "title_page"  # content before the first detected heading
    FRONT_MATTER = "front_matter"  # unnumbered headings before the numbered body (synopsis, SoA)
    TOC = "toc"  # printed table of contents / list of tables — excluded from mapping
    BODY = "body"  # numbered sections
    APPENDIX = "appendix"  # "Appendix N" / "Attachment X" and everything under it


class HeadingSource(StrEnum):
    TEXT = "text"  # detected from typography + numbering on the page
    OUTLINE = "outline"  # PDF bookmark only (not found as a styled heading)
    TEXT_AND_OUTLINE = "text_and_outline"  # both agree — strongest evidence
    SYNTHETIC = "synthetic"  # created by the parser (e.g. the title-page section)


class PageInfo(BaseModel):
    number: int
    width: float
    height: float
    rotation: int
    landscape: bool
    image_path: str  # relative to the run folder
    char_count: int
    is_toc_page: bool = False
    removed_header_footer_lines: int = 0
    redaction_marks: int = 0  # e.g. oversized "CCI" stamps filtered out of the text


class Section(BaseModel):
    id: str
    number: str | None  # "3.4.2.1"; None for unnumbered front matter / appendices
    title: str
    level: int = Field(ge=1)
    kind: SectionKind
    parent_id: str | None
    page_start: int
    page_end: int
    heading_bbox: BBox | None
    heading_source: HeadingSource
    # This section's own content only, excluding subsections. A [[PAGE n]] marker precedes the
    # content from each page; tables appear as [[TABLE id]].
    text: str
    table_ids: list[str] = Field(default_factory=list)
    #: The start page the parser found, when a reviewer moved the start (section_boundaries.json).
    parsed_page_start: int | None = None


class Table(BaseModel):
    id: str
    page: int
    bbox: BBox
    section_id: str | None
    caption: str | None
    row_count: int
    col_count: int
    cells: list[list[str | None]]  # None marks a cell merged into a neighbour
    markdown: str
    merged_cell_count: int
    empty_cell_ratio: float
    # Consecutive-page tables with the same header are grouped (multi-page SoA grids).
    group_id: str
    soa_score: float = Field(ge=0, le=1)
    is_soa_candidate: bool
    # True when the parsed structure is unlikely to be faithful; downstream agents should
    # read the page image rather than trust `cells`.
    needs_vision: bool
    vision_reasons: list[str] = Field(default_factory=list)


class OutlineEntry(BaseModel):
    level: int
    title: str
    page: int
    matched_section_id: str | None


class SourceInfo(BaseModel):
    filename: str
    sha256: str
    page_count: int


class ExtractorInfo(BaseModel):
    name: str
    version: str
    library_version: str
    page_image_dpi: int


class DocumentStats(BaseModel):
    body_font_size: float
    headings_from_text: int
    headings_from_outline_only: int
    rejected_heading_candidates: int
    tables: int
    tables_needing_vision: int
    soa_pages: list[int]
    elapsed_seconds: float


class ParsedDocument(BaseModel):
    schema_version: int = PARSED_DOCUMENT_SCHEMA_VERSION
    source: SourceInfo
    extractor: ExtractorInfo
    pages: list[PageInfo]
    sections: list[Section]
    tables: list[Table]
    outline: list[OutlineEntry]
    stats: DocumentStats
    warnings: list[str] = Field(default_factory=list)

    def section(self, section_id: str) -> Section:
        for s in self.sections:
            if s.id == section_id:
                return s
        raise KeyError(section_id)
