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

    return {"StudyOut": study, "EligibilityOut": eligibility}
