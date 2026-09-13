"""`studyDesignPopulations` sheet: the main study population and any cohorts.

The model reports numbers and units as it reads them; the workbook formats ("300", "280..320",
"18..75 YEARS", Y/N) are produced here, with CDISC unit submission values.
"""

from typing import Literal

from pydantic import BaseModel, Field

from backend.models.extraction import ExtractedField, PopulationRecord
from backend.models.study import StudyMeta
from backend.pipeline.agents.base import SheetAgent
from backend.pipeline.agents.common import Cited, terms_hint
from backend.pipeline.agents.context import AgentContext
from backend.pipeline.terminology.ct import PLANNED_SEX, CtResolver
from backend.pipeline.workbook import formats
from backend.pipeline.workbook.cells import resolve_cell
from backend.pipeline.workbook.layout import POPULATIONS

_YES = {"yes", "y", "true"}
_NO = {"no", "n", "false"}


class CountOut(BaseModel):
    value: Cited = Field(description="The planned number of participants, digits only, e.g. '300'.")
    upper: str | None = Field(
        description="Only when the protocol gives a range ('approximately 280 to 320'): the upper "
        "number, with value holding the lower number."
    )


class PopulationOut(BaseModel):
    level: Literal["main", "cohort"] = Field(
        description="main: the overall study population (exactly one). cohort: a distinct "
        "sub-population the protocol defines and enrols separately (e.g. dose-escalation cohorts, "
        "a paediatric cohort)."
    )
    label: Cited = Field(description="A short name for the population as the protocol words it.")
    description: Cited = Field(
        description="Who the population is, verbatim from the population description, one or two "
        "sentences."
    )
    planned_enrollment: CountOut | None = Field(
        description="How many participants will be enrolled or randomised. Null if not stated."
    )
    planned_completion: CountOut | None = Field(
        description="How many are planned to complete (or be evaluable). Null if not stated."
    )
    age_min: Cited = Field(
        description="The minimum age as a number, from the eligibility criteria."
    )
    age_max: Cited = Field(
        description="The maximum age as a number. Null when there is no maximum."
    )
    age_unit: str | None = Field(description="The unit of the ages, e.g. 'years'.")
    sex: list[Cited] = Field(
        description="The sexes that may participate, as controlled-terminology phrases."
    )
    healthy_subjects: Cited = Field(
        description="'yes' if healthy volunteers are enrolled, 'no' if participants have the "
        "condition under study. Quote the text that shows it."
    )


class PopulationsOut(BaseModel):
    populations: list[PopulationOut]


