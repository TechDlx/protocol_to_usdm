"""End-to-end ingestion + segmentation on the generated fixture and on the CDISC Pilot protocol."""

import json
from pathlib import Path

import pytest

from backend.models.document import SectionKind
from backend.models.run_config import RunConfig
from backend.pipeline.ingest import PARSED_DOCUMENT_FILE, SECTION_MAPPING_FILE, run_ingestion
from tests.fixtures import synthetic_protocol

REPO = Path(__file__).resolve().parents[2]
PILOT_PDF = REPO / "goldstandard" / "cdisc_pilot" / "CDISC_Pilot_Study.pdf"


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("synthetic")
    pdf = synthetic_protocol.build(root / "synthetic.pdf")
    return pdf, root / "run"


def _ingest(pdf: Path, run_dir: Path, **kwargs: object):  # type: ignore[no-untyped-def]
    run_dir.mkdir(parents=True, exist_ok=True)
    config = RunConfig(source_filename=pdf.name, page_image_dpi=50)
    return run_ingestion(run_dir, pdf, config, **kwargs)  # type: ignore[arg-type]


def test_synthetic_protocol_structure(synthetic: tuple[Path, Path]) -> None:
    pdf, run_dir = synthetic
    doc = _ingest(pdf, run_dir, force=True).document
    by_id = {s.id: s for s in doc.sections}

    assert list(by_id) == [
        "title-page",
        "toc",
        "fm-schedule-of-activities",
        "sec-1",
        "sec-1.1",
        "sec-2",
        "sec-2.1",
        "sec-2.2",
        "sec-3",
        "sec-3.1",
        "app-appendix-1-list-of-abbreviations",
    ]
    assert by_id["sec-3"].title == "Statistical Methods"  # the bold "3." list item was rejected
    assert doc.stats.rejected_heading_candidates == 1
    assert by_id["sec-2.2"].title.endswith(synthetic_protocol.WRAPPED_TITLE_2)
    assert by_id["sec-1.1"].parent_id == "sec-1"
    assert by_id["app-appendix-1-list-of-abbreviations"].kind == SectionKind.APPENDIX
    assert [p.number for p in doc.pages if p.is_toc_page] == [2]


def test_running_header_and_footer_do_not_leak(synthetic: tuple[Path, Path]) -> None:
    pdf, run_dir = synthetic
    doc = _ingest(pdf, run_dir).document
    all_text = "\n".join(s.text for s in doc.sections)
    assert synthetic_protocol.HEADER_TEXT not in all_text
    assert "Page 4" not in all_text


def test_soa_grid_detected_with_structure(synthetic: tuple[Path, Path]) -> None:
    pdf, run_dir = synthetic
    doc = _ingest(pdf, run_dir).document

    (table,) = doc.tables
    assert table.is_soa_candidate and table.needs_vision
    assert table.section_id == "fm-schedule-of-activities"
    assert table.cells == synthetic_protocol.SOA_ROWS
    assert doc.stats.soa_pages == [3]
    assert f"[[TABLE {table.id}]]" in doc.section("fm-schedule-of-activities").text


def test_synthetic_protocol_m11_mapping(synthetic: tuple[Path, Path]) -> None:
    pdf, run_dir = synthetic
    mapping = _ingest(pdf, run_dir).mapping
    expected = {
        "title-page": "0",
        "fm-schedule-of-activities": "1.3",
        "sec-1": "2",
        "sec-2": "5",
        "sec-2.1": "5.2",
        "sec-3": "10",
        "sec-3.1": "10.11",
        "app-appendix-1-list-of-abbreviations": "13",
    }
    assert {sid: mapping.assignment(sid).m11_number for sid in expected} == expected
    assert mapping.assignment("toc").m11_number is None


def test_outputs_written_and_page_images_rendered(synthetic: tuple[Path, Path]) -> None:
    pdf, run_dir = synthetic
    doc = _ingest(pdf, run_dir).document
    assert (
        json.loads((run_dir / PARSED_DOCUMENT_FILE).read_text(encoding="utf-8"))["source"][
            "page_count"
        ]
        == 6
    )
    assert (run_dir / SECTION_MAPPING_FILE).is_file()
    assert all((run_dir / p.image_path).is_file() for p in doc.pages)


def test_parse_is_resumable(tmp_path: Path) -> None:
    pdf = synthetic_protocol.build(tmp_path / "p.pdf")
    run_dir = tmp_path / "run"

    assert _ingest(pdf, run_dir).parse_skipped is False
    assert _ingest(pdf, run_dir).parse_skipped is True
    assert _ingest(pdf, run_dir, force=True).parse_skipped is False

    run_dir.mkdir(exist_ok=True)
    changed = RunConfig(source_filename=pdf.name, page_image_dpi=60)
    assert run_ingestion(run_dir, pdf, changed).parse_skipped is False


@pytest.mark.skipif(not PILOT_PDF.is_file(), reason="CDISC Pilot protocol not present")
def test_cdisc_pilot_regression(tmp_path: Path) -> None:
    """Anchors on the gold-standard protocol: structure and mapping that must not regress."""
    result = _ingest(PILOT_PDF, tmp_path / "run")
    doc, mapping = result.document, result.mapping
    ids = [s.id for s in doc.sections]

    assert len(ids) == len(set(ids))
    assert doc.stats.soa_pages == [53, 54]
    # Attachment internal numbering ("7. Orientation" in ADAS-Cog) must not become sections.
    assert not any(s.number and s.number.split(".")[0] in {"7", "8", "9"} for s in doc.sections)
    assert sum(1 for s in doc.sections if s.kind == SectionKind.APPENDIX) == 9
    assert doc.section("sec-3.9.3.4").title == "Other Safety Measures"

    expected = {
        "sec-2.1": "3.1",
        "sec-3.4": "5",
        "sec-3.4.2.1": "5.2",
        "sec-3.4.2.2": "5.3",
        "sec-3.4.4": "10.11",
        "sec-3.7": "6.7.3",
        "sec-3.8": "6.10",
        "sec-3.9.2": "8.5",
        "sec-4": "10",
        "sec-4.3.5": "10.10",
        "sec-5.1": "11.3",
        "sec-6": "14",
    }
    assert {sid: mapping.assignment(sid).m11_number for sid in expected} == expected
    soa_section = next(s for s in doc.sections if s.title.startswith("Protocol Attachment LZZT.1"))
    assert mapping.assignment(soa_section.id).m11_number == "1.3"
