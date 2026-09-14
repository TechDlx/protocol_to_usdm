"""`studyDesignOE` sheet: objectives with their endpoints, one workbook row per endpoint.

The objective's columns are filled on its first row only; following rows are further endpoints of
the same objective, which is how the importer reads the sheet.
"""

from typing import Any

from pydantic import BaseModel, Field

from backend.models.extraction import ExtractedField, ObjectiveEndpointRecord
from backend.models.study import StudyMeta
from backend.pipeline.agents.base import SheetAgent
from backend.pipeline.agents.common import Cited, terms_hint
from backend.pipeline.agents.context import AgentContext
from backend.pipeline.terminology.ct import ENDPOINT_LEVEL, OBJECTIVE_LEVEL, CtResolver
from backend.pipeline.workbook.layout import OBJECTIVES_ENDPOINTS


class EndpointOut(BaseModel):
    text: Cited = Field(description="The endpoint, verbatim.")
    label: str | None = Field(
        description="A short label of at most 6 words, e.g. 'Cough count change'."
    )
    level: Cited = Field(description="The endpoint level, as a controlled-terminology phrase.")
    purpose: Cited = Field(
        description="What the endpoint measures for: efficacy, safety, pharmacokinetic, "
        "pharmacodynamic, ... as the protocol states or clearly implies. Null if unclear."
    )


class ObjectiveOut(BaseModel):
    text: Cited = Field(description="The objective, verbatim.")
    label: str | None = Field(description="A short label of at most 6 words.")
    level: Cited = Field(description="The objective level, as a controlled-terminology phrase.")
    endpoints: list[EndpointOut] = Field(
        description="The endpoints that measure this objective, in protocol order."
    )


class ObjectivesOut(BaseModel):
    objectives: list[ObjectiveOut]


