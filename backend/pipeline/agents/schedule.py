"""Schedule of activities: epochs, encounters, timelines, timepoints, timings, activities and marks.

Schedule tables are the hardest part of a protocol to parse: merged header cells, spanning arrows,
footnote letters, landscape pages. The model therefore sees the page images as well as the parsed
table text, and reports what it reads in protocol terms: epochs, visit columns with their planned
time and window, activity rows with the columns they are marked in.

Everything USDM needs beyond that is built here, deterministically:
- names (unique per kind), timeline sheet names (`main-timeline`, `timeline-2`, ...);
- one encounter per main-timeline visit, one timepoint per column of every timeline;
- each timepoint's default next timepoint (the next column; `(Exit)` after the last);
- timings: the anchor visit is a Fixed Reference, every other visit with a stated planned time is
  Before/After the anchor by that amount, with its window.
Values the protocol does not state (contact modes, settings) are not invented; the entry condition
USDM requires gets a generic, visibly generated statement.
"""

from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from backend.models.extraction import (
    ActivityRecord,
    EncounterRecord,
    EpochRecord,
    ExtractedField,
    ScheduleRowRecord,
    ScheduleSheet,
    TimelineRecord,
    TimepointRecord,
    TimingRecord,
)
from backend.models.study import StudyMeta
from backend.pipeline.agents.base import SheetAgent
from backend.pipeline.agents.common import Cited, terms_hint
from backend.pipeline.agents.context import AgentContext
from backend.pipeline.identifiers.names import NameRegistry, safe_name
from backend.pipeline.terminology.ct import EPOCH_TYPE, CtResolver
from backend.pipeline.workbook import formats
from backend.pipeline.workbook.cells import resolve_cell
from backend.pipeline.workbook.layout import ENCOUNTERS, EPOCHS, EXIT, TIMINGS

MAX_PAGE_IMAGES = 12
DEFAULT_SETTING = "Clinic"
DEFAULT_CONTACT_MODE = "In Person"
PAGE_IMAGE_DIR = "page_images"


class EpochOut(BaseModel):
    key: str = Field(description="A short key you invent for this epoch.")
    label: Cited = Field(description="The epoch or study period name as printed, e.g. 'Screening'.")
    type: Cited = Field(description="The kind of epoch, as a controlled-terminology phrase.")


class OffsetOut(BaseModel):
    value: Cited = Field(
        description="The planned time from the timeline's anchor visit, digits only. Quote the "
        "column heading or row that states it (e.g. 'Day 15')."
    )
    unit: str = Field(description="days, weeks, hours or minutes.")
    direction: Literal["before", "after"]


class WindowOut(BaseModel):
    before: str | None = Field(description="How early the visit may be, digits only.")
    after: str | None = Field(description="How late the visit may be, digits only.")
    unit: str = Field(description="days, weeks, hours or minutes.")
    quote: Cited = Field(description="value: the window as printed, e.g. '±2 days'.")


class VisitOut(BaseModel):
    key: str = Field(description="A short key you invent for this column.")
    label: Cited = Field(
        description="value: the column's full heading, joining spanning headings from top to "
        "bottom, e.g. 'Period 1 Week 2'. quote: the lowest heading cell as printed."
    )
    epoch_key: str | None = Field(description="The key of the epoch this column belongs to.")
    anchor: bool = Field(
        description="true for the single visit all planned times in this timeline count from "
        "(usually Day 1, randomisation or first dose)."
    )
    offset: OffsetOut | None = Field(
        description="The planned time relative to the anchor. Null for the anchor itself and "
        "for visits without a fixed planned time (screening 'within 14 days before', end of treatment, "
        "repeating cycles)."
    )
    window: WindowOut | None = Field(description="The allowed window, only if printed.")
    main_visit_key: str | None = Field(
        description="Only for columns of a timeline other than the main one: the key of the main "
        "schedule visit this timepoint happens at, if it is one of them."
    )


