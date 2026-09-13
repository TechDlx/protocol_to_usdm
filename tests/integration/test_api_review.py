import json
import shutil
from collections.abc import Iterator
from pathlib import Path

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

    ops = [
        {"op": "set", "sheet": "study", "field": "description", "value": "A synthetic trial."},
        {"op": "set", "sheet": "study", "field": "label", "value": "SYN"},
        {"op": "set", "sheet": "study", "field": "study_version", "value": "1.0"},
    ]
    state = client.post(
        f"{base}/review/operations", json={"base_revision": 0, "operations": ops}
    ).json()
    assert state["validation"]["blocking"] == 0
    confirmed = client.post(
        f"{base}/review/confirm", json={"base_revision": state["document"]["revision"]}
    )
    assert confirmed.status_code == 200 and confirmed.json()["document"]["status"] == "confirmed"
    assert client.get(base).json()["status"] == "reviewed"

    revision = confirmed.json()["document"]["revision"]
    client.post(
        f"{base}/review/operations",
        json={
            "base_revision": revision,
            "operations": [{"op": "set", "sheet": "study", "field": "label", "value": "S"}],
        },
    )
    assert client.get(base).json()["status"] == "awaiting_review"


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
