import io
import json
from pathlib import Path

import pytest

from backend.models.study import StudyCreate
from backend.storage.errors import (
    InvalidUploadError,
    RunNotFoundError,
    SourceNotFoundError,
    StudyNotFoundError,
)
from backend.storage.studies import StudyStore, sanitize_pdf_filename
from tests.conftest import MakePdf


def _study(store: StudyStore, name: str = "PALOMA-3 Breast Cancer") -> str:
    return store.create_study(
        StudyCreate(name=name, sponsor="Pfizer", protocol_identifier="A5481023")
    ).slug


# ----- studies -------------------------------------------------------------------------------


def test_create_study_builds_isolated_folder(store: StudyStore) -> None:
    slug = _study(store)

    assert slug == "paloma-3-breast-cancer"
    study_dir = store.root / slug
    assert (study_dir / "source").is_dir()
    assert (study_dir / "runs").is_dir()
    saved = json.loads((study_dir / "study.json").read_text(encoding="utf-8"))
    assert saved["sponsor"] == "Pfizer"
    assert saved["protocol_identifier"] == "A5481023"
    assert saved["sources"] == []


def test_duplicate_study_names_get_distinct_slugs(store: StudyStore) -> None:
    assert _study(store, "Same Name") == "same-name"
    assert _study(store, "Same Name") == "same-name-2"
    assert _study(store, "Same Name") == "same-name-3"


def test_name_without_slug_characters_falls_back(store: StudyStore) -> None:
    assert _study(store, "!!!") == "study"


@pytest.mark.parametrize("bad", ["../escape", "..", "UPPER", "a/b", "", "-leading"])
def test_invalid_slugs_are_rejected(store: StudyStore, bad: str) -> None:
    with pytest.raises(StudyNotFoundError):
        store.get_study(bad)


def test_listing_skips_corrupt_study_without_failing(store: StudyStore) -> None:
    good = _study(store, "Good")
    bad = _study(store, "Bad")
    (store.root / bad / "study.json").write_text("{not json", encoding="utf-8")

    assert [s.slug for s in store.list_studies()] == [good]


def test_listing_ignores_non_study_folders(store: StudyStore) -> None:
    _study(store)
    (store.root / "stray-folder").mkdir()
    (store.root / "notes.txt").write_text("x", encoding="utf-8")

    assert len(store.list_studies()) == 1


# ----- uploads -------------------------------------------------------------------------------


def test_upload_stores_pdf_in_study_source_folder(store: StudyStore, make_pdf: MakePdf) -> None:
    slug = _study(store)
    data = make_pdf(pages=3)

    doc = store.add_source(slug, "Pfizer A5481023 PALOMA-3 (NCT01942135).pdf", io.BytesIO(data))

    assert doc.filename == "Pfizer A5481023 PALOMA-3 (NCT01942135).pdf"
    assert doc.relative_path == f"source/{doc.filename}"
    assert doc.page_count == 3
    assert doc.size_bytes == len(data)
    on_disk = store.root / slug / "source" / doc.filename
    assert on_disk.read_bytes() == data
    assert store.get_study(slug).sources == [doc]


def test_source_paths_are_study_relative(store: StudyStore, make_pdf: MakePdf) -> None:
    slug = _study(store)
    doc = store.add_source(slug, "p.pdf", io.BytesIO(make_pdf()))

    raw = (store.root / slug / "study.json").read_text(encoding="utf-8")
    assert str(store.root) not in raw
    assert not doc.relative_path.startswith(("/", slug))


def test_identical_upload_is_deduplicated(store: StudyStore, make_pdf: MakePdf) -> None:
    slug = _study(store)
    data = make_pdf()

    first = store.add_source(slug, "a.pdf", io.BytesIO(data))
    second = store.add_source(slug, "renamed.pdf", io.BytesIO(data))

    assert second == first
    assert [p.name for p in (store.root / slug / "source").iterdir()] == ["a.pdf"]


def test_same_name_different_content_is_kept_side_by_side(
    store: StudyStore, make_pdf: MakePdf
) -> None:
    slug = _study(store)

    store.add_source(slug, "protocol.pdf", io.BytesIO(make_pdf(marker="v1")))
    second = store.add_source(slug, "protocol.pdf", io.BytesIO(make_pdf(marker="v2")))

    assert second.filename == "protocol (2).pdf"
    assert len(store.get_study(slug).sources) == 2


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "empty"),
        (b"PK\x03\x04 this is a zip", "not a PDF"),
        (b"%PDF-1.7\n garbage that is not a real pdf", "could not be opened"),
    ],
)
def test_invalid_uploads_are_rejected_and_leave_no_files(
    store: StudyStore, payload: bytes, message: str
) -> None:
    slug = _study(store)

    with pytest.raises(InvalidUploadError, match=message):
        store.add_source(slug, "bad.pdf", io.BytesIO(payload))

    assert list((store.root / slug / "source").iterdir()) == []
    assert store.get_study(slug).sources == []


