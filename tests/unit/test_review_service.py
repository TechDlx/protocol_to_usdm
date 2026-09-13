import json
from pathlib import Path

import pytest

from backend.models.review import (
    AcceptValue,
    AddRow,
    DeleteRow,
    IssueKind,
    MoveRow,
    ReviewStatus,
    SetValue,
)
from backend.pipeline.review.service import (
    ARCHIVE_DIR,
    AUDIT_FILE,
    REVIEWED_FILE,
    ReviewBlockedError,
    ReviewOperationError,
    ReviewService,
    ReviewUnavailableError,
    RevisionConflictError,
)
from backend.pipeline.terminology.ct import get_ct_resolver
from tests.fixtures.extracted_run import build_extracted_run


@pytest.fixture(scope="module")
def template_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    run_dir, _ = build_extracted_run(tmp_path_factory.mktemp("extracted"))
    return run_dir


@pytest.fixture
def service(template_run: Path, tmp_path: Path) -> ReviewService:
    import shutil

    run_dir = tmp_path / "run"
    shutil.copytree(template_run, run_dir)
    return ReviewService(run_dir, get_ct_resolver(), confidence_threshold=0.7)


def _audit(service: ReviewService) -> list[dict]:  # type: ignore[type-arg]
    path = service.run_dir / AUDIT_FILE
    return (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if path.exists()
        else []
    )


def _issues(service: ReviewService, kind: IssueKind) -> list[str]:
    return [f"{i.sheet}.{i.field}" for i in service.state().validation.issues if i.kind == kind]


def test_open_copies_extraction_and_assigns_row_ids(service: ReviewService) -> None:
    doc = service.open()
    assert (service.run_dir / REVIEWED_FILE).is_file()
    assert doc.revision == 0 and doc.status == ReviewStatus.DRAFT
    criteria = doc.sheets.eligibility_criteria or []
    assert [c.row_id for c in criteria] == ["crit-1", "crit-2"]
    assert service.state().stale is False


def test_review_requires_an_extraction(tmp_path: Path) -> None:
    empty = ReviewService(tmp_path, get_ct_resolver(), 0.7)
    with pytest.raises(ReviewUnavailableError):
        empty.open()


def test_set_value_records_human_provenance_and_audits_the_cell(service: ReviewService) -> None:
    service.open()
    doc = service.apply(
        0,
        [
            SetValue(
                sheet="eligibility_criteria", row_id="crit-2", field="label", value="Edited label"
            )
        ],
    )

    assert doc.revision == 1
    criterion = (doc.sheets.eligibility_criteria or [])[1]
    assert criterion.label.value == "Edited label"
    assert criterion.label.provenance and criterion.label.provenance.origin == "human"
    (entry,) = _audit(service)
    assert entry["action"] == "set"
    assert entry["cell"] == "studyDesignEligibilityCriteria!E3"
    assert (entry["old_value"], entry["new_value"]) == ("Paraphrased", "Edited label")


def test_unchanged_value_is_not_a_revision(service: ReviewService) -> None:
    service.open()
    doc = service.apply(
        0, [SetValue(sheet="eligibility_criteria", row_id="crit-1", field="identifier", value="1")]
    )
    assert doc.revision == 0 and _audit(service) == []


def test_study_key_value_cell_reference(service: ReviewService) -> None:
    service.open()
    service.apply(0, [SetValue(sheet="study", field="description", value="A synthetic trial.")])
    assert _audit(service)[0]["cell"] == "study!B2"


def test_terminology_by_code_is_exact_and_checked_against_the_codelist(
    service: ReviewService,
) -> None:
    service.open()
    doc = service.apply(
        0,
        [
            SetValue(
                sheet="eligibility_criteria",
                row_id="crit-1",
                field="category",
                value=None,
                code="C25370",
            )
        ],
    )
    category = (doc.sheets.eligibility_criteria or [])[0].category
    assert category.value == "Exclusion Criteria"
    assert category.terminology and category.terminology.matched_on == "reviewer"
    assert _audit(service)[0]["new_code"] == "C25370"

    with pytest.raises(ReviewOperationError, match="not a term"):
        service.apply(
            1,
            [
                SetValue(
                    sheet="eligibility_criteria",
                    row_id="crit-1",
                    field="category",
                    value=None,
                    code="C174268",
                )
            ],
        )
    with pytest.raises(ReviewOperationError, match="not a terminology field"):
        service.apply(
            1,
            [
                SetValue(
                    sheet="eligibility_criteria",
                    row_id="crit-1",
                    field="label",
                    value="x",
                    code="C25370",
                )
            ],
        )


def test_free_text_terminology_is_revalidated_and_blocks_when_not_exact(
    service: ReviewService,
) -> None:
    service.open()
    service.apply(
        0, [SetValue(sheet="eligibility_criteria", row_id="crit-1", field="category", value="incl")]
    )
    assert "eligibility_criteria.category" in _issues(service, IssueKind.TERMINOLOGY_NOT_EXACT)


def test_revision_conflict_is_rejected(service: ReviewService) -> None:
    service.open()
    service.apply(0, [SetValue(sheet="study", field="description", value="one")])
    with pytest.raises(RevisionConflictError) as exc:
        service.apply(0, [SetValue(sheet="study", field="description", value="two")])
    assert exc.value.current == 1


