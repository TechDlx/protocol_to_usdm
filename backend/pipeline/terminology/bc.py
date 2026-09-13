"""Biomedical Concept resolution against usdm4's bundled CDISC BC catalogue.

The catalogue (about 1,500 concepts) ships with usdm4, so no CDISC Library key is needed (D3). A
phrase is an exact match when it equals a concept's name, or a synonym that belongs to that concept
alone, case-insensitive; usdm4's importer finds a BC by name or synonym the same way. Anything else
is not matched: the importer then creates a BiomedicalConceptSurrogate carrying only the name,
which is valid USDM but has no concept definition, so review warns about it and offers candidates.
"""

from pathlib import Path
from typing import Any

import usdm4
from rapidfuzz import fuzz, process
from usdm4.bc.cdisc.library import Library as BcLibrary

from backend.models.extraction import TermCandidate, TerminologyResolution, TerminologyStatus

CODELIST = "BC"
CODELIST_NAME = "CDISC Biomedical Concepts (usdm4 bundled catalogue)"
FUZZY_THRESHOLD = 85.0
MAX_CANDIDATES = 5


class BcResolver:
    def __init__(self, ct_library: Any, ct_version: str) -> None:
        self._library = BcLibrary(str(Path(usdm4.__file__).parent), ct_library)
        self._library.load()
        self.version = ct_version
        # Search keys: every name and synonym, pointing at the concepts that carry it. A synonym
        # shared by several concepts ("WBC" for blood and urine leukocytes) identifies none of them.
        self._index: dict[str, set[str]] = {}
        for key, item in self._library._bcs.items():
            self._index.setdefault(key.upper(), set()).add(key)
            for synonym in item.get("synonyms") or []:
                self._index.setdefault(str(synonym).upper(), set()).add(key)
        self._choices = list(self._index)

    def _term(self, key: str, score: float = 100.0) -> TermCandidate:
        item = self._library._bcs[key]
        code = ((item.get("code") or {}).get("standardCode") or {}).get("code") or ""
        return TermCandidate(
            code=code, submission_value=key, preferred_term=item.get("label") or key, score=score
        )

    def resolve(self, phrase: str | None) -> TerminologyResolution | None:
        if phrase is None or not phrase.strip():
            return None
        needle = phrase.strip().upper()
        keys = self._index.get(needle, set())
        key = (
            needle if needle in self._library._bcs else next(iter(keys)) if len(keys) == 1 else None
        )
        if key is not None:
            term = self._term(key)
            return TerminologyResolution(
                status=TerminologyStatus.EXACT,
                code=term.code,
                submission_value=term.submission_value,
                preferred_term=term.preferred_term,
                matched_on="name or synonym",
                codelist=CODELIST,
                codelist_name=CODELIST_NAME,
                ct_version=self.version,
            )
        candidates = self.search(phrase, MAX_CANDIDATES)
        best = candidates[0].score if candidates else 0.0
        return TerminologyResolution(
            status=TerminologyStatus.FUZZY
            if best >= FUZZY_THRESHOLD
            else TerminologyStatus.UNRESOLVED,
            matched_on="fuzzy" if best >= FUZZY_THRESHOLD else None,
            candidates=candidates,
            codelist=CODELIST,
            codelist_name=CODELIST_NAME,
            ct_version=self.version,
        )

    def search(self, query: str | None, limit: int = 40) -> list[TermCandidate]:
        """Concepts best matching the query (by name or synonym), each concept once."""
        if not query or not query.strip():
            keys = sorted(self._library._bcs)[:limit]
            return [self._term(k, 0.0) for k in keys]
        results = process.extract(
            query.strip().upper(), self._choices, scorer=fuzz.WRatio, limit=limit * 3
        )
        seen: set[str] = set()
        found: list[TermCandidate] = []
        for text, score, _ in results:
            for key in sorted(self._index[text]):
                if key in seen:
                    continue
                seen.add(key)
                found.append(self._term(key, round(float(score), 1)))
            if len(found) >= limit:
                break
        return found
