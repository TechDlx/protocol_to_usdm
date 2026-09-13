"""Deterministic post-processing of the Phase 5 agents, and cross-sheet linking."""

from datetime import UTC, datetime

import pytest

from backend.models.document import HeadingSource, Section, SectionKind
from backend.models.extraction import (
    ExtractedField,
    ExtractionSheets,
    Provenance,
    TerminologyStatus,
    ValueOrigin,
)
from backend.models.study import StudyMeta
from backend.pipeline.agents.abbreviations import (
    AbbreviationOut,
    AbbreviationsAgent,
    AbbreviationsOut,
)
from backend.pipeline.agents.amendments import (
    AmendmentOut,
    AmendmentsAgent,
    AmendmentsOut,
    ReasonOut,
)
from backend.pipeline.agents.common import Cited
from backend.pipeline.agents.context import AgentContext
from backend.pipeline.agents.estimands import (
    EstimandOut,
    EstimandsAgent,
    EstimandsOut,
    IntercurrentEventOut,
)
from backend.pipeline.agents.identifiers import (
    AddressOut,
    IdentifierOut,
    IdentifiersAgent,
    IdentifiersOut,
    OrganizationOut,
)
from backend.pipeline.agents.interventions import (
    AdministrationOut,
    InterventionOut,
    InterventionsAgent,
    InterventionsOut,
)
from backend.pipeline.agents.objectives_endpoints import (
    EndpointOut,
    ObjectiveOut,
    ObjectivesEndpointsAgent,
    ObjectivesOut,
)
from backend.pipeline.agents.populations import (
    CountOut,
    PopulationOut,
    PopulationsAgent,
    PopulationsOut,
)
from backend.pipeline.agents.study_design import StudyDesignAgent, StudyDesignOut
from backend.pipeline.identifiers.linking import link_references
from backend.pipeline.identifiers.references import validate_references
from backend.pipeline.terminology.ct import CtResolver, get_ct_resolver

