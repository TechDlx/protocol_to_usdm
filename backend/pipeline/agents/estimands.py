"""`studyDesignEstimands` sheet: estimands, one workbook row per intercurrent event.

Estimands refer to a population, an intervention and an endpoint by name. Those names come from
other agents' sheets, so the model quotes what the protocol says ("the ITT population", "the
primary endpoint of PFS") and the assembly step links each phrase to a name
(backend/pipeline/identifiers/linking.py). Phrases that match nothing stay visible and block review.
"""

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from backend.models.extraction import EstimandRecord, ExtractedField
from backend.models.study import StudyMeta
from backend.pipeline.agents.base import SheetAgent
from backend.pipeline.agents.common import Cited
from backend.pipeline.agents.context import AgentContext


class IntercurrentEventOut(BaseModel):
    label: str = Field(description="A short name for the event, e.g. 'Treatment discontinuation'.")
    description: Cited = Field(description="The intercurrent event as the protocol describes it.")
    strategy: Cited = Field(
        description="The strategy for handling it (treatment policy, hypothetical, composite "
        "variable, while on treatment, principal stratum) with the protocol's wording."
    )
    text: Cited = Field(description="The protocol's full statement about this event, verbatim.")


class EstimandOut(BaseModel):
    summary_measure: Cited = Field(
        description="The population-level summary, e.g. 'hazard ratio' or 'difference in mean "
        "change from baseline'."
    )
    population_description: Cited = Field(description="The estimand's population, verbatim.")
    population: Cited = Field(
        description="The phrase naming the population or analysis set, e.g. 'intent-to-treat "
        "population'."
    )
    treatment: Cited = Field(
        description="The phrase naming the investigational treatment compared, e.g. "
        "'examplumab 200 mg'."
    )
    endpoint: Cited = Field(description="The phrase naming the endpoint (variable) it uses.")
    intercurrent_events: list[IntercurrentEventOut] = Field(
        description="At least one; the events the protocol lists for this estimand."
    )


class EstimandsOut(BaseModel):
    estimands: list[EstimandOut]


class EstimandsAgent(SheetAgent):
    sheet = "estimands"
    workbook_sheets = ("studyDesignEstimands",)
    prompt_version = "1"
    m11_sections = ("1.1.1", "3", "4.2.1", "10.1", "10.4", "10.5")
    fallback_m11_sections = ()
    output_model = EstimandsOut
    max_tokens = 16000
    empty_when_missing = True
    empty_records: ClassVar[list[EstimandRecord]] = []

    def instructions(self, resolver: object) -> str:
        return """\
List the estimands for the USDM `studyDesignEstimands` sheet.

- Only estimands the protocol defines: it states the attributes (population, treatment, variable or \
endpoint, intercurrent events and their handling, summary measure), usually in the objectives, an \
estimands table or the statistical section. Many older protocols define none: return an empty list \
rather than assembling an estimand from general analysis text.
- population, treatment and endpoint are short phrases naming what the estimand uses, quoted from \
the protocol; they are matched to the other sheets later.
- Every estimand needs at least one intercurrent event; if the protocol defines an estimand without \
any, give one event whose description says none are specified, quoting that statement."""

    def example(self) -> str:
        return """\
Input (invented protocol, not the one below):
<section id="sec-3.1" number="3.1" title="Primary Objective and Estimand" pages="9-9">
[[PAGE 9]]
Estimand: the difference between examplumab 200 mg and placebo in mean change from baseline in \
24-hour cough count at Week 12 in all randomised participants. Discontinuation of study intervention \
is handled using a treatment policy strategy: data collected after discontinuation are used.
</section>

Output:
{"estimands": [{"summary_measure": {"value": "Difference in mean change from baseline", "quote": "difference between examplumab 200 mg and placebo in mean change from baseline", "section_id": "sec-3.1", "confidence": 0.85},
 "population_description": {"value": "All randomised participants", "quote": "in all randomised participants", "section_id": "sec-3.1", "confidence": 0.9},
 "population": {"value": "all randomised participants", "quote": "in all randomised participants", "section_id": "sec-3.1", "confidence": 0.9},
 "treatment": {"value": "examplumab 200 mg", "quote": "between examplumab 200 mg and placebo", "section_id": "sec-3.1", "confidence": 0.9},
 "endpoint": {"value": "change from baseline in 24-hour cough count at Week 12", "quote": "change from baseline in 24-hour cough count at Week 12", "section_id": "sec-3.1", "confidence": 0.9},
 "intercurrent_events": [{"label": "Discontinuation of study intervention",
   "description": {"value": "Discontinuation of study intervention", "quote": "Discontinuation of study intervention", "section_id": "sec-3.1", "confidence": 0.9},
   "strategy": {"value": "Treatment policy", "quote": "handled using a treatment policy strategy", "section_id": "sec-3.1", "confidence": 0.9},
   "text": {"value": "Discontinuation of study intervention is handled using a treatment policy strategy: data collected after discontinuation are used.", "quote": "data collected after discontinuation are used", "section_id": "sec-3.1", "confidence": 0.9}}]}]}"""

    def to_records(
        self, output: EstimandsOut, context: AgentContext, resolver: object, study: StudyMeta
    ) -> tuple[list[EstimandRecord], list[str]]:
        warnings: list[str] = []
        records: list[EstimandRecord] = []
        event_number = 0
        empty = ExtractedField[str]
        for i, estimand in enumerate(output.estimands, start=1):
            head: dict[str, Any] = {
                "name": self.derived(f"EST{i}", "generated"),
                "summary_measure": self.extracted(estimand.summary_measure, context),
                "population_description": self.extracted(estimand.population_description, context),
                "population": self.extracted(estimand.population, context),
                "treatment": self.extracted(estimand.treatment, context),
                "endpoint": self.extracted(estimand.endpoint, context),
            }
            events = estimand.intercurrent_events or []
            if not events:
                warnings.append(f"EST{i} has no intercurrent event; the workbook needs one")
                records.append(
                    EstimandRecord(
                        **head,
                        event_name=empty(),
                        event_description=empty(),
                        event_strategy=empty(),
                        event_text=empty(),
                    )
                )
                continue
            for j, event in enumerate(events):
                event_number += 1
                description = self.extracted(event.description, context)
                records.append(
                    EstimandRecord(
                        **(head if j == 0 else self.blank(head)),
                        event_name=self.derived(f"ICE{event_number}", "generated"),
                        event_description=description,
                        event_strategy=self.extracted(event.strategy, context),
                        event_text=self.extracted(event.text, context),
                    )
                )
        return records, warnings
