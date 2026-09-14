import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.pipeline.jobs import JobRunner
from tests.fixtures import synthetic_protocol
from tests.fixtures.fake_llm import FakeLlm, synthetic_responders


def _wait(client: TestClient, url: str, timeout: float = 90) -> dict:  # type: ignore[type-arg]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get(url).json()
        if state["status"] != "running":
            return state  # type: ignore[no-any-return]
        time.sleep(0.2)
    raise AssertionError("run did not finish")


@pytest.fixture
def app_and_client(tmp_path: Path) -> Iterator[tuple[object, TestClient]]:
    settings = Settings(_env_file=None, studies_root=tmp_path / "studies", max_upload_mb=5)  # type: ignore[call-arg]
    app = create_app(settings)
    app.state.jobs = JobRunner(app.state.store, llm_factory=lambda: FakeLlm(synthetic_responders()))
    with TestClient(app) as client:
        yield app, client


@pytest.fixture
def parsed_run(app_and_client, tmp_path: Path) -> str:  # type: ignore[no-untyped-def]
    _, client = app_and_client
    slug = client.post("/api/studies", json={"name": "Synthetic"}).json()["slug"]
    pdf = synthetic_protocol.build(tmp_path / "s.pdf")
    filename = client.post(
        f"/api/studies/{slug}/sources",
        files={"file": (pdf.name, pdf.read_bytes(), "application/pdf")},
    ).json()["filename"]
    run_id = client.post(
        f"/api/studies/{slug}/runs", json={"source_filename": filename, "page_image_dpi": 50}
    ).json()["run_id"]
    base = f"/api/studies/{slug}/runs/{run_id}"
    assert _wait(client, base)["status"] == "parsed"
    return base


def test_extraction_runs_and_serves_artefacts(app_and_client, parsed_run: str) -> None:  # type: ignore[no-untyped-def]
    _, client = app_and_client
    resp = client.post(f"{parsed_run}/extract", json={})
    assert resp.status_code == 202, resp.text

    state = _wait(client, parsed_run)
    assert state["status"] == "awaiting_review"
    assert state["stages"]["extract"]["status"] == "failed"  # the arms agent has no sections
    assert state["agents"]["study"]["status"] == "done"
    assert state["agents"]["study_design_arms"]["status"] == "failed"

    extraction = client.get(f"{parsed_run}/extraction").json()
    assert (
        extraction["sheets"]["study"]["official_title"]["value"] == "A Phase 3 Trial of Examplumab"
    )
    assert client.get(f"{parsed_run}/provenance").status_code == 200
    assert client.get(f"{parsed_run}/reference-validation").json()["valid"] is True

    config = client.get(f"{parsed_run}/document")  # still served after extraction
    assert config.status_code == 200


def test_ct_version_is_pinned_in_run_config(
    app_and_client, parsed_run: str, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    import json

    _, client = app_and_client
    client.post(f"{parsed_run}/extract", json={})
    _wait(client, parsed_run)
    run_dir = next((tmp_path / "studies").glob("*/runs/*"))
    config = json.loads((run_dir / "run_config.json").read_text(encoding="utf-8"))
    assert config["ct_version"]

    config["ct_version"] = "1999-01-01"
    (run_dir / "run_config.json").write_text(json.dumps(config), encoding="utf-8")
    client.post(f"{parsed_run}/extract", json={"force": True})
    state = _wait(client, parsed_run)
    assert state["status"] == "failed"
    assert "pinned to CDISC CT 1999-01-01" in state["stages"]["extract"]["error"]


@pytest.mark.parametrize(
    ("body", "detail"),
    [({"sheets": ["no_such_sheet"]}, "unknown sheets")],
)
def test_extraction_request_validation(
    app_and_client, parsed_run: str, body: dict, detail: str
) -> None:  # type: ignore[no-untyped-def,type-arg]
    _, client = app_and_client
    resp = client.post(f"{parsed_run}/extract", json=body)
    assert resp.status_code == 422 and detail in resp.json()["detail"]


def test_extraction_without_api_key_is_refused(app_and_client, parsed_run: str) -> None:  # type: ignore[no-untyped-def]
    app, client = app_and_client
    app.state.jobs = JobRunner(app.state.store, llm_factory=None)
    resp = client.post(f"{parsed_run}/extract", json={})
    assert resp.status_code == 422 and "ANTHROPIC_API_KEY" in resp.json()["detail"]


def test_agents_endpoint_lists_sheets(app_and_client) -> None:  # type: ignore[no-untyped-def]
    _, client = app_and_client
    sheets = {a["sheet"] for a in client.get("/api/agents").json()}
    from backend.pipeline.agents.registry import AGENTS

    assert sheets == set(AGENTS) and {"study", "estimands", "abbreviations"} <= sheets


def test_reviewer_maps_a_section_and_extraction_sees_which_agents_changed(
    app_and_client, parsed_run: str, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    _, client = app_and_client
    client.post(f"{parsed_run}/extract", json={})
    _wait(client, parsed_run)
    assert client.get(f"{parsed_run}/extraction/input-changes").json() == []
    template = {s["number"]: s for s in client.get("/api/m11/template").json()}
    assert template["4.1"]["level"] == 2

    section = "sec-2.2"  # inherits "5" from its parent; the arms agent reads 4.1
    assert client.put(f"{parsed_run}/section-mapping/{section}", json={}).status_code == 422
    bad = client.put(f"{parsed_run}/section-mapping/{section}", json={"m11_number": "99"})
    assert bad.status_code == 422
    unknown = client.put(f"{parsed_run}/section-mapping/nope", json={"m11_number": "4.1"})
    assert unknown.status_code == 404

    mapping = client.put(f"{parsed_run}/section-mapping/{section}", json={"m11_number": "4.1"})
    assert mapping.status_code == 200, mapping.text
    assignment = next(a for a in mapping.json()["assignments"] if a["section_id"] == section)
    assert (assignment["m11_number"], assignment["method"]) == ("4.1", "reviewer")
    coverage = {c["m11_number"]: c for c in mapping.json()["coverage"]}
    assert coverage["4.1"]["status"] == "found" and coverage["4.1"]["section_ids"] == [section]
    assert "1 mapped by a reviewer" in client.get(parsed_run).json()["stages"]["segment"]["detail"]

    changes = {c["sheet"]: c for c in client.get(f"{parsed_run}/extraction/input-changes").json()}
    assert changes["study_design_arms"]["added"] == [section]
    assert any(section in c["removed"] for c in changes.values())  # the population agents

    # Re-running ingestion keeps the reviewer's mapping.
    assert client.post(f"{parsed_run}/ingest").status_code == 202
    _wait(client, parsed_run)
    kept = client.get(f"{parsed_run}/section-mapping").json()
    assert (
        next(a for a in kept["assignments"] if a["section_id"] == section)["method"] == "reviewer"
    )

    cleared = client.delete(f"{parsed_run}/section-mapping/{section}")
    assert cleared.status_code == 200
    back = next(a for a in cleared.json()["assignments"] if a["section_id"] == section)
    assert back["method"] == "inherited" and not back["reviewer_override"]
    assert client.get(f"{parsed_run}/extraction/input-changes").json() == []
    assert client.delete(f"{parsed_run}/section-mapping/{section}").status_code == 404

    audit_path = next((tmp_path / "studies").glob("*/runs/*/section_mapping_audit.jsonl"))
    audit = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert [(e["action"], e["old"]["m11_number"], e["new"]["m11_number"]) for e in audit] == [
        ("set", "5", "4.1"),
        ("clear", "4.1", "5"),
    ]
