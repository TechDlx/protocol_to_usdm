"""A stand-in for the Claude API: canned structured outputs, no network, no cost."""

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from backend.models.extraction import LlmUsage
from backend.pipeline.agents.common import Cited
from backend.pipeline.llm import LlmRequest
from tests.fixtures.synthetic_protocol import FULL_LINE

Responder = Callable[[LlmRequest], BaseModel]


def cited(
    value: str | None, quote: str | None, section: str | None, confidence: float = 0.9
) -> Cited:
    return Cited(value=value, quote=quote, section_id=section, confidence=confidence)


NULL = cited(None, None, None, 0.0)


class FakeLlm:
    def __init__(self, responders: dict[str, Responder] | None = None) -> None:
        self.responders = responders or {}
        self.calls: list[tuple[str, LlmRequest]] = []

    async def extract(self, request: LlmRequest, output_model: type[Any]) -> tuple[Any, LlmUsage]:
        name = output_model.__name__
        self.calls.append((name, request))
        responder = self.responders.get(name)
        if responder is None:
            raise RuntimeError(f"FakeLlm has no response for {name}")
        usage = LlmUsage(model=request.model, input_tokens=1000, output_tokens=200, cost_usd=0.004)
        return responder(request), usage


def synthetic_responders() -> dict[str, Responder]:
    """Outputs consistent with tests/fixtures/synthetic_protocol.py."""
    from backend.pipeline.agents.eligibility import CriterionOut, EligibilityOut
    from backend.pipeline.agents.study import StudyOut

    def study(_: LlmRequest) -> BaseModel:
        return StudyOut(
            official_title=cited(
                "A Phase 3 Trial of Examplumab", "A Phase 3 Trial of Examplumab", "title-page", 0.95
            ),
            brief_title=NULL,
            public_title=NULL,
            scientific_title=NULL,
            acronym=NULL,
            sponsor_protocol_identifier=cited("EX-001", "Protocol EX-001", "title-page", 0.9),
            protocol_version=NULL,
            # The quote does not exist in the protocol: provenance must catch it.
            protocol_status=cited("Final", "Final Protocol Version 2.0", "title-page", 0.9),
            rationale=NULL,
            governance_dates=[],
        )

    def eligibility(_: LlmRequest) -> BaseModel:
        inclusion = cited("Inclusion Criteria", FULL_LINE[:40], "sec-2.1", 0.95)
        return EligibilityOut(
            criteria=[
                CriterionOut(
                    category=inclusion,
                    identifier="1",
                    label="Long body line",
                    text=cited(FULL_LINE, FULL_LINE[:50], "sec-2.1", 0.95),
                ),
                CriterionOut(
                    category=inclusion,
                    identifier="2",
                    label="Paraphrased",
                    # Quote is genuine, but the full text is a paraphrase.
                    text=cited(
                        "A line that is long and reaches the edge.", FULL_LINE[:50], "sec-2.1", 0.95
                    ),
                ),
            ]
        )

    def identifiers(_: LlmRequest) -> BaseModel:
        from backend.pipeline.agents.identifiers import (
            IdentifierOut,
            IdentifiersOut,
            OrganizationOut,
        )

        return IdentifiersOut(
            organizations=[
                OrganizationOut(
                    key="sponsor",
                    name=cited("Examplumab Sponsor", "Protocol EX-001", "title-page", 0.6),
                    type=cited("Drug Company", "Protocol EX-001", "title-page", 0.6),
                    identifier_scheme=NULL,
                    identifier=NULL,
                    address=None,
                )
            ],
            identifiers=[
                IdentifierOut(
                    identifier=cited("EX-001", "Protocol EX-001", "title-page", 0.95),
                    organization_key="sponsor",
                )
            ],
        )

    def study_design(_: LlmRequest) -> BaseModel:
        from backend.pipeline.agents.study_design import StudyDesignOut

        title = "A Phase 3 Trial of Examplumab"
        return StudyDesignOut(
            description=cited(title, title, "title-page", 0.6),
            rationale=NULL,
            study_type=cited("Interventional Study", title, "title-page", 0.7),
            study_phase=cited("Phase III Trial", "A Phase 3 Trial", "title-page", 0.95),
            blinding_schema=NULL,
            intervention_model=NULL,
            intent_types=[cited("Treatment Study", title, "title-page", 0.6)],
            sub_types=[],
            characteristics=[],
        )

    def populations(_: LlmRequest) -> BaseModel:
        from backend.pipeline.agents.populations import CountOut, PopulationOut, PopulationsOut

        return PopulationsOut(
            populations=[
                PopulationOut(
                    level="main",
                    label=cited("Study population", "Study Population", "sec-2", 0.7),
                    description=cited(FULL_LINE, FULL_LINE[:40], "sec-2", 0.7),
                    planned_enrollment=CountOut(
                        value=cited("120", FULL_LINE[:30], "sec-3.1", 0.5), upper=None
                    ),
                    planned_completion=None,
                    age_min=cited("18", FULL_LINE[:30], "sec-2.1", 0.5),
                    age_max=NULL,
                    age_unit="years",
                    sex=[cited("Both", FULL_LINE[:30], "sec-2.1", 0.5)],
                    healthy_subjects=cited("no", FULL_LINE[:30], "sec-2.1", 0.5),
                )
            ]
        )

    def objectives(_: LlmRequest) -> BaseModel:
        from backend.pipeline.agents.objectives_endpoints import ObjectivesOut

        return ObjectivesOut(objectives=[])

    def indications(_: LlmRequest) -> BaseModel:
        from backend.pipeline.agents.indications import IndicationsOut

        return IndicationsOut(indications=[])

    def abbreviations(_: LlmRequest) -> BaseModel:
        from backend.pipeline.agents.abbreviations import AbbreviationOut, AbbreviationsOut

        return AbbreviationsOut(
            abbreviations=[
                AbbreviationOut(
                    abbreviation="AE",
                    expansion=cited(
                        "Adverse event",
                        "AE Adverse event",
                        "app-appendix-1-list-of-abbreviations",
                        0.95,
                    ),
                )
            ]
        )

    def schedule(_: LlmRequest) -> BaseModel:
        """The synthetic SoA grid (tests/fixtures/synthetic_protocol.py SOA_ROWS)."""
        from backend.pipeline.agents.schedule import (
            ActivityOut,
            EpochOut,
            OffsetOut,
            ScheduleOut,
            TimelineOut,
            VisitOut,
            WindowOut,
        )
        from tests.fixtures.synthetic_protocol import SOA_ROWS

        section = "fm-schedule-of-activities"
        header = SOA_ROWS[0][1:]
        epochs = ["scr", "trt", "trt", "trt"]
        visits = []
        for i, label in enumerate(header):
            weeks = label.split()[-1] if label.startswith("Week") else None
            visits.append(
                VisitOut(
                    key=f"v{i}",
                    label=cited(label, label, section, 0.95),
                    epoch_key=epochs[i],
                    anchor=label == "Day 1",
                    offset=OffsetOut(
                        value=cited(weeks, label, section), unit="weeks", direction="after"
                    )
                    if weeks
                    else None,
                    window=WindowOut(
                        before="3", after="3", unit="days", quote=cited("±3 days", None, None, 0.5)
                    )
                    if weeks
                    else None,
                    main_visit_key=None,
                )
            )
        activities = [
            ActivityOut(
                label=cited(row[0], row[0], section, 0.95),
                group=None,
                visit_keys=[f"v{i}" for i, mark in enumerate(row[1:]) if mark == "X"],
                marks_confidence=0.9,
            )
            for row in SOA_ROWS[1:]
        ]
        return ScheduleOut(
            epochs=[
                EpochOut(
                    key="scr",
                    label=cited("Screening", "Screening", section),
                    type=cited("Screening Epoch", "Screening", section),
                ),
                EpochOut(
                    key="trt",
                    label=cited("Treatment", "Day 1", section, 0.6),
                    type=cited("Treatment Epoch", "Day 1", section, 0.6),
                ),
            ],
            timelines=[
                TimelineOut(
                    key="main",
                    label="Schedule of Activities",
                    main=True,
                    description="Main schedule",
                    entry_condition=NULL,
                    visits=visits,
                    activities=activities,
                )
            ],
        )

    return {
        "ScheduleOut": schedule,
        "StudyOut": study,
        "EligibilityOut": eligibility,
        "IdentifiersOut": identifiers,
        "StudyDesignOut": study_design,
        "PopulationsOut": populations,
        "ObjectivesOut": objectives,
        "IndicationsOut": indications,
        "AbbreviationsOut": abbreviations,
    }
