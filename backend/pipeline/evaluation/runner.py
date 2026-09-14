"""Stage A for evaluation: parse and extract a protocol with review disabled, then write the
workbook straight from the extraction.

This is the one place a workbook is written from unreviewed extraction, and it is written into the
evaluation workspace only: the point is to measure what the model and the deterministic code
produce before a reviewer corrects anything. Blocking review issues (empty required cells,
unresolved terminology) are written as they are.

The workspace is an ordinary run folder, so evaluation is resumable like any run: an agent whose
inputs are unchanged is not sent to the model again, and changed post-processing is re-applied to
the stored model output for free.
"""

import asyncio
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from backend.models.extraction import AgentRun, AgentStatus, Extraction, LlmUsage
from backend.models.run_config import RunConfig
from backend.models.study import StudyMeta
from backend.pipeline.extract import AgentUpdate, load_extraction, run_extraction
from backend.pipeline.ingest import assist_mapping, run_ingestion, segment
from backend.pipeline.llm import LlmRequest, StructuredLlm
from backend.pipeline.terminology.ct import CtResolver
from backend.pipeline.workbook.writer import write_workbook
from backend.storage.fs import write_model
from backend.storage.studies import RUN_CONFIG_FILE

UNREVIEWED_WORKBOOK = "workbook/unreviewed.xlsx"
# The study name a user would enter for the reference protocol. It becomes the generated study
# name, which is an identifier and not scored.
EVAL_STUDY_NAME = "CDISC Pilot"

OutT = TypeVar("OutT", bound=BaseModel)


class EvaluationError(RuntimeError):
    pass


class UnavailableLlm:
    """Stands in when no API key is configured: agents whose stored output is current are still
    reused, and any agent that needs the model fails with a clear message."""

    async def extract(self, request: LlmRequest, output_model: type[OutT]) -> tuple[OutT, LlmUsage]:
        raise EvaluationError(
            "ANTHROPIC_API_KEY is not configured, and this agent's stored output is not current"
        )


def run_stage_a(
    run_dir: Path,
    pdf: Path,
    llm: StructuredLlm,
    resolver: CtResolver,
    force: bool = False,
    on_update: AgentUpdate = lambda _run: None,
) -> Extraction:
    """Parse, segment and extract `pdf` in `run_dir` (created or resumed)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    config_path = run_dir / RUN_CONFIG_FILE
    config = (
        RunConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
        if config_path.is_file()
        else RunConfig(source_filename=pdf.name)
    )
    if config.ct_version is None:
        config.ct_version = resolver.version
    elif config.ct_version != resolver.version:
        raise EvaluationError(
            f"the workspace is pinned to CDISC CT {config.ct_version} but the installed usdm4 "
            f"provides {resolver.version}; use a new workspace"
        )
    write_model(config_path, config)

    ingested = run_ingestion(run_dir, pdf, config)
    mapping = ingested.mapping
    if config.mapping_assist != "off":
        try:  # as in a run: Claude's mapping when available, else the title-based one
            if assist_mapping(run_dir, ingested.document, config, llm) is not None:
                mapping = segment(run_dir, ingested.document, config)
        except Exception:
            mapping = ingested.mapping
    now = datetime.now(UTC)
    study = StudyMeta(slug="evaluation", name=EVAL_STUDY_NAME, created_at=now, updated_at=now)
    return asyncio.run(
        run_extraction(
            run_dir,
            ingested.document,
            mapping,
            config,
            study,
            llm,
            resolver,
            force=force,
            on_update=on_update,
        )
    )


def existing_extraction(run_dir: Path) -> Extraction:
    extraction = load_extraction(run_dir)
    if extraction is None:
        raise EvaluationError(f"{run_dir} has no extraction.json; run extraction first")
    return extraction


def write_unreviewed_workbook(run_dir: Path, extraction: Extraction) -> tuple[Path, list[str]]:
    """The workbook exactly as extracted. Returns its path and the writer's warnings."""
    path = run_dir / UNREVIEWED_WORKBOOK
    summary = write_workbook(extraction.sheets, path)
    return path, summary.warnings


def extraction_summary(extraction: Extraction, run_dir: Path) -> dict[str, object]:
    runs: list[AgentRun] = list(extraction.agents.values())
    statuses = Counter(run.status.value for run in runs)
    # DONE and FAILED agents called the model in the run that produced this extraction; SKIPPED
    # ones reused (or re-processed) stored output and carry the usage of the original call.
    called = [r for r in runs if r.usage is not None and r.status != AgentStatus.SKIPPED]
    stored = [r for r in runs if r.usage is not None]
    models = sorted({r.usage.model for r in stored if r.usage is not None})
    config_path = run_dir / RUN_CONFIG_FILE
    model = (
        RunConfig.model_validate_json(config_path.read_text(encoding="utf-8")).extraction_model
        if config_path.is_file()
        else ", ".join(models)
    )
    return {
        "model": model,
        "agents": ", ".join(f"{n} {status}" for status, n in sorted(statuses.items())),
        "model calls this run": len(called),
        "reprocessed from stored output": sum(1 for r in runs if r.reprocessed),
        "cost this run (USD)": round(sum(r.usage.cost_usd for r in called if r.usage), 4),
        "cost of the stored outputs (USD)": round(
            sum(r.usage.cost_usd for r in stored if r.usage), 4
        ),
        "failed agents": ", ".join(sorted(r.sheet for r in runs if r.status == AgentStatus.FAILED)),
    }