class ActivityOut(BaseModel):
    label: Cited = Field(
        description="The activity row name as printed, without footnote letters. quote: the row "
        "name as printed."
    )
    group: str | None = Field(
        description="The heading row the activity sits under (e.g. 'Safety laboratory'), if any."
    )
    visit_keys: list[str] = Field(
        description="Keys of every column in which the activity is marked (X, a tick, or an "
        "arrow or text spanning that column that says it is done there)."
    )
    marks_confidence: float = Field(
        description="0 to 1: how sure you are the marks were read correctly from the table."
    )


class TimelineOut(BaseModel):
    key: str
    label: str = Field(description="The timeline's name, e.g. the table title.")
    main: bool = Field(description="true for the main schedule of activities (exactly one).")
    description: str | None = Field(description="What this schedule covers, in a few words.")
    entry_condition: Cited = Field(
        description="What makes a participant start this timeline, only if the protocol states it."
    )
    visits: list[VisitOut] = Field(description="The columns, left to right.")
    activities: list[ActivityOut] = Field(description="The activity rows, top to bottom.")


class ScheduleOut(BaseModel):
    epochs: list[EpochOut]
    timelines: list[TimelineOut]


class ScheduleAgent(SheetAgent):
    sheet = "schedule"
    workbook_sheets = (
        "studyDesignEpochs",
        "studyDesignEncounters",
        "studyDesignTiming",
        "studyDesignActivities",
        "main-timeline",
    )
    prompt_version = "2"
    # 2: default encounter setting and contact mode. 3: no window on the anchor timing, and
    # every timeline gets an anchor timing.
    postprocess_version = "3"
    m11_sections = ("1.3",)
    output_model = ScheduleOut
    max_tokens = 48000
    empty_when_missing = True
    empty_records: ClassVar[None] = None

    def images(self, context: AgentContext, run_dir: Path) -> list[bytes]:
        pages = sorted({p for s in context.sections for p in range(s.page_start, s.page_end + 1)})
        found: list[bytes] = []
        for page in pages[:MAX_PAGE_IMAGES]:
            path = run_dir / PAGE_IMAGE_DIR / f"page-{page:04d}.png"
            if path.is_file():
                found.append(path.read_bytes())
        return found

    def instructions(self, resolver: CtResolver) -> str:
        return f"""\
Read the schedule of activities for the USDM timeline sheets. The images are the protocol pages \
holding the schedule, in page order; the <protocol> text below has the same tables as parsed text, \
which can be garbled for merged or rotated cells. Trust the images for which cells are marked; copy \
quotes from the text.

- Tables that continue onto following pages, or repeat the same visits, are one timeline. A table \
scheduling a separate process on its own time points (for example pharmacokinetic sampling around \
a dose, or tumour assessments every N weeks) is a separate timeline. Exactly one timeline is main.
- Visits are the columns, left to right. Give each column its full heading path so every label is \
unique. Do not invent columns for rows of footnotes.
- epochs: the study periods the columns fall in (screening, treatment, follow-up, ...). type: \
{terms_hint(self.terms(resolver, EPOCH_TYPE))}.
- Planned times: pick the anchor visit (Day 1, first dose or randomisation). Day N is N-1 days after \
Day 1; Week N is N weeks after the anchor unless the protocol defines it differently. Give an offset \
only when the column states a specific time; leave it null for 'within 14 days before', 'end of \
treatment', 'every cycle' and similar.
- Offsets and windows are whole numbers: use the unit that makes them whole (3 days, not 0.4 weeks).
- Windows: '±2 days' is before 2, after 2, unit days.
- Activities are the rows. Skip heading rows that only group activities (give them as group). An \
activity marked conditionally (a footnote letter, 'if applicable') is still marked. An arrow or text \
spanning several columns marks every column it spans only if it says the activity is done there.
- Keep activity names as printed but without footnote letters or trailing symbols."""

    def example(self) -> str:
        return """\
Input (invented protocol, not the one below):
<section id="fm-soa" number="" title="Schedule of Activities" pages="8-8">
[[PAGE 8]]
[[TABLE tbl-p0008-1]]
| Procedure | Screening | Day 1 | Day 8 | Follow-up |
| Visit window |  |  | ±1 day |  |
| Informed consent | X |  |  |  |
| Vital signs | X | X | X | X |
| Laboratory tests |  |  |  |  |
| Hematology | X |  | X |  |
[[/TABLE]]
</section>

Output:
{"epochs": [{"key": "scr", "label": {"value": "Screening", "quote": "Screening", "section_id": "fm-soa", "confidence": 0.9}, "type": {"value": "Screening Epoch", "quote": "Screening", "section_id": "fm-soa", "confidence": 0.9}},
            {"key": "trt", "label": {"value": "Treatment", "quote": "Day 1", "section_id": "fm-soa", "confidence": 0.6}, "type": {"value": "Treatment Epoch", "quote": "Day 1", "section_id": "fm-soa", "confidence": 0.6}},
            {"key": "fu", "label": {"value": "Follow-up", "quote": "Follow-up", "section_id": "fm-soa", "confidence": 0.9}, "type": {"value": "Follow-Up Epoch", "quote": "Follow-up", "section_id": "fm-soa", "confidence": 0.9}}],
 "timelines": [{"key": "main", "label": "Schedule of Activities", "main": true, "description": "Main schedule of visits and procedures",
   "entry_condition": {"value": null, "quote": null, "section_id": null, "confidence": 0},
   "visits": [
    {"key": "v1", "label": {"value": "Screening", "quote": "Screening", "section_id": "fm-soa", "confidence": 0.95}, "epoch_key": "scr", "anchor": false, "offset": null, "window": null, "main_visit_key": null},
    {"key": "v2", "label": {"value": "Day 1", "quote": "Day 1", "section_id": "fm-soa", "confidence": 0.95}, "epoch_key": "trt", "anchor": true, "offset": null, "window": null, "main_visit_key": null},
    {"key": "v3", "label": {"value": "Day 8", "quote": "Day 8", "section_id": "fm-soa", "confidence": 0.95}, "epoch_key": "trt", "anchor": false,
     "offset": {"value": {"value": "7", "quote": "Day 8", "section_id": "fm-soa", "confidence": 0.9}, "unit": "days", "direction": "after"},
     "window": {"before": "1", "after": "1", "unit": "days", "quote": {"value": "±1 day", "quote": "±1 day", "section_id": "fm-soa", "confidence": 0.9}}, "main_visit_key": null},
    {"key": "v4", "label": {"value": "Follow-up", "quote": "Follow-up", "section_id": "fm-soa", "confidence": 0.95}, "epoch_key": "fu", "anchor": false, "offset": null, "window": null, "main_visit_key": null}],
   "activities": [
    {"label": {"value": "Informed consent", "quote": "Informed consent", "section_id": "fm-soa", "confidence": 0.95}, "group": null, "visit_keys": ["v1"], "marks_confidence": 0.95},
    {"label": {"value": "Vital signs", "quote": "Vital signs", "section_id": "fm-soa", "confidence": 0.95}, "group": null, "visit_keys": ["v1", "v2", "v3", "v4"], "marks_confidence": 0.95},
    {"label": {"value": "Hematology", "quote": "Hematology", "section_id": "fm-soa", "confidence": 0.95}, "group": "Laboratory tests", "visit_keys": ["v1", "v3"], "marks_confidence": 0.9}]}]}"""

    def to_records(
        self, output: ScheduleOut, context: AgentContext, resolver: CtResolver, study: StudyMeta
    ) -> tuple[ScheduleSheet, list[str]]:
        return _Builder(self, context, resolver).build(output)


