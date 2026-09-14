"""`abbreviations` sheet: the protocol's list of abbreviations."""

from typing import ClassVar

from pydantic import BaseModel, Field

from backend.models.extraction import AbbreviationRecord
from backend.models.study import StudyMeta
from backend.pipeline.agents.base import SheetAgent
from backend.pipeline.agents.common import Cited
from backend.pipeline.agents.context import AgentContext


class AbbreviationOut(BaseModel):
    abbreviation: str = Field(description="The abbreviation exactly as printed, e.g. 'AE'.")
    expansion: Cited = Field(
        description="value: its expansion exactly as printed; quote: the printed row or line, "
        "abbreviation and expansion together as they appear."
    )


class AbbreviationsOut(BaseModel):
    abbreviations: list[AbbreviationOut]


class AbbreviationsAgent(SheetAgent):
    sheet = "abbreviations"
    workbook_sheets = ("abbreviations",)
    prompt_version = "1"
    m11_sections = ("13",)
    output_model = AbbreviationsOut
    max_tokens = 32000
    empty_when_missing = True
    empty_records: ClassVar[list[AbbreviationRecord]] = []

    def instructions(self, resolver: object) -> str:
        return """\
Copy the protocol's list of abbreviations for the USDM `abbreviations` sheet.

- Every entry of the list, in order, exactly as printed. Do not add abbreviations that are only \
used in the body text, and do not expand ones the list leaves unexpanded.
- In tables the quote is the text of the row's cells; keep it short."""

    def example(self) -> str:
        return """\
Input (invented protocol, not the one below):
<section id="sec-13" number="13" title="Abbreviations" pages="70-70">
[[PAGE 70]]
AE    adverse event
BID   twice daily
</section>

Output:
{"abbreviations": [
 {"abbreviation": "AE", "expansion": {"value": "adverse event", "quote": "AE    adverse event", "section_id": "sec-13", "confidence": 0.95}},
 {"abbreviation": "BID", "expansion": {"value": "twice daily", "quote": "BID   twice daily", "section_id": "sec-13", "confidence": 0.95}}]}"""

    def to_records(
        self, output: AbbreviationsOut, context: AgentContext, resolver: object, study: StudyMeta
    ) -> tuple[list[AbbreviationRecord], list[str]]:
        records: list[AbbreviationRecord] = []
        seen: set[str] = set()
        duplicates: list[str] = []
        for item in output.abbreviations:
            expansion = self.extracted(item.expansion, context)
            abbreviation = item.abbreviation.strip()
            if not abbreviation or expansion.value is None:
                continue
            if abbreviation in seen:
                duplicates.append(abbreviation)
            seen.add(abbreviation)
            records.append(
                AbbreviationRecord(
                    abbreviated_text=self.reformatted(
                        abbreviation, expansion, "printed beside the expansion"
                    ),
                    expanded_text=expansion,
                )
            )
        warnings = (
            [f"listed more than once: {', '.join(sorted(set(duplicates)))}"] if duplicates else []
        )
        return records, warnings