class PopulationsAgent(SheetAgent):
    sheet = "populations"
    workbook_sheets = ("studyDesignPopulations",)
    prompt_version = "1"
    postprocess_version = "2"  # 2: planned sex Both written as Female, Male
    m11_sections = ("1.1.2", "4.1", "5.1", "5.2", "10.11")
    m11_exact_sections = ("1", "1.1", "5")
    fallback_m11_sections = ("5",)
    output_model = PopulationsOut
    max_tokens = 12000

    def instructions(self, resolver: CtResolver) -> str:
        return f"""\
Describe the planned study population for the USDM `studyDesignPopulations` sheet.

- Give exactly one main population. Add cohorts only when the protocol defines separately enrolled \
cohorts; arms are not cohorts.
- Numbers: copy digits only (no words such as "approximately"); quote the sentence that gives them. \
Enrollment is the number to be enrolled or randomised for the whole study; completion is only given \
when the protocol states how many should complete or be evaluable.
- Ages come from the inclusion criteria: "18 years or older" gives age_min 18, age_max null.
- sex: {terms_hint(self.terms(resolver, PLANNED_SEX))}. Use "Both" when men and women may take part; \
"Female" for studies only of women, including postmenopausal women.
- healthy_subjects: "no" when participants must have the disease or condition studied."""

    def example(self) -> str:
        return """\
Input (invented protocol, not the one below):
<section id="sec-5.2" number="5.2" title="Inclusion Criteria" pages="20-20">
[[PAGE 20]]
1. Men or women aged 18 to 80 years with refractory chronic cough for at least one year.
</section>
<section id="sec-10.11" number="10.11" title="Sample Size Determination" pages="41-41">
[[PAGE 41]]
Approximately 240 participants will be randomised.
</section>

Output:
{"populations": [{"level": "main",
  "label": {"value": "Adults with refractory chronic cough", "quote": "refractory chronic cough for at least one year", "section_id": "sec-5.2", "confidence": 0.8},
  "description": {"value": "Men or women aged 18 to 80 years with refractory chronic cough for at least one year.", "quote": "Men or women aged 18 to 80 years with refractory chronic cough", "section_id": "sec-5.2", "confidence": 0.8},
  "planned_enrollment": {"value": {"value": "240", "quote": "Approximately 240 participants will be randomised.", "section_id": "sec-10.11", "confidence": 0.9}, "upper": null},
  "planned_completion": null,
  "age_min": {"value": "18", "quote": "aged 18 to 80 years", "section_id": "sec-5.2", "confidence": 0.95},
  "age_max": {"value": "80", "quote": "aged 18 to 80 years", "section_id": "sec-5.2", "confidence": 0.95},
  "age_unit": "years",
  "sex": [{"value": "Both", "quote": "Men or women", "section_id": "sec-5.2", "confidence": 0.9}],
  "healthy_subjects": {"value": "no", "quote": "with refractory chronic cough", "section_id": "sec-5.2", "confidence": 0.85}}]}"""

    def _count(
        self, count: CountOut | None, context: AgentContext, what: str, warnings: list[str]
    ) -> ExtractedField[str]:
        if count is None:
            return ExtractedField()
        basis = self.extracted(count.value, context)
        lower = formats.number_text(basis.value)
        if basis.value is not None and lower is None:
            warnings.append(f"{what} '{basis.value}' is not a number; left for the reviewer")
            return basis
        upper = formats.number_text(count.upper) if count.upper else None
        text = f"{lower}..{upper}" if lower and upper else lower
        return self.reformatted(text, basis, "digits only")

    def _sex(
        self, sexes: list[Cited], context: AgentContext, resolver: CtResolver
    ) -> ExtractedField[str]:
        """USDM expects planned sex as Male and/or Female codes (rule DDF00188), so the CT term
        "Both" is written as "Female, Male"."""
        column = POPULATIONS.column("planned_sex")
        cell = self.joined(sexes, context, resolver, column)
        terms = cell.terminology.preferred_term if cell.terminology else None
        if not terms or "Both" not in [t.strip() for t in terms.split(",")]:
            return cell
        written = self.reformatted("Female, Male", cell, "Both written as Female and Male")
        written.terminology = resolve_cell(column, "Female, Male", resolver)
        return written

    def to_records(
        self, output: PopulationsOut, context: AgentContext, resolver: CtResolver, study: StudyMeta
    ) -> tuple[list[PopulationRecord], list[str]]:
        warnings: list[str] = []
        records: list[PopulationRecord] = []
        mains = [p for p in output.populations if p.level == "main"]
        if len(mains) != 1:
            warnings.append(f"expected one main population, found {len(mains)}")
        cohort_number = 0
        for pop in sorted(output.populations, key=lambda p: p.level != "main"):
            if pop.level == "main":
                name, level = ("POP1" if pop is mains[0] else f"POP{mains.index(pop) + 1}"), "Main"
            else:
                cohort_number += 1
                name, level = f"COHORT{cohort_number}", "Cohort"

            age_min = self.extracted(pop.age_min, context)
            age_max = self.extracted(pop.age_max, context)
            age = ExtractedField[str]()
            if age_min.value is not None and age_max.value is not None:
                text = formats.format_range(age_min.value, age_max.value, pop.age_unit, resolver)
                basis = age_min if age_min.provenance else age_max
                age = self.reformatted(text, basis, "range with CDISC unit")
            elif age_min.value is not None:
                warnings.append(
                    f"{name}: only a minimum age ({age_min.value}) is stated; a planned age needs "
                    "both ends, so it is left empty"
                )

            healthy = self.extracted(pop.healthy_subjects, context)
            answer = (healthy.value or "").strip().casefold()
            flag = "Y" if answer in _YES else "N" if answer in _NO else None
            records.append(
                PopulationRecord(
                    level=self.derived(level, "main population or cohort, as the model classed it"),
                    name=self.derived(name, "generated"),
                    label=self.extracted(pop.label, context),
                    description=self.extracted(pop.description, context),
                    planned_completion_number=self._count(
                        pop.planned_completion, context, f"{name} completion number", warnings
                    ),
                    planned_enrollment_number=self._count(
                        pop.planned_enrollment, context, f"{name} enrollment number", warnings
                    ),
                    planned_age=age,
                    planned_sex=self._sex(pop.sex, context, resolver),
                    includes_healthy_subjects=self.reformatted(flag, healthy, "as Y/N")
                    if flag
                    else healthy,
                )
            )
        return records, warnings
