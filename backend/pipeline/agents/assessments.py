"""Assessments: what each scheduled assessment measures, for Biomedical Concepts.

The schedule names activities ("Vital signs", "Hematology"); the assessment chapter and the
laboratory appendix say what they measure ("systolic and diastolic blood pressure, pulse rate",
"hemoglobin, platelets, ..."). This agent reads those sections. At assembly each schedule row is
matched to an assessment by name, and each measurement is looked up in the CDISC BC catalogue
(backend/pipeline/identifiers/concepts.py), so the model never names a concept itself.
"""

from typing import ClassVar

from pydantic import BaseModel, Field

from backend.models.extraction import AssessmentRecord
from backend.models.study import StudyMeta
from backend.pipeline.agents.base import SheetAgent
from backend.pipeline.agents.common import Cited
from backend.pipeline.agents.context import AgentContext


class AssessmentOut(BaseModel):
    name: Cited = Field(
        description="The assessment as the protocol names it, preferably as in the schedule of "
        "activities (e.g. 'Vital signs', 'Hematology')."
    )
    measurements: list[Cited] = Field(
        description="Each individual parameter it measures, one per entry, as printed (e.g. "
        "'systolic blood pressure', 'platelet count'). A rating scale or questionnaire measured as "
        "a whole is one entry with its name."
    )


class AssessmentsOut(BaseModel):
    assessments: list[AssessmentOut]


class AssessmentsAgent(SheetAgent):
    sheet = "assessments"
    workbook_sheets = ("main-timeline",)
    prompt_version = "1"
    m11_sections = ("8", "12.1")
    output_model = AssessmentsOut
    max_tokens = 32000
    empty_when_missing = True
    empty_records: ClassVar[list[AssessmentRecord]] = []

    def instructions(self, resolver: object) -> str:
        return """\
List the study's assessments and the individual parameters each one measures, so they can be \
matched to CDISC Biomedical Concepts.

- Include assessments with listed parameters: vital signs, physical measurements, ECG parameters, \
laboratory panels (hematology, chemistry, urinalysis, coagulation, ...), and named scales or \
questionnaires.
- List parameters exactly as the protocol lists them, one per entry. Split lists ("sodium, potassium \
and chloride" is three). Do not add parameters the protocol does not list, and do not expand a panel \
name into its usual contents.
- Skip procedures that measure nothing specific (informed consent, randomisation, drug dispensing).
- In tables, quote the cell text."""

    def example(self) -> str:
        return """\
Input (invented protocol, not the one below):
<section id="sec-8.2" number="8.2" title="Vital Signs" pages="30-30">
[[PAGE 30]]
Vital signs (sitting blood pressure, pulse rate and oral temperature) are measured after 5 minutes rest.
</section>

Output:
{"assessments": [{"name": {"value": "Vital signs", "quote": "Vital signs (sitting blood pressure", "section_id": "sec-8.2", "confidence": 0.95},
  "measurements": [{"value": "sitting blood pressure", "quote": "sitting blood pressure", "section_id": "sec-8.2", "confidence": 0.9},
                   {"value": "pulse rate", "quote": "pulse rate", "section_id": "sec-8.2", "confidence": 0.95},
                   {"value": "oral temperature", "quote": "oral temperature", "section_id": "sec-8.2", "confidence": 0.95}]}]}"""

    def to_records(
        self, output: AssessmentsOut, context: AgentContext, resolver: object, study: StudyMeta
    ) -> tuple[list[AssessmentRecord], list[str]]:
        records: list[AssessmentRecord] = []
        for item in output.assessments:
            name = self.extracted(item.name, context)
            if name.value is None:
                continue
            measurements = [self.extracted(m, context) for m in item.measurements]
            records.append(
                AssessmentRecord(assessment=name, measurements=[m for m in measurements if m.value])
            )
        return records, [] if records else ["no assessments with listed parameters found"]