class ObjectivesEndpointsAgent(SheetAgent):
    sheet = "objectives_endpoints"
    workbook_sheets = ("studyDesignOE",)
    prompt_version = "2"
    # The analysis sections name each objective's variables when the objectives section does not.
    m11_sections = ("3", "1.1.1", "10.4", "10.5")
    fallback_m11_sections = ("1",)
    output_model = ObjectivesOut
    max_tokens = 24000

    def instructions(self, resolver: CtResolver) -> str:
        return f"""\
List the objectives and endpoints for the USDM `studyDesignOE` sheet.

- Copy each objective and endpoint text verbatim, one objective or endpoint per entry. Do not merge \
separate objectives; do not split one sentence into several.
- Attach each endpoint to the objective it measures. When the protocol lists objectives and \
endpoints separately (for example side by side in a table, or as parallel numbered lists), pair \
them by position and level; when a single endpoint serves several objectives, repeat it under each.
- When an objective itself names what is measured ("the change in daily cough count", "time to \
first exacerbation"), that measure is its endpoint; quote it from the objective. When the analysis sections \
state the variables analysed for an objective, use those. An objective with no measure anywhere has \
an empty endpoints list.
- objective level: {terms_hint(self.terms(resolver, OBJECTIVE_LEVEL))}. Tertiary or other \
objectives are "Trial Exploratory Objective".
- endpoint level: {terms_hint(self.terms(resolver, ENDPOINT_LEVEL))}; normally the level of its \
objective.
- Estimand details (population, intercurrent events, summary measures) are not endpoints; leave \
them out."""

    def example(self) -> str:
        return """\
Input (invented protocol, not the one below):
<section id="sec-3" number="3" title="Objectives and Endpoints" pages="9-9">
[[PAGE 9]]
[[TABLE t-9-1]]
| Objectives | Endpoints |
| Primary: To evaluate the effect of examplumab on cough frequency | Change from baseline in 24-hour cough count at Week 12 |
| Secondary: To evaluate the safety of examplumab | Incidence of adverse events; Change in blood pressure |
[[/TABLE]]
</section>

Output:
{"objectives": [
 {"text": {"value": "To evaluate the effect of examplumab on cough frequency", "quote": "To evaluate the effect of examplumab on cough frequency", "section_id": "sec-3", "confidence": 0.95},
  "label": "Cough frequency", "level": {"value": "Trial Primary Objective", "quote": "Primary: To evaluate the effect", "section_id": "sec-3", "confidence": 0.95},
  "endpoints": [{"text": {"value": "Change from baseline in 24-hour cough count at Week 12", "quote": "Change from baseline in 24-hour cough count at Week 12", "section_id": "sec-3", "confidence": 0.95},
    "label": "24-hour cough count", "level": {"value": "Primary Endpoint", "quote": "Primary: To evaluate the effect", "section_id": "sec-3", "confidence": 0.85},
    "purpose": {"value": "Efficacy", "quote": "To evaluate the effect of examplumab on cough frequency", "section_id": "sec-3", "confidence": 0.7}}]},
 {"text": {"value": "To evaluate the safety of examplumab", "quote": "To evaluate the safety of examplumab", "section_id": "sec-3", "confidence": 0.95},
  "label": "Safety", "level": {"value": "Trial Secondary Objective", "quote": "Secondary: To evaluate the safety", "section_id": "sec-3", "confidence": 0.95},
  "endpoints": [{"text": {"value": "Incidence of adverse events", "quote": "Incidence of adverse events", "section_id": "sec-3", "confidence": 0.95},
    "label": "Adverse events", "level": {"value": "Secondary Endpoint", "quote": "Secondary: To evaluate the safety", "section_id": "sec-3", "confidence": 0.85},
    "purpose": {"value": "Safety", "quote": "To evaluate the safety of examplumab", "section_id": "sec-3", "confidence": 0.8}},
   {"text": {"value": "Change in blood pressure", "quote": "Change in blood pressure", "section_id": "sec-3", "confidence": 0.95},
    "label": "Blood pressure", "level": {"value": "Secondary Endpoint", "quote": "Secondary: To evaluate the safety", "section_id": "sec-3", "confidence": 0.85},
    "purpose": {"value": "Safety", "quote": "To evaluate the safety of examplumab", "section_id": "sec-3", "confidence": 0.8}}]}]}"""

    def to_records(
        self, output: ObjectivesOut, context: AgentContext, resolver: CtResolver, study: StudyMeta
    ) -> tuple[list[ObjectiveEndpointRecord], list[str]]:
        warnings: list[str] = []
        column = OBJECTIVES_ENDPOINTS.column
        records: list[ObjectiveEndpointRecord] = []
        objective_number = endpoint_number = 0
        empty = ExtractedField[str]
        for objective in output.objectives:
            text = self.extracted(objective.text, context)
            if text.value is None:
                warnings.append("an objective without text was dropped")
                continue
            objective_number += 1
            objective_fields: dict[str, Any] = {
                "objective_name": self.derived(f"OBJ{objective_number}", "generated"),
                "objective_label": self.judged(
                    objective.label, text, "short label written by the extraction model"
                ),
                "objective_description": empty(),
                "objective_text": text,
                "objective_level": self.cell(
                    objective.level, context, resolver, column("objective_level")
                ),
            }
            endpoints = [e for e in objective.endpoints if e.text.value]
            if not endpoints:
                records.append(
                    ObjectiveEndpointRecord(
                        **objective_fields,
                        **self.blank(_ENDPOINT_FIELDS),
                    )
                )
                continue
            for i, endpoint in enumerate(endpoints):
                endpoint_number += 1
                endpoint_text = self.extracted(endpoint.text, context)
                continuation = self.blank(objective_fields)
                records.append(
                    ObjectiveEndpointRecord(
                        **(objective_fields if i == 0 else continuation),
                        endpoint_name=self.derived(f"END{endpoint_number}", "generated"),
                        endpoint_label=self.judged(
                            endpoint.label,
                            endpoint_text,
                            "short label written by the extraction model",
                        ),
                        endpoint_description=empty(),
                        endpoint_text=endpoint_text,
                        endpoint_purpose=self.extracted(endpoint.purpose, context),
                        endpoint_level=self.cell(
                            endpoint.level, context, resolver, column("endpoint_level")
                        ),
                    )
                )
        if not records:
            warnings.append("no objectives found")
        return records, warnings


_ENDPOINT_FIELDS = (
    "endpoint_name",
    "endpoint_label",
    "endpoint_description",
    "endpoint_text",
    "endpoint_purpose",
    "endpoint_level",
)
