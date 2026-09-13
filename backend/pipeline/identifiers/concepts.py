"""Assign Biomedical Concepts to schedule rows, and derive the study design's elements and cells.

Both run when the intermediate model is assembled, because they combine several agents' sheets.

Biomedical Concepts: a schedule row's activity is matched by name to an assessment the assessments
agent read (the word matching of cross-sheet links, with a stricter threshold). Each of that
assessment's measurements is looked up in the bundled CDISC BC catalogue; only exact matches
(name, or a synonym unique to one concept) become concepts, so nothing is guessed. Measurements
not in the catalogue are named in the note for the reviewer. When no assessment matches, an
activity whose own name is a concept ("Weight") gets that concept.

Elements and cells: USDM's study design needs an element for every arm in every epoch. Protocols
rarely spell these out, so a documented default is generated: one shared element per non-treatment
epoch (screening, follow-up), and one element per arm per treatment epoch. Every value is marked as
generated, so a crossover or unequal design is corrected in review.
"""

from backend.models.extraction import (
    DesignStructure,
    ElementRecord,
    ExtractedField,
    ExtractionSheets,
    Provenance,
    StudyCellRecord,
    TerminologyStatus,
    ValueOrigin,
)
from backend.pipeline.identifiers.linking import MARGIN, Candidate, score
from backend.pipeline.identifiers.names import NameRegistry, safe_name
from backend.pipeline.terminology.ct import CtResolver
from backend.pipeline.workbook.cells import resolve_cell
from backend.pipeline.workbook.layout import SCHEDULE

# Concepts attach clinical meaning, so an activity must name its assessment almost exactly: one
# shared word ("Ophthalmic Examination" / "Physical Examination") is not enough.
CONCEPT_MIN_SCORE = 90.0

TREATMENT_EPOCH_TERMS = {
    "Blinded Treatment Epoch",
    "Continuation Therapy Epoch",
    "Induction Therapy Epoch",
    "Investigational Intervention Epoch",
    "Open Label Treatment Epoch",
    "Product Exposure Epoch",
    "Treatment Epoch",
}


def _schedulable(resolution: object) -> bool:
    """An exact concept that can be collected at a visit: trial summary parameters ("(TS)") and
    retired concepts describe no measurement on a participant."""
    if getattr(resolution, "status", None) != TerminologyStatus.EXACT:
        return False
    key = (getattr(resolution, "submission_value", None) or "").upper()
    return not key.endswith("(TS)") and "[RETIRED]" not in key


