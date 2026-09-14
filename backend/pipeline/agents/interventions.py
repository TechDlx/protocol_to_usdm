"""`studyInterventions` sheet: study interventions, one workbook row per administration.

The intervention's columns are filled on its first row only; further rows are more administrations
of the same intervention (e.g. two dose levels). Doses and durations are written as CDISC
quantities ("125 mg", "24 WEEKS") here, from the numbers and units the model reports.
The `studyProducts` sheet (administrable products, dose forms) is not extracted yet.
"""

from typing import Any

from pydantic import BaseModel, Field

from backend.models.extraction import ExtractedField, InterventionRecord
from backend.models.study import StudyMeta
from backend.pipeline.agents.base import SheetAgent
from backend.pipeline.agents.common import Cited, terms_hint
from backend.pipeline.agents.context import AgentContext
from backend.pipeline.identifiers.names import NameRegistry, safe_name
from backend.pipeline.terminology.ct import (
    FREQUENCY,
    INTERVENTION_ROLE,
    INTERVENTION_TYPE,
    ROUTE,
    CtField,
    CtResolver,
)
from backend.pipeline.workbook import formats
from backend.pipeline.workbook.layout import INTERVENTIONS


class AdministrationOut(BaseModel):
    label: Cited = Field(
        description="A short name for this administration, e.g. 'Examplumab 200 mg'."
    )
    description: Cited = Field(description="How it is given, verbatim, one sentence.")
    route: Cited = Field(description="The route, as a CDISC submission value from the list.")
    dose_value: Cited = Field(description="The dose amount, digits only, e.g. '200'.")
    dose_unit: str | None = Field(description="The dose unit as printed, e.g. 'mg', 'mg/kg'.")
    frequency: Cited = Field(description="How often, as a CDISC submission value from the list.")
    duration_description: Cited = Field(
        description="How long it is given, verbatim, e.g. 'for 12 weeks' or 'until disease "
        "progression'."
    )
    duration_value: Cited = Field(
        description="The planned duration amount, digits only, when a fixed duration is stated."
    )
    duration_unit: str | None = Field(description="The duration unit as printed, e.g. 'weeks'.")
    duration_will_vary: bool = Field(
        description="true when the duration differs between participants (e.g. treatment until "
        "progression or unacceptable toxicity); false for a fixed duration."
    )
    duration_will_vary_reason: Cited = Field(
        description="Why the duration varies, verbatim. Null when it does not vary."
    )


class InterventionOut(BaseModel):
    label: Cited = Field(description="The intervention's name as printed, e.g. 'Examplumab'.")
    description: Cited = Field(description="What it is, verbatim, one sentence.")
    role: Cited = Field(description="Its role in the trial, as a controlled-terminology phrase.")
    type: Cited = Field(description="The kind of intervention, as a controlled-terminology phrase.")
    administrations: list[AdministrationOut]


class InterventionsOut(BaseModel):
    interventions: list[InterventionOut]


def _submission_values(resolver: CtResolver, field: CtField) -> str:
    terms: list[dict[str, Any]] = resolver.codelist(field).get("terms") or []
    values = sorted({t.get("submissionValue") or "" for t in terms} - {""})
    return "Allowed values: " + ", ".join(values)


