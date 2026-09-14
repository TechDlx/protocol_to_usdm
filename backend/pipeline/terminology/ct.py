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
from typing import TYPE_CHECKING, Any

import usdm4
from rapidfuzz import fuzz
from usdm4.ct.cdisc.library import Library as CdiscCtLibrary

from backend.models.extraction import TermCandidate, TerminologyResolution, TerminologyStatus

if TYPE_CHECKING:
    from backend.pipeline.terminology.bc import BcResolver

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


# The CT fields used by the extraction agents.
STUDY_PROTOCOL_STATUS = CtField("StudyProtocolVersion", "protocolStatus")
GOVERNANCE_DATE_TYPE = CtField("GovernanceDate", "type")
ARM_TYPE = CtField("StudyArm", "type")
ARM_DATA_ORIGIN_TYPE = CtField("StudyArm", "dataOriginType")
ELIGIBILITY_CATEGORY = CtField("EligibilityCriterion", "category")
ORGANIZATION_TYPE = CtField("Organization", "type")
STUDY_TYPE = CtField("StudyDesign", "studyType")
STUDY_PHASE = CtField("StudyDesign", "studyPhase")
INTERVENTION_MODEL = CtField("StudyDesign", "interventionModel")
DESIGN_CHARACTERISTICS = CtField("StudyDesign", "characteristics")
BLINDING_SCHEMA = CtField("InterventionalStudyDesign", "blindingSchema")
TRIAL_INTENT_TYPES = CtField("InterventionalStudyDesign", "intentTypes")
TRIAL_SUB_TYPES = CtField("InterventionalStudyDesign", "subTypes")
PLANNED_SEX = CtField("StudyDesignPopulation", "plannedSex")
OBJECTIVE_LEVEL = CtField("Objective", "level")
ENDPOINT_LEVEL = CtField("Endpoint", "level")
INTERVENTION_ROLE = CtField("StudyIntervention", "role")
INTERVENTION_TYPE = CtField("StudyIntervention", "type")
ROUTE = CtField("Administration", "route")
FREQUENCY = CtField("Administration", "frequency")
AMENDMENT_REASON = CtField("StudyAmendmentReason", "code")
UNIT = CtField("Quantity", "unit")
EPOCH_TYPE = CtField("StudyEpoch", "type")
ENCOUNTER_TYPE = CtField("Encounter", "type")
ENCOUNTER_SETTINGS = CtField("Encounter", "environmentalSettings")
CONTACT_MODES = CtField("Encounter", "contactModes")
TIMING_TYPE = CtField("Timing", "type")

#: Separator of multi-valued terminology cells (no term of those codelists contains a comma).
MULTI_SEPARATOR = ","


def combine(results: list[TerminologyResolution | None]) -> TerminologyResolution | None:
    """One resolution for a multi-valued cell.

    Exact only when every item is exact; then `code`, `submission_value` and `preferred_term` hold
    the items' values joined by ", ". Otherwise no code is given and the candidates are those of the
    first item that did not match.
    """
    resolved = [r for r in results if r is not None]
    if not resolved:
        return None
    failing = next((r for r in resolved if r.status != TerminologyStatus.EXACT), None)
    first = resolved[0]
    if failing is None:
        return first.model_copy(
            update={
                "code": ", ".join(r.code or "" for r in resolved),
                "submission_value": ", ".join(r.submission_value or "" for r in resolved),
                "preferred_term": ", ".join(r.preferred_term or "" for r in resolved),
                "matched_on": first.matched_on if len(resolved) == 1 else "multiple",
            }
        )
    worst = (
        TerminologyStatus.UNRESOLVED
        if any(r.status == TerminologyStatus.UNRESOLVED for r in resolved)
        else TerminologyStatus.FUZZY
    )
    return failing.model_copy(update={"status": worst, "code": None, "matched_on": None})


class CodelistNotConfiguredError(LookupError):
    pass


class CtResolver:
    def __init__(self) -> None:
        self._library = CdiscCtLibrary(str(Path(usdm4.__file__).parent))
        self._library.load()
        self._bcs: BcResolver | None = None

    @property
    def bcs(self) -> "BcResolver":
        """Biomedical Concept resolution, loaded on first use (it shares this CT library)."""
        if self._bcs is None:
            from backend.pipeline.terminology.bc import BcResolver

            self._bcs = BcResolver(self._library, self.version)
        return self._bcs

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

    def resolve_many(self, phrases: str | None, field: CtField) -> TerminologyResolution | None:
        """Resolve a comma-separated list of phrases, as in multi-valued workbook cells."""
        items = [p.strip() for p in (phrases or "").split(MULTI_SEPARATOR) if p.strip()]
        return combine([self.resolve(item, field) for item in items])

    def unit(self, phrase: str) -> dict[str, Any] | None:
        """A unit term exactly as usdm4's importer finds one: C-code, preferred term or submission
        value, case-insensitive, no synonyms."""
        needle = phrase.strip().upper()
        terms: list[dict[str, Any]] = self.codelist(UNIT).get("terms") or []
        for key in ("conceptId", "preferredTerm", "submissionValue"):
            for term in terms:
                if (term.get(key) or "").upper() == needle:
                    return term
        return None

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
