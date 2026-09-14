"""Background execution of pipeline stages with status persisted to run_state.json."""

import asyncio
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from backend.models.extraction import AgentRun, AgentStatus
from backend.models.run_config import RunConfig
from backend.models.segmentation import SectionMapping
from backend.models.study import RunState, RunStatus, StageName, StageState, StageStatus
from backend.pipeline.agents.registry import AGENTS
from backend.pipeline.extract import run_extraction
from backend.pipeline.ingest import SECTION_MAPPING_FILE, load_parsed_document, parse, segment
from backend.pipeline.llm import StructuredLlm
from backend.pipeline.terminology.ct import CtResolver, get_ct_resolver
from backend.pipeline.usdm_gen.stage import UsdmReport, check_ready, generate_usdm
from backend.storage.fs import write_model
from backend.storage.studies import RUN_CONFIG_FILE, StudyStore

log = logging.getLogger(__name__)

LlmFactory = Callable[[], StructuredLlm]


class RunAlreadyActiveError(RuntimeError):
    pass


class ExtractionNotReadyError(RuntimeError):
    """The run cannot extract yet (not parsed, no API key, unknown sheet)."""


def _now() -> datetime:
    return datetime.now(UTC)


class JobRunner:
    def __init__(
        self,
        store: StudyStore,
        llm_factory: LlmFactory | None = None,
        resolver_factory: Callable[[], CtResolver] = get_ct_resolver,
        max_workers: int = 2,
    ) -> None:
        self._store = store
        self._llm_factory = llm_factory
        self._resolver_factory = resolver_factory
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="pipeline")
        self._active: dict[tuple[str, str], Future[None]] = {}
        self._lock = threading.Lock()

    def is_active(self, slug: str, run_id: str) -> bool:
        with self._lock:
            future = self._active.get((slug, run_id))
            return future is not None and not future.done()

    def _submit(
        self, slug: str, run_id: str, queued: Callable[[RunState], None], work: Callable[[], None]
    ) -> None:
        key = (slug, run_id)
        with self._lock:
            current = self._active.get(key)
            if current is not None and not current.done():
                raise RunAlreadyActiveError(f"run {run_id} is already processing")
            self._store.update_run(slug, run_id, queued)
            self._active[key] = self._pool.submit(work)

    def start_ingestion(self, slug: str, run_id: str, force: bool = False) -> None:
        def queued(state: RunState) -> None:
            state.status = RunStatus.RUNNING
            state.stages[StageName.INGEST] = StageState()
            state.stages[StageName.SEGMENT] = StageState()

        self._submit(slug, run_id, queued, lambda: self._ingest(slug, run_id, force))

    def start_extraction(
        self, slug: str, run_id: str, sheets: list[str] | None = None, force: bool = False
    ) -> None:
        unknown = sorted(set(sheets or []) - set(AGENTS))
        if unknown:
            raise ExtractionNotReadyError(f"unknown sheets: {', '.join(unknown)}")
        if self._llm_factory is None:
            raise ExtractionNotReadyError("ANTHROPIC_API_KEY is not configured")
        run_dir = self._store.run_dir(slug, run_id)
        if load_parsed_document(run_dir) is None or not (run_dir / SECTION_MAPPING_FILE).is_file():
            raise ExtractionNotReadyError("the protocol must be parsed before extraction")

        def queued(state: RunState) -> None:
            state.status = RunStatus.RUNNING
            state.stages[StageName.EXTRACT] = StageState()
            for sheet in sheets or list(AGENTS):
                state.agents[sheet] = AgentRun(sheet=sheet, status=AgentStatus.QUEUED)

        self._submit(slug, run_id, queued, lambda: self._extract(slug, run_id, sheets, force))

    def start_usdm(self, slug: str, run_id: str, force: bool = False) -> None:
        """Stage C in the background: import the workbook (about 20 s) and validate the JSON."""
        check_ready(self._store.run_dir(slug, run_id), slug)

        def queued(state: RunState) -> None:
            state.status = RunStatus.GENERATING
            state.stages[StageName.USDM] = StageState()

        self._submit(slug, run_id, queued, lambda: self._usdm(slug, run_id, force))

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ----- workers -----------------------------------------------------------------------------

    def _set_stage(self, slug: str, run_id: str, stage: StageName, **changes: object) -> None:
        def apply(state: RunState) -> None:
            current = state.stages.setdefault(stage, StageState())
            for field, value in changes.items():
                setattr(current, field, value)

        self._store.update_run(slug, run_id, apply)

    def _fail(self, slug: str, run_id: str, stage: StageName, exc: Exception) -> None:
        log.exception("stage failed", extra={"study": slug, "run": run_id, "stage": stage})
        message = f"{type(exc).__name__}: {exc}"

        def failed(state: RunState) -> None:
            state.status = RunStatus.FAILED
            current = state.stages.setdefault(stage, StageState())
            current.status = StageStatus.FAILED
            current.error = message
            current.finished_at = _now()

        try:
            self._store.update_run(slug, run_id, failed)
        except Exception:
            log.exception("could not record stage failure", extra={"run": run_id})

    def _config(self, slug: str, run_id: str) -> RunConfig:
        run_dir = self._store.run_dir(slug, run_id)
        return RunConfig.model_validate_json(
            (run_dir / RUN_CONFIG_FILE).read_text(encoding="utf-8")
        )

    def _ingest(self, slug: str, run_id: str, force: bool) -> None:
        stage = StageName.INGEST
        try:
            run_dir = self._store.run_dir(slug, run_id)
            config = self._config(slug, run_id)
            pdf_path = self._store.source_path(slug, config.source_filename)

            self._set_stage(slug, run_id, stage, status=StageStatus.RUNNING, started_at=_now())
            document, skipped = parse(run_dir, pdf_path, config, force=force)
            self._set_stage(
                slug,
                run_id,
                stage,
                status=StageStatus.SKIPPED if skipped else StageStatus.DONE,
                finished_at=_now(),
                detail=f"{len(document.pages)} pages, {len(document.sections)} sections, "
                f"{len(document.tables)} tables",
            )

            stage = StageName.SEGMENT
            self._set_stage(slug, run_id, stage, status=StageStatus.RUNNING, started_at=_now())
            mapping = segment(run_dir, document, config)
            review = sum(a.needs_review for a in mapping.assignments)
            self._set_stage(
                slug,
                run_id,
                stage,
                status=StageStatus.DONE,
                finished_at=_now(),
                detail=f"{review} of {len(mapping.assignments)} sections flagged for review",
            )

            def finished(state: RunState) -> None:
                extracted = state.stages.get(StageName.EXTRACT)
                done = extracted is not None and extracted.status == StageStatus.DONE
                state.status = RunStatus.AWAITING_REVIEW if done else RunStatus.PARSED

            self._store.update_run(slug, run_id, finished)
        except Exception as exc:
            self._fail(slug, run_id, stage, exc)

    def _extract(self, slug: str, run_id: str, sheets: list[str] | None, force: bool) -> None:
        stage = StageName.EXTRACT
        try:
            assert self._llm_factory is not None
            run_dir = self._store.run_dir(slug, run_id)
            config = self._config(slug, run_id)
            document = load_parsed_document(run_dir)
            if document is None:
                raise ExtractionNotReadyError("parsed_document.json is missing")
            mapping = SectionMapping.model_validate_json(
                (run_dir / SECTION_MAPPING_FILE).read_text(encoding="utf-8")
            )
            study = self._store.get_study(slug)

            self._set_stage(slug, run_id, stage, status=StageStatus.RUNNING, started_at=_now())
            resolver = self._resolver_factory()
            if config.ct_version is None:
                config.ct_version = resolver.version
                write_model(run_dir / RUN_CONFIG_FILE, config)
            elif config.ct_version != resolver.version:
                raise ExtractionNotReadyError(
                    f"run is pinned to CDISC CT {config.ct_version} but the installed usdm4 "
                    f"provides {resolver.version}; start a new run to use the new release"
                )

            def on_update(agent_run: AgentRun) -> None:
                def apply(state: RunState) -> None:
                    state.agents[agent_run.sheet] = agent_run.model_copy(deep=True)

                self._store.update_run(slug, run_id, apply)

            extraction = asyncio.run(
                run_extraction(
                    run_dir,
                    document,
                    mapping,
                    config,
                    study,
                    self._llm_factory(),
                    resolver,
                    sheets=sheets,
                    force=force,
                    on_update=on_update,
                )
            )
            detail, produced, failed = _summarise(extraction.agents)
            self._set_stage(
                slug,
                run_id,
                stage,
                status=StageStatus.FAILED if failed else StageStatus.DONE,
                finished_at=_now(),
                detail=detail,
                error=None if not failed else f"{failed} agent(s) failed; see agent details",
            )

            def finished(state: RunState) -> None:
                state.status = RunStatus.AWAITING_REVIEW if produced else RunStatus.FAILED

            self._store.update_run(slug, run_id, finished)
        except Exception as exc:
            self._fail(slug, run_id, stage, exc)

    def _usdm(self, slug: str, run_id: str, force: bool) -> None:
        stage = StageName.USDM
        try:
            run_dir = self._store.run_dir(slug, run_id)
            self._set_stage(slug, run_id, stage, status=StageStatus.RUNNING, started_at=_now())
            report = generate_usdm(run_dir, slug, force=force)
            produced = report.file is not None
            self._set_stage(
                slug,
                run_id,
                stage,
                status=(
                    StageStatus.FAILED
                    if not produced
                    else StageStatus.SKIPPED
                    if report.reused
                    else StageStatus.DONE
                ),
                finished_at=_now(),
                detail=_usdm_detail(report),
                error=None if produced else "the workbook import produced no USDM; see the results",
            )

            def finished(state: RunState) -> None:
                state.status = RunStatus.COMPLETED if produced else RunStatus.FAILED

            self._store.update_run(slug, run_id, finished)
        except Exception as exc:
            self._fail(slug, run_id, stage, exc)


def _usdm_detail(report: UsdmReport) -> str:
    rules = report.rules
    parts = [
        f"{len(report.import_errors)} import error(s)",
        f"{rules.failed} of {rules.rules} rules failed, {rules.findings} finding(s) "
        f"({rules.expected_findings} expected)",
        f"CORE {'ran' if report.core.ran else 'not run'}",
    ]
    return ", ".join(parts)


def _summarise(agents: dict[str, AgentRun]) -> tuple[str, int, int]:
    counts: dict[str, int] = {}
    cost = 0.0
    for run in agents.values():
        counts[run.status.value] = counts.get(run.status.value, 0) + 1
        if run.usage is not None and run.status != AgentStatus.SKIPPED:
            cost += run.usage.cost_usd
    produced = counts.get("done", 0) + counts.get("skipped", 0)
    parts: list[Any] = [f"{n} {status}" for status, n in sorted(counts.items())]
    parts.append(f"${cost:.2f} this run")
    return ", ".join(parts), produced, counts.get("failed", 0)
