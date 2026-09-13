"""Link cross-sheet references from quoted phrases to entity names.

Agents run independently, so an agent that refers to another sheet's entity (an estimand's endpoint,
an amendment's date) cannot know that entity's generated name. It records the protocol's phrase
instead, and this step, run whenever the intermediate model is assembled, replaces the phrase with
the name of the entity whose text matches it best.

Deterministic and conservative:
- only extracted values are linked; a reviewer's value is never touched;
- a phrase that already is a valid name is left alone;
- a link needs a clear best match (score at least MIN_SCORE and ahead of the runner-up), otherwise
  the phrase stays and the reference graph reports it for the reviewer;
- a linked value keeps the quoted phrase as its source, notes the match, and its confidence never
  exceeds the match score.
"""

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from backend.models.extraction import ExtractedField, ExtractionSheets, Provenance, ValueOrigin
from backend.pipeline.workbook.layout import SHEETS, SheetKind
from backend.pipeline.workbook.sources import group_active, sheet_rows

MIN_SCORE = 70.0
MARGIN = 5.0
_WS = re.compile(r"\s+")


@dataclass
class Candidate:
    name: str
    texts: list[str]


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().casefold()


def _candidates(sheets: ExtractionSheets) -> dict[str, list[Candidate]]:
    """Entity kind -> the entities of that kind with the texts describing each."""
    found: dict[str, list[Candidate]] = {}
    for spec in SHEETS.values():
        for row in sheet_rows(sheets, spec):
            for column in spec.columns:
                if not column.entity or column.field is None:
                    continue
                if column.group and not group_active(spec, row, column.group):
                    continue
                name = getattr(row, column.field).value
                if not name:
                    continue
                siblings = [
                    c.field
                    for c in spec.columns
                    if c.field and c.group == column.group and not c.ref and c.ct is None
                ]
                texts = [name]
                for field in siblings:
                    value = getattr(row, field).value
                    if value and value != name:
                        texts.append(value)
                found.setdefault(column.entity, []).append(Candidate(name, texts))
    return found


_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = {"a", "an", "the", "of", "in", "and", "or", "for", "to", "with", "on", "by", "at"}


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.casefold()) if w not in _STOPWORDS}


def score(phrase: str, candidate: Candidate) -> float:
    """How well the phrase names the candidate, by words rather than characters.

    A phrase whose words all appear in a candidate text (or the reverse) scores 100 ("cough count"
    in "change in 24-hour cough count", "Vital signs" for "Vital signs/Temperature"); otherwise the
    word-set similarity decides. Character-level partial matching is avoided: it scores "Medical
    History" against "Clinical Chemistry" at 76.
    """
    needle = _norm(phrase)
    needle_words = _words(phrase)
    best = 0.0
    for text in candidate.texts:
        t = _norm(text)
        text_words = _words(text)
        value = float(fuzz.token_set_ratio(needle, t))
        smaller = min(len(needle_words), len(text_words))
        if smaller:
            value = max(value, 100.0 * len(needle_words & text_words) / smaller)
        best = max(best, value)
    return best


def link_references(sheets: ExtractionSheets) -> list[str]:
    """Link reference phrases in place. Returns notes about phrases that could not be linked."""
    candidates = _candidates(sheets)
    notes: list[str] = []
    for spec in SHEETS.values():
        for index, row in enumerate(sheet_rows(sheets, spec)):
            for column in spec.columns:
                if not column.ref or column.multi or column.field is None:
                    continue  # agents write multi-name references as names already
                value: ExtractedField[str] = getattr(row, column.field)
                p = value.provenance
                if value.is_empty or p is None or p.origin != ValueOrigin.EXTRACTED:
                    continue
                pool = [c for kind in column.ref for c in candidates.get(kind, [])]
                phrase = value.value or ""
                if phrase in column.ref_literals or any(c.name == phrase for c in pool):
                    continue
                where = (
                    spec.workbook_sheet
                    if spec.kind == SheetKind.KEY_VALUE
                    else f"{spec.workbook_sheet} row {spec.row_number(index)}"
                )
                ranked = sorted(((score(phrase, c), c) for c in pool), key=lambda s: -s[0])
                if not ranked:
                    notes.append(f"{where} {column.header}: nothing to link '{phrase}' to")
                    continue
                best_score, best = ranked[0]
                runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
                if best_score < MIN_SCORE or best_score - runner_up < MARGIN:
                    notes.append(
                        f"{where} {column.header}: '{phrase}' matches no single "
                        f"{' or '.join(column.ref)} clearly; choose one"
                    )
                    continue
                setattr(
                    row,
                    column.field,
                    ExtractedField(
                        value=best.name,
                        provenance=Provenance(
                            origin=ValueOrigin.EXTRACTED,
                            source_section_id=p.source_section_id,
                            source_page=p.source_page,
                            raw_phrase=p.raw_phrase,
                            confidence=round(min(p.confidence, best_score / 100), 2),
                            verified=p.verified,
                            note=f"linked from the phrase '{phrase}' (match {best_score:.0f})",
                        ),
                    ),
                )
    return notes
