"""`studyAmendments` sheet: the protocol's amendment history.

Each amendment's date is linked to a governance date on the study sheet at assembly time
(backend/pipeline/identifiers/linking.py), by matching the date the model reads here.
Amendment impacts and detailed changes (amendmentImpact, amendmentChanges) are not extracted yet.
"""

from typing import ClassVar

from pydantic import BaseModel, Field

from backend.models.extraction import AmendmentRecord, ExtractedField
from backend.models.study import StudyMeta
from backend.pipeline.agents.base import SheetAgent
from backend.pipeline.agents.common import Cited, terms_hint
from backend.pipeline.agents.context import AgentContext
from backend.pipeline.terminology.ct import AMENDMENT_REASON, CtResolver
from backend.pipeline.workbook.layout import AMENDMENTS


class ReasonOut(BaseModel):
    term: Cited = Field(description="The reason, as a controlled-terminology phrase.")
    other: str | None = Field(
        description="When term is 'Other': the protocol's reason in a few words. Otherwise null."
    )


class AmendmentOut(BaseModel):
    number: Cited = Field(description="The amendment number, digits only, e.g. '2'.")
    summary: Cited = Field(
        description="What the amendment changed, verbatim or closely following the protocol, at "
        "most about 80 words."
    )
    primary_reason: ReasonOut
    secondary_reasons: list[ReasonOut]
    scope: str = Field(
        description="'Global', or 'Region: <name>' / 'Country: <ISO 3166 alpha-3 code>' when the "
        "amendment applies only there."
    )
    date: Cited = Field(description="The amendment's date as yyyy-mm-dd. Null if not stated.")


class AmendmentsOut(BaseModel):
    amendments: list[AmendmentOut]


class AmendmentsAgent(SheetAgent):
    sheet = "amendments"
    workbook_sheets = ("studyAmendments",)
    prompt_version = "1"
    m11_sections = ("12.3",)
    output_model = AmendmentsOut
    max_tokens = 16000
    empty_when_missing = True
    empty_records: ClassVar[list[AmendmentRecord]] = []

    def instructions(self, resolver: CtResolver) -> str:
        return f"""\
List the protocol amendments for the USDM `studyAmendments` sheet, from the amendment history or \
summary of changes.

- One entry per amendment (not per changed section). Skip the original protocol version.
- reason terms: {terms_hint(self.terms(resolver, AMENDMENT_REASON))}. Choose the term that best \
matches the stated rationale; when none fits, use "Other" and give the reason in other.
- The primary reason is the main rationale the protocol gives; list further reasons as secondary.
- scope is "Global" unless the protocol says the amendment is country- or region-specific."""

    def example(self) -> str:
        return """\
Input (invented protocol, not the one below):
<section id="sec-12.3" number="12.3" title="Protocol Amendment History" pages="60-60">
[[PAGE 60]]
Amendment 1 (02 May 2024): Updated the exclusion criteria following new safety information from the \
first-in-human study, and corrected typographical errors.
</section>

Output:
{"amendments": [{"number": {"value": "1", "quote": "Amendment 1 (02 May 2024)", "section_id": "sec-12.3", "confidence": 0.95},
 "summary": {"value": "Updated the exclusion criteria following new safety information from the first-in-human study, and corrected typographical errors.", "quote": "Updated the exclusion criteria following new safety information", "section_id": "sec-12.3", "confidence": 0.9},
 "primary_reason": {"term": {"value": "New Safety Information Available", "quote": "following new safety information", "section_id": "sec-12.3", "confidence": 0.85}, "other": null},
 "secondary_reasons": [{"term": {"value": "Inconsistency and/or Error In The Protocol", "quote": "corrected typographical errors", "section_id": "sec-12.3", "confidence": 0.8}, "other": null}],
 "scope": "Global",
 "date": {"value": "2024-05-02", "quote": "Amendment 1 (02 May 2024)", "section_id": "sec-12.3", "confidence": 0.95}}]}"""

    def _reason(self, reason: ReasonOut, context: AgentContext) -> Cited:
        """A reason cell item: the term, or 'Other=<reason>'."""
        value = reason.term.value
        if value and value.strip().casefold() == "other" and reason.other:
            value = f"Other={reason.other.replace(',', ' ').replace('=', ' ').strip()}"
        return reason.term.model_copy(update={"value": value})

    def to_records(
        self, output: AmendmentsOut, context: AgentContext, resolver: CtResolver, study: StudyMeta
    ) -> tuple[list[AmendmentRecord], list[str]]:
        warnings: list[str] = []
        column = AMENDMENTS.column
        records: list[AmendmentRecord] = []
        seen: set[str] = set()
        for i, amendment in enumerate(output.amendments, start=1):
            number = self.extracted(amendment.number, context)
            key = number.value or str(i)
            name = f"AMEND_{key}" if key not in seen else f"AMEND_{key}_{i}"
            seen.add(key)
            summary = self.extracted(amendment.summary, context)
            scope = amendment.scope.strip() or "Global"
            records.append(
                AmendmentRecord(
                    name=self.derived(name, "generated from the amendment number"),
                    label=self.derived(f"Amendment {key}", "generated from the amendment number"),
                    description=ExtractedField(),
                    number=number,
                    summary=summary,
                    primary_reason=self.cell(
                        self._reason(amendment.primary_reason, context),
                        context,
                        resolver,
                        column("primary_reason"),
                    ),
                    secondary_reasons=self.joined(
                        [self._reason(r, context) for r in amendment.secondary_reasons],
                        context,
                        resolver,
                        column("secondary_reasons"),
                    ),
                    geographic_scope=self.judged(
                        scope, summary, "Global unless the protocol restricts the amendment"
                    ),
                    enrollment=ExtractedField(),
                    date=self.extracted(amendment.date, context),
                )
            )
        return records, warnings
