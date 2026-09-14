import json
import shutil
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.models.study import StudyCreate
from tests.fixtures.extracted_run import build_extracted_run
from tests.fixtures.synthetic_protocol import FULL_LINE

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    return build_extracted_run(tmp_path_factory.mktemp("review-template"))


@pytest.fixture
def api(template: tuple[Path, Path], tmp_path: Path) -> Iterator[tuple[TestClient, str]]:
    import io

    settings = Settings(_env_file=None, studies_root=tmp_path / "studies", max_upload_mb=5)  # type: ignore[call-arg]
    app = create_app(settings)
    store = app.state.store
    slug = store.create_study(StudyCreate(name="Synthetic")).slug
    run_template, pdf = template
    doc = store.add_source(slug, pdf.name, io.BytesIO(pdf.read_bytes()))
    run = store.create_run(slug, doc.filename)
    run_dir = store.run_dir(slug, run.run_id)
    for item in run_template.iterdir():
        target = run_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    with TestClient(app) as client:
        yield client, f"/api/studies/{slug}/runs/{run.run_id}"


REQUIRED_OPS: list[dict[str, str]] = [
    {"op": "set", "sheet": "study", "field": "description", "value": "A synthetic trial."},
    {"op": "set", "sheet": "study", "field": "label", "value": "SYN"},
    {"op": "set", "sheet": "study", "field": "study_version", "value": "1.0"},
    {
        "op": "set",
        "sheet": "organizations",
        "row_id": "org-1",
        "field": "identifier_scheme",
        "value": "DUNS",
    },
    {
        "op": "set",
        "sheet": "organizations",
        "row_id": "org-1",
        "field": "identifier",
        "value": "123456789",
    },
    {"op": "set", "sheet": "study_design", "field": "rationale", "value": "To test review."},
    {
        "op": "set",
        "sheet": "study_design",
        "field": "intervention_model",
        "value": "Parallel Study",
    },
]


def _confirm(client: TestClient, base: str) -> int:
    """Fill the synthetic review's blocking cells and confirm it; returns the confirmed revision."""
    state = client.post(
        f"{base}/review/operations", json={"base_revision": 0, "operations": REQUIRED_OPS}
    ).json()
    assert state["validation"]["blocking"] == 0
    confirmed = client.post(
        f"{base}/review/confirm", json={"base_revision": state["document"]["revision"]}
    )
    assert confirmed.status_code == 200 and confirmed.json()["document"]["status"] == "confirmed"
    revision: int = confirmed.json()["document"]["revision"]
    return revision


def test_get_review_returns_document_validation_and_layouts(api) -> None:  # type: ignore[no-untyped-def]
    client, base = api
    state = client.get(f"{base}/review").json()
    assert state["document"]["revision"] == 0
    assert state["validation"]["blocking"] > 0
    layout = {sheet["key"]: sheet for sheet in state["layouts"]}
    arms = layout["study_design_arms"]
    assert arms["workbook_sheet"] == "studyDesignArms"
    assert [c["letter"] for c in arms["columns"]] == list("ABCDEFG")
    assert arms["columns"][3]["ct_klass"] == "StudyArm"


def test_operations_conflicts_and_audit(api) -> None:  # type: ignore[no-untyped-def]
    client, base = api
    client.get(f"{base}/review")
    op = {"op": "set", "sheet": "study", "field": "description", "value": "A synthetic trial."}

    ok = client.post(f"{base}/review/operations", json={"base_revision": 0, "operations": [op]})
    assert ok.status_code == 200 and ok.json()["document"]["revision"] == 1

    stale = client.post(f"{base}/review/operations", json={"base_revision": 0, "operations": [op]})
    assert stale.status_code == 409 and stale.json()["detail"]["current_revision"] == 1

    bad = client.post(
        f"{base}/review/operations",
        json={
            "base_revision": 1,
            "operations": [{"op": "set", "sheet": "nope", "field": "x", "value": "y"}],
        },
    )
    assert bad.status_code == 422

    audit = client.get(f"{base}/review/audit").json()
    assert [a["cell"] for a in audit] == ["study!B2"]


def test_confirm_blocked_then_allowed_and_run_status_follows(api) -> None:  # type: ignore[no-untyped-def]
    client, base = api
    client.get(f"{base}/review")
    assert client.post(f"{base}/review/confirm", json={"base_revision": 0}).status_code == 422
    refused = client.post(f"{base}/workbook")
    assert refused.status_code == 409 and refused.json()["detail"]["reasons"] == [
        "the review is not confirmed"
    ]
    assert client.get(f"{base}/workbook").status_code == 404

    revision = _confirm(client, base)
    assert client.get(base).json()["status"] == "reviewed"

    report = client.post(f"{base}/workbook")
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["review_revision"] == revision
    assert body["sheets"]["main-timeline"] > 0 and not body["reused"]
    assert client.get(f"{base}/workbook").json()["sha256"] == body["sha256"]
    assert client.post(f"{base}/workbook").json()["reused"] is True
    download = client.get(f"{base}/workbook/download")
    assert download.status_code == 200 and download.content[:2] == b"PK"  # an xlsx is a zip
    assert "attachment" in download.headers["content-disposition"]
    assert client.get(base).json()["stages"]["workbook"]["status"] in ("done", "skipped")

    client.post(
        f"{base}/review/operations",
        json={
            "base_revision": revision,
            "operations": [{"op": "set", "sheet": "study", "field": "label", "value": "S"}],
        },
    )
    assert client.get(base).json()["status"] == "awaiting_review"


