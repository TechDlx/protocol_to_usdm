"""`studyDesign` sheet, key/value block: the design's classification and rationale.

The epoch-by-arm grid below the block depends on epochs and elements from the schedule of
activities and arrives with the SoA agent (Phase 6).
"""

from pydantic import BaseModel, Field

from backend.models.extraction import StudyDesignRecord
from backend.models.study import StudyMeta
from backend.pipeline.agents.base import SheetAgent
from backend.pipeline.agents.common import Cited, terms_hint
from backend.pipeline.agents.context import AgentContext
from backend.pipeline.terminology.ct import (
    BLINDING_SCHEMA,
    DESIGN_CHARACTERISTICS,
    INTERVENTION_MODEL,
    STUDY_PHASE,
    STUDY_TYPE,
    TRIAL_INTENT_TYPES,
    TRIAL_SUB_TYPES,
    CtResolver,
)
from backend.pipeline.workbook.layout import STUDY_DESIGN

DESIGN_NAME = "Study Design 1"


class StudyDesignOut(BaseModel):
    description: Cited = Field(
        description="A one or two sentence summary of the design, verbatim from the protocol's "
        "overall design description (e.g. 'a randomised, double-blind, placebo-controlled, "
        "parallel-group study')."
    )
    rationale: Cited = Field(
        description="The rationale for the trial design, verbatim, at most about 150 words. Null "
        "when the protocol gives no design rationale."
    )
    study_type: Cited = Field(description="The study type, as a controlled-terminology phrase.")
    study_phase: Cited = Field(description="The trial phase, as a controlled-terminology phrase.")
    blinding_schema: Cited = Field(description="The blinding, as a controlled-terminology phrase.")
    intervention_model: Cited = Field(
        description="The intervention model, as a controlled-terminology phrase."
    )
    intent_types: list[Cited] = Field(
        description="The trial's primary purposes, one controlled-terminology phrase each."
    )
    sub_types: list[Cited] = Field(
        description="What kinds of study it is (efficacy, safety, pharmacokinetic, ...), one "
        "controlled-terminology phrase each, only those the protocol states or clearly describes."
    )
    characteristics: list[Cited] = Field(
        description="Design characteristics (randomized, multicenter, adaptive, ...), one "
        "controlled-terminology phrase each, only those the protocol states."
    )


class StudyDesignAgent(SheetAgent):
    sheet = "study_design"
    workbook_sheets = ("studyDesign",)
    prompt_version = "1"
    m11_sections = ("1.1.2", "4.1", "4.2", "6.7")
    m11_exact_sections = ("1", "1.1", "4", "6")
    fallback_m11_sections = ("4",)
    extra_section_ids = ("title-page",)
    output_model = StudyDesignOut
    max_tokens = 12000

    def instructions(self, resolver: CtResolver) -> str:
        t = self.terms
        return f"""\
Classify the trial design for the USDM `studyDesign` sheet.

- study_type: {terms_hint(t(resolver, STUDY_TYPE))}. A trial assigning participants to \
interventions is "Interventional Study".
- study_phase: {terms_hint(t(resolver, STUDY_PHASE))}. Take it from the title page or synopsis.
- blinding_schema: {terms_hint(t(resolver, BLINDING_SCHEMA))}.
- intervention_model: {terms_hint(t(resolver, INTERVENTION_MODEL))}. Arms run side by side are \
"Parallel Study".
- intent_types: {terms_hint(t(resolver, TRIAL_INTENT_TYPES))}. A trial of a therapy for a disease \
is normally "Treatment Study".
- sub_types: {terms_hint(t(resolver, TRIAL_SUB_TYPES))}. Include a sub type only when the \
objectives or design state it (a primary efficacy objective supports "Efficacy Study"; a safety \
objective supports "Safety Study"; a pharmacokinetic objective supports "Pharmacokinetic Study").
- characteristics: {terms_hint(t(resolver, DESIGN_CHARACTERISTICS))}. Only those the protocol \
states in words such as "randomised", "multicentre", "multinational", "adaptive".
- Each listed term needs its own quote. Leave lists empty rather than guessing."""

    def example(self) -> str:
        return """\
Input (invented protocol, not the one below):
<section id="sec-4.1" number="4.1" title="Overall Design" pages="12-12">
[[PAGE 12]]
This is a Phase 2, multicentre, randomised, double-blind, placebo-controlled, parallel-group study \
to evaluate the efficacy and safety of examplumab in adults with chronic cough.
</section>

Output:
{"description": {"value": "This is a Phase 2, multicentre, randomised, double-blind, placebo-controlled, parallel-group study to evaluate the efficacy and safety of examplumab in adults with chronic cough.", "quote": "multicentre, randomised, double-blind, placebo-controlled, parallel-group study", "section_id": "sec-4.1", "confidence": 0.9},
 "rationale": {"value": null, "quote": null, "section_id": null, "confidence": 0},
 "study_type": {"value": "Interventional Study", "quote": "randomised, double-blind, placebo-controlled", "section_id": "sec-4.1", "confidence": 0.8},
 "study_phase": {"value": "Phase II Trial", "quote": "This is a Phase 2", "section_id": "sec-4.1", "confidence": 0.95},
 "blinding_schema": {"value": "Double Blind Study", "quote": "double-blind", "section_id": "sec-4.1", "confidence": 0.95},
 "intervention_model": {"value": "Parallel Study", "quote": "parallel-group study", "section_id": "sec-4.1", "confidence": 0.95},
 "intent_types": [{"value": "Treatment Study", "quote": "evaluate the efficacy and safety of examplumab in adults with chronic cough", "section_id": "sec-4.1", "confidence": 0.7}],
 "sub_types": [{"value": "Efficacy Study", "quote": "evaluate the efficacy", "section_id": "sec-4.1", "confidence": 0.8},
               {"value": "Safety Study", "quote": "efficacy and safety", "section_id": "sec-4.1", "confidence": 0.8}],
 "characteristics": [{"value": "Multicenter Study", "quote": "multicentre", "section_id": "sec-4.1", "confidence": 0.9},
                     {"value": "Randomized Controlled Clinical Trial", "quote": "randomised, double-blind, placebo-controlled", "section_id": "sec-4.1", "confidence": 0.8}]}"""

    def to_records(
        self, output: StudyDesignOut, context: AgentContext, resolver: CtResolver, study: StudyMeta
    ) -> tuple[StudyDesignRecord, list[str]]:
        column = STUDY_DESIGN.column
        record = StudyDesignRecord(
            name=self.derived(DESIGN_NAME, "the single study design of this protocol"),
            label=self.derived(None, ""),
            description=self.extracted(output.description, context),
            rationale=self.extracted(output.rationale, context),
            blinding_schema=self.cell(
                output.blinding_schema, context, resolver, column("blinding_schema")
            ),
            intent_types=self.joined(
                output.intent_types, context, resolver, column("intent_types")
            ),
            sub_types=self.joined(output.sub_types, context, resolver, column("sub_types")),
            intervention_model=self.cell(
                output.intervention_model, context, resolver, column("intervention_model")
            ),
            characteristics=self.joined(
                output.characteristics, context, resolver, column("characteristics")
            ),
            study_type=self.cell(output.study_type, context, resolver, column("study_type")),
            study_phase=self.cell(output.study_phase, context, resolver, column("study_phase")),
        )
        warnings = []
        if record.rationale.is_empty:
            warnings.append("no design rationale found; the studyDesign sheet requires one")
        return record, warnings
