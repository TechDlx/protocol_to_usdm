"""`studyDesignIndications` sheet: the diseases or conditions the trial studies.

Indication codes (SNOMED CT, ICD-10, MedDRA) are not assigned: no licensed source for those
dictionaries is bundled with the keyless setup, and the model must not invent codes.
"""

from pydantic import BaseModel, Field

from backend.models.extraction import IndicationRecord
from backend.models.study import StudyMeta
from backend.pipeline.agents.base import SheetAgent
from backend.pipeline.agents.common import Cited
from backend.pipeline.agents.context import AgentContext


class IndicationOut(BaseModel):
    label: Cited = Field(description="The disease or condition, as the protocol names it.")
    description: Cited = Field(
        description="A fuller description of the condition as studied (stage, subtype, line of "
        "therapy), verbatim, one sentence."
    )
    rare_disease: bool = Field(
        description="true only when the protocol calls the condition rare or orphan."
    )


class IndicationsOut(BaseModel):
    indications: list[IndicationOut]


class IndicationsAgent(SheetAgent):
    sheet = "indications"
    workbook_sheets = ("studyDesignIndications",)
    prompt_version = "1"
    m11_sections = ("1.1.2", "5.1")
    m11_exact_sections = ("1", "1.1", "2", "5")
    fallback_m11_sections = ("2",)
    extra_section_ids = ("title-page",)
    output_model = IndicationsOut
    max_tokens = 6000

    def instructions(self, resolver: object) -> str:
        return """\
List the indications for the USDM `studyDesignIndications` sheet: the disease or condition the \
trial intervention is intended to treat, prevent or diagnose.

- Usually one indication, named in the title or synopsis. List more only when the trial studies \
several distinct conditions.
- Do not list comorbidities, exclusion conditions or the conditions of background therapy.
- Do not give any medical codes."""

    def example(self) -> str:
        return """\
Input (invented protocol, not the one below):
<section id="title-page" number="" title="Title Page" pages="1-1">
[[PAGE 1]]
A Phase 2 Study of Examplumab in Adults with Refractory Chronic Cough
</section>

Output:
{"indications": [{"label": {"value": "Refractory chronic cough", "quote": "Adults with Refractory Chronic Cough", "section_id": "title-page", "confidence": 0.9},
  "description": {"value": "Refractory chronic cough in adults", "quote": "Examplumab in Adults with Refractory Chronic Cough", "section_id": "title-page", "confidence": 0.8},
  "rare_disease": false}]}"""

    def to_records(
        self, output: IndicationsOut, context: AgentContext, resolver: object, study: StudyMeta
    ) -> tuple[list[IndicationRecord], list[str]]:
        records: list[IndicationRecord] = []
        for indication in output.indications:
            label = self.extracted(indication.label, context)
            if label.value is None:
                continue
            records.append(
                IndicationRecord(
                    name=self.derived(f"IND{len(records) + 1}", "generated"),
                    label=label,
                    description=self.extracted(indication.description, context),
                    is_rare_disease=self.judged(
                        "Y" if indication.rare_disease else "N",
                        label,
                        "Y only when the protocol calls the condition rare",
                    ),
                )
            )
        return records, [] if records else ["no indications found"]
