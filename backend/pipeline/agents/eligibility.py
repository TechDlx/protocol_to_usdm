"""Eligibility criteria (legacy `studyDesignEligibilityCriteria` or the split v4 sheets)."""

from pydantic import BaseModel, Field

from backend.models.extraction import EligibilityCriterionRecord, ExtractedField, Provenance
from backend.models.study import StudyMeta
from backend.pipeline.agents.base import SheetAgent
from backend.pipeline.agents.common import Cited, terms_hint
from backend.pipeline.agents.context import AgentContext, locate_quote
from backend.pipeline.identifiers.names import criterion_name
from backend.pipeline.terminology.ct import ELIGIBILITY_CATEGORY, CtResolver

NOT_VERBATIM_CAP = 0.5


class CriterionOut(BaseModel):
    category: Cited = Field(
        description="Inclusion or exclusion, as a controlled-terminology phrase."
    )
    identifier: str | None = Field(
        description="The criterion's number or code exactly as printed, e.g. '3', '16b', 'E2'. "
        "Null when criteria are unnumbered."
    )
    label: str = Field(description="A short label summarising the criterion, at most 8 words.")
    text: Cited = Field(
        description="The complete criterion text, verbatim, including any sub-items (one per line). "
        "The quote is its first sentence or first 25 words."
    )


class EligibilityOut(BaseModel):
    criteria: list[CriterionOut] = Field(
        description="Every inclusion criterion then every exclusion criterion, in protocol order."
    )


class EligibilityAgent(SheetAgent):
    sheet = "eligibility_criteria"
    workbook_sheets = ("studyDesignEligibilityCriteria",)
    prompt_version = "1"
    m11_sections = ("5.2", "5.3")
    m11_exact_sections = ("5",)  # the population chapter's own introduction
    fallback_m11_sections = ("5",)
    output_model = EligibilityOut
    max_tokens = 48000

    def instructions(self, resolver: CtResolver) -> str:
        return f"""\
List every eligibility criterion for the USDM eligibility criteria sheets.

- category: {terms_hint(self.terms(resolver, ELIGIBILITY_CATEGORY))}.
- One entry per numbered (or bulleted top-level) criterion. Keep lettered or bulleted sub-items \
inside their parent criterion's text; never split them into separate criteria or merge criteria.
- text must be verbatim: copy the wording exactly, keeping sub-item markers such as "a)" or "-" at \
the start of their lines. Do not correct typos or expand abbreviations.
- Include criteria presented in tables. Exclude introductory sentences such as "Patients must meet \
all of the following criteria"."""

    def example(self) -> str:
        return """\
Input (invented protocol, not the one below):
<section id="sec-5.1" number="5.1" title="Inclusion Criteria" pages="20-20">
[[PAGE 20]]
Participants must meet all of the following criteria:
1. Aged 18 to 75 years at screening.
2. Chronic cough for at least 12 months, defined as either:
a) cough on most days, or
b) cough-related sleep disturbance.
</section>

Output:
{"criteria": [
 {"category": {"value": "Inclusion Criteria", "quote": "Participants must meet all of the following criteria", "section_id": "sec-5.1", "confidence": 0.95},
  "identifier": "1", "label": "Age 18 to 75 years",
  "text": {"value": "Aged 18 to 75 years at screening.", "quote": "Aged 18 to 75 years at screening.", "section_id": "sec-5.1", "confidence": 0.95}},
 {"category": {"value": "Inclusion Criteria", "quote": "Participants must meet all of the following criteria", "section_id": "sec-5.1", "confidence": 0.95},
  "identifier": "2", "label": "Chronic cough for 12 months or more",
  "text": {"value": "Chronic cough for at least 12 months, defined as either:\\na) cough on most days, or\\nb) cough-related sleep disturbance.", \
"quote": "Chronic cough for at least 12 months, defined as either:", "section_id": "sec-5.1", "confidence": 0.95}}]}"""

    def to_records(
        self, output: EligibilityOut, context: AgentContext, resolver: CtResolver, study: StudyMeta
    ) -> tuple[list[EligibilityCriterionRecord], list[str]]:
        warnings: list[str] = []
        per_category: dict[str, int] = {}
        records: list[EligibilityCriterionRecord] = []
        by_id = {s.id: s for s in context.sections}

        for i, criterion in enumerate(output.criteria, start=1):
            text = self.extracted(criterion.text, context)
            if text.value is None:
                warnings.append(f"criterion {i} dropped: no text")
                continue
            category = self.extracted(criterion.category, context, resolver, ELIGIBILITY_CATEGORY)
            code = category.terminology.code if category.terminology else None
            per_category[code or ""] = per_category.get(code or "", 0) + 1

            # Criterion text is meant to be verbatim: check the whole text, not just the quote.
            section = (
                by_id.get(text.provenance.source_section_id or "") if text.provenance else None
            )
            if (
                text.provenance
                and section is not None
                and locate_quote(text.value, section, context.tables) is None
            ):
                text.provenance.confidence = min(text.provenance.confidence, NOT_VERBATIM_CAP)
                text.provenance.note = "criterion text is not a verbatim match for the protocol"
                warnings.append(f"criterion {criterion.identifier or i}: text is not verbatim")

            label_provenance = (
                Provenance(
                    origin=text.provenance.origin,
                    source_section_id=text.provenance.source_section_id,
                    source_page=text.provenance.source_page,
                    confidence=min(text.provenance.confidence, 0.8),
                    verified=text.provenance.verified,
                    note="short label written by the extraction model from the criterion text",
                )
                if text.provenance
                else None
            )
            records.append(
                EligibilityCriterionRecord(
                    name=self.derived(
                        criterion_name(code, per_category[code or ""]),
                        "generated from the category and position",
                    ),
                    category=category,
                    identifier=(
                        ExtractedField(value=criterion.identifier, provenance=text.provenance)
                        if criterion.identifier
                        else ExtractedField()
                    ),
                    label=ExtractedField(
                        value=criterion.label.strip() or None, provenance=label_provenance
                    ),
                    description=ExtractedField(),
                    text=text,
                )
            )
        if not records:
            warnings.append("no eligibility criteria found")
        return records, warnings