def test_add_move_delete_rows_are_audited(service: ReviewService) -> None:
    service.open()
    doc = service.apply(0, [AddRow(sheet="eligibility_criteria", after_row_id="crit-1")])
    criteria = doc.sheets.eligibility_criteria or []
    assert [c.name.value for c in criteria] == ["IN01", "IE01", "IN02"]
    new_id = criteria[1].row_id
    assert new_id == "crit-3"

    doc = service.apply(1, [MoveRow(sheet="eligibility_criteria", row_id=new_id, to_index=2)])
    assert [c.row_id for c in doc.sheets.eligibility_criteria or []] == [
        "crit-1",
        "crit-2",
        "crit-3",
    ]

    doc = service.apply(2, [DeleteRow(sheet="eligibility_criteria", row_id="crit-2")])
    assert [c.row_id for c in doc.sheets.eligibility_criteria or []] == ["crit-1", "crit-3"]

    actions = [e["action"] for e in _audit(service)]
    assert actions == ["add_row", "move_row", "delete_row"]
    deleted = json.loads(_audit(service)[2]["old_value"])
    assert deleted["name"] == "IN02"


def test_key_value_sheet_has_no_rows(service: ReviewService) -> None:
    service.open()
    with pytest.raises(ReviewOperationError):
        service.apply(0, [AddRow(sheet="study")])


def test_adding_arms_to_an_empty_sheet_creates_it(service: ReviewService) -> None:
    service.open()
    doc = service.apply(0, [AddRow(sheet="study_design_arms"), AddRow(sheet="study_design_arms")])
    names = [a.name.value for a in doc.sheets.study_design_arms or []]
    assert names == ["New arm 2", "New arm"]  # each inserted at the top, names unique


def test_accept_clears_the_warning_but_not_for_derived_values(service: ReviewService) -> None:
    service.open()
    assert "study.protocol_status" in _issues(service, IssueKind.UNVERIFIED_SOURCE)
    service.apply(0, [AcceptValue(sheet="study", field="protocol_status")])
    assert "study.protocol_status" not in _issues(service, IssueKind.UNVERIFIED_SOURCE)
    assert _audit(service)[0]["action"] == "accept"

    with pytest.raises(ReviewOperationError, match="only extracted"):
        service.apply(1, [AcceptValue(sheet="study", field="name")])


def test_duplicate_names_block_with_both_cells(service: ReviewService) -> None:
    service.open()
    service.apply(
        0, [SetValue(sheet="eligibility_criteria", row_id="crit-2", field="name", value="IN01")]
    )
    cells = [
        i.cell for i in service.state().validation.issues if i.kind == IssueKind.DUPLICATE_NAME
    ]
    assert sorted(cells) == [
        "studyDesignEligibilityCriteria!C2",
        "studyDesignEligibilityCriteria!C3",
    ]


def _make_confirmable(service: ReviewService, revision: int) -> int:
    doc = service.apply(
        revision,
        [
            SetValue(sheet="study", field="description", value="A synthetic trial."),
            SetValue(sheet="study", field="label", value="SYN"),
            SetValue(sheet="study", field="study_version", value="1.0"),
        ],
    )
    return doc.revision


def test_confirm_is_blocked_until_blocking_issues_are_resolved(service: ReviewService) -> None:
    service.open()
    missing = set(_issues(service, IssueKind.MISSING_REQUIRED))
    assert "study.study_version" in missing
    # Optional in the usdm4 model, so never blocking.
    assert not missing & {"study.description", "study.label"}
    with pytest.raises(ReviewBlockedError):
        service.confirm(0)

    revision = _make_confirmable(service, 0)
    assert service.state().validation.blocking == 0
    doc = service.confirm(revision)
    assert doc.status == ReviewStatus.CONFIRMED and doc.confirmed_at is not None

    reopened = service.apply(doc.revision, [SetValue(sheet="study", field="label", value="SYN-2")])
    assert reopened.status == ReviewStatus.DRAFT
    assert [e["action"] for e in _audit(service)][-3:] == ["confirm", "reopen", "set"]


def test_stale_review_can_be_restarted_and_old_one_is_archived(service: ReviewService) -> None:
    from backend.pipeline.extract import EXTRACTION_FILE

    service.open()
    service.apply(0, [SetValue(sheet="study", field="description", value="kept in archive")])
    path = service.run_dir / EXTRACTION_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    data["generated_at"] = "2030-01-01T00:00:00Z"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert service.state().stale

    doc = service.restart(1)
    assert doc.revision == 2 and doc.sheets.study and doc.sheets.study.description.value is None
    assert not service.state().stale
    (archived,) = (service.run_dir / ARCHIVE_DIR).iterdir()
    assert "kept in archive" in archived.read_text(encoding="utf-8")
    assert _audit(service)[-1]["action"] == "restart"


def test_audit_trail_is_append_only(service: ReviewService) -> None:
    service.open()
    service.apply(0, [SetValue(sheet="study", field="description", value="first")])
    first = (service.run_dir / AUDIT_FILE).read_text(encoding="utf-8")
    service.apply(1, [SetValue(sheet="study", field="description", value="second")])
    second = (service.run_dir / AUDIT_FILE).read_text(encoding="utf-8")
    assert second.startswith(first) and len(second.splitlines()) == 2
