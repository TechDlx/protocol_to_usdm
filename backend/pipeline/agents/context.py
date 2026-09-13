"""Building the protocol context an agent sees, and verifying the quotes it returns."""

import hashlib
import re
from dataclasses import dataclass, field
from html import escape

from rapidfuzz import fuzz

from backend.models.document import ParsedDocument, Section, Table
from backend.models.extraction import Provenance, ValueOrigin
from backend.models.segmentation import SectionMapping
from backend.pipeline.agents.common import Cited
from backend.pipeline.segmentation.m11 import sections_for

_TABLE_MARKER = re.compile(r"\[\[TABLE (?P<id>[^\]]+)\]\]")
_PAGE_MARKER = re.compile(r"\[\[PAGE (?P<page>\d+)\]\]")
_WS = re.compile(r"\s+")

# A quote shorter than this is too generic to confirm a location by fuzzy matching.
MIN_FUZZY_QUOTE = 20
FUZZY_QUOTE_SCORE = 90.0
UNVERIFIED_QUOTE_CAP = 0.3  # a quote that cannot be found may be a hallucination
NO_QUOTE_CAP = 0.6  # a value with no supporting quote at all
# Bump when quote verification or model-output normalisation rules change: stored agent outputs
# are re-verified for free. 2: tables searchable, whitespace-tolerant. 3: literal unicode escapes
# in model output decoded.
VERIFICATION_VERSION = "3"


@dataclass
class AgentContext:
    sections: list[Section]
    rendered: str
    used_fallback: bool
    tables: dict[str, Table] = field(default_factory=dict)

    @property
    def section_ids(self) -> list[str]:
        return [s.id for s in self.sections]


def build_context(
    document: ParsedDocument,
    mapping: SectionMapping,
    m11_numbers: list[str],
    fallback_m11_numbers: list[str] | None = None,
    extra_section_ids: list[str] | None = None,
    exact_m11_numbers: list[str] | None = None,
) -> AgentContext:
    """Render the sections mapped to the agent's M11 sections (plus any extra ids), in order."""
    wanted = set(sections_for(mapping, document, m11_numbers, exact_numbers=exact_m11_numbers))
    used_fallback = False
    if not wanted and fallback_m11_numbers:
        wanted = set(sections_for(mapping, document, fallback_m11_numbers))
        used_fallback = True
    wanted.update(extra_section_ids or [])
    sections = [s for s in document.sections if s.id in wanted]
    tables = {t.id: t for t in document.tables}

    def render(section: Section) -> str:
        def table(match: re.Match[str]) -> str:
            t = tables.get(match["id"])
            body = t.markdown if t else "(table not available)"
            return f"[[TABLE {match['id']}]]\n{body}\n[[/TABLE]]"

        text = _TABLE_MARKER.sub(table, section.text)
        attrs = (
            f'id="{escape(section.id)}" number="{escape(section.number or "")}" '
            f'title="{escape(section.title)}" pages="{section.page_start}-{section.page_end}"'
        )
        return f"<section {attrs}>\n{text}\n</section>"

    rendered = "<protocol>\n" + "\n\n".join(render(s) for s in sections) + "\n</protocol>"
    used_tables = {tid: tables[tid] for s in sections for tid in s.table_ids if tid in tables}
    return AgentContext(
        sections=sections, rendered=rendered, used_fallback=used_fallback, tables=used_tables
    )


