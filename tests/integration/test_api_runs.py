import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from tests.fixtures import synthetic_protocol


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(_env_file=None, studies_root=tmp_path / "studies", max_upload_mb=5)  # type: ignore[call-arg]
    with TestClient(create_app(settings)) as c:  # context manager runs the lifespan
        yield c  # type: ignore[misc]


def _wait_for(
    client: TestClient, url: str, done: set[str], timeout: float = 60
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state: dict[str, object] = client.get(url).json()
        if state["status"] in done:
            return state
        time.sleep(0.2)
    raise AssertionError(f"run did not finish: {state}")


@pytest.fixture
def study_with_pdf(client: TestClient, tmp_path: Path) -> tuple[str, str]:
    slug = client.post("/api/studies", json={"name": "Synthetic"}).json()["slug"]
    pdf = synthetic_protocol.build(tmp_path / "synthetic protocol.pdf")
    uploaded = client.post(
        f"/api/studies/{slug}/sources",
        files={"file": (pdf.name, pdf.read_bytes(), "application/pdf")},
    )
    return slug, uploaded.json()["filename"]


def test_run_parses_and_serves_artefacts(
    client: TestClient, study_with_pdf: tuple[str, str]
) -> None:
    slug, filename = study_with_pdf

    created = client.post(
        f"/api/studies/{slug}/runs", json={"source_filename": filename, "page_image_dpi": 50}
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]
    base = f"/api/studies/{slug}/runs/{run_id}"

    state = _wait_for(client, base, {"parsed", "failed"})
    assert state["status"] == "parsed", state
    stages = state["stages"]
    assert isinstance(stages, dict)
    assert stages["ingest"]["status"] == "done"
    assert stages["segment"]["status"] == "done"

    doc = client.get(f"{base}/document").json()
    assert doc["stats"]["soa_pages"] == [3]
    mapping = client.get(f"{base}/section-mapping").json()
    assert any(a["m11_number"] == "1.3" for a in mapping["assignments"])
    image = client.get(f"{base}/pages/page-0003.png")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"


def test_rerun_skips_parse_when_output_current(
    client: TestClient, study_with_pdf: tuple[str, str]
) -> None:
    slug, filename = study_with_pdf
    run_id = client.post(
        f"/api/studies/{slug}/runs", json={"source_filename": filename, "page_image_dpi": 50}
    ).json()["run_id"]
    base = f"/api/studies/{slug}/runs/{run_id}"
    _wait_for(client, base, {"parsed", "failed"})

    assert client.post(f"{base}/ingest").status_code == 202
    state = _wait_for(client, base, {"parsed", "failed"})
    assert state["stages"]["ingest"]["status"] == "skipped"  # type: ignore[index]


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"source_filename": "missing.pdf"}, 422),
        ({"source_filename": "{file}", "pdf_backend": "no-such-backend"}, 422),
        ({"source_filename": "{file}", "page_image_dpi": 5000}, 422),
    ],
)
def test_run_creation_validation(
    client: TestClient, study_with_pdf: tuple[str, str], body: dict[str, object], code: int
) -> None:
    slug, filename = study_with_pdf
    payload = {k: (filename if v == "{file}" else v) for k, v in body.items()}
    assert client.post(f"/api/studies/{slug}/runs", json=payload).status_code == code


@pytest.mark.parametrize(
    "path",
    [
        "/api/studies/nope/runs/20260101T000000Z-abcdef",
        "/api/studies/{slug}/runs/20260101T000000Z-abcdef/document",
        "/api/studies/{slug}/runs/not-a-run-id",
    ],
)
def test_unknown_runs_404(client: TestClient, study_with_pdf: tuple[str, str], path: str) -> None:
    slug, _ = study_with_pdf
    assert client.get(path.format(slug=slug)).status_code == 404


def test_page_image_path_traversal_rejected(
    client: TestClient, study_with_pdf: tuple[str, str]
) -> None:
    slug, filename = study_with_pdf
    run_id = client.post(
        f"/api/studies/{slug}/runs", json={"source_filename": filename, "page_image_dpi": 50}
    ).json()["run_id"]
    base = f"/api/studies/{slug}/runs/{run_id}"
    _wait_for(client, base, {"parsed", "failed"})
    assert client.get(f"{base}/pages/..%2Frun_config.json").status_code == 404
    assert client.get(f"{base}/pages/run_state.json").status_code == 404
