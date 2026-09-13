"""`studyDesignArms` sheet: the arms participants are assigned to."""

from pydantic import BaseModel, Field

from backend.models.extraction import ArmRecord
from backend.models.study import StudyMeta
from backend.pipeline.agents.base import SheetAgent
from backend.pipeline.agents.common import Cited, terms_hint
from backend.pipeline.agents.context import AgentContext
from backend.pipeline.identifiers.names import NameRegistry, safe_name
from backend.pipeline.terminology.ct import ARM_DATA_ORIGIN_TYPE, ARM_TYPE, CtResolver


class ArmOut(BaseModel):
    label: Cited = Field(description="The arm's name as the protocol writes it.")
    description: Cited = Field(
        description="What participants in this arm receive, verbatim from the protocol (one or "
        "two sentences)."
    )
    type: Cited = Field(description="The arm's role, as a controlled-terminology phrase.")
    data_origin_type: Cited = Field(
        description="Where the arm's data comes from, as a controlled-terminology phrase."
    )
    data_origin_description: Cited = Field(
        description="A short plain description of the data origin, e.g. 'Data collected from "
        "randomised participants'. Quote the protocol text that supports it."
    )


class ArmsOut(BaseModel):
    arms: list[ArmOut] = Field(description="Every arm, in the order the protocol presents them.")


class ArmsAgent(SheetAgent):
    sheet = "study_design_arms"
    workbook_sheets = ("studyDesignArms",)
    prompt_version = "1"
    m11_sections = ("1.1.2", "1.2", "4.1", "6.1", "6.7")
    m11_exact_sections = ("1", "4", "6")  # protocol summary and the chapters' own text
    fallback_m11_sections = ("4", "6")
    output_model = ArmsOut
    max_tokens = 16000

    def instructions(self, resolver: CtResolver) -> str:
        return f"""\
List the study arms for the USDM `studyDesignArms` sheet.

- An arm is a group participants are randomised or assigned to for the whole study, such as \
"Drug X plus standard therapy" and "Placebo plus standard therapy". Dose cohorts are separate arms only \
when participants are assigned to them as distinct groups.
- Do not list epochs or periods (screening, follow-up), sub-studies, or strata as arms.
- type: {terms_hint(self.terms(resolver, ARM_TYPE))}. The arm receiving the product under \
investigation is normally "Investigational Arm"; an arm receiving placebo on top of standard \
therapy is normally "Placebo Control Arm".
- data_origin_type: {terms_hint(self.terms(resolver, ARM_DATA_ORIGIN_TYPE))}. Arms of participants \
enrolled and treated in this study produce "Data Generated Within Study"; quote the text showing \
participants are randomised or enrolled, with confidence about 0.7."""

    def example(self) -> str:
        return """\
Input (invented protocol, not the one below):
<section id="sec-3.1" number="3.1" title="Overall Design" pages="12-12">
[[PAGE 12]]
Eligible adults will be randomised 1:1 to receive examplumab 200 mg once daily or matching placebo \
once daily for 12 weeks.
</section>

Output:
{"arms": [
 {"label": {"value": "Examplumab 200 mg", "quote": "examplumab 200 mg once daily", "section_id": "sec-3.1", "confidence": 0.85},
  "description": {"value": "Examplumab 200 mg once daily for 12 weeks", "quote": "examplumab 200 mg once daily", "section_id": "sec-3.1", "confidence": 0.8},
  "type": {"value": "Investigational Arm", "quote": "receive examplumab 200 mg", "section_id": "sec-3.1", "confidence": 0.85},
  "data_origin_type": {"value": "Data Generated Within Study", "quote": "Eligible adults will be randomised 1:1", "section_id": "sec-3.1", "confidence": 0.7},
  "data_origin_description": {"value": "Data collected from randomised participants", "quote": "Eligible adults will be randomised 1:1", "section_id": "sec-3.1", "confidence": 0.7}},
 {"label": {"value": "Placebo", "quote": "matching placebo once daily", "section_id": "sec-3.1", "confidence": 0.85},
  "description": {"value": "Matching placebo once daily for 12 weeks", "quote": "matching placebo once daily", "section_id": "sec-3.1", "confidence": 0.8},
  "type": {"value": "Placebo Control Arm", "quote": "matching placebo once daily", "section_id": "sec-3.1", "confidence": 0.85},
  "data_origin_type": {"value": "Data Generated Within Study", "quote": "Eligible adults will be randomised 1:1", "section_id": "sec-3.1", "confidence": 0.7},
  "data_origin_description": {"value": "Data collected from randomised participants", "quote": "Eligible adults will be randomised 1:1", "section_id": "sec-3.1", "confidence": 0.7}}]}"""

    def to_records(
        self, output: ArmsOut, context: AgentContext, resolver: CtResolver, study: StudyMeta
    ) -> tuple[list[ArmRecord], list[str]]:
        warnings: list[str] = []
        names = NameRegistry()
        records: list[ArmRecord] = []
        for i, arm in enumerate(output.arms, start=1):
            label = self.extracted(arm.label, context)
            if label.value is None:
                warnings.append(f"arm {i} dropped: no label")
                continue
            records.append(
                ArmRecord(
                    name=self.derived(
                        names.claim(safe_name(label.value, f"Arm {i}")),
                        "generated from the arm label",
                    ),
                    label=label,
                    description=self.extracted(arm.description, context),
                    type=self.extracted(arm.type, context, resolver, ARM_TYPE),
                    data_origin_description=self.extracted(arm.data_origin_description, context),
                    data_origin_type=self.extracted(
                        arm.data_origin_type, context, resolver, ARM_DATA_ORIGIN_TYPE
                    ),
                )
            )
        if not records:
            warnings.append("no arms found")
        return records, warnings