def input_hash(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().casefold()


def _pages_and_text(
    section: Section, tables: dict[str, Table] | None = None
) -> tuple[str, list[tuple[int, int]]]:
    """Searchable section text plus (char offset, page) breakpoints.

    Page markers are removed; table markers are replaced by the table's cell text so that values
    quoted from tables (document history, dose tables) can be verified like any other text.
    """
    pieces: list[str] = []
    breaks: list[tuple[int, int]] = []
    offset = 0

    def add(text: str, at_page: int) -> None:
        nonlocal offset
        norm = _norm(text)
        if norm:
            breaks.append((offset, at_page))
            pieces.append(norm)
            offset += len(norm) + 1

    page = section.page_start
    for part in _PAGE_MARKER.split(section.text):
        if part.isdigit():
            page = int(part)
            continue
        position = 0
        for match in _TABLE_MARKER.finditer(part):
            add(part[position : match.start()], page)
            table = (tables or {}).get(match["id"])
            if table is not None:
                for row in table.cells:
                    add(" ".join(c for c in row if c), table.page)
            position = match.end()
        add(part[position:], page)
    return " ".join(pieces), breaks


def _page_at(breaks: list[tuple[int, int]], position: int) -> int | None:
    page = None
    for start, p in breaks:
        if start <= position:
            page = p
        else:
            break
    return page


_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_NEGATION = re.compile(r"\b(?:no|not|non|without|never|except|excluding)\b")


def _same_meaning_markers(a: str, b: str) -> bool:
    """Fuzzy matching tolerates typos, not changed facts: numbers and negations must agree."""
    return _NUMBER.findall(a) == _NUMBER.findall(b) and _NEGATION.findall(a) == _NEGATION.findall(b)


def _compact_find(needle: str, haystack: str) -> int | None:
    """Find ignoring whitespace ("20 October 2015" against extracted "20 October2015").

    Returns the start offset in `haystack`. The match must still start and end on word
    boundaries, so "male aged 18" is not found inside "female aged 18".
    """
    compact_needle = needle.replace(" ", "")
    if not compact_needle:
        return None
    index = [i for i, ch in enumerate(haystack) if ch != " "]
    compact_haystack = "".join(haystack[i] for i in index)
    position = compact_haystack.find(compact_needle)
    while position >= 0:
        start, end = index[position], index[position + len(compact_needle) - 1] + 1
        starts_on_word = start == 0 or not (haystack[start - 1].isalnum() and needle[0].isalnum())
        ends_on_word = end >= len(haystack) or not (
            haystack[end].isalnum() and needle[-1].isalnum()
        )
        if starts_on_word and ends_on_word:
            return start
        position = compact_haystack.find(compact_needle, position + 1)
    return None


def locate_quote(
    quote: str, section: Section, tables: dict[str, Table] | None = None
) -> int | None:
    """Page of `quote` within `section`, or None when it cannot be found there.

    Exact matches must fall on word boundaries ("male" is not found inside "female"). A near match
    is accepted only for longer quotes, starting at a word boundary, and with exactly the same
    numbers and negations: "aged 65 or older" never verifies against "aged 18 or older".
    """
    needle = _norm(quote)
    if not needle:
        return None
    haystack, breaks = _pages_and_text(section, tables)
    left = r"(?<!\w)" if needle[0].isalnum() else ""
    right = r"(?!\w)" if needle[-1].isalnum() else ""
    exact = re.search(f"{left}{re.escape(needle)}{right}", haystack)
    if exact:
        return _page_at(breaks, exact.start())
    # Whitespace lost in PDF text extraction. Restricted to quotes with a digit or three or more
    # words, so a short common word cannot match across a word boundary by accident.
    if any(ch.isdigit() for ch in needle) or len(needle.split()) >= 3:
        compact = _compact_find(needle, haystack)
        if compact is not None:
            return _page_at(breaks, compact)
    if len(needle) >= MIN_FUZZY_QUOTE:
        alignment = fuzz.partial_ratio_alignment(needle, haystack, score_cutoff=FUZZY_QUOTE_SCORE)
        if alignment is not None:
            start, end = alignment.dest_start, alignment.dest_end
            at_word_start = start == 0 or not haystack[start - 1].isalnum()
            if at_word_start and _same_meaning_markers(needle, haystack[start:end]):
                return _page_at(breaks, start)
    return None


def provenance_for(cited: Cited, context: AgentContext, note: str | None = None) -> Provenance:
    """Verify a cited value's quote against the protocol and build its provenance.

    The page is taken from where the quote is actually found, never from the model. A quote that
    is not in the cited section is searched for in the agent's other sections before giving up.
    """
    confidence = max(0.0, min(float(cited.confidence), 1.0))
    by_id = {s.id: s for s in context.sections}
    if not cited.quote:
        return Provenance(
            origin=ValueOrigin.EXTRACTED,
            source_section_id=cited.section_id if cited.section_id in by_id else None,
            confidence=min(confidence, NO_QUOTE_CAP),
            verified=False,
            note=note or "no supporting quote was given",
        )

    ordered = ([by_id[cited.section_id]] if cited.section_id in by_id else []) + [
        s for s in context.sections if s.id != cited.section_id
    ]
    for section in ordered:
        page = locate_quote(cited.quote, section, context.tables)
        if page is not None:
            moved = section.id != cited.section_id
            return Provenance(
                origin=ValueOrigin.EXTRACTED,
                source_section_id=section.id,
                source_page=page,
                raw_phrase=cited.quote,
                confidence=confidence,
                verified=True,
                note=(
                    note
                    if not moved
                    else f"quote found in {section.id}, not the cited {cited.section_id}"
                ),
            )
    return Provenance(
        origin=ValueOrigin.EXTRACTED,
        source_section_id=cited.section_id if cited.section_id in by_id else None,
        raw_phrase=cited.quote,
        confidence=min(confidence, UNVERIFIED_QUOTE_CAP),
        verified=False,
        note="quote not found in the protocol text; the value may not be supported",
    )
