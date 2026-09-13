"""`study` sheet: titles, identifiers, version, status, rationale and governance dates."""

from typing import Literal

from pydantic import BaseModel, Field

from backend.models.extraction import ExtractedField, GovernanceDateRecord, StudyRecord
from backend.models.study import StudyMeta
from backend.pipeline.agents.base import SheetAgent
from backend.pipeline.agents.common import Cited, terms_hint
from backend.pipeline.agents.context import AgentContext
from backend.pipeline.identifiers.names import NameRegistry, governance_date_name, safe_name
from backend.pipeline.terminology.ct import GOVERNANCE_DATE_TYPE, STUDY_PROTOCOL_STATUS, CtResolver


class GovernanceDateOut(BaseModel):
    category: Literal["study_version", "protocol_document", "amendment"] = Field(
        description=(
            "study_version: the date of the protocol version this document is. amendment: the "
            "date of a specific amendment. protocol_document: another dated version of the "
            "protocol document."
        )
    )
    label: str = Field(description="Short label, e.g. 'Amendment 3' or 'Original protocol'.")
    type: Cited = Field(description="The kind of date, as a controlled-terminology phrase.")
    date: Cited = Field(description="The date in ISO format yyyy-mm-dd.")
    geographic_scope: str = Field(
        description="'Global', or 'Region: <name>' / 'Country: <name>' only when the protocol "
        "restricts the date to a region or country."
    )


class StudyOut(BaseModel):
    official_title: Cited = Field(description="The full protocol title, usually on the title page.")
    brief_title: Cited = Field(description="A short title only if the protocol labels one.")
    public_title: Cited = Field(description="A public/lay title only if the protocol labels one.")
    scientific_title: Cited = Field(
        description="A scientific title only if the protocol labels one."
    )
    acronym: Cited = Field(description="The study acronym or short name, e.g. COUGH-2.")
    sponsor_protocol_identifier: Cited = Field(description="The sponsor's protocol number.")
    protocol_version: Cited = Field(
        description="The version of this protocol document as the protocol states it, e.g. "
        "'Amendment 3' or '2.0'."
    )
    protocol_status: Cited = Field(
        description="Document status, as a controlled-terminology phrase."
    )
    rationale: Cited = Field(
        description="The study rationale, verbatim, at most about 150 words: the protocol's "
        "statement of why this study is being done. Use a section titled rationale when there is "
        "one; otherwise the passage, often at the end of the introduction or background, that "
        "explains the reason for or approach of this particular study. Null only when no passage "
        "explains it."
    )
    governance_dates: list[GovernanceDateOut] = Field(
        description="Every protocol version, amendment, approval or effective date the protocol "
        "states. Empty when none are given."
    )


