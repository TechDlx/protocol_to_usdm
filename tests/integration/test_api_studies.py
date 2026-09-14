from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from tests.conftest import MakePdf


@pytest.fixture
def studies_root(tmp_path: Path) -> Path:
    return tmp_path / "studies"


@pytest.fixture
def client(studies_root: Path) -> TestClient:
    settings = Settings(_env_file=None, studies_root=studies_root, max_upload_mb=5)  # type: ignore[call-arg]
    return TestClient(create_app(settings))


def test_create_upload_and_list_round_trip(
    client: TestClient, studies_root: Path, make_pdf: MakePdf
) -> None:
    created = client.post(
        "/api/studies",
        json={"name": "PALOMA-3", "sponsor": "Pfizer", "protocol_identifier": "A5481023"},
    )
    assert created.status_code == 201
    slug = created.json()["slug"]

    pdf = make_pdf(pages=4)
    uploaded = client.post(
        f"/api/studies/{slug}/sources",
        files={"file": ("Pfizer A5481023 PALOMA-3 (NCT01942135).pdf", pdf, "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    source = uploaded.json()
    assert source["page_count"] == 4

    # The file is on disk exactly where the folder contract says it should be.
    on_disk = studies_root / slug / "source" / source["filename"]
    assert on_disk.read_bytes() == pdf

    listing = client.get("/api/studies").json()
    assert [s["slug"] for s in listing] == [slug]
    assert listing[0]["sources"][0]["filename"] == source["filename"]
    assert listing[0]["runs"] == []

    fetched = client.get(f"/api/studies/{slug}/sources/{source['filename']}")
    assert fetched.status_code == 200
    assert fetched.content == pdf


def test_non_pdf_upload_returns_422(client: TestClient) -> None:
    slug = client.post("/api/studies", json={"name": "x"}).json()["slug"]
    resp = client.post(
        f"/api/studies/{slug}/sources", files={"file": ("notes.pdf", b"hello", "application/pdf")}
    )
    assert resp.status_code == 422
    assert "not a PDF" in resp.json()["detail"]


def test_blank_study_name_is_rejected(client: TestClient) -> None:
    assert client.post("/api/studies", json={"name": "   "}).status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/api/studies/does-not-exist",
        "/api/studies/does-not-exist/sources/a.pdf",
    ],
)
def test_unknown_resources_return_404(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404


def test_health_reports_key_presence_without_values(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert set(body) == {"status", "anthropic_key_configured", "cdisc_key_configured"}


def test_api_json_responses_are_not_cached(client: TestClient) -> None:
    assert client.get("/api/health").headers["cache-control"] == "no-store"
    assert client.get("/api/studies").headers["cache-control"] == "no-store"