def _wait_idle(client: TestClient, base: str) -> dict[str, Any]:
    for _ in range(200):
        run: dict[str, Any] = client.get(base).json()
        if run["status"] not in ("running", "generating"):
            return run
        time.sleep(0.05)
    raise AssertionError("the job did not finish")


def test_usdm_generation_runs_in_the_background_and_reports_staleness(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    client, base = api
    client.get(f"{base}/review")
    refused = client.post(f"{base}/usdm")
    assert refused.status_code == 409 and "not confirmed" in json.dumps(refused.json())
    assert client.get(f"{base}/usdm").status_code == 404

    # The importer takes ~20 s and is covered by test_usdm_stage; here only the plumbing matters.
    from backend.pipeline import jobs
    from backend.pipeline.usdm_gen import stage

    calls: list[bool] = []

    def fake_generate(run_dir: Path, slug: str, force: bool = False) -> stage.UsdmReport:
        calls.append(force)
        workbook = stage.load_workbook_report(run_dir)
        assert workbook is not None
        out = stage.usdm_path(run_dir, slug)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text('{"usdmVersion": "4.0.0"}', encoding="utf-8")
        report = stage.UsdmReport(
            generated_at=workbook.generated_at,
            file=out.relative_to(run_dir).as_posix(),
            sha256="x",
            workbook_file=workbook.file,
            workbook_sha256=workbook.sha256,
            review_revision=workbook.review_revision,
            ct_version=workbook.ct_version,
            rules=stage.RulesSummary(rules=213, passed=210, failed=3, findings=4),
        )
        (run_dir / stage.REPORT_FILE).write_text(report.model_dump_json(), encoding="utf-8")
        return report

    monkeypatch.setattr(jobs, "generate_usdm", fake_generate)
    revision = _confirm(client, base)

    started = client.post(f"{base}/usdm")
    assert started.status_code == 202, started.text
    assert started.json()["status"] == "generating"
    run = _wait_idle(client, base)
    assert run["status"] == "completed", run
    assert run["stages"]["usdm"]["status"] == "done"
    assert "3 of 213 rules failed" in run["stages"]["usdm"]["detail"]
    assert run["stages"]["workbook"]["status"] == "done"  # written on the way, as Stage B would
    assert calls == [False]

    result = client.get(f"{base}/usdm").json()
    assert result["stale"] == [] and result["report"]["review_revision"] == revision
    download = client.get(f"{base}/usdm/download")
    assert download.status_code == 200 and download.json() == {"usdmVersion": "4.0.0"}
    assert "attachment" in download.headers["content-disposition"]
    assert client.get(f"{base}/usdm/report/download").json()["rules"]["rules"] == 213

    client.post(
        f"{base}/review/operations",
        json={
            "base_revision": revision,
            "operations": [{"op": "set", "sheet": "study", "field": "label", "value": "S"}],
        },
    )
    assert client.get(f"{base}/usdm").json()["stale"] == [
        f"the review has changed since (revision {revision + 1}, this USDM is from revision "
        f"{revision})"
    ]


def test_terminology_endpoints(api) -> None:  # type: ignore[no-untyped-def]
    client, _ = api
    codelist = client.get(
        "/api/terminology/codelist",
        params={"klass": "StudyArm", "attribute": "type", "q": "placebo"},
    ).json()
    assert codelist["codelist"] == "C174222" and codelist["terms"][0]["code"] == "C174268"
    assert (
        client.get(
            "/api/terminology/codelist", params={"klass": "Nope", "attribute": "x"}
        ).status_code
        == 404
    )

    resolved = client.post(
        "/api/terminology/resolve",
        json={"klass": "EligibilityCriterion", "attribute": "category", "phrase": "INCLUSION"},
    ).json()
    assert resolved["status"] == "exact" and resolved["code"] == "C25532"


def test_source_highlight(api) -> None:  # type: ignore[no-untyped-def]
    client, base = api
    found = client.get(f"{base}/source-highlight", params={"page": 5, "quote": FULL_LINE[:40]})
    assert found.status_code == 200 and found.content.startswith(PNG)
    assert found.headers["x-quote-found"] == "true"

    missing = client.get(
        f"{base}/source-highlight", params={"page": 5, "quote": "text that is not on the page"}
    )
    assert missing.headers["x-quote-found"] == "false" and missing.content.startswith(PNG)

    assert client.get(f"{base}/source-highlight", params={"page": 99}).status_code == 422


def test_review_before_extraction_is_a_conflict(api, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    client, base = api
    run_dir = next((tmp_path / "studies").glob("*/runs/*"))
    (run_dir / "extraction.json").unlink()
    resp = client.get(f"{base}/review")
    assert resp.status_code == 409 and "extraction" in json.dumps(resp.json())


def test_biomedical_concept_search_and_resolution(api) -> None:  # type: ignore[no-untyped-def]
    client, _ = api
    found = client.get("/api/terminology/biomedical-concepts", params={"q": "heart rate"}).json()
    assert found["codelist"] == "BC" and found["terms"][0]["preferred_term"] == "Heart Rate"
    exact = client.post(
        "/api/terminology/resolve-biomedical-concept",
        json={"klass": "", "attribute": "", "phrase": "pulse"},
    ).json()
    assert exact["status"] == "exact" and exact["code"]