TEXT = (
    "[[PAGE 7]]\nThis is a randomised, double-blind, parallel-group study of Examplumab. "
    "Examplumab 125 mg capsules are taken orally once daily for 21 days. "
    "Men and women aged 18 to 75 years with chronic cough. About 300 participants will be "
    "randomised. The primary objective is to compare cough frequency. Primary endpoint: 24-hour "
    "cough count. Secondary endpoint: quality of life. Amendment 2 (30 September 2014) added "
    "sites and fixed typographical errors. Sponsor: Examplar Pharma Ltd, 1 Example Way, "
    "Cambridge, CB1 2AB, United Kingdom. Protocol EX-001. NCT01234567. AE adverse event. "
    "Estimand: hazard ratio in the intent-to-treat population for Examplumab on 24-hour cough "
    "count; treatment discontinuation handled by treatment policy."
)
SECTION = Section(
    id="sec-1",
    number="1",
    title="Everything",
    level=1,
    kind=SectionKind.BODY,
    parent_id=None,
    page_start=7,
    page_end=7,
    heading_bbox=None,
    heading_source=HeadingSource.TEXT,
    text=TEXT,
)
CONTEXT = AgentContext(sections=[SECTION], rendered="", used_fallback=False)
STUDY = StudyMeta(slug="s", name="S", created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
NULL = Cited(value=None, quote=None, section_id=None, confidence=0)


def c(value: str | None, quote: str | None = None, confidence: float = 0.9) -> Cited:
    return Cited(value=value, quote=quote or value, section_id="sec-1", confidence=confidence)


@pytest.fixture(scope="module")
def resolver() -> CtResolver:
    return get_ct_resolver()


def test_identifiers_link_organizations_and_generate_registry_identifiers(
    resolver: CtResolver,
) -> None:
    output = IdentifiersOut(
        organizations=[
            OrganizationOut(
                key="sponsor",
                name=c("Examplar Pharma Ltd"),
                type=c("Drug Company", "Sponsor: Examplar Pharma Ltd"),
                identifier_scheme=NULL,
                identifier=NULL,
                address=AddressOut(
                    lines=["1 Example Way"],
                    city="Cambridge",
                    district=None,
                    state=None,
                    postal_code="CB1 2AB",
                    country_code="GBR",
                    quote=c("1 Example Way, Cambridge", "1 Example Way, Cambridge"),
                ),
            ),
            OrganizationOut(
                key="ctgov",
                name=c("ClinicalTrials.gov", "NCT01234567", 0.8),
                type=c("Study Registry", "NCT01234567"),
                identifier_scheme=NULL,
                identifier=NULL,
                address=None,
            ),
        ],
        identifiers=[
            IdentifierOut(identifier=c("EX-001", "Protocol EX-001"), organization_key="sponsor"),
            IdentifierOut(identifier=c("NCT01234567"), organization_key="ctgov"),
        ],
    )
    sheet, _ = IdentifiersAgent().to_records(output, CONTEXT, resolver, STUDY)
    sponsor, registry = sheet.organizations
    assert sponsor.address.value == "1 Example Way||Cambridge||CB1 2AB|GBR"
    assert sponsor.identifier.is_empty  # never invented for the sponsor
    assert registry.identifier.value == "https://clinicaltrials.gov"
    assert (
        registry.identifier.provenance
        and registry.identifier.provenance.origin == ValueOrigin.DERIVED
    )
    assert [i.organization.value for i in sheet.identifiers] == [
        "Examplar Pharma Ltd",
        "ClinicalTrials.gov",
    ]
    assert validate_references(ExtractionSheets(identifiers=sheet)).valid


def test_study_design_joins_multi_valued_terms(resolver: CtResolver) -> None:
    output = StudyDesignOut(
        description=c("randomised, double-blind, parallel-group study"),
        rationale=NULL,
        study_type=c("Interventional Study", "randomised, double-blind"),
        study_phase=NULL,
        blinding_schema=c("Double Blind Study", "double-blind"),
        intervention_model=c("Parallel Study", "parallel-group study"),
        intent_types=[c("Treatment Study", "study of Examplumab", 0.6)],
        sub_types=[],
        characteristics=[
            c("Randomized Controlled Clinical Trial", "randomised"),
            c("Stratification", "invented quote that is not in the text"),
        ],
    )
    record, warnings = StudyDesignAgent().to_records(output, CONTEXT, resolver, STUDY)
    assert record.characteristics.value == "Randomized Controlled Clinical Trial, Stratification"
    assert (
        record.characteristics.terminology
        and record.characteristics.terminology.status == TerminologyStatus.EXACT
    )
    assert record.characteristics.provenance and not record.characteristics.provenance.verified
    assert record.name.value == "Study Design 1"
    assert warnings == ["no design rationale found; the studyDesign sheet requires one"]


def test_populations_format_counts_ranges_and_flags(resolver: CtResolver) -> None:
    output = PopulationsOut(
        populations=[
            PopulationOut(
                level="main",
                label=c("Adults with chronic cough", "with chronic cough"),
                description=c("Men and women aged 18 to 75 years with chronic cough."),
                planned_enrollment=CountOut(value=c("300", "About 300 participants"), upper=None),
                planned_completion=None,
                age_min=c("18", "aged 18 to 75 years"),
                age_max=c("75", "aged 18 to 75 years"),
                age_unit="years",
                sex=[c("Both", "Men and women")],
                healthy_subjects=c("no", "with chronic cough"),
            )
        ]
    )
    (pop,), _ = PopulationsAgent().to_records(output, CONTEXT, resolver, STUDY)
    assert (pop.level.value, pop.name.value) == ("Main", "POP1")
    assert pop.planned_enrollment_number.value == "300"
    assert pop.planned_age.value == "18..75 YEARS"
    assert pop.planned_age.provenance and pop.planned_age.provenance.verified
    assert pop.includes_healthy_subjects.value == "N"
    # USDM wants Female and Male, not the CT term "Both" (rule DDF00188).
    assert pop.planned_sex.value == "Female, Male"
    assert pop.planned_sex.terminology and pop.planned_sex.terminology.code == "C16576, C20197"
    assert pop.planned_sex.provenance and pop.planned_sex.provenance.verified


def test_objectives_flatten_to_one_row_per_endpoint(resolver: CtResolver) -> None:
    output = ObjectivesOut(
        objectives=[
            ObjectiveOut(
                text=c("to compare cough frequency"),
                label="Cough frequency",
                level=c("Trial Primary Objective", "The primary objective"),
                endpoints=[
                    EndpointOut(
                        text=c("24-hour cough count"),
                        label=None,
                        level=c("Primary Endpoint", "Primary endpoint"),
                        purpose=NULL,
                    ),
                    EndpointOut(
                        text=c("quality of life"),
                        label=None,
                        level=c("Secondary Endpoint", "Secondary endpoint"),
                        purpose=NULL,
                    ),
                ],
            ),
            ObjectiveOut(
                text=c("to compare cough frequency", confidence=0.5),
                label=None,
                level=c("Trial Secondary Objective"),
                endpoints=[],
            ),
        ]
    )
    rows, _ = ObjectivesEndpointsAgent().to_records(output, CONTEXT, resolver, STUDY)
    assert [(r.objective_name.value, r.endpoint_name.value) for r in rows] == [
        ("OBJ1", "END1"),
        (None, "END2"),
        ("OBJ2", None),
    ]


def test_interventions_write_cdisc_quantities(resolver: CtResolver) -> None:
    admin = AdministrationOut(
        label=c("Examplumab 125 mg"),
        description=c("Examplumab 125 mg capsules are taken orally once daily for 21 days."),
        route=c("ORAL", "taken orally"),
        dose_value=c("125", "Examplumab 125 mg"),
        dose_unit="mg",
        frequency=c("QD", "once daily"),
        duration_description=c("for 21 days"),
        duration_value=c("21", "for 21 days"),
        duration_unit="days",
        duration_will_vary=False,
        duration_will_vary_reason=NULL,
    )
    output = InterventionsOut(
        interventions=[
            InterventionOut(
                label=c("Examplumab"),
                description=c("Examplumab 125 mg capsules"),
                role=c("Protocol Agent", "Examplumab"),
                type=c("Pharmacologic Substance", "capsules", 0.6),
                administrations=[admin, admin],
            )
        ]
    )
    first, second = InterventionsAgent().to_records(output, CONTEXT, resolver, STUDY)[0]
    assert first.administration_dose.value == "125 mg"
    assert first.duration_quantity.value == "21 DAYS"
    assert first.duration_will_vary.value == "N"
    assert (
        first.administration_route.terminology
        and first.administration_route.terminology.status == TerminologyStatus.EXACT
    )
    assert second.name.is_empty and second.administration_name.value == "Examplumab 125 mg 2"


def test_amendment_other_reasons_and_date_linking(resolver: CtResolver) -> None:
    from backend.models.extraction import GovernanceDateRecord, StudyRecord
    from backend.pipeline.workbook.layout import DATES
    from backend.pipeline.workbook.layout import STUDY as STUDY_SPEC
    from backend.pipeline.workbook.sources import empty_record

    output = AmendmentsOut(
        amendments=[
            AmendmentOut(
                number=c("2", "Amendment 2"),
                summary=c("added sites and fixed typographical errors"),
                primary_reason=ReasonOut(term=c("Other", "added sites"), other="Added sites, more"),
                secondary_reasons=[
                    ReasonOut(
                        term=c(
                            "Inconsistency and/or Error In The Protocol",
                            "fixed typographical errors",
                        ),
                        other=None,
                    )
                ],
                scope="Global",
                date=c("2014-09-30", "30 September 2014"),
            )
        ]
    )
    (amendment,), _ = AmendmentsAgent().to_records(output, CONTEXT, resolver, STUDY)
    assert amendment.primary_reason.value == "Other=Added sites  more"
    assert (
        amendment.primary_reason.terminology
        and amendment.primary_reason.terminology.status == TerminologyStatus.EXACT
    )

    study = empty_record(STUDY_SPEC)
    assert isinstance(study, StudyRecord)
    study.name = ExtractedField(value="S")

    def date(name: str, value: str) -> GovernanceDateRecord:
        record = empty_record(DATES, row_id=name)
        assert isinstance(record, GovernanceDateRecord)
        record.name = ExtractedField(value=name)
        record.category = ExtractedField(value="amendment")
        record.date = ExtractedField(value=value)
        return record

    study.governance_dates = [
        date("AMENDMENT_ISSUED_DATE_1", "2014-04-04"),
        date("AMENDMENT_ISSUED_DATE_2", "2014-09-30"),
    ]
    sheets = ExtractionSheets(study=study, amendments=[amendment])
    assert link_references(sheets) == []
    linked = sheets.amendments[0].date  # type: ignore[index]
    assert linked.value == "AMENDMENT_ISSUED_DATE_2"
    assert linked.provenance and "linked from the phrase '2014-09-30'" in (
        linked.provenance.note or ""
    )
    assert validate_references(sheets).valid


def test_estimand_phrases_link_to_names_and_ambiguity_is_left_for_review(
    resolver: CtResolver,
) -> None:
    from backend.models.extraction import (
        InterventionRecord,
        ObjectiveEndpointRecord,
        PopulationRecord,
    )
    from backend.pipeline.workbook.layout import INTERVENTIONS, OBJECTIVES_ENDPOINTS, POPULATIONS
    from backend.pipeline.workbook.sources import empty_record

    output = EstimandsOut(
        estimands=[
            EstimandOut(
                summary_measure=c("hazard ratio"),
                population_description=c("intent-to-treat population"),
                population=c("intent-to-treat population"),
                treatment=c("Examplumab"),
                endpoint=c("24-hour cough count"),
                intercurrent_events=[
                    IntercurrentEventOut(
                        label="Discontinuation",
                        description=c("treatment discontinuation"),
                        strategy=c("treatment policy"),
                        text=c("treatment discontinuation handled by treatment policy"),
                    )
                ],
            )
        ]
    )
    (estimand,), _ = EstimandsAgent().to_records(output, CONTEXT, resolver, STUDY)

    def with_values(spec, **values):  # type: ignore[no-untyped-def]
        record = empty_record(spec, row_id="r")
        for k, v in values.items():
            setattr(record, k, ExtractedField(value=v))
        return record

    pops = [with_values(POPULATIONS, name="POP1", label="Adults with chronic cough", level="Main")]
    endpoints = [
        with_values(
            OBJECTIVES_ENDPOINTS,
            objective_name="OBJ1",
            objective_text="compare",
            endpoint_name="END1",
            endpoint_text="24-hour cough count",
        ),
        with_values(OBJECTIVES_ENDPOINTS, endpoint_name="END2", endpoint_text="quality of life"),
    ]
    interventions = [
        with_values(INTERVENTIONS, name="Examplumab", label="Examplumab"),
        with_values(INTERVENTIONS, name="Examplumab placebo", label="Examplumab placebo"),
    ]
    assert isinstance(pops[0], PopulationRecord) and isinstance(
        endpoints[0], ObjectiveEndpointRecord
    )
    assert isinstance(interventions[0], InterventionRecord)
    sheets = ExtractionSheets(
        estimands=[estimand],
        populations=pops,  # type: ignore[arg-type]
        objectives_endpoints=endpoints,
        interventions=interventions,
    )  # type: ignore[arg-type]
    notes = link_references(sheets)
    linked = sheets.estimands[0]  # type: ignore[index]
    assert linked.endpoint.value == "END1"
    assert linked.treatment.value == "Examplumab"  # an exact name is never re-linked
    # "intent-to-treat population" does not clearly match the one population's texts.
    assert linked.population.value == "intent-to-treat population"
    assert len(notes) == 1 and "populationSubset" in notes[0]
    issues = validate_references(sheets).issues
    assert [i.name for i in issues] == ["intent-to-treat population"]


def test_reviewer_values_are_never_relinked() -> None:
    from backend.pipeline.workbook.layout import IDENTIFIERS
    from backend.pipeline.workbook.sources import empty_record

    ident = empty_record(IDENTIFIERS, row_id="ident-1")
    ident.identifier = ExtractedField(value="X")  # type: ignore[attr-defined]
    ident.organization = ExtractedField(  # type: ignore[attr-defined]
        value="sponsor", provenance=Provenance(origin=ValueOrigin.HUMAN)
    )
    from backend.models.extraction import IdentifiersSheet

    sheets = ExtractionSheets(identifiers=IdentifiersSheet(identifiers=[ident]))  # type: ignore[list-item]
    assert link_references(sheets) == []
    assert ident.organization.value == "sponsor"  # type: ignore[attr-defined]


def test_abbreviations_keep_order_and_report_duplicates(resolver: CtResolver) -> None:
    output = AbbreviationsOut(
        abbreviations=[
            AbbreviationOut(abbreviation="AE", expansion=c("adverse event", "AE adverse event")),
            AbbreviationOut(abbreviation="AE", expansion=c("adverse event", "AE adverse event")),
        ]
    )
    records, warnings = AbbreviationsAgent().to_records(output, CONTEXT, resolver, STUDY)
    assert [r.abbreviated_text.value for r in records] == ["AE", "AE"]
    assert warnings == ["listed more than once: AE"]
