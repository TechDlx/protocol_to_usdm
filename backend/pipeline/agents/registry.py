"""The extraction agents that exist so far, by sheet key."""

from backend.pipeline.agents.arms import ArmsAgent
from backend.pipeline.agents.base import SheetAgent
from backend.pipeline.agents.eligibility import EligibilityAgent
from backend.pipeline.agents.study import StudyAgent

AGENTS: dict[str, SheetAgent] = {
    agent.sheet: agent for agent in (StudyAgent(), ArmsAgent(), EligibilityAgent())
}
