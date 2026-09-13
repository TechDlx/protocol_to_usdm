"""The schedule agent's deterministic build, BC assignment, and the derived design structure."""

from datetime import UTC, datetime

import pytest

from backend.models.document import HeadingSource, Section, SectionKind
from backend.models.extraction import (
    ArmRecord,
    AssessmentRecord,
    ExtractedField,
    ExtractionSheets,
    Provenance,
    TerminologyStatus,
    ValueOrigin,
)
from backend.models.study import StudyMeta
from backend.pipeline.agents.common import Cited
from backend.pipeline.agents.context import AgentContext
from backend.pipeline.agents.schedule import (
    ActivityOut,
    EpochOut,
    OffsetOut,
    ScheduleAgent,
    ScheduleOut,
    TimelineOut,
    VisitOut,
    WindowOut,
)
from backend.pipeline.identifiers.concepts import assign_biomedical_concepts, derive_design
from backend.pipeline.identifiers.references import validate_references
from backend.pipeline.terminology.ct import CtResolver, get_ct_resolver
from backend.pipeline.workbook import formats
from backend.pipeline.workbook.formats import ValueFormat

TEXT = (
    "[[PAGE 5]]\n| Procedure | Screening | Day 1 | Day 15 | Week 8 | Follow-up |\n"
    "| Visit window | | | ±2 days | ±7 days | |\n| Vital signs | X | X | X | X | X |\n"
    "| Weight | X | | | X | |\n| PK sample | | X | X | | |\n"
)
SECTION = Section(
    id="fm-soa",
    number=None,
    title="Schedule of Activities",
    level=1,
    kind=SectionKind.FRONT_MATTER,
    parent_id=None,
    page_start=5,
    page_end=6,
    heading_bbox=None,
    heading_source=HeadingSource.TEXT,
    text=TEXT,
)
CONTEXT = AgentContext(sections=[SECTION], rendered="", used_fallback=False)
STUDY = StudyMeta(slug="s", name="S", created_at=datetime.now(UTC), updated_at=datetime.now(UTC))


def c(value: str | None, quote: str | None = None, confidence: float = 0.9) -> Cited:
    return Cited(value=value, quote=quote or value, section_id="fm-soa", confidence=confidence)


def visit(key: str, label: str, epoch: str, **extra: object) -> VisitOut:
    fields: dict[str, object] = {
        "anchor": False,
        "offset": None,
        "window": None,
        "main_visit_key": None,
        **extra,
    }
    return VisitOut(key=key, label=c(label), epoch_key=epoch, **fields)  # type: ignore[arg-type]


def output() -> ScheduleOut:
    main = TimelineOut(
        key="main",
        label="Schedule of Activities",
        main=True,
        description=None,
        entry_condition=Cited(value=None, quote=None, section_id=None, confidence=0),
        visits=[
            visit("scr", "Screening", "e1"),
            visit("d1", "Day 1", "e2", anchor=True),
            visit(
                "d15",
                "Day 15",
                "e2",
                offset=OffsetOut(value=c("14", "Day 15"), unit="days", direction="after"),
                window=WindowOut(before="2", after="2", unit="days", quote=c("±2 days")),
            ),
            visit(
                "w8",
                "Week 8",
                "e2",
                offset=OffsetOut(value=c("8", "Week 8"), unit="weeks", direction="after"),
                window=WindowOut(before="7", after="7", unit="days", quote=c("±7 days")),
            ),
            visit("fu", "Follow-up", "e3"),
        ],
        activities=[
            ActivityOut(
                label=c("Vital signs"),
                group=None,
                visit_keys=["scr", "d1", "d15", "w8", "fu"],
                marks_confidence=0.95,
            ),
            ActivityOut(
                label=c("Weight"), group="Physical", visit_keys=["scr", "w8"], marks_confidence=0.6
            ),
            ActivityOut(label=c("Unmarked row"), group=None, visit_keys=[], marks_confidence=0.9),
        ],
    )
    pk = TimelineOut(
        key="pk",
        label="PK sampling",
        main=False,
        description="Sampling around dosing",
        entry_condition=Cited(value=None, quote=None, section_id=None, confidence=0),
        visits=[
            visit("p1", "Day 1", "e2", anchor=True, main_visit_key="d1"),
            visit(
                "p2",
                "Day 15",
                "e2",
                main_visit_key="d15",
                offset=OffsetOut(value=c("14", "Day 15"), unit="days", direction="after"),
            ),
        ],
        activities=[
            ActivityOut(
                label=c("PK sample"),
                group=None,
                visit_keys=["p1", "p2", "zz"],
                marks_confidence=0.8,
            )
        ],
    )
    return ScheduleOut(
        epochs=[
            EpochOut(key="e1", label=c("Screening"), type=c("Screening Epoch", "Screening")),
            EpochOut(key="e2", label=c("Treatment", "Day 1"), type=c("Treatment Epoch", "Day 1")),
            EpochOut(key="e3", label=c("Follow-up"), type=c("Follow-Up Epoch", "Follow-up")),
        ],
        timelines=[pk, main],  # the main timeline is built first whatever the order
    )


