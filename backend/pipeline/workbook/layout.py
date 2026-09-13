"""Workbook sheet layouts: which intermediate-model field sits in which workbook cell.

Column order and headers follow the usdm4-excel legacy single-workbook dialect
(docs/usdm_workbook_spec.md), the dialect of the CDISC Pilot gold workbook: governance dates are a
table on the `study` sheet below the key/value block, and eligibility is the one-sheet layout.
Required flags follow what the usdm4 model requires, so the review never blocks on a value the
workbook import does not need.
The review page renders sheets exactly this way, and the Stage B writers will write them this way,
so what a reviewer confirms is what lands in the workbook.

Rows or columns the implemented agents do not extract yet (notes, dictionaries, therapeutic areas,
study design files) are listed with `field=None` so letters and row numbers match the real
workbook; they are shown read-only until the phase that fills them.
"""

from dataclasses import dataclass
from enum import StrEnum

from backend.pipeline.terminology.ct import (
    ARM_DATA_ORIGIN_TYPE,
    ARM_TYPE,
    ELIGIBILITY_CATEGORY,
    GOVERNANCE_DATE_TYPE,
    STUDY_PROTOCOL_STATUS,
    CtField,
)


class SheetKind(StrEnum):
    KEY_VALUE = "key_value"  # column A holds the key, column B the value, one row per key
    TABLE = "table"  # headers on the row above `first_row`, one record per row from `first_row`


@dataclass(frozen=True)
class ColumnSpec:
    header: str  # workbook header (table) or key (key/value)
    field: str | None  # attribute of the record; None = not extracted in this phase
    required: bool = False
    ct: CtField | None = None
    multiline: bool = False


@dataclass(frozen=True)
class SheetSpec:
    key: str  # review sheet key
    workbook_sheet: str
    kind: SheetKind
    columns: tuple[ColumnSpec, ...]
    #: Where the records live in ExtractionSheets: an attribute name, or "study.governance_dates".
    source: str
    title: str
    #: Workbook row of the first record (tables only); the header row is the one above.
    first_row: int = 2

    def column(self, field: str) -> ColumnSpec:
        for c in self.columns:
            if c.field == field:
                return c
        raise KeyError(f"{self.key} has no field {field!r}")

    def letters(self) -> list[str]:
        """Workbook column letter per column spec (B for every key/value row)."""
        if self.kind == SheetKind.KEY_VALUE:
            return ["B"] * len(self.columns)
        return [column_letter(i) for i in range(len(self.columns))]

    def letter(self, field: str) -> str:
        return self.letters()[[c.field for c in self.columns].index(field)]

    def row_number(self, row_index: int) -> int:
        """Workbook row of a 0-based table record."""
        return self.first_row + row_index

    def cell(self, field: str, row_index: int | None) -> str:
        """A1-style reference such as `studyDesignArms!D3` (row_index is 0-based for tables)."""
        if self.kind == SheetKind.KEY_VALUE:
            row = [c.field for c in self.columns].index(field) + 1
        else:
            row = self.row_number(row_index or 0)
        return f"{self.workbook_sheet}!{self.letter(field)}{row}"


def column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


STUDY = SheetSpec(
    key="study",
    workbook_sheet="study",
    kind=SheetKind.KEY_VALUE,
    source="study",
    title="Study",
    columns=(
        ColumnSpec("name", "name", required=True),
        ColumnSpec("description", "description", multiline=True),
        ColumnSpec("label", "label"),
        ColumnSpec("studyVersion", "study_version", required=True),
        ColumnSpec("studyAcronym", "acronym"),
        ColumnSpec("studyRationale", "rationale", multiline=True),
        ColumnSpec("businessTherapeuticAreas", None),
        ColumnSpec("briefTitle", "brief_title", multiline=True),
        ColumnSpec("officialTitle", "official_title", multiline=True),
        ColumnSpec("publicTitle", "public_title", multiline=True),
        ColumnSpec("scientificTitle", "scientific_title", multiline=True),
        ColumnSpec("studyDesigns", None),
        ColumnSpec("notes", None),
        ColumnSpec("protocolVersion", "protocol_version"),
        ColumnSpec("protocolStatus", "protocol_status", ct=STUDY_PROTOCOL_STATUS),
    ),
)

# Legacy dates table: a blank row ends the study key/value block, the next row holds these
# headers, and dates follow. The column order is fixed by the importer, not by the headers.
DATES = SheetSpec(
    key="dates",
    workbook_sheet="study",
    kind=SheetKind.TABLE,
    source="study.governance_dates",
    title="Governance dates",
    first_row=len(STUDY.columns) + 3,
    columns=(
        ColumnSpec("category", "category", required=True),
        ColumnSpec("name", "name", required=True),
        ColumnSpec("description", "description"),
        ColumnSpec("label", "label"),
        ColumnSpec("type", "type", required=True, ct=GOVERNANCE_DATE_TYPE),
        ColumnSpec("date", "date", required=True),
        ColumnSpec("scopes", "geographic_scopes"),
    ),
)

ARMS = SheetSpec(
    key="study_design_arms",
    workbook_sheet="studyDesignArms",
    kind=SheetKind.TABLE,
    source="study_design_arms",
    title="Arms",
    columns=(
        ColumnSpec("name", "name", required=True),
        ColumnSpec("description", "description", required=True, multiline=True),
        ColumnSpec("label", "label"),
        ColumnSpec("type", "type", required=True, ct=ARM_TYPE),
        ColumnSpec("dataOriginDescription", "data_origin_description", required=True),
        ColumnSpec("dataOriginType", "data_origin_type", required=True, ct=ARM_DATA_ORIGIN_TYPE),
        ColumnSpec("notes", None),
    ),
)

# The legacy one-sheet eligibility layout: one row per criterion, matching the intermediate
# record 1:1. The writer can also emit the preferred split eligibilityCriteria + items sheets.
ELIGIBILITY = SheetSpec(
    key="eligibility_criteria",
    workbook_sheet="studyDesignEligibilityCriteria",
    kind=SheetKind.TABLE,
    source="eligibility_criteria",
    title="Eligibility criteria",
    columns=(
        ColumnSpec("category", "category", required=True, ct=ELIGIBILITY_CATEGORY),
        ColumnSpec("identifier", "identifier", required=True),
        ColumnSpec("name", "name", required=True),
        ColumnSpec("description", "description"),
        ColumnSpec("label", "label"),
        ColumnSpec("text", "text", required=True, multiline=True),
        ColumnSpec("dictionary", None),
    ),
)

SHEETS: dict[str, SheetSpec] = {s.key: s for s in (STUDY, DATES, ARMS, ELIGIBILITY)}
