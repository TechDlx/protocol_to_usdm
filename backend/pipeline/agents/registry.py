"""The extraction agents, by sheet key, in the order their sheets appear in review."""

from backend.pipeline.agents.abbreviations import AbbreviationsAgent
from backend.pipeline.agents.amendments import AmendmentsAgent
from backend.pipeline.agents.arms import ArmsAgent
from backend.pipeline.agents.assessments import AssessmentsAgent
from backend.pipeline.agents.base import SheetAgent
from backend.pipeline.agents.eligibility import EligibilityAgent
from backend.pipeline.agents.estimands import EstimandsAgent
from backend.pipeline.agents.identifiers import IdentifiersAgent
from backend.pipeline.agents.indications import IndicationsAgent
from backend.pipeline.agents.interventions import InterventionsAgent
from backend.pipeline.agents.objectives_endpoints import ObjectivesEndpointsAgent
from backend.pipeline.agents.populations import PopulationsAgent
from backend.pipeline.agents.schedule import ScheduleAgent
from backend.pipeline.agents.study import StudyAgent
from backend.pipeline.agents.study_design import StudyDesignAgent

AGENTS: dict[str, SheetAgent] = {
    agent.sheet: agent
    for agent in (
        StudyAgent(),
        IdentifiersAgent(),
        StudyDesignAgent(),
        ArmsAgent(),
        PopulationsAgent(),
        EligibilityAgent(),
        ObjectivesEndpointsAgent(),
        EstimandsAgent(),
        InterventionsAgent(),
        IndicationsAgent(),
        AmendmentsAgent(),
        AbbreviationsAgent(),
        ScheduleAgent(),
        AssessmentsAgent(),
    )
}
