"""Deterministic segmentation of a parsed protocol against the ICH M11 template.

For each document section, every M11 section is scored against its M11 title and known aliases.
Titles are normalised for vocabulary that differs between sponsors and M11 (study -> trial,
patient/subject -> participant, medication -> therapy, ...) and compared by word overlap weighted
by rarity across the M11 vocabulary: sharing "pharmacokinetics" is strong evidence, sharing
"assessment" is weak, and a specific unmatched word such as a drug name pulls the score down.
Character-level fuzzy ratios are deliberately not used: they match lookalike words
("Indication" ~ "Introduction", "Hospitalization" ~ "Population").

Structure then adjusts the result: a candidate under the parent's M11 section gets a bonus, and a
jump to a different M11 chapter than the parent's needs strong evidence, otherwise the section
inherits the parent's mapping.

A reviewer can override any section's mapping (or mark it as not protocol content). Overrides are
applied while mapping, so subsections inherit from and are biased towards the reviewer's choice,
and coverage reflects it; an override whose section no longer exists or has a different title is
ignored and reported.

Mappings below the review threshold are flagged, never silently accepted. Sections with no
acceptable match inherit their parent's mapping at reduced confidence, so an agent asking for
"everything about the trial population" still receives unconventionally titled subsections.
"""

import math
import re
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from backend.models.document import ParsedDocument, Section, SectionKind
from backend.models.segmentation import (
    Candidate,
    CoverageStatus,
    M11Coverage,
    M11Ref,
    MappingMethod,
    SectionAssignment,
    SectionMapping,
    SectionOverride,
)

TEMPLATE_PATH = Path(__file__).with_name("m11_template.yaml")
DEFAULT_REVIEW_THRESHOLD = 0.70
_MIN_MATCH = 0.55  # below this a title match is not trusted at all
_CROSS_CHAPTER_MIN = 0.75  # a child mapped outside its parent's M11 chapter needs this much
_PARENT_BONUS = 0.08
_CHAPTER_BONUS = 0.03
_INHERIT_FACTOR = 0.75
_APPENDIX_DEFAULT_CONFIDENCE = 0.72  # 12.X is M11's own catch-all for additional appendices

_SYNONYMS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p), r)
    for p, r in [
        (r"\bstudies\b|\bstudy\b|\bclinical trial\b|\bprotocol\b", "trial"),
        (r"\bpatients?\b|\bsubjects?\b|\bparticipants?\b|\bvolunteers?\b", "participant"),
        (r"\bmedications?\b|\bdrugs?\b(?!\s+induced)|\bmedicines?\b", "therapy"),
        (r"\btreatments?\b|\binterventions?\b|\binvestigational products?\b", "intervention"),
        (r"\brandomization\b", "randomisation"),
        (r"\butilization\b", "utilisation"),
        (r"\bbehavior\b", "behaviour"),
        (r"\bcompliance\b", "adherence"),
        (r"\bwithdrawals?\b|\bdiscontinuations?\b", "discontinuation"),
        (r"\bassessments?\b|\bevaluations?\b|\bmeasures?\b|\bmeasurements?\b", "assessment"),
        (r"\banalyses\b", "analysis"),
        (
            r"\bschedule of events\b|\bschedule of assessments?\b|\btime and events\b",
            "schedule of activities",
        ),
        (r"\bflow ?charts?\b", "schedule of activities"),
    ]
]
_STOPWORDS = {
    "of", "the", "and", "for", "to", "in", "on", "a", "an", "with", "or", "by", "from", "at",
    "trial", "during", "after", "associated", "related", "its", "all",
}  # fmt: skip
# Generic words such as "description", "overview" or "other" are deliberately NOT stopwords: the
# rarity weighting already discounts them, and dropping them collapses distinct titles
# ("Description of Trial Design" would become "design", identical to chapter 4 "Trial Design").
_APPENDIX_PREFIX = re.compile(
    r"^(protocol\s+)?(appendix|attachment|annex)\s+[a-z0-9]{1,6}([.\-][a-z0-9]{1,6}){0,3}\.?\s*",
    re.IGNORECASE,
)
_SOA = re.compile(
    r"schedule of (activities|events|assessments)|time and events|flow ?chart", re.IGNORECASE
)


class M11Section(BaseModel):
    number: str
    title: str
    optional: bool = False
    repeating: bool = False
    aliases: list[str] = Field(default_factory=list)

    @property
    def level(self) -> int:
        return len(self.number.split("."))

    @property
    def chapter(self) -> str:
        return self.number.split(".")[0]

    def is_under(self, ancestor: str) -> bool:
        return self.number == ancestor or self.number.startswith(ancestor + ".")


class M11Template(BaseModel):
    template: str
    version: str
    sections: list[M11Section]

    def get(self, number: str) -> M11Section:
        for s in self.sections:
            if s.number == number:
                return s
        raise KeyError(number)


