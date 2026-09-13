"""A run folder with the synthetic protocol parsed and extracted (fake model), ready to review."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from backend.models.run_config import RunConfig
from backend.models.study import StudyMeta
from backend.pipeline.extract import run_extraction
from backend.pipeline.ingest import run_ingestion
from backend.pipeline.terminology.ct import get_ct_resolver
from backend.storage.fs import write_model
from backend.storage.studies import RUN_CONFIG_FILE
from tests.fixtures import synthetic_protocol
from tests.fixtures.fake_llm import FakeLlm, synthetic_responders

STUDY = StudyMeta(
    slug="synthetic",
    name="Synthetic Study",
    created_at=datetime.now(UTC),
    updated_at=datetime.now(UTC),
)


def build_extracted_run(root: Path) -> tuple[Path, Path]:
    """Returns (run_dir, pdf_path)."""
    pdf = synthetic_protocol.build(root / "synthetic.pdf")
    run_dir = root / "run"
    run_dir.mkdir(parents=True)
    config = RunConfig(source_filename=pdf.name, page_image_dpi=50)
    write_model(run_dir / RUN_CONFIG_FILE, config)
    result = run_ingestion(run_dir, pdf, config)
    asyncio.run(
        run_extraction(
            run_dir,
            result.document,
            result.mapping,
            config,
            STUDY,
            FakeLlm(synthetic_responders()),
            get_ct_resolver(),
        )
    )
    return run_dir, pdf