def _short(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut or text[:limit]


def _derived(value: str, note: str) -> ExtractedField[str]:
    return ExtractedField(
        value=value,
        provenance=Provenance(origin=ValueOrigin.DERIVED, confidence=1.0, verified=True, note=note),
    )


def assign_biomedical_concepts(sheets: ExtractionSheets, resolver: CtResolver) -> list[str]:
    schedule = sheets.schedule
    if schedule is None or not schedule.rows:
        return []
    notes: list[str] = []
    assessments = sheets.assessments or []
    candidates = [
        Candidate(a.assessment.value or "", [a.assessment.value or ""]) for a in assessments
    ]
    labels = {a.name.value: (a.label.value or a.name.value or "") for a in schedule.activities}
    column = SCHEDULE.column("biomedical_concepts")

    for row in schedule.rows:
        if not row.biomedical_concepts.is_empty:
            continue
        activity = row.activity.value or ""
        phrase = labels.get(activity, activity)
        ranked = sorted(
            ((score(phrase, c), i) for i, c in enumerate(candidates)), key=lambda s: -s[0]
        )
        best = ranked[0] if ranked else (0.0, -1)
        runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
        if best[0] >= CONCEPT_MIN_SCORE and best[0] - runner_up >= MARGIN:
            assessment = assessments[best[1]]
            exact: list[str] = []
            missing: list[str] = []
            quotes: list[ExtractedField[str]] = []
            for measurement in assessment.measurements:
                resolution = resolver.bcs.resolve(measurement.value)
                if resolution and _schedulable(resolution):
                    if resolution.preferred_term and resolution.preferred_term not in exact:
                        exact.append(resolution.preferred_term.replace(",", " "))
                        quotes.append(measurement)
                elif measurement.value:
                    missing.append(measurement.value)
            if not exact:
                if missing:
                    notes.append(
                        f"{activity}: none of '{assessment.assessment.value}' measurements is a "
                        f"CDISC Biomedical Concept ({', '.join(missing[:5])})"
                    )
                continue
            first = quotes[0].provenance
            note = f"matched to the assessment '{assessment.assessment.value}'"
            if missing:
                note += f"; not in the BC catalogue: {', '.join(missing)}"
            value = ", ".join(exact)
            row.biomedical_concepts = ExtractedField(
                value=value,
                provenance=Provenance(
                    origin=ValueOrigin.EXTRACTED,
                    source_section_id=first.source_section_id if first else None,
                    source_page=first.source_page if first else None,
                    raw_phrase=" | ".join(
                        q.provenance.raw_phrase or "" for q in quotes if q.provenance
                    ),
                    confidence=round(
                        min(
                            [best[0] / 100]
                            + [q.provenance.confidence for q in quotes if q.provenance]
                        ),
                        2,
                    ),
                    verified=all(q.provenance and q.provenance.verified for q in quotes),
                    note=note,
                ),
                terminology=resolve_cell(column, value, resolver),
            )
            continue
        own = resolver.bcs.resolve(phrase)
        if own and _schedulable(own) and own.preferred_term:
            value = own.preferred_term.replace(",", " ")
            row.biomedical_concepts = ExtractedField(
                value=value,
                provenance=Provenance(
                    origin=ValueOrigin.DERIVED,
                    confidence=0.8,
                    verified=True,
                    note="the activity's own name is a CDISC Biomedical Concept",
                ),
                terminology=resolve_cell(column, value, resolver),
            )
    return notes


def derive_design(sheets: ExtractionSheets) -> None:
    arms = sheets.study_design_arms or []
    epochs = sheets.schedule.epochs if sheets.schedule else []
    if not arms or not epochs:
        sheets.design = None
        return
    names = NameRegistry()
    design = DesignStructure()
    note = "generated: every arm passes through every epoch; correct crossover or unequal designs"
    for epoch in epochs:
        epoch_name = epoch.name.value or ""
        term = epoch.type.terminology.preferred_term if epoch.type.terminology else None
        if term in TREATMENT_EPOCH_TERMS:
            per_arm = []
            for arm in arms:
                arm_name = arm.name.value or ""
                # Both parts shortened so neither is cut off mid-word by the name length limit.
                element = names.claim(
                    safe_name(f"{_short(arm_name, 32)} - {_short(epoch_name, 24)}", "Element")
                )
                design.elements.append(
                    ElementRecord(
                        name=_derived(element, note),
                        label=_derived(f"{arm.label.value or arm_name} ({epoch_name})", note),
                        description=_derived(f"{arm_name} during {epoch_name}", note),
                        transition_start_rule=ExtractedField(),
                        transition_end_rule=ExtractedField(),
                    )
                )
                per_arm.append((arm_name, element))
            for arm_name, element in per_arm:
                design.cells.append(
                    StudyCellRecord(
                        arm=_derived(arm_name, note),
                        epoch=_derived(epoch_name, note),
                        elements=_derived(element, note),
                    )
                )
        else:
            element = names.claim(safe_name(epoch_name, "Element"))
            design.elements.append(
                ElementRecord(
                    name=_derived(element, note),
                    label=_derived(epoch.label.value or epoch_name, note),
                    description=_derived(f"All arms during {epoch_name}", note),
                    transition_start_rule=ExtractedField(),
                    transition_end_rule=ExtractedField(),
                )
            )
            for arm in arms:
                design.cells.append(
                    StudyCellRecord(
                        arm=_derived(arm.name.value or "", note),
                        epoch=_derived(epoch_name, note),
                        elements=_derived(element, note),
                    )
                )
    sheets.design = design