def test_oversized_upload_is_rejected(tmp_path: Path, make_pdf: MakePdf) -> None:
    small = StudyStore(tmp_path / "s", max_upload_bytes=200)
    slug = small.create_study(StudyCreate(name="x")).slug

    with pytest.raises(InvalidUploadError, match="upload limit"):
        small.add_source(slug, "big.pdf", io.BytesIO(make_pdf(pages=5)))
    assert list((small.root / slug / "source").iterdir()) == []


def test_upload_to_missing_study_fails(store: StudyStore, make_pdf: MakePdf) -> None:
    with pytest.raises(StudyNotFoundError):
        store.add_source("nope", "a.pdf", io.BytesIO(make_pdf()))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("protocol.pdf", "protocol.pdf"),
        ("Protocol.PDF", "Protocol.pdf"),
        ("no_extension", "no_extension.pdf"),
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("C:\\Users\\me\\Desktop\\trial.pdf", "trial.pdf"),
        ("a<b>c:d|e?.pdf", "a_b_c_d_e.pdf"),
        ("Pfizer A5481023 (NCT01942135).pdf", "Pfizer A5481023 (NCT01942135).pdf"),
        ("   .pdf", "protocol.pdf"),
        ("x" * 300 + ".pdf", "x" * 150 + ".pdf"),
    ],
)
def test_sanitize_pdf_filename(raw: str, expected: str) -> None:
    assert sanitize_pdf_filename(raw) == expected


def test_source_path_rejects_unknown_filename(store: StudyStore) -> None:
    slug = _study(store)
    with pytest.raises(SourceNotFoundError):
        store.source_path(slug, "../study.json")


# ----- runs ----------------------------------------------------------------------------------


def test_create_run_makes_timestamped_folder(store: StudyStore, make_pdf: MakePdf) -> None:
    slug = _study(store)
    doc = store.add_source(slug, "p.pdf", io.BytesIO(make_pdf()))

    run = store.create_run(slug, doc.filename)

    run_dir = store.root / slug / "runs" / run.run_id
    assert (run_dir / "run_state.json").is_file()
    assert run.status == "created"
    assert store.get_run(slug, run.run_id) == run
    listed = store.list_studies()[0]
    assert [r.run_id for r in listed.runs] == [run.run_id]


def test_run_requires_existing_source(store: StudyStore) -> None:
    slug = _study(store)
    with pytest.raises(SourceNotFoundError):
        store.create_run(slug, "missing.pdf")


@pytest.mark.parametrize("bad", ["../../other-study", "20260101T000000Z-zzzzzz", "latest"])
def test_invalid_run_ids_are_rejected(store: StudyStore, bad: str) -> None:
    slug = _study(store)
    with pytest.raises(RunNotFoundError):
        store.get_run(slug, bad)


def test_interrupted_running_run_is_marked_failed(store: StudyStore, make_pdf: MakePdf) -> None:
    from backend.models.study import RunStatus, StageName, StageState, StageStatus

    slug = _study(store)
    doc = store.add_source(slug, "p.pdf", io.BytesIO(make_pdf()))
    run = store.create_run(slug, doc.filename)

    def running(state):  # type: ignore[no-untyped-def]
        state.status = RunStatus.RUNNING
        state.stages = {StageName.INGEST: StageState(status=StageStatus.RUNNING)}

    store.update_run(slug, run.run_id, running)

    assert store.mark_interrupted_runs() == 1
    after = store.get_run(slug, run.run_id)
    assert after.status == RunStatus.FAILED
    assert after.stages[StageName.INGEST].status == StageStatus.FAILED
    assert "interrupted" in (after.stages[StageName.INGEST].error or "")


def test_create_run_writes_config(store: StudyStore, make_pdf: MakePdf) -> None:
    slug = _study(store)
    doc = store.add_source(slug, "p.pdf", io.BytesIO(make_pdf()))
    run = store.create_run(
        slug, doc.filename, {"source_filename": doc.filename, "pdf_backend": "pymupdf"}
    )
    saved = json.loads(
        (store.root / slug / "runs" / run.run_id / "run_config.json").read_text(encoding="utf-8")
    )
    assert saved["pdf_backend"] == "pymupdf"
