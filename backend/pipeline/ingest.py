"""Stage A1 + A2: parse the protocol PDF, then map its sections onto ICH M11.

Outputs, all inside the run folder:
    page_images/page-NNNN.png
    parsed_document.json
    section_mapping.json

Resumable: parsing is skipped when parsed_document.json already exists for the same source hash,
backend, backend version and image DPI. Segmentation is cheap and deterministic, so it always
re-runs; that keeps section_mapping.json in step with template or threshold changes. A reviewer's
overrides (section_overrides.json) are applied on every run, so re-segmenting never loses them.

Command line (useful for the evaluation harness and for eyeballing a protocol):
    uv run python -m backend.pipeline.ingest <protocol.pdf> <output-dir>
"""

import argparse
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from pydantic import ValidationError

from backend.models.document import ParsedDocument, SourceInfo
from backend.models.run_config import RunConfig
from backend.models.segmentation import SectionMapping
from backend.pipeline.extractors.registry import get_extractor
from backend.pipeline.segmentation.m11 import map_sections
from backend.pipeline.segmentation.overrides import load_overrides
from backend.storage.fs import write_model

log = logging.getLogger(__name__)

PARSED_DOCUMENT_FILE = "parsed_document.json"
SECTION_MAPPING_FILE = "section_mapping.json"
PAGE_IMAGES_DIR = "page_images"


@dataclass
class IngestResult:
    document: ParsedDocument
    mapping: SectionMapping
    parse_skipped: bool


def source_info(pdf_path: Path) -> SourceInfo:
    data = pdf_path.read_bytes()
    with pymupdf.open(stream=data, filetype="pdf") as doc:  # type: ignore[no-untyped-call]
        pages = int(doc.page_count)
    return SourceInfo(
        filename=pdf_path.name, sha256=hashlib.sha256(data).hexdigest(), page_count=pages
    )


def load_parsed_document(run_dir: Path) -> ParsedDocument | None:
    path = run_dir / PARSED_DOCUMENT_FILE
    if not path.is_file():
        return None
    try:
        return ParsedDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError:
        log.warning(
            "existing parsed_document.json is invalid; re-parsing", extra={"path": str(path)}
        )
        return None


def _reusable(
    existing: ParsedDocument | None, source: SourceInfo, config: RunConfig, version: str
) -> bool:
    return (
        existing is not None
        and existing.source.sha256 == source.sha256
        and existing.extractor.name == config.pdf_backend
        and existing.extractor.version == version
        and existing.extractor.page_image_dpi == config.page_image_dpi
    )


def parse(
    run_dir: Path, pdf_path: Path, config: RunConfig, force: bool = False
) -> tuple[ParsedDocument, bool]:
    extractor = get_extractor(config.pdf_backend)
    source = source_info(pdf_path)
    existing = None if force else load_parsed_document(run_dir)
    if _reusable(existing, source, config, extractor.version):
        assert existing is not None
        log.info("parse skipped: output current", extra={"run_dir": str(run_dir)})
        return existing, True

    # Write the document last: its presence marks the stage complete, so a crash mid-way
    # (e.g. while rendering page images) simply re-runs next time.
    (run_dir / PARSED_DOCUMENT_FILE).unlink(missing_ok=True)
    document = extractor.extract(
        pdf_path, source, run_dir / PAGE_IMAGES_DIR, PAGE_IMAGES_DIR, config.page_image_dpi
    )
    write_model(run_dir / PARSED_DOCUMENT_FILE, document)
    log.info(
        "parse complete",
        extra={
            "run_dir": str(run_dir),
            "pages": len(document.pages),
            "sections": len(document.sections),
            "tables": len(document.tables),
            "seconds": document.stats.elapsed_seconds,
        },
    )
    return document, False


def segment(run_dir: Path, document: ParsedDocument, config: RunConfig) -> SectionMapping:
    mapping = map_sections(
        document,
        review_threshold=config.segmentation_review_threshold,
        overrides=load_overrides(run_dir).overrides,
    )
    write_model(run_dir / SECTION_MAPPING_FILE, mapping)
    log.info(
        "segmentation complete",
        extra={
            "run_dir": str(run_dir),
            "needs_review": sum(a.needs_review for a in mapping.assignments),
            "reviewer_overrides": sum(a.reviewer_override for a in mapping.assignments),
            "ignored_overrides": len(mapping.ignored_overrides),
            "sections": len(mapping.assignments),
        },
    )
    return mapping


def run_ingestion(
    run_dir: Path, pdf_path: Path, config: RunConfig, force: bool = False
) -> IngestResult:
    document, skipped = parse(run_dir, pdf_path, config, force=force)
    return IngestResult(document, segment(run_dir, document, config), skipped)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("pdf", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--backend", default="pymupdf")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--force", action="store_true", help="re-parse even if output is current")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    config = RunConfig(
        source_filename=args.pdf.name, pdf_backend=args.backend, page_image_dpi=args.dpi
    )
    result = run_ingestion(args.out, args.pdf, config, force=args.force)
    doc, mapping = result.document, result.mapping
    print(
        f"pages={len(doc.pages)} sections={len(doc.sections)} tables={len(doc.tables)} "
        f"soa_pages={doc.stats.soa_pages} parse_skipped={result.parse_skipped}"
    )
    flagged = sum(a.needs_review for a in mapping.assignments)
    print(f"needs_review={flagged}/{len(mapping.assignments)}")
    for warning in doc.warnings:
        print(f"warning: {warning}")


if __name__ == "__main__":
    main()
