"""Workbook sheet layouts: which intermediate-model field sits in which workbook cell.

Column order and headers follow the usdm4-excel legacy single-workbook dialect
(docs/usdm_workbook_spec.md), the dialect of the CDISC Pilot gold workbook: governance dates are a
table on the `study` sheet below the key/value block, and eligibility is the one-sheet layout.
Required flags follow what the usdm4 model and importer need, so the review never blocks on a value
the workbook import does not need.
The review page renders sheets exactly this way, and the Stage B writers will write them this way,
so what a reviewer confirms is what lands in the workbook.

Rows or columns the implemented agents do not extract yet (notes, dictionaries, therapeutic areas,
timelines) are listed with `field=None` so letters and row numbers match the real workbook; they
are shown read-only until the phase that fills them.

Some sheets hold two levels in one table (an objective and its endpoints, an intervention and its
administrations, an estimand and its intercurrent events). The upper level's columns form a
`group` filled only on its first row; following rows leave them empty and belong to the entry
above, which is how the importer reads them. A group's required columns are required only on rows
where the group has a value, and the sheet's `leading_group` must start on the first row.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from backend.pipeline.terminology.ct import (
    AMENDMENT_REASON,
    ARM_DATA_ORIGIN_TYPE,
    ARM_TYPE,
    BLINDING_SCHEMA,
    CONTACT_MODES,
    DESIGN_CHARACTERISTICS,
    ELIGIBILITY_CATEGORY,
    ENCOUNTER_SETTINGS,
    ENCOUNTER_TYPE,
    ENDPOINT_LEVEL,
    EPOCH_TYPE,
    FREQUENCY,
    GOVERNANCE_DATE_TYPE,
    INTERVENTION_MODEL,
    INTERVENTION_ROLE,
    INTERVENTION_TYPE,
    OBJECTIVE_LEVEL,
    ORGANIZATION_TYPE,
    PLANNED_SEX,
    ROUTE,
    STUDY_PHASE,
    STUDY_PROTOCOL_STATUS,
    STUDY_TYPE,
    TIMING_TYPE,
    TRIAL_INTENT_TYPES,
    TRIAL_SUB_TYPES,
    CtField,
)
from backend.pipeline.workbook.formats import ValueFormat


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
    #: Comma-separated list of terms (multi-valued terminology cell).
    multi: bool = False
    #: A terminology cell that also accepts "Other=<free text>".
    other_allowed: bool = False
    format: ValueFormat | None = None
    #: Allowed values (case-insensitive) for small fixed vocabularies that are not CDISC CT.
    choices: tuple[str, ...] = ()
    #: Two-level sheets: the group this column belongs to (see module docstring).
    group: str | None = None
    #: The kind of entity this column names (its value is referenced by name from other sheets).
    entity: str | None = None
    #: The entity kinds this column refers to by name (comma-separated names when `multi`).
    ref: tuple[str, ...] = ()
    #: Fixed values a reference column also accepts, e.g. "(Exit)".
    ref_literals: tuple[str, ...] = ()
    #: Biomedical Concept names (comma-separated), resolved against the bundled BC catalogue.
    bc: bool = False
    #: The importer reads the cell as XHTML: plain text is escaped and wrapped in <p>.
    xhtml: bool = False


@dataclass(frozen=True)
class SheetSpec:
    key: str  # review sheet key
    workbook_sheet: str
    kind: SheetKind
    columns: tuple[ColumnSpec, ...]
    #: Where the records live in ExtractionSheets: a dotted attribute path, e.g. "study" or
    #: "identifiers.organizations".
    source: str
    title: str
    #: Workbook row of the first record (tables only); the header row is the one above.
    first_row: int = 2
    #: Two-level sheets: the group that must start on the first row.
    leading_group: str | None = None
    #: Prefix of review row ids for this sheet.
    row_prefix: str = "row"
    groups: tuple[str, ...] = field(default=(), init=False)

    def __post_init__(self) -> None:
        seen: list[str] = []
        for c in self.columns:
            if c.group and c.group not in seen:
                seen.append(c.group)
        object.__setattr__(self, "groups", tuple(seen))

    def column(self, field: str) -> ColumnSpec:
        for c in self.columns:
            if c.field == field:
                return c
        raise KeyError(f"{self.key} has no field {field!r}")

    def group_columns(self, group: str) -> list[ColumnSpec]:
        return [c for c in self.columns if c.group == group and c.field is not None]

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


# Entity kinds, named after the USDM class (or class family) whose names they are.
STUDY_ENTITY = "Study"
DATE_ENTITY = "GovernanceDate"
ARM_ENTITY = "StudyArm"
CRITERION_ENTITY = "EligibilityCriterion"
ORGANIZATION_ENTITY = "Organization"
DESIGN_ENTITY = "StudyDesign"
POPULATION_ENTITY = "Population"  # StudyDesignPopulation or StudyCohort
OBJECTIVE_ENTITY = "Objective"
ENDPOINT_ENTITY = "Endpoint"
INTERVENTION_ENTITY = "StudyIntervention"
ADMINISTRATION_ENTITY = "Administration"
INDICATION_ENTITY = "Indication"
ESTIMAND_ENTITY = "Estimand"
EVENT_ENTITY = "IntercurrentEvent"
AMENDMENT_ENTITY = "StudyAmendment"
EPOCH_ENTITY = "StudyEpoch"
ENCOUNTER_ENTITY = "Encounter"
TIMING_ENTITY = "Timing"
TIMELINE_ENTITY = "ScheduleTimeline"
TIMEPOINT_ENTITY = "ScheduledInstance"
ACTIVITY_ENTITY = "Activity"
ELEMENT_ENTITY = "StudyElement"
EXIT = "(Exit)"


STUDY = SheetSpec(
    key="study",
    workbook_sheet="study",
    kind=SheetKind.KEY_VALUE,
    source="study",
    title="Study",
    columns=(
        ColumnSpec("name", "name", required=True, entity=STUDY_ENTITY),
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
        # Optional in USDM, but the importer rejects an empty protocol status.
        ColumnSpec("protocolStatus", "protocol_status", required=True, ct=STUDY_PROTOCOL_STATUS),
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
    row_prefix="date",
    columns=(
        ColumnSpec(
            "category",
            "category",
            required=True,
            choices=("study_version", "protocol_document", "amendment"),
        ),
        ColumnSpec("name", "name", required=True, entity=DATE_ENTITY),
        ColumnSpec("description", "description"),
        ColumnSpec("label", "label"),
        ColumnSpec("type", "type", required=True, ct=GOVERNANCE_DATE_TYPE),
        ColumnSpec("date", "date", required=True, format=ValueFormat.DATE),
        ColumnSpec("scopes", "geographic_scopes", format=ValueFormat.GEOGRAPHIC_SCOPE),
    ),
)

ORGANIZATIONS = SheetSpec(
    key="organizations",
    workbook_sheet="studyOrganizations",
    kind=SheetKind.TABLE,
    source="identifiers.organizations",
    title="Organizations",
    row_prefix="org",
    columns=(
        ColumnSpec("identifierScheme", "identifier_scheme", required=True),
        ColumnSpec("identifier", "identifier", required=True),
        ColumnSpec("name", "name", required=True, entity=ORGANIZATION_ENTITY),
        ColumnSpec("label", "label"),
        ColumnSpec("type", "type", required=True, ct=ORGANIZATION_TYPE),
        ColumnSpec("organisationAddress", "address", multiline=True),
    ),
)

IDENTIFIERS = SheetSpec(
    key="identifiers",
    workbook_sheet="studyIdentifiers",
    kind=SheetKind.TABLE,
    source="identifiers.identifiers",
    title="Identifiers",
    row_prefix="ident",
    columns=(
        ColumnSpec("studyIdentifier", "identifier", required=True),
        ColumnSpec("organization", "organization", required=True, ref=(ORGANIZATION_ENTITY,)),
    ),
)

STUDY_DESIGN = SheetSpec(
    key="study_design",
    workbook_sheet="studyDesign",
    kind=SheetKind.KEY_VALUE,
    source="study_design",
    title="Study design",
    columns=(
        ColumnSpec("studyDesignName", "name", required=True, entity=DESIGN_ENTITY),
        ColumnSpec("studyDesignDescription", "description", multiline=True),
        ColumnSpec("label", "label"),
        ColumnSpec("therapeuticAreas", None),
        ColumnSpec("studyDesignRationale", "rationale", required=True, multiline=True),
        ColumnSpec("studyDesignBlindingScheme", "blinding_schema", ct=BLINDING_SCHEMA),
        ColumnSpec("trialIntentTypes", "intent_types", ct=TRIAL_INTENT_TYPES, multi=True),
        ColumnSpec("trialSubTypes", "sub_types", ct=TRIAL_SUB_TYPES, multi=True),
        ColumnSpec("interventionModel", "intervention_model", required=True, ct=INTERVENTION_MODEL),
        ColumnSpec("characteristics", "characteristics", ct=DESIGN_CHARACTERISTICS, multi=True),
        ColumnSpec("mainTimeline", None),
        ColumnSpec("otherTimelines", None),
        # Optional in USDM, but the importer rejects empty study type and phase cells.
        ColumnSpec("studyType", "study_type", required=True, ct=STUDY_TYPE),
        ColumnSpec("studyPhase", "study_phase", required=True, ct=STUDY_PHASE),
    ),
)

ARMS = SheetSpec(
    key="study_design_arms",
    workbook_sheet="studyDesignArms",
    kind=SheetKind.TABLE,
    source="study_design_arms",
    title="Arms",
    row_prefix="arm",
    columns=(
        ColumnSpec("name", "name", required=True, entity=ARM_ENTITY),
        ColumnSpec("description", "description", required=True, multiline=True),
        ColumnSpec("label", "label"),
        ColumnSpec("type", "type", required=True, ct=ARM_TYPE),
        ColumnSpec("dataOriginDescription", "data_origin_description", required=True),
        ColumnSpec("dataOriginType", "data_origin_type", required=True, ct=ARM_DATA_ORIGIN_TYPE),
        ColumnSpec("notes", None),
    ),
)

POPULATIONS = SheetSpec(
    key="populations",
    workbook_sheet="studyDesignPopulations",
    kind=SheetKind.TABLE,
    source="populations",
    title="Populations",
    row_prefix="pop",
    columns=(
        ColumnSpec("level", "level", required=True, choices=("Main", "Cohort")),
        ColumnSpec("name", "name", required=True, entity=POPULATION_ENTITY),
        ColumnSpec("description", "description", multiline=True),
        ColumnSpec("label", "label"),
        ColumnSpec(
            "plannedCompletionNumber", "planned_completion_number", format=ValueFormat.COUNT
        ),
        ColumnSpec(
            "plannedEnrollmentNumber", "planned_enrollment_number", format=ValueFormat.COUNT
        ),
        ColumnSpec("plannedAge", "planned_age", format=ValueFormat.RANGE),
        ColumnSpec("plannedSexOfParticipants", "planned_sex", ct=PLANNED_SEX, multi=True),
        ColumnSpec(
            "includesHealthySubjects",
            "includes_healthy_subjects",
            required=True,
            format=ValueFormat.BOOLEAN,
        ),
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
    row_prefix="crit",
    columns=(
        ColumnSpec("category", "category", required=True, ct=ELIGIBILITY_CATEGORY),
        ColumnSpec("identifier", "identifier", required=True),
        ColumnSpec("name", "name", required=True, entity=CRITERION_ENTITY),
        ColumnSpec("description", "description"),
        ColumnSpec("label", "label"),
        ColumnSpec("text", "text", required=True, multiline=True, xhtml=True),
        ColumnSpec("dictionary", None),
    ),
)

_OBJ, _END = "objective", "endpoint"
OBJECTIVES_ENDPOINTS = SheetSpec(
    key="objectives_endpoints",
    workbook_sheet="studyDesignOE",
    kind=SheetKind.TABLE,
    source="objectives_endpoints",
    title="Objectives & endpoints",
    row_prefix="oe",
    leading_group=_OBJ,
    columns=(
        ColumnSpec(
            "objectiveName", "objective_name", required=True, group=_OBJ, entity=OBJECTIVE_ENTITY
        ),
        ColumnSpec("objectiveDescription", "objective_description", group=_OBJ),
        ColumnSpec("objectiveLabel", "objective_label", group=_OBJ),
        ColumnSpec("objectiveText", "objective_text", required=True, group=_OBJ, multiline=True),
        ColumnSpec(
            "objectiveLevel", "objective_level", required=True, group=_OBJ, ct=OBJECTIVE_LEVEL
        ),
        ColumnSpec(
            "endpointName", "endpoint_name", required=True, group=_END, entity=ENDPOINT_ENTITY
        ),
        ColumnSpec("endpointDescription", "endpoint_description", group=_END, multiline=True),
        ColumnSpec("endpointLabel", "endpoint_label", group=_END),
        ColumnSpec("endpointText", "endpoint_text", required=True, group=_END, multiline=True),
        ColumnSpec("endpointPurpose", "endpoint_purpose", group=_END),
        ColumnSpec("endpointLevel", "endpoint_level", required=True, group=_END, ct=ENDPOINT_LEVEL),
    ),
)

_EST, _ICE = "estimand", "intercurrent event"
ESTIMANDS = SheetSpec(
    key="estimands",
    workbook_sheet="studyDesignEstimands",
    kind=SheetKind.TABLE,
    source="estimands",
    title="Estimands",
    row_prefix="est",
    leading_group=_EST,
    columns=(
        ColumnSpec("xref", "name", required=True, group=_EST, entity=ESTIMAND_ENTITY),
        ColumnSpec("summaryMeasure", "summary_measure", required=True, group=_EST, multiline=True),
        ColumnSpec(
            "populationDescription",
            "population_description",
            required=True,
            group=_EST,
            multiline=True,
        ),
        ColumnSpec(
            "populationSubset", "population", required=True, group=_EST, ref=(POPULATION_ENTITY,)
        ),
        # Every row is one intercurrent event of the estimand above (the importer requires one).
        ColumnSpec("intercurrentEventName", "event_name", required=True, entity=EVENT_ENTITY),
        ColumnSpec("intercurrentEventDescription", "event_description", multiline=True),
        ColumnSpec(
            "treatmentXref", "treatment", required=True, group=_EST, ref=(INTERVENTION_ENTITY,)
        ),
        ColumnSpec("endpointXref", "endpoint", required=True, group=_EST, ref=(ENDPOINT_ENTITY,)),
        ColumnSpec("intercurrentEventStrategy", "event_strategy", required=True, multiline=True),
        ColumnSpec("intercurrentEventText", "event_text", required=True, multiline=True),
    ),
)

_INT, _ADM = "intervention", "administration"
INTERVENTIONS = SheetSpec(
    key="interventions",
    workbook_sheet="studyInterventions",  # preferred name; studyDesignInterventions is deprecated
    kind=SheetKind.TABLE,
    source="interventions",
    title="Interventions",
    row_prefix="int",
    leading_group=_INT,
    columns=(
        ColumnSpec("name", "name", required=True, group=_INT, entity=INTERVENTION_ENTITY),
        ColumnSpec("description", "description", group=_INT, multiline=True),
        ColumnSpec("label", "label", group=_INT),
        ColumnSpec("codes", None),
        ColumnSpec("role", "role", required=True, group=_INT, ct=INTERVENTION_ROLE),
        ColumnSpec("type", "type", required=True, group=_INT, ct=INTERVENTION_TYPE),
        ColumnSpec(
            "minimumResponseDuration",
            "minimum_response_duration",
            group=_INT,
            format=ValueFormat.QUANTITY,
        ),
        ColumnSpec(
            "administrationName",
            "administration_name",
            required=True,
            group=_ADM,
            entity=ADMINISTRATION_ENTITY,
        ),
        ColumnSpec(
            "administrationDescription", "administration_description", group=_ADM, multiline=True
        ),
        ColumnSpec("administrationLabel", "administration_label", group=_ADM),
        ColumnSpec("administrationRoute", "administration_route", group=_ADM, ct=ROUTE),
        ColumnSpec(
            "administrationDose", "administration_dose", group=_ADM, format=ValueFormat.QUANTITY
        ),
        ColumnSpec("administrationFrequency", "administration_frequency", group=_ADM, ct=FREQUENCY),
        ColumnSpec(
            "administrationDurationDescription", "duration_description", group=_ADM, multiline=True
        ),
        ColumnSpec(
            "administrationDurationWillVary",
            "duration_will_vary",
            required=True,
            group=_ADM,
            format=ValueFormat.BOOLEAN,
        ),
        ColumnSpec(
            "administrationDurationWillVaryReason",
            "duration_will_vary_reason",
            group=_ADM,
            multiline=True,
        ),
        ColumnSpec(
            "administrationDurationQuantity",
            "duration_quantity",
            group=_ADM,
            format=ValueFormat.QUANTITY,
        ),
    ),
)

INDICATIONS = SheetSpec(
    key="indications",
    workbook_sheet="studyDesignIndications",
    kind=SheetKind.TABLE,
    source="indications",
    title="Indications",
    row_prefix="ind",
    columns=(
        ColumnSpec("name", "name", required=True, entity=INDICATION_ENTITY),
        ColumnSpec("description", "description", multiline=True),
        ColumnSpec("label", "label"),
        ColumnSpec("codes", None),
        ColumnSpec("isRareDisease", "is_rare_disease", format=ValueFormat.BOOLEAN),
    ),
)

AMENDMENTS = SheetSpec(
    key="amendments",
    workbook_sheet="studyAmendments",
    kind=SheetKind.TABLE,
    source="amendments",
    title="Amendments",
    row_prefix="amend",
    columns=(
        ColumnSpec("name", "name", required=True, entity=AMENDMENT_ENTITY),
        ColumnSpec("description", "description", multiline=True),
        ColumnSpec("label", "label"),
        ColumnSpec("number", "number", required=True),
        ColumnSpec("summary", "summary", required=True, multiline=True),
        ColumnSpec(
            "primaryReason",
            "primary_reason",
            required=True,
            ct=AMENDMENT_REASON,
            other_allowed=True,
        ),
        ColumnSpec(
            "secondaryReasons",
            "secondary_reasons",
            ct=AMENDMENT_REASON,
            multi=True,
            other_allowed=True,
        ),
        ColumnSpec("geographicScope", "geographic_scope", format=ValueFormat.GEOGRAPHIC_SCOPE),
        # Optional in USDM, but the workbook importer rejects an empty enrollment cell.
        ColumnSpec("enrollment", "enrollment", required=True, format=ValueFormat.ENROLLMENT),
        ColumnSpec("date", "date", ref=(DATE_ENTITY,)),
        ColumnSpec("template", None),
    ),
)

ABBREVIATIONS = SheetSpec(
    key="abbreviations",
    workbook_sheet="abbreviations",
    kind=SheetKind.TABLE,
    source="abbreviations",
    title="Abbreviations",
    row_prefix="abbr",
    columns=(
        ColumnSpec("abbreviatedText", "abbreviated_text", required=True),
        ColumnSpec("expandedText", "expanded_text", required=True, multiline=True),
    ),
)

EPOCHS = SheetSpec(
    key="epochs",
    workbook_sheet="studyDesignEpochs",
    kind=SheetKind.TABLE,
    source="schedule.epochs",
    title="Epochs",
    row_prefix="epoch",
    columns=(
        ColumnSpec("name", "name", required=True, entity=EPOCH_ENTITY),
        ColumnSpec("description", "description", multiline=True),
        ColumnSpec("label", "label"),
        ColumnSpec("type", "type", required=True, ct=EPOCH_TYPE),
    ),
)

ENCOUNTERS = SheetSpec(
    key="encounters",
    workbook_sheet="studyDesignEncounters",
    kind=SheetKind.TABLE,
    source="schedule.encounters",
    title="Encounters",
    row_prefix="enc",
    columns=(
        ColumnSpec("name", "name", required=True, entity=ENCOUNTER_ENTITY),
        ColumnSpec("description", "description", multiline=True),
        ColumnSpec("label", "label"),
        ColumnSpec("type", "type", required=True, ct=ENCOUNTER_TYPE),
        # Optional in USDM, but the workbook importer rejects empty cells.
        ColumnSpec(
            "environmentalSetting",
            "environmental_settings",
            required=True,
            ct=ENCOUNTER_SETTINGS,
            multi=True,
        ),
        ColumnSpec("contactModes", "contact_modes", required=True, ct=CONTACT_MODES, multi=True),
        ColumnSpec("transitionStartRule", "transition_start_rule", multiline=True),
        ColumnSpec("transitionEndRule", "transition_end_rule", multiline=True),
        ColumnSpec("window", "window", ref=(TIMING_ENTITY,)),
    ),
)

TIMINGS = SheetSpec(
    key="timings",
    workbook_sheet="studyDesignTiming",
    kind=SheetKind.TABLE,
    source="schedule.timings",
    title="Timings",
    row_prefix="tim",
    columns=(
        ColumnSpec("name", "name", required=True, entity=TIMING_ENTITY),
        ColumnSpec("description", "description", multiline=True),
        ColumnSpec("label", "label"),
        ColumnSpec("type", "type", required=True, ct=TIMING_TYPE),
        ColumnSpec("from", "relative_from", required=True, ref=(TIMEPOINT_ENTITY,)),
        ColumnSpec("to", "relative_to", required=True, ref=(TIMEPOINT_ENTITY,)),
        ColumnSpec("timingValue", "value", required=True, format=ValueFormat.DURATION),
        ColumnSpec("toFrom", "relative_to_from", choices=("S2S", "S2E", "E2S", "E2E")),
        ColumnSpec("window", "window", format=ValueFormat.WINDOW),
    ),
)

# The timeline sheets are matrices (timepoints across, activities down). Review shows them as three
# tables that map onto them one to one; the workbook writer lays them out as the importer expects.
TIMELINES = SheetSpec(
    key="timelines",
    workbook_sheet="timelines",
    kind=SheetKind.TABLE,
    source="schedule.timelines",
    title="Timelines",
    row_prefix="tl",
    columns=(
        ColumnSpec("name", "name", required=True, entity=TIMELINE_ENTITY),
        ColumnSpec("sheet", "sheet_name", required=True),
        ColumnSpec("mainTimeline", "main", required=True, format=ValueFormat.BOOLEAN),
        ColumnSpec("description", "description", multiline=True),
        ColumnSpec("label", "label"),
        ColumnSpec("condition", "entry_condition", required=True, multiline=True),
    ),
)

TIMEPOINTS = SheetSpec(
    key="timepoints",
    workbook_sheet="timeline columns",
    kind=SheetKind.TABLE,
    source="schedule.timepoints",
    title="Timepoints",
    row_prefix="tp",
    columns=(
        ColumnSpec("timeline", "timeline", required=True, ref=(TIMELINE_ENTITY,)),
        ColumnSpec("name", "name", required=True, entity=TIMEPOINT_ENTITY),
        ColumnSpec("description", "description"),
        ColumnSpec("label", "label"),
        ColumnSpec("type", "type", required=True, choices=("Activity", "Decision")),
        ColumnSpec("default", "default", ref=(TIMEPOINT_ENTITY,), ref_literals=(EXIT,)),
        ColumnSpec("condition", "condition"),
        ColumnSpec("epoch", "epoch", ref=(EPOCH_ENTITY,)),
        ColumnSpec("encounter", "encounter", ref=(ENCOUNTER_ENTITY,)),
    ),
)

ACTIVITIES = SheetSpec(
    key="activities",
    workbook_sheet="studyDesignActivities",
    kind=SheetKind.TABLE,
    source="schedule.activities",
    title="Activities",
    row_prefix="act",
    columns=(
        ColumnSpec("name", "name", required=True, entity=ACTIVITY_ENTITY),
        ColumnSpec("description", "description", multiline=True),
        ColumnSpec("label", "label"),
    ),
)

SCHEDULE = SheetSpec(
    key="schedule",
    workbook_sheet="timeline rows",
    kind=SheetKind.TABLE,
    source="schedule.rows",
    title="Schedule",
    row_prefix="sched",
    columns=(
        ColumnSpec("timeline", "timeline", required=True, ref=(TIMELINE_ENTITY,)),
        ColumnSpec("activity", "activity", required=True, ref=(ACTIVITY_ENTITY,)),
        ColumnSpec("biomedicalConcepts", "biomedical_concepts", bc=True, multi=True),
        ColumnSpec(
            "scheduledAt",
            "scheduled_at",
            required=True,
            ref=(TIMEPOINT_ENTITY,),
            multi=True,
            multiline=True,
        ),
    ),
)

ELEMENTS = SheetSpec(
    key="elements",
    workbook_sheet="studyDesignElements",
    kind=SheetKind.TABLE,
    source="design.elements",
    title="Elements",
    row_prefix="el",
    columns=(
        ColumnSpec("name", "name", required=True, entity=ELEMENT_ENTITY),
        ColumnSpec("description", "description", multiline=True),
        ColumnSpec("label", "label"),
        ColumnSpec("transitionStartRule", "transition_start_rule", multiline=True),
        ColumnSpec("transitionEndRule", "transition_end_rule", multiline=True),
    ),
)

STUDY_CELLS = SheetSpec(
    key="study_cells",
    workbook_sheet="studyDesign grid",
    kind=SheetKind.TABLE,
    source="design.cells",
    title="Arm-epoch cells",
    row_prefix="cell",
    columns=(
        ColumnSpec("arm", "arm", required=True, ref=(ARM_ENTITY,)),
        ColumnSpec("epoch", "epoch", required=True, ref=(EPOCH_ENTITY,)),
        ColumnSpec("elements", "elements", required=True, ref=(ELEMENT_ENTITY,), multi=True),
    ),
)

SHEETS: dict[str, SheetSpec] = {
    s.key: s
    for s in (
        STUDY,
        DATES,
        ORGANIZATIONS,
        IDENTIFIERS,
        STUDY_DESIGN,
        ARMS,
        EPOCHS,
        ELEMENTS,
        STUDY_CELLS,
        POPULATIONS,
        ELIGIBILITY,
        OBJECTIVES_ENDPOINTS,
        ESTIMANDS,
        INTERVENTIONS,
        INDICATIONS,
        AMENDMENTS,
        ENCOUNTERS,
        TIMELINES,
        TIMEPOINTS,
        TIMINGS,
        ACTIVITIES,
        SCHEDULE,
        ABBREVIATIONS,
    )
}
