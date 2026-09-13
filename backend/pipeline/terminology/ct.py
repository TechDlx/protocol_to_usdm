"""CDISC Controlled Terminology resolution.

CT is loaded through usdm4's own `Library` — the same object Stage C uses to convert the workbook
— so a term accepted here is exactly a term usdm4 will accept. No CDISC Library API key is needed:
usdm4 ships the CT cache (see docs/decisions.md D3).

Resolution never guesses:
- exact:      the phrase equals a term's C-code, submission value, preferred term or a CT synonym
              (case-insensitive, whitespace-normalised)
- fuzzy:      a close but non-identical term exists; candidates are offered to the reviewer
- unresolved: nothing close enough; the reviewer must choose
Only `exact` results carry a code. Fuzzy and unresolved results leave `code` empty and list
candidates, so a hallucinated or approximate phrase can never silently become a valid C-code.
"""

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import usdm4
from rapidfuzz import fuzz
from usdm4.ct.cdisc.library import Library as CdiscCtLibrary

from backend.models.extraction import TermCandidate, TerminologyResolution, TerminologyStatus

FUZZY_THRESHOLD = 80.0
MAX_CANDIDATES = 5
_WS = re.compile(r"\s+")


def _norm(value: str) -> str:
    return _WS.sub(" ", value).strip().casefold()


@dataclass(frozen=True)
class CtField:
    """A USDM class attribute bound to a codelist through usdm4's ct_config.yaml."""

    klass: str
    attribute: str


# The CT fields used by the implemented extraction agents.
STUDY_PROTOCOL_STATUS = CtField("StudyProtocolVersion", "protocolStatus")
GOVERNANCE_DATE_TYPE = CtField("GovernanceDate", "type")
ARM_TYPE = CtField("StudyArm", "type")
ARM_DATA_ORIGIN_TYPE = CtField("StudyArm", "dataOriginType")
ELIGIBILITY_CATEGORY = CtField("EligibilityCriterion", "category")


class CodelistNotConfiguredError(LookupError):
    pass


class CtResolver:
    def __init__(self) -> None:
        self._library = CdiscCtLibrary(str(Path(usdm4.__file__).parent))
        self._library.load()

    @property
    def version(self) -> str:
        return str(self._library.version)

    def codelist(self, field: CtField) -> dict[str, Any]:
        codelist: dict[str, Any] | None = self._library.klass_and_attribute(
            field.klass, field.attribute
        )
        if not codelist:
            raise CodelistNotConfiguredError(f"no codelist for {field.klass}.{field.attribute}")
        return codelist

    def resolve(self, phrase: str | None, field: CtField) -> TerminologyResolution | None:
        """Resolve a human phrase to a term of the field's codelist. None for an empty phrase."""
        if phrase is None or not phrase.strip():
            return None
        codelist = self.codelist(field)
        terms: list[dict[str, Any]] = codelist.get("terms") or []
        base = {
            "codelist": codelist["conceptId"],
            "codelist_name": codelist.get("name") or "",
            "ct_version": (codelist.get("source") or {}).get("effective_date") or self.version,
        }
        needle = _norm(phrase)

        for matched_on, values in (
            ("conceptId", lambda t: [t.get("conceptId") or ""]),
            ("submissionValue", lambda t: [t.get("submissionValue") or ""]),
            ("preferredTerm", lambda t: [t.get("preferredTerm") or ""]),
            ("synonym", lambda t: list(t.get("synonyms") or [])),
        ):
            for term in terms:
                if any(v and _norm(v) == needle for v in values(term)):
                    return TerminologyResolution(
                        status=TerminologyStatus.EXACT,
                        code=term["conceptId"],
                        submission_value=term.get("submissionValue") or "",
                        preferred_term=term.get("preferredTerm") or "",
                        matched_on=matched_on,
                        **base,
                    )

        candidates = sorted(
            (self._candidate(needle, term) for term in terms), key=lambda c: c.score, reverse=True
        )[:MAX_CANDIDATES]
        best = candidates[0].score if candidates else 0.0
        return TerminologyResolution(
            status=TerminologyStatus.FUZZY
            if best >= FUZZY_THRESHOLD
            else TerminologyStatus.UNRESOLVED,
            matched_on="fuzzy" if best >= FUZZY_THRESHOLD else None,
            candidates=candidates,
            **base,
        )

    def by_code(self, code: str, field: CtField) -> TerminologyResolution | None:
        """A reviewer's explicit choice: the term with this C-code in the field's codelist."""
        codelist = self.codelist(field)
        term = next((t for t in codelist.get("terms") or [] if t.get("conceptId") == code), None)
        if term is None:
            return None
        return TerminologyResolution(
            status=TerminologyStatus.EXACT,
            code=term["conceptId"],
            submission_value=term.get("submissionValue") or "",
            preferred_term=term.get("preferredTerm") or "",
            matched_on="reviewer",
            codelist=codelist["conceptId"],
            codelist_name=codelist.get("name") or "",
            ct_version=(codelist.get("source") or {}).get("effective_date") or self.version,
        )

    def terms(self, field: CtField, query: str | None = None) -> list[TermCandidate]:
        """All terms of the field's codelist, best matches for `query` first."""
        terms = self.codelist(field).get("terms") or []
        if not query or not query.strip():
            return [
                TermCandidate(
                    code=t["conceptId"],
                    submission_value=t.get("submissionValue") or "",
                    preferred_term=t.get("preferredTerm") or "",
                    score=0.0,
                )
                for t in sorted(terms, key=lambda t: (t.get("preferredTerm") or "").casefold())
            ]
        needle = _norm(query)
        scored = [self._candidate(needle, t) for t in terms]
        for candidate in scored:
            if needle == candidate.code.casefold():
                candidate.score = 100.0
        return sorted(scored, key=lambda c: (-c.score, c.preferred_term.casefold()))

    @staticmethod
    def _candidate(needle: str, term: dict[str, Any]) -> TermCandidate:
        texts = [
            term.get("preferredTerm") or "",
            term.get("submissionValue") or "",
            *(term.get("synonyms") or []),
        ]
        score = max(
            (
                max(fuzz.token_set_ratio(needle, _norm(t)), fuzz.ratio(needle, _norm(t)))
                for t in texts
                if t
            ),
            default=0.0,
        )
        return TermCandidate(
            code=term["conceptId"],
            submission_value=term.get("submissionValue") or "",
            preferred_term=term.get("preferredTerm") or "",
            score=round(float(score), 1),
        )


_instance: CtResolver | None = None
_lock = threading.Lock()


def get_ct_resolver() -> CtResolver:
    """Loading CT takes a moment; share one resolver per process."""
    global _instance
    with _lock:
        if _instance is None:
            _instance = CtResolver()
        return _instance