@lru_cache
def load_template(path: Path = TEMPLATE_PATH) -> M11Template:
    return M11Template.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def normalise(title: str) -> str:
    text = title.lower().replace("&", " and ")
    text = re.sub(r"\(s\)|<#>|[{}]", "", text)
    text = re.sub(r"[^a-z0-9/]+", " ", text).replace("/", " ")
    for pattern, replacement in _SYNONYMS:
        text = pattern.sub(replacement, text)
    tokens = []
    for tok in text.split():
        if tok in _STOPWORDS or tok.isdigit():
            continue
        if len(tok) > 4 and tok.endswith("s") and not tok.endswith(("ss", "is", "us")):
            tok = tok[:-1]
        tokens.append(tok)
    return " ".join(tokens)


def _tokens_match(a: str, b: str) -> bool:
    """Exact, or a near-identical spelling variant of a long word ("pharmacokinetic(s)").

    The shared-prefix requirement stops affixed words matching ("screening" vs "rescreening").
    """
    return a == b or (min(len(a), len(b)) >= 6 and a[:4] == b[:4] and fuzz.ratio(a, b) >= 90)


class _Vocabulary:
    """Inverse document frequency of words across all M11 titles and aliases."""

    def __init__(self, variants: list[str]) -> None:
        df: dict[str, int] = {}
        for v in variants:
            for tok in set(v.split()):
                df[tok] = df.get(tok, 0) + 1
        n = len(variants)
        self._idf = {tok: math.log((n + 1) / (count + 1)) + 1 for tok, count in df.items()}
        self._unknown = math.log(n + 1) + 1  # words M11 never uses are maximally specific

    def idf(self, token: str) -> float:
        return self._idf.get(token, self._unknown)