@pytest.fixture(scope="module")
def resolver() -> CtResolver:
    return get_ct_resolver()


@pytest.fixture(scope="module")
def built(resolver: CtResolver):  # type: ignore[no-untyped-def]
    return ScheduleAgent().to_records(output(), CONTEXT, resolver, STUDY)


def test_timelines_timepoints_and_encounters(built) -> None:  # type: ignore[no-untyped-def]
    sheet, warnings = built
    assert [(t.name.value, t.sheet_name.value, t.main.value) for t in sheet.timelines] == [
        ("Main Timeline", "main-timeline", "Y"),
        ("PK sampling", "timeline-2", "N"),
    ]
    assert sheet.timelines[0].entry_condition.provenance.origin == ValueOrigin.DERIVED
    main = [tp for tp in sheet.timepoints if tp.timeline.value == "Main Timeline"]
    assert [tp.name.value for tp in main] == ["Screening", "Day 1", "Day 15", "Week 8", "Follow-up"]
    assert [tp.default.value for tp in main][-2:] == ["Follow-up", "(Exit)"]
    pk = [tp for tp in sheet.timepoints if tp.timeline.value == "PK sampling"]
    assert [(tp.name.value, tp.encounter.value) for tp in pk] == [
        ("PK sampling Day 1", "Day 1"),
        ("PK sampling Day 15", "Day 15"),
    ]
    assert len(sheet.encounters) == 5  # only the main timeline's visits are encounters
    assert any("marks in unknown columns" in w for w in warnings)
    assert any("not marked in any column" in w for w in warnings)


def test_timings_are_relative_to_the_anchor_with_windows(built, resolver: CtResolver) -> None:  # type: ignore[no-untyped-def]
    sheet, warnings = built
    rows = [
        (t.relative_from.value, t.type.value, t.relative_to.value, t.value.value, t.window.value)
        for t in sheet.timings
    ]
    assert rows[:3] == [
        ("Day 1", "Fixed Reference", "Day 1", "0 days", None),
        ("Day 15", "After", "Day 1", "14 days", "-2..2 days"),
        ("Week 8", "After", "Day 1", "8 weeks", "-7..7 days"),
    ]
    for timing in sheet.timings:
        assert timing.type.terminology and timing.type.terminology.status == TerminologyStatus.EXACT
        assert formats.check(ValueFormat.DURATION, timing.value.value, resolver) is None
        assert formats.check(ValueFormat.WINDOW, timing.window.value, resolver) is None
    day15 = next(e for e in sheet.encounters if e.name.value == "Day 15")
    assert day15.window.value == "Day 15 timing"
    assert any("Screening: no planned time stated" in w for w in warnings)


def test_schedule_rows_and_references_are_valid(built) -> None:  # type: ignore[no-untyped-def]
    sheet, _ = built
    assert [(r.timeline.value, r.activity.value, r.scheduled_at.value) for r in sheet.rows] == [
        ("Main Timeline", "Vital signs", "Screening, Day 1, Day 15, Week 8, Follow-up"),
        ("Main Timeline", "Weight", "Screening, Week 8"),
        ("PK sampling", "PK sample", "PK sampling Day 1, PK sampling Day 15"),
    ]
    weight = sheet.rows[1].scheduled_at.provenance
    assert weight and weight.confidence == 0.6 and weight.verified
    assert [a.description.value for a in sheet.activities if a.name.value == "Weight"] == [
        "Physical"
    ]
    result = validate_references(ExtractionSheets(schedule=sheet))
    assert result.valid, result.issues


