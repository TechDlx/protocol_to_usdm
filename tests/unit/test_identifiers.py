import pytest

from backend.models.extraction import (
    ArmRecord,
    EligibilityCriterionRecord,
    ExtractedField,
    ExtractionSheets,
    ReferenceIssueKind,
)
from backend.pipeline.identifiers.names import (
    NameRegistry,
    criterion_name,
    governance_date_name,
    safe_name,
)
from backend.pipeline.identifiers.references import build_graph, validate_references


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Palbociclib, plus Fulvestrant", "Palbociclib plus Fulvestrant"),
        ('Arm "A"', "Arm A"),
        ("  spaced\n  out ", "spaced out"),
        ("", "fallback"),
        ("x" * 80, "x" * 60),
    ],
)
def test_safe_name_is_usable_in_reference_lists(raw: str, expected: str) -> None:
    assert safe_name(raw, "fallback") == expected


def test_name_registry_dedupes_case_insensitively_and_deterministically() -> None:
    names = NameRegistry()
    assert [names.claim(n) for n in ["Placebo", "placebo", "Placebo", "Other"]] == [
        "Placebo",
        "placebo 2",
        "Placebo 3",
        "Other",
    ]


def test_criterion_names_follow_category() -> None:
    assert criterion_name("C25532", 1) == "IN01"
    assert criterion_name("C25370", 12) == "EX12"
    assert criterion_name(None, 3) == "IE03"


def test_governance_date_name() -> None:
    assert governance_date_name("amendment", "Approval Date", 2) == "AMENDMENT_APPROVAL_DATE_2"
    assert governance_date_name("study_version", None, 1) == "STUDY_VERSION_DATE_1"


def _field(value: str | None) -> ExtractedField[str]:
    return ExtractedField(value=value)


def _arm(name: str | None) -> ArmRecord:
    return ArmRecord(
        name=_field(name),
        label=_field(name),
        description=_field(None),
        type=_field(None),
        data_origin_description=_field(None),
        data_origin_type=_field(None),
    )


def _criterion(name: str) -> EligibilityCriterionRecord:
    return EligibilityCriterionRecord(
        name=_field(name),
        category=_field(None),
        identifier=_field(None),
        label=_field(None),
        description=_field(None),
        text=_field("text"),
    )


def test_clean_graph_is_valid() -> None:
    result = validate_references(
        ExtractionSheets(
            study_design_arms=[_arm("A"), _arm("B")], eligibility_criteria=[_criterion("IN01")]
        )
    )
    assert result.valid and result.entities == 3 and result.issues == []


def test_duplicate_names_within_a_kind_are_reported() -> None:
    result = validate_references(
        ExtractionSheets(study_design_arms=[_arm("Placebo"), _arm("Placebo")])
    )
    assert not result.valid
    (issue,) = result.issues
    assert issue.kind == ReferenceIssueKind.DUPLICATE_NAME
    assert len(issue.locations) == 2 and len(issue.anchors) == 2


def test_same_name_in_different_kinds_is_allowed() -> None:
    # usdm4 keys cross-references by class and name: an arm and a criterion may share a name.
    result = validate_references(
        ExtractionSheets(
            study_design_arms=[_arm("IN01")], eligibility_criteria=[_criterion("IN01")]
        )
    )
    assert result.valid


def test_missing_names_are_reported() -> None:
    result = validate_references(ExtractionSheets(study_design_arms=[_arm(None)]))
    assert [i.kind for i in result.issues] == [ReferenceIssueKind.MISSING_NAME]


def test_dangling_reference_is_reported_and_names_are_case_sensitive() -> None:
    graph = build_graph(ExtractionSheets(study_design_arms=[_arm("Arm A")]))
    graph.reference(("StudyArm",), "Arm A", "studyDesign arms")
    graph.reference(("StudyArm",), "Missing Arm", "studyDesign arms")
    graph.reference(("StudyArm",), "arm a", "studyDesign arms")
    graph.reference(("Endpoint",), "Arm A", "estimand endpointXref")
    result = graph.validate()
    assert [(i.kind, i.name) for i in result.issues] == [
        (ReferenceIssueKind.DANGLING_REFERENCE, "Missing Arm"),
        (ReferenceIssueKind.DANGLING_REFERENCE, "arm a"),
        (ReferenceIssueKind.DANGLING_REFERENCE, "Arm A"),
    ]
    assert "did you mean 'Arm A'" in result.issues[1].message