class _Builder:
    def __init__(self, agent: ScheduleAgent, context: AgentContext, resolver: CtResolver) -> None:
        self.agent, self.context, self.resolver = agent, context, resolver
        self.warnings: list[str] = []
        self.sheet = ScheduleSheet()
        self.epoch_names: dict[str, str] = {}
        self.encounter_of_visit: dict[str, str] = {}  # main visit key -> encounter name
        self.names = {kind: NameRegistry() for kind in ("epoch", "enc", "tp", "act", "tim", "tl")}

    def derived(self, value: str | None, note: str) -> ExtractedField[str]:
        return self.agent.derived(value, note)

    def _default_cell(self, field: str, value: str) -> ExtractedField[str]:
        """The workbook importer rejects empty settings and contact modes, and protocols rarely
        state them per visit; a visible default is given for the reviewer to correct."""
        cell = self.derived(
            value,
            "default: an in-person clinic visit; change it for telephone, remote or home visits",
        )
        cell.terminology = resolve_cell(ENCOUNTERS.column(field), value, self.resolver)
        return cell

    def build(self, output: ScheduleOut) -> tuple[ScheduleSheet, list[str]]:
        for i, epoch in enumerate(output.epochs, start=1):
            label = self.agent.extracted(epoch.label, self.context)
            if label.value is None:
                continue
            name = self.names["epoch"].claim(safe_name(label.value, f"Epoch {i}"))
            self.epoch_names[epoch.key] = name
            self.sheet.epochs.append(
                EpochRecord(
                    name=self.derived(name, "generated from the epoch name"),
                    label=label,
                    description=ExtractedField(),
                    type=self.agent.cell(
                        epoch.type, self.context, self.resolver, EPOCHS.column("type")
                    ),
                )
            )

        mains = [t for t in output.timelines if t.main]
        if len(mains) != 1:
            self.warnings.append(f"expected one main timeline, found {len(mains)}")
        ordered = sorted(output.timelines, key=lambda t: not t.main)
        # Main visits first, so other timelines can point at their encounters.
        for number, timeline in enumerate(ordered, start=1):
            self._timeline(timeline, number, is_main=timeline is (mains[0] if mains else None))
        if not self.sheet.timelines:
            self.warnings.append("no schedule of activities found")
        return self.sheet, self.warnings

    def _timeline(self, timeline: TimelineOut, number: int, is_main: bool) -> None:
        label = timeline.label.strip() or f"Timeline {number}"
        name = self.names["tl"].claim(
            "Main Timeline" if is_main else safe_name(label, f"Timeline {number}")
        )
        entry = self.agent.extracted(timeline.entry_condition, self.context)
        if entry.is_empty:
            # USDM requires an entry condition; protocols rarely state one. A generic, visibly
            # generated statement keeps the timeline valid without inventing specifics.
            entry = self.derived(
                "Participant enters the study" if is_main else "As scheduled by the main timeline",
                "generic entry condition: the protocol states none; replace if it does",
            )
        self.sheet.timelines.append(
            TimelineRecord(
                name=self.derived(name, "generated"),
                label=self.derived(label, "the schedule's title as the model read it"),
                description=self.derived(
                    timeline.description, "summary written by the extraction model"
                )
                if timeline.description
                else ExtractedField(),
                main=self.derived(
                    "Y" if is_main else "N",
                    "the main schedule of activities" if is_main else "a separate schedule",
                ),
                entry_condition=entry,
                sheet_name=self.derived(
                    "main-timeline" if is_main else f"timeline-{number}", "deterministic sheet name"
                ),
            )
        )
        timepoint_of_visit: dict[str, str] = {}
        visits = [v for v in timeline.visits if v.label.value]
        for index, visit in enumerate(visits):
            visit_label = self.agent.extracted(visit.label, self.context)
            base = safe_name(visit_label.value or "", f"Visit {index + 1}")
            tp_name = self.names["tp"].claim(base if is_main else f"{name} {base}")
            timepoint_of_visit[visit.key] = tp_name
            encounter = ""
            if is_main:
                encounter = self.names["enc"].claim(base)
                self.encounter_of_visit[visit.key] = encounter
                self.sheet.encounters.append(
                    EncounterRecord(
                        name=self.derived(encounter, "generated from the visit heading"),
                        label=visit_label,
                        description=ExtractedField(),
                        type=ExtractedField(
                            value="Visit",
                            provenance=self.derived("Visit", "every scheduled visit").provenance,
                            terminology=resolve_cell(
                                ENCOUNTERS.column("type"), "Visit", self.resolver
                            ),
                        ),
                        environmental_settings=self._default_cell(
                            "environmental_settings", DEFAULT_SETTING
                        ),
                        contact_modes=self._default_cell("contact_modes", DEFAULT_CONTACT_MODE),
                        transition_start_rule=ExtractedField(),
                        transition_end_rule=ExtractedField(),
                        window=ExtractedField(),
                    )
                )
            elif visit.main_visit_key:
                encounter = self.encounter_of_visit.get(visit.main_visit_key, "")
            epoch = self.epoch_names.get(visit.epoch_key or "")
            if visit.epoch_key and epoch is None:
                self.warnings.append(f"{tp_name}: unknown epoch key {visit.epoch_key!r}")
            self.sheet.timepoints.append(
                TimepointRecord(
                    timeline=self.derived(name, "the timeline this column belongs to"),
                    name=self.derived(tp_name, "generated from the visit heading"),
                    label=visit_label,
                    description=ExtractedField(),
                    type=self.derived("Activity", "a column of scheduled activities"),
                    default=ExtractedField(),  # set below, once every column is named
                    condition=ExtractedField(),
                    epoch=self.agent.judged(
                        epoch, visit_label, "the epoch the model placed this column in"
                    )
                    if epoch
                    else ExtractedField(),
                    encounter=self.agent.judged(
                        encounter, visit_label, "the visit this timepoint happens at"
                    )
                    if encounter
                    else ExtractedField(),
                )
            )
        own = [tp for tp in self.sheet.timepoints if tp.timeline.value == name]
        for current, following in zip(own, [*own[1:], None], strict=True):
            current.default = self.derived(
                following.name.value if following else EXIT,
                "the next column" if following else "the timeline ends after its last column",
            )

        self._timings(name, visits, timepoint_of_visit, is_main)
        self._activities(name, timeline, timepoint_of_visit)

    def _timings(
        self,
        timeline: str,
        visits: list[VisitOut],
        timepoint_of_visit: dict[str, str],
        is_main: bool,
    ) -> None:
        if not visits:
            return
        # USDM needs exactly one anchor per timeline (rule DDF00009). Planned times are only
        # meaningful relative to an anchor the protocol names, so without one only the anchor
        # timing itself is built, on the first timepoint.
        anchors = [v for v in visits if v.anchor]
        if len(anchors) > 1:
            self.warnings.append(
                f"{timeline}: {len(anchors)} anchor visits; only the anchor timing is built, on "
                "the first, so choose the anchor and add the other timings in review"
            )
        elif not anchors:
            self.warnings.append(
                f"{timeline}: no anchor visit stated; the first timepoint is used as the "
                "timeline's reference, so check it in review"
            )
        anchor = anchors[0] if anchors else visits[0]
        anchor_tp = timepoint_of_visit[anchor.key]
        column = TIMINGS.column

        def timing(
            visit: VisitOut,
            type_term: str,
            value: str,
            basis: ExtractedField[str],
            window: ExtractedField[str],
        ) -> None:
            tp = timepoint_of_visit[visit.key]
            name = self.names["tim"].claim(f"{tp} timing")
            type_field = (
                self.agent.reformatted(type_term, basis, "timing type")
                if basis.provenance
                else self.derived(type_term, "timing type")
            )
            type_field.terminology = resolve_cell(column("type"), type_term, self.resolver)
            self.sheet.timings.append(
                TimingRecord(
                    name=self.derived(name, "generated"),
                    label=self.derived(tp, "the timepoint it schedules"),
                    description=ExtractedField(),
                    type=type_field,
                    relative_from=self.derived(tp, "the timepoint scheduled"),
                    relative_to=self.derived(anchor_tp, "the timeline's anchor visit"),
                    value=self.agent.reformatted(value, basis, "planned time")
                    if basis.provenance
                    else self.derived(value, "the anchor itself"),
                    relative_to_from=self.derived("S2S", "start to start"),
                    window=window,
                )
            )
            if is_main and visit.key in self.encounter_of_visit:
                encounter = next(
                    e
                    for e in self.sheet.encounters
                    if e.name.value == self.encounter_of_visit[visit.key]
                )
                encounter.window = self.derived(name, "the timing that schedules this visit")

        anchor_label = self.agent.extracted(anchor.label, self.context)
        if anchor.window is not None:
            # USDM does not allow a window on the anchor timing (rule DDF00025).
            self.warnings.append(
                f"{anchor_tp}: the anchor visit's window is not written; USDM allows no window "
                "on the reference timing"
            )
        timing(anchor, "Fixed Reference", "0 days", anchor_label, ExtractedField())
        if len(anchors) != 1:
            return
        for visit in visits:
            if visit is anchor:
                continue
            if visit.offset is None:
                self.warnings.append(
                    f"{timepoint_of_visit[visit.key]}: no planned time stated, so no timing"
                )
                continue
            basis = self.agent.extracted(visit.offset.value, self.context)
            amount = formats.number_text(basis.value)
            unit = visit.offset.unit.strip().lower()
            if amount is None or "." in amount or unit not in formats.TIME_UNITS:
                self.warnings.append(
                    f"{timepoint_of_visit[visit.key]}: planned time '{basis.value} {unit}' is not a "
                    "whole number of a time unit; no timing"
                )
                continue
            timing(
                visit,
                "After" if visit.offset.direction == "after" else "Before",
                f"{amount} {unit}",
                basis,
                self._window(visit),
            )

    def _window(self, visit: VisitOut) -> ExtractedField[str]:
        if visit.window is None:
            return ExtractedField()
        basis = self.agent.extracted(visit.window.quote, self.context)
        before = formats.number_text(visit.window.before) or "0"
        after = formats.number_text(visit.window.after) or "0"
        unit = visit.window.unit.strip().lower()
        if "." in before + after or not basis.provenance:
            return ExtractedField()
        return self.agent.reformatted(f"-{before}..{after} {unit}", basis, "window as lower..upper")

    def _activities(
        self, timeline: str, output: TimelineOut, timepoint_of_visit: dict[str, str]
    ) -> None:
        existing = {a.label.value: a.name.value for a in self.sheet.activities}
        for i, activity in enumerate(output.activities, start=1):
            label = self.agent.extracted(activity.label, self.context)
            if label.value is None:
                continue
            name = existing.get(label.value)
            if name is None:
                name = self.names["act"].claim(safe_name(label.value, f"Activity {i}"))
                existing[label.value] = name
                self.sheet.activities.append(
                    ActivityRecord(
                        name=self.derived(name, "generated from the activity row name"),
                        label=label,
                        description=self.agent.judged(
                            activity.group, label, "the heading row the activity sits under"
                        )
                        if activity.group
                        else ExtractedField(),
                    )
                )
            keys = [k for k in activity.visit_keys if k in timepoint_of_visit]
            unknown = [k for k in activity.visit_keys if k not in timepoint_of_visit]
            if unknown:
                self.warnings.append(f"{name}: marks in unknown columns {unknown} ignored")
            if not keys:
                self.warnings.append(
                    f"{timeline}: '{name}' is not marked in any column; row dropped"
                )
                continue
            marks = ", ".join(timepoint_of_visit[k] for k in dict.fromkeys(keys))
            p = label.provenance
            self.sheet.rows.append(
                ScheduleRowRecord(
                    timeline=self.derived(timeline, "the timeline this row belongs to"),
                    activity=self.derived(name, "the activity of this row"),
                    biomedical_concepts=ExtractedField(),
                    scheduled_at=ExtractedField(
                        value=marks,
                        provenance=p.model_copy(
                            update={
                                "confidence": round(
                                    min(p.confidence, activity.marks_confidence), 2
                                ),
                                "note": "marks read from the schedule table (page images)",
                            }
                        )
                        if p
                        else None,
                    ),
                )
            )