class StudyAgent(SheetAgent):
    sheet = "study"
    workbook_sheets = ("study",)
    prompt_version = "2"
    m11_sections = ("0", "1.1", "2.1", "12.3")
    # Own text only: the protocol summary (not its schedule of activities, 1.3) and the
    # introduction chapter, where many protocols state the rationale.
    m11_exact_sections = ("1", "2")
    fallback_m11_sections = ("2",)
    extra_section_ids = ("title-page",)  # the title page carries most study-level facts
    output_model = StudyOut
    max_tokens = 16000

    def instructions(self, resolver: CtResolver) -> str:
        return f"""\
Extract the study-level facts for the USDM `study` sheet from the protocol below.

- Titles: return a title only when the protocol gives it. Do not shorten the official title into a \
brief title yourself.
- protocol_status: {terms_hint(self.terms(resolver, STUDY_PROTOCOL_STATUS))}. A protocol marked \
"Final" is Final.
- governance_dates[].type: {terms_hint(self.terms(resolver, GOVERNANCE_DATE_TYPE))}.
- Dates must be converted to yyyy-mm-dd; quote them as printed. Skip dates you cannot resolve to a \
full day.
- Document history tables list one row per version or amendment; each dated row is a governance date."""

    def example(self) -> str:
        return """\
Input (invented protocol, not the one below):
<section id="title-page" number="" title="Title Page" pages="1-1">
[[PAGE 1]]
A RANDOMISED PHASE 2 STUDY OF EXAMPLUMAB IN ADULTS WITH CHRONIC COUGH
Protocol Number: EX-204 (COUGH-2)
Final Protocol Amendment 1, 14 March 2024
</section>

Output (abridged):
{"official_title": {"value": "A RANDOMISED PHASE 2 STUDY OF EXAMPLUMAB IN ADULTS WITH CHRONIC COUGH", \
"quote": "A RANDOMISED PHASE 2 STUDY OF EXAMPLUMAB", "section_id": "title-page", "confidence": 0.95},
 "brief_title": {"value": null, "quote": null, "section_id": null, "confidence": 0},
 "acronym": {"value": "COUGH-2", "quote": "EX-204 (COUGH-2)", "section_id": "title-page", "confidence": 0.9},
 "sponsor_protocol_identifier": {"value": "EX-204", "quote": "Protocol Number: EX-204", "section_id": "title-page", "confidence": 0.95},
 "protocol_version": {"value": "Amendment 1", "quote": "Final Protocol Amendment 1", "section_id": "title-page", "confidence": 0.9},
 "protocol_status": {"value": "Final", "quote": "Final Protocol Amendment 1", "section_id": "title-page", "confidence": 0.9},
 "governance_dates": [{"category": "amendment", "label": "Amendment 1", "type": {"value": "Issued Date", \
"quote": "Amendment 1, 14 March 2024", "section_id": "title-page", "confidence": 0.6}, "date": {"value": "2024-03-14", \
"quote": "14 March 2024", "section_id": "title-page", "confidence": 0.95}, "geographic_scope": "Global"}]}"""

    def to_records(
        self, output: StudyOut, context: AgentContext, resolver: CtResolver, study: StudyMeta
    ) -> tuple[StudyRecord, list[str]]:
        warnings: list[str] = []
        acronym = self.extracted(output.acronym, context)
        version = self.extracted(output.protocol_version, context)
        brief = self.extracted(output.brief_title, context)

        names = NameRegistry()
        dates: list[GovernanceDateRecord] = []
        per_category: dict[str, int] = {}
        for d in output.governance_dates:
            date_field = self.extracted(d.date, context)
            if date_field.value is None:
                warnings.append(f"governance date '{d.label}' dropped: no date value")
                continue
            type_field = self.extracted(d.type, context, resolver, GOVERNANCE_DATE_TYPE)
            per_category[d.category] = per_category.get(d.category, 0) + 1
            type_term = (
                type_field.terminology.preferred_term
                if type_field.terminology and type_field.terminology.preferred_term
                else type_field.value
            )
            dates.append(
                GovernanceDateRecord(
                    name=self.derived(
                        names.claim(
                            governance_date_name(d.category, type_term, per_category[d.category])
                        ),
                        "generated from the date category and type",
                    ),
                    category=self.judged(
                        d.category, date_field, "date category chosen by the extraction model"
                    ),
                    label=self.judged(
                        safe_name(d.label, d.category),
                        date_field,
                        "label written by the extraction model",
                    ),
                    description=ExtractedField(),
                    type=type_field,
                    date=date_field,
                    geographic_scopes=self.judged(
                        d.geographic_scope or "Global",
                        date_field,
                        "geographic scope chosen by the extraction model",
                    ),
                )
            )

        label_source = acronym if acronym.value else brief
        record = StudyRecord(
            name=self.derived(safe_name(study.name, "STUDY"), "the study name entered in this app"),
            label=ExtractedField(value=label_source.value, provenance=label_source.provenance),
            description=ExtractedField(),
            study_version=ExtractedField(value=version.value, provenance=version.provenance),
            acronym=acronym,
            rationale=self.extracted(output.rationale, context),
            brief_title=brief,
            official_title=self.extracted(output.official_title, context),
            public_title=self.extracted(output.public_title, context),
            scientific_title=self.extracted(output.scientific_title, context),
            protocol_version=version,
            protocol_status=self.extracted(
                output.protocol_status, context, resolver, STUDY_PROTOCOL_STATUS
            ),
            sponsor_protocol_identifier=self.extracted(output.sponsor_protocol_identifier, context),
            governance_dates=dates,
        )
        if record.official_title.value is None:
            warnings.append("no official title found")
        return record, warnings