def test_biomedical_concepts_come_only_from_exact_catalogue_matches(
    built, resolver: CtResolver
) -> None:  # type: ignore[no-untyped-def]
    sheet, _ = built
    quoted = ExtractedField(
        value="x",
        provenance=Provenance(origin=ValueOrigin.EXTRACTED, confidence=0.9, verified=True),
    )

    def m(value: str) -> ExtractedField[str]:
        return quoted.model_copy(update={"value": value})

    sheets = ExtractionSheets(
        schedule=sheet.model_copy(deep=True),
        assessments=[
            AssessmentRecord(
                assessment=m("Vital signs"),
                measurements=[m("systolic blood pressure"), m("pulse"), m("mood of the day")],
            )
        ],
    )
    notes = assign_biomedical_concepts(sheets, resolver)
    rows = {r.activity.value: r.biomedical_concepts for r in sheets.schedule.rows}  # type: ignore[union-attr]
    vitals = rows["Vital signs"]
    assert vitals.value == "Systolic Blood Pressure, Pulse"
    assert vitals.terminology and vitals.terminology.status == TerminologyStatus.EXACT
    assert vitals.provenance and "mood of the day" in (vitals.provenance.note or "")
    assert rows["Weight"].value == "Weight"  # the activity's own name is a concept
    assert rows["PK sample"].is_empty
    assert notes == []


def test_design_structure_gives_arms_their_own_treatment_elements(built) -> None:  # type: ignore[no-untyped-def]
    sheet, _ = built

    def arm(name: str) -> ArmRecord:
        f = ExtractedField(value=name)
        return ArmRecord(
            name=f, label=f, description=f, type=f, data_origin_description=f, data_origin_type=f
        )

    sheets = ExtractionSheets(schedule=sheet, study_design_arms=[arm("Drug"), arm("Placebo")])
    derive_design(sheets)
    assert sheets.design is not None
    cells = [(c.arm.value, c.epoch.value, c.elements.value) for c in sheets.design.cells]
    assert cells == [
        ("Drug", "Screening", "Screening"),
        ("Placebo", "Screening", "Screening"),
        ("Drug", "Treatment", "Drug - Treatment"),
        ("Placebo", "Treatment", "Placebo - Treatment"),
        ("Drug", "Follow-up", "Follow-up"),
        ("Placebo", "Follow-up", "Follow-up"),
    ]
    assert validate_references(sheets).valid
    assert ExtractionSheets(schedule=sheet).design is None


def test_trial_summary_concepts_are_not_scheduled(resolver: CtResolver) -> None:
    from backend.pipeline.identifiers.concepts import _schedulable

    assert resolver.bcs.resolve("Randomization") is not None
    assert not _schedulable(resolver.bcs.resolve("Trial is Randomized (TS)"))
    assert _schedulable(resolver.bcs.resolve("Weight"))


def test_every_timeline_gets_one_anchor_timing_without_a_window(resolver: CtResolver) -> None:
    schedule = output()
    main = next(t for t in schedule.timelines if t.main)
    anchor = next(v for v in main.visits if v.anchor)
    anchor.window = WindowOut(before="1", after="1", unit="days", quote=c("±1 day"))
    pk = next(t for t in schedule.timelines if not t.main)
    for v in pk.visits:
        v.anchor = False
    sheet, warnings = ScheduleAgent().to_records(schedule, CONTEXT, resolver, STUDY)

    anchors = [t for t in sheet.timings if t.type.value == "Fixed Reference"]
    assert [(t.relative_from.value, t.window.value) for t in anchors] == [
        ("Day 1", None),
        ("PK sampling Day 1", None),
    ]
    # Without a stated anchor, planned times have no reference: only the anchor timing is built.
    assert not any(t.relative_from.value == "PK sampling Day 15" for t in sheet.timings)
    assert any("anchor visit's window is not written" in w for w in warnings)
    assert any("PK sampling: no anchor visit stated" in w for w in warnings)