def title_similarity(a: str, b: str, vocab: "_Vocabulary | None" = None) -> float:
    """Rarity-weighted word overlap (Dice) of two normalised titles, 0..1."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    weight = vocab.idf if vocab else (lambda _t: 1.0)
    ta, tb = list(dict.fromkeys(a.split())), list(dict.fromkeys(b.split()))
    shared = sum(weight(t) for t in ta if any(_tokens_match(t, u) for u in tb))
    shared_b = sum(weight(u) for u in tb if any(_tokens_match(u, t) for t in ta))
    total = sum(weight(t) for t in ta) + sum(weight(u) for u in tb)
    return round((shared + shared_b) / total, 3) if total else 0.0


def _clean_doc_title(section: Section) -> str:
    return _APPENDIX_PREFIX.sub("", section.title).strip() or section.title


class _Scorer:
    def __init__(self, template: M11Template) -> None:
        self.template = template
        self._norm = [
            (
                s,
                [
                    (normalise(s.title), s.title, False),
                    *((normalise(a), a, True) for a in s.aliases),
                ],
            )
            for s in template.sections
        ]
        self.vocab = _Vocabulary([n for _, variants in self._norm for n, _, _ in variants])

    def score(self, title: str) -> list[tuple[M11Section, float, str, bool]]:
        """Best (score, matched text, is_alias) per M11 section, highest first."""
        norm_title = normalise(title)
        results = []
        for section, variants in self._norm:
            best = max(
                (
                    (title_similarity(norm_title, n, self.vocab), text, is_alias)
                    for n, text, is_alias in variants
                ),
                key=lambda v: v[0],
            )
            results.append((section, best[0], best[1], best[2]))
        return sorted(results, key=lambda r: r[1], reverse=True)


def map_sections(
    document: ParsedDocument,
    template: M11Template | None = None,
    review_threshold: float = DEFAULT_REVIEW_THRESHOLD,
    overrides: dict[str, SectionOverride] | None = None,
) -> SectionMapping:
    template = template or load_template()
    overrides = overrides or {}
    scorer = _Scorer(template)
    soa_sections = {t.section_id for t in document.tables if t.is_soa_candidate and t.section_id}
    by_id = {s.id: s for s in document.sections}

    native_ratio = _m11_native_ratio(document, template, scorer.vocab)
    native = native_ratio >= 0.6

    ignored = [
        f"{o.section_id} ({o.doc_title}): the section is no longer in the parsed document"
        for o in overrides.values()
        if o.section_id not in by_id
    ]
    assignments: dict[str, SectionAssignment] = {}
    for section in document.sections:
        parent = assignments.get(section.parent_id) if section.parent_id else None
        computed = _assign(
            section, parent, scorer, template, native, section.id in soa_sections, review_threshold
        )
        override = overrides.get(section.id)
        if override is not None and override.doc_title != section.title:
            ignored.append(
                f"{section.id}: the section's title changed from '{override.doc_title}' to "
                f"'{section.title}'; the reviewer's mapping was not applied"
            )
            override = None
        assignments[section.id] = (
            _overridden(computed, override, template) if override is not None else computed
        )

    ordered = [assignments[s.id] for s in document.sections]
    return SectionMapping(
        template=template.template,
        template_version=template.version,
        source_sha256=document.source.sha256,
        m11_native=native,
        m11_native_ratio=round(native_ratio, 3),
        review_threshold=review_threshold,
        assignments=ordered,
        coverage=_coverage(template, ordered, by_id, review_threshold),
        ignored_overrides=ignored,
    )


def _overridden(
    computed: SectionAssignment, override: SectionOverride, template: M11Template
) -> SectionAssignment:
    """The reviewer's mapping, keeping the computed candidates for reference. With no M11 section
    of its own the override only adds further sections to the computed mapping."""
    if override.excluded:
        update: dict[str, object] = {
            "m11_number": None,
            "m11_title": None,
            "confidence": 1.0,
            "method": MappingMethod.EXCLUDED,
            "matched_text": None,
            "needs_review": False,
        }
    elif override.m11_number is not None:
        m11 = template.get(override.m11_number)
        update = {
            "m11_number": m11.number,
            "m11_title": m11.title,
            "confidence": 1.0,
            "method": MappingMethod.REVIEWER,
            "matched_text": None,
            "needs_review": False,
        }
    else:
        update = {}
    primary = None if override.excluded else update.get("m11_number", computed.m11_number)
    also = (
        []
        if override.excluded
        else [
            M11Ref(m11_number=n, m11_title=template.get(n).title)
            for n in dict.fromkeys(override.also)
            if n != primary
        ]
    )
    return computed.model_copy(update={**update, "also_m11": also, "reviewer_override": True})


def _m11_native_ratio(document: ParsedDocument, template: M11Template, vocab: _Vocabulary) -> float:
    top = [s for s in document.sections if s.kind == SectionKind.BODY and s.level == 1 and s.number]
    if not top:
        return 0.0
    hits = 0
    for s in top:
        try:
            m11 = template.get(s.number or "")
        except KeyError:
            continue
        if title_similarity(normalise(s.title), normalise(m11.title), vocab) >= 0.8:
            hits += 1
    return hits / len(top)


def _assign(
    section: Section,
    parent: SectionAssignment | None,
    scorer: _Scorer,
    template: M11Template,
    native: bool,
    holds_soa: bool,
    threshold: float,
) -> SectionAssignment:
    def build(
        m11: M11Section | None,
        confidence: float,
        method: MappingMethod,
        matched: str | None,
        candidates: list[Candidate],
    ) -> SectionAssignment:
        confidence = round(max(0.0, min(confidence, 1.0)), 3)
        return SectionAssignment(
            section_id=section.id,
            doc_number=section.number,
            doc_title=section.title,
            page_start=section.page_start,
            page_end=section.page_end,
            m11_number=m11.number if m11 else None,
            m11_title=m11.title if m11 else None,
            confidence=confidence,
            method=method,
            matched_text=matched,
            candidates=candidates,
            needs_review=method != MappingMethod.EXCLUDED and confidence < threshold,
        )

    if section.kind == SectionKind.TOC:
        return build(None, 1.0, MappingMethod.EXCLUDED, None, [])
    if section.kind == SectionKind.TITLE_PAGE:
        return build(template.get("0"), 0.95, MappingMethod.STRUCTURAL, "Title Page", [])

    ranked = scorer.score(_clean_doc_title(section))
    parent_m11 = parent.m11_number if parent and parent.confidence >= 0.5 else None
    adjusted: list[tuple[M11Section, float, str, bool]] = []
    for m11, score, text, is_alias in ranked[:25]:
        bonus = 0.0
        if parent_m11 and parent_m11 != "0":
            # The parent's own section counts too: a child whose title matches it as well as a
            # sub-section stays at the parent's level rather than being pushed deeper.
            if m11.is_under(parent_m11):
                bonus = _PARENT_BONUS
            elif m11.chapter == parent_m11.split(".")[0]:
                bonus = _CHAPTER_BONUS
        # Not capped here: two perfect title matches must still be separable by the bonus.
        adjusted.append((m11, score + bonus if score >= 0.4 else score, text, is_alias))
    # Exact ties (M11 repeats titles, e.g. 10.4.1.1 / 10.5.1.1) go to the less specific section,
    # then to template order.
    order = {m.number: i for i, m in enumerate(template.sections)}
    adjusted.sort(key=lambda r: (-round(r[1], 6), r[0].level, order[r[0].number]))
    candidates = [
        Candidate(m11_number=m.number, m11_title=m.title, score=round(min(sc, 1.0), 3))
        for m, sc, _, _ in adjusted[:3]
    ]
    best, best_score, best_text, best_alias = adjusted[0]
    best_score = min(best_score, 1.0)
    if parent is not None and parent_m11 == best.number:
        # Landing on the parent's own section is at least as good as inheriting it.
        best_score = max(best_score, parent.confidence * _INHERIT_FACTOR)

    # M11-native protocols: the number itself is strong evidence when the title agrees.
    if native and section.number:
        try:
            same = template.get(section.number)
            title_score = title_similarity(
                normalise(section.title), normalise(same.title), scorer.vocab
            )
            if title_score >= 0.6:
                return build(
                    same,
                    0.5 + 0.5 * title_score,
                    MappingMethod.NUMBER_AND_TITLE,
                    same.title,
                    candidates,
                )
        except KeyError:
            pass

    if _SOA.search(section.title) or (holds_soa and best_score < 0.8):
        soa = template.get("1.3")
        return build(
            soa,
            max(best_score if best.number == "1.3" else 0.0, 0.85),
            MappingMethod.CONTENT_SOA if holds_soa else MappingMethod.ALIAS_MATCH,
            section.title,
            candidates,
        )

    parent_chapter = parent_m11.split(".")[0] if parent_m11 and parent_m11 != "0" else None
    confident_parent = parent is not None and parent.confidence >= 0.8
    jumps_chapter = parent_chapter is not None and best.chapter != parent_chapter
    if best_score >= _MIN_MATCH and not (
        confident_parent and jumps_chapter and best_score < _CROSS_CHAPTER_MIN
    ):
        method = MappingMethod.ALIAS_MATCH if best_alias else MappingMethod.TITLE_MATCH
        return build(best, best_score, method, best_text, candidates)

    if section.kind == SectionKind.APPENDIX and section.level == 1:
        return build(
            template.get("12.X"),
            _APPENDIX_DEFAULT_CONFIDENCE,
            MappingMethod.APPENDIX_DEFAULT,
            None,
            candidates,
        )

    if parent and parent.m11_number and parent.confidence >= 0.5:
        inherited = template.get(parent.m11_number)
        return build(
            inherited,
            parent.confidence * _INHERIT_FACTOR,
            MappingMethod.INHERITED,
            None,
            candidates,
        )

    return build(None, best_score, MappingMethod.UNMAPPED, None, candidates)


def _coverage(
    template: M11Template,
    assignments: list[SectionAssignment],
    by_id: dict[str, Section],
    threshold: float,
) -> list[M11Coverage]:
    rows: list[M11Coverage] = []
    for m11 in template.sections:
        primary = [
            a
            for a in assignments
            if a.m11_number == m11.number
            and a.method not in (MappingMethod.INHERITED, MappingMethod.EXCLUDED)
        ]
        # A reviewer's further mapping is as certain as a mapping made by hand.
        primary_ids = {a.section_id for a in primary}
        also = [
            a
            for a in assignments
            if a.section_id not in primary_ids
            and any(r.m11_number == m11.number for r in a.also_m11)
        ]
        mapped_ids = primary_ids | {a.section_id for a in also}
        direct = [a for a in assignments if a.section_id in mapped_ids]
        best = max([*(a.confidence for a in primary), *(1.0 for _ in also)], default=None)
        if best is None:
            status = CoverageStatus.MISSING
        elif best >= threshold:
            status = CoverageStatus.FOUND
        else:
            status = CoverageStatus.LOW_CONFIDENCE
        rows.append(
            M11Coverage(
                m11_number=m11.number,
                m11_title=m11.title,
                level=m11.level,
                optional=m11.optional,
                section_ids=[a.section_id for a in direct if a.section_id in by_id],
                best_confidence=best,
                status=status,
            )
        )
    return rows


def sections_for(
    mapping: SectionMapping,
    document: ParsedDocument,
    m11_numbers: list[str],
    include_inherited: bool = True,
    exact_numbers: list[str] | None = None,
) -> list[str]:
    """Document section ids covering the given M11 sections.

    `m11_numbers` match the M11 section and everything under it; `exact_numbers` match only
    sections mapped to that M11 number itself (e.g. a protocol summary mapped to "1" without the
    schedule of activities mapped to "1.3"). This is how a per-sheet extraction agent receives
    only its relevant protocol text.
    """
    wanted = tuple(m11_numbers)
    exact = set(exact_numbers or [])
    chosen: set[str] = set()
    for a in mapping.assignments:
        if a.method == MappingMethod.EXCLUDED:
            continue
        numbers = [r.m11_number for r in a.also_m11]
        if a.m11_number is not None and (include_inherited or a.method != MappingMethod.INHERITED):
            numbers.append(a.m11_number)
        for number in numbers:
            in_subtree = any(number == n or number.startswith(n + ".") for n in wanted)
            if in_subtree or number in exact:
                chosen.add(a.section_id)
    return [s.id for s in document.sections if s.id in chosen]