class InterventionsAgent(SheetAgent):
    sheet = "interventions"
    workbook_sheets = ("studyInterventions",)
    prompt_version = "2"
    m11_sections = ("6.1", "6.2", "6.3", "6.9", "1.1.2")
    m11_exact_sections = ("6", "1.1")
    fallback_m11_sections = ("6",)
    output_model = InterventionsOut
    max_tokens = 24000

    def instructions(self, resolver: CtResolver) -> str:
        return f"""\
List the study interventions and how they are administered, for the USDM \
`studyDesignInterventions` sheet.

- One entry per product or treatment given as part of the trial: investigational products, \
placebos, comparators, and background or rescue therapy the protocol requires. Do not list \
concomitant medications that are merely permitted or prohibited.
- A placebo matching an investigational product is its own intervention with role "Placebo".
- One administration per distinct dose level, route or schedule of that intervention.
- role: {terms_hint(self.terms(resolver, INTERVENTION_ROLE))}. The product being tested is \
"Protocol Agent" unless the protocol calls it a comparator.
- type: {terms_hint(self.terms(resolver, INTERVENTION_TYPE))}.
- route: {_submission_values(resolver, ROUTE)}.
- frequency: {_submission_values(resolver, FREQUENCY)}. Once daily is QD, twice daily BID, \
every 4 weeks Q4W.
- Numbers are digits only; give the unit separately as printed. Leave a value null rather than \
estimating it."""

    def example(self) -> str:
        return """\
Input (invented protocol, not the one below):
<section id="sec-6.1" number="6.1" title="Study Intervention" pages="15-15">
[[PAGE 15]]
Examplumab 200 mg film-coated tablets, or matching placebo, are taken orally once daily for 12 weeks.
</section>

Output:
{"interventions": [
 {"label": {"value": "Examplumab", "quote": "Examplumab 200 mg film-coated tablets", "section_id": "sec-6.1", "confidence": 0.95},
  "description": {"value": "Examplumab 200 mg film-coated tablets", "quote": "Examplumab 200 mg film-coated tablets", "section_id": "sec-6.1", "confidence": 0.9},
  "role": {"value": "Protocol Agent", "quote": "Examplumab 200 mg film-coated tablets", "section_id": "sec-6.1", "confidence": 0.7},
  "type": {"value": "Pharmacologic Substance", "quote": "film-coated tablets", "section_id": "sec-6.1", "confidence": 0.7},
  "administrations": [{"label": {"value": "Examplumab 200 mg", "quote": "Examplumab 200 mg", "section_id": "sec-6.1", "confidence": 0.9},
   "description": {"value": "Examplumab 200 mg taken orally once daily for 12 weeks", "quote": "taken orally once daily for 12 weeks", "section_id": "sec-6.1", "confidence": 0.85},
   "route": {"value": "ORAL", "quote": "taken orally", "section_id": "sec-6.1", "confidence": 0.9},
   "dose_value": {"value": "200", "quote": "Examplumab 200 mg", "section_id": "sec-6.1", "confidence": 0.95}, "dose_unit": "mg",
   "frequency": {"value": "QD", "quote": "once daily", "section_id": "sec-6.1", "confidence": 0.9},
   "duration_description": {"value": "for 12 weeks", "quote": "once daily for 12 weeks", "section_id": "sec-6.1", "confidence": 0.9},
   "duration_value": {"value": "12", "quote": "for 12 weeks", "section_id": "sec-6.1", "confidence": 0.9}, "duration_unit": "weeks",
   "duration_will_vary": false,
   "duration_will_vary_reason": {"value": null, "quote": null, "section_id": null, "confidence": 0}}]},
 {"label": {"value": "Placebo", "quote": "matching placebo", "section_id": "sec-6.1", "confidence": 0.9},
  "description": {"value": "Placebo matching examplumab tablets", "quote": "or matching placebo", "section_id": "sec-6.1", "confidence": 0.8},
  "role": {"value": "Placebo", "quote": "matching placebo", "section_id": "sec-6.1", "confidence": 0.9},
  "type": {"value": "Pharmacologic Substance", "quote": "matching placebo", "section_id": "sec-6.1", "confidence": 0.5},
  "administrations": [{"label": {"value": "Placebo", "quote": "matching placebo", "section_id": "sec-6.1", "confidence": 0.9},
   "description": {"value": "Matching placebo taken orally once daily for 12 weeks", "quote": "taken orally once daily for 12 weeks", "section_id": "sec-6.1", "confidence": 0.85},
   "route": {"value": "ORAL", "quote": "taken orally", "section_id": "sec-6.1", "confidence": 0.9},
   "dose_value": {"value": null, "quote": null, "section_id": null, "confidence": 0}, "dose_unit": null,
   "frequency": {"value": "QD", "quote": "once daily", "section_id": "sec-6.1", "confidence": 0.9},
   "duration_description": {"value": "for 12 weeks", "quote": "once daily for 12 weeks", "section_id": "sec-6.1", "confidence": 0.9},
   "duration_value": {"value": "12", "quote": "for 12 weeks", "section_id": "sec-6.1", "confidence": 0.9}, "duration_unit": "weeks",
   "duration_will_vary": false,
   "duration_will_vary_reason": {"value": null, "quote": null, "section_id": null, "confidence": 0}}]}]}"""

    def _quantity(
        self, cited: Cited, unit: str | None, context: AgentContext, resolver: CtResolver
    ) -> ExtractedField[str]:
        basis = self.extracted(cited, context)
        if basis.value is None:
            return basis
        text = formats.format_quantity(basis.value, unit, resolver)
        if text is None:
            return basis  # not a number: left as read, validation flags it
        return self.reformatted(text, basis, "number with CDISC unit")

    def to_records(
        self,
        output: InterventionsOut,
        context: AgentContext,
        resolver: CtResolver,
        study: StudyMeta,
    ) -> tuple[list[InterventionRecord], list[str]]:
        warnings: list[str] = []
        column = INTERVENTIONS.column
        names, admin_names = NameRegistry(), NameRegistry()
        records: list[InterventionRecord] = []
        empty = ExtractedField[str]
        for i, intervention in enumerate(output.interventions, start=1):
            label = self.extracted(intervention.label, context)
            if label.value is None:
                warnings.append(f"intervention {i} dropped: no name")
                continue
            head: dict[str, Any] = {
                "name": self.derived(
                    names.claim(safe_name(label.value, f"Intervention {i}")),
                    "generated from the intervention name",
                ),
                "label": label,
                "description": self.extracted(intervention.description, context),
                "role": self.cell(intervention.role, context, resolver, column("role")),
                "type": self.cell(intervention.type, context, resolver, column("type")),
                "minimum_response_duration": empty(),
            }
            administrations = [a for a in intervention.administrations if a.label.value]
            if not administrations:
                records.append(InterventionRecord(**head, **self.blank(_ADMIN_FIELDS)))
                continue
            for j, admin in enumerate(administrations):
                admin_label = self.extracted(admin.label, context)
                duration_text = self.extracted(admin.duration_description, context)
                basis = duration_text if duration_text.provenance else admin_label
                records.append(
                    InterventionRecord(
                        **(head if j == 0 else self.blank(head)),
                        administration_name=self.derived(
                            admin_names.claim(safe_name(admin_label.value or "", f"Admin {i}.{j}")),
                            "generated from the administration name",
                        ),
                        administration_label=admin_label,
                        administration_description=self.extracted(admin.description, context),
                        administration_route=self.cell(
                            admin.route, context, resolver, column("administration_route")
                        ),
                        administration_dose=self._quantity(
                            admin.dose_value, admin.dose_unit, context, resolver
                        ),
                        administration_frequency=self.cell(
                            admin.frequency, context, resolver, column("administration_frequency")
                        ),
                        duration_description=duration_text,
                        duration_will_vary=self.judged(
                            "Y" if admin.duration_will_vary else "N",
                            basis,
                            "judged from the stated duration",
                        ),
                        duration_will_vary_reason=self.extracted(
                            admin.duration_will_vary_reason, context
                        ),
                        duration_quantity=self._quantity(
                            admin.duration_value, admin.duration_unit, context, resolver
                        ),
                    )
                )
        if not records:
            warnings.append("no interventions found")
        return records, warnings


_ADMIN_FIELDS = (
    "administration_name",
    "administration_label",
    "administration_description",
    "administration_route",
    "administration_dose",
    "administration_frequency",
    "duration_description",
    "duration_will_vary",
    "duration_will_vary_reason",
    "duration_quantity",
)
